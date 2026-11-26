"""Account management and server-enforced screen/view/manage permissions."""
import functools
import json
import re
import secrets
from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import generate_password_hash
from db import connect, setting
from engine import now

accounts=Blueprint('accounts',__name__)
MODULES={'dashboard':'Genel bakış','monitor':'Sunucu durumu','live':'Canlı geçiş','people':'Kişiler','unknowns':'Bu kim?',
         'events':'Geçiş kayıtları','camera':'Kamera bağlantısı',
         'settings':'Sistem ayarları'}

def init_accounts():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
          permissions TEXT NOT NULL DEFAULT '{}', session_version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, last_login TEXT)''')
    legacy=setting('admin_password')
    with connect() as db:
        has_admin = db.execute("SELECT 1 FROM users WHERE is_admin=1 LIMIT 1").fetchone()
    if legacy and not has_admin:
        with connect() as db:
            db.execute('''INSERT OR IGNORE INTO users(username,display_name,password_hash,is_admin,permissions,created_at)
              VALUES ('giysunpass','KemalPass',?,1,'{}',?)''',(legacy,now()))

def load_user():
    g.current_user=None
    username=session.get('user')
    if not username:return
    with connect() as db: row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    if not row or not row['active']:
        session.clear();return
    version=session.get('session_version')
    # Preserve only the pre-migration administrator's already-signed session.
    if version is None and row['is_admin'] and row['session_version']==1:
        session['session_version']=row['session_version'];version=row['session_version']
    if version!=row['session_version']:
        session.clear();return
    g.current_user=dict(row,permissions=json.loads(row['permissions']))

def can(module,manage=False):
    user=getattr(g,'current_user',None)
    if not user:return False
    if module=='profile':return True
    if user['is_admin']:return True
    if module=='admin':return False
    level=user['permissions'].get(module,'none')
    return level=='manage' if manage else level in ('view','manage')

def me_data():
    user=g.current_user
    return {'user':user['username'],'display_name':user['display_name'],'is_admin':bool(user['is_admin']),
            'permissions':{key:('manage' if user['is_admin'] else user['permissions'].get(key,'none')) for key in MODULES},
            'csrf':session['csrf']}

def denial(message='Bu işlem için yetkiniz yok.',status=403):return jsonify(error=message),status

def authorize_request():
    endpoint=request.endpoint
    if endpoint in (None,'static','index','login','health'):return
    if not getattr(g,'current_user',None):return denial('Oturum açın.',401)
    if endpoint in ('me','logout','password'):return
    if endpoint.startswith('accounts.'):
        return None if can('admin') else denial()
    if endpoint=='status':return
    if endpoint=='media':return # Each image is authorized against its actual database references.
    if endpoint=='person_options':
        return None if any(can(m,True) for m in ('unknowns','events')) or can('people') else denial()
    if endpoint=='settings':return None if can('camera') or can('settings') else denial()
    if endpoint=='recent_people':return None if can('people') or can('events') else denial()
    if endpoint=='live_info':return None if can('live') else denial()
    if endpoint=='events':
        # The live feed is deliberately limited to recent real camera events.
        if can('events'):return
        if can('live') and request.args.get('source','camera')=='camera':return
        return denial()
    if endpoint in ('camera_frame','camera_stream'):
        return None if can('live') else denial()
    if endpoint in ('backup','audit_list'):
        return None if can('admin') else denial()
    module={
      'people':'people','add_person':'people','add_photos':'people','person_detail':'people','edit_person':'people',
      'delete_person':'people','delete_photo':'people','merge_person':'people','test_recognition':'people',
      'unknowns':'unknowns','resolve_unknown':'unknowns','ignore_unknown':'unknowns','export_csv':'events','correct_event':'events',
      'camera_collection':'camera','save_camera':'camera','toggle_camera':'camera','camera_profiles':'camera','camera_test':'camera','camera_test_image':'camera',
      'recognition_settings':'settings','clear_tests':'settings',
    }.get(endpoint)
    if not module:return denial()
    manage=request.method not in ('GET','HEAD','OPTIONS')
    return None if can(module,manage) else denial()

def public_user(row):
    return {key:row[key] for key in ('id','username','display_name','is_admin','active','created_at','last_login')} | {'permissions':{k:v for k,v in json.loads(row['permissions']).items() if k in MODULES}}

def validate_permissions(value):
    if not isinstance(value,dict):raise ValueError('Ekran izinleri geçersiz.')
    if any(key not in MODULES or level not in ('none','view','manage') for key,level in value.items()):raise ValueError('Ekran izinleri geçersiz.')
    return {key:value.get(key,'none') for key in MODULES}

@accounts.get('/api/admin/users')
def list_users():
    with connect() as db:rows=db.execute('SELECT * FROM users ORDER BY is_admin DESC,username').fetchall()
    return jsonify(users=[public_user(r) for r in rows],modules=MODULES)

@accounts.post('/api/admin/users')
def create_user():
    data=request.get_json() or {};username=str(data.get('username','')).strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,31}',username):raise ValueError('Kullanıcı adı 3–32 karakter olmalı; harf, rakam, nokta, alt çizgi ve tire kullanılabilir.')
    name=str(data.get('display_name','')).strip()
    if not 1<=len(name)<=100:raise ValueError('Görünen ad 1–100 karakter olmalı.')
    password=str(data.get('password',''))
    if not 8<=len(password)<=256:raise ValueError('Şifre 8–256 karakter olmalı.')
    permissions=validate_permissions(data.get('permissions',{}));hashed=generate_password_hash(password)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone():raise ValueError('Bu kullanıcı adı zaten kullanılıyor.')
        ident=db.execute('''INSERT INTO users(username,display_name,password_hash,permissions,active,created_at)
          VALUES (?,?,?,?,?,?)''',(username,name,hashed,json.dumps(permissions),int(bool(data.get('active',True))),now())).lastrowid
    return jsonify(ok=True,id=ident),201

@accounts.patch('/api/admin/users/<int:ident>')
def edit_user(ident):
    data=request.get_json() or {}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
        if not row:return denial('Kullanıcı bulunamadı.',404)
        name=str(data.get('display_name',row['display_name'])).strip()
        if not 1<=len(name)<=100:raise ValueError('Görünen ad 1–100 karakter olmalı.')
        active=bool(data.get('active',row['active']))
        if row['is_admin'] and not active:raise ValueError('Ana yönetici pasifleştirilemez.')
        permissions=validate_permissions(data.get('permissions',json.loads(row['permissions'])))
        hashed=row['password_hash'];password=data.get('password')
        if password:
            if not 8<=len(str(password))<=256:raise ValueError('Şifre 8–256 karakter olmalı.')
            hashed=generate_password_hash(str(password))
        # Administrators remain active; other users receive explicit screen permissions.
        db.execute('''UPDATE users SET display_name=?,active=?,permissions=?,password_hash=?,
          session_version=session_version+1 WHERE id=?''',(name,int(active),json.dumps(permissions),hashed,ident))
        version=row['session_version']+1
    if ident==g.current_user['id']:session['session_version']=version
    return jsonify(ok=True)
