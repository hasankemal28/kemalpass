"""Persistent independent camera slots; safely migrates the former single camera."""
import json
from datetime import datetime,timezone
from db import connect

MAX_CAMERAS=32
KINDS=('Giriş','Çıkış')

def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def blank(kind='Giriş',position=1):
    return {'mode':'onvif','url':'','onvif_url':'','username':'','password':'','profile':'',
      'enabled':False,'name':f'{kind} {position}','kind':kind,'position':position}

def init_cameras():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS cameras(
          id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,position INTEGER NOT NULL,
          config TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(kind,position))''')
        if not db.execute('SELECT 1 FROM cameras LIMIT 1').fetchone():
            row=db.execute("SELECT value FROM settings WHERE key='camera'").fetchone()
            legacy=json.loads(row[0]) if row else {}
            legacy_kind=legacy.get('kind','Giriş')
            if legacy_kind not in KINDS:legacy_kind='Giriş'
            for kind in KINDS:
                for position in range(1,5):
                    config=blank(kind,position)
                    if kind==legacy_kind and position==1:
                        config.update(legacy);config.update(kind=kind,position=position)
                    db.execute('INSERT INTO cameras(kind,position,config,created_at) VALUES (?,?,?,?)',
                      (kind,position,json.dumps(config,ensure_ascii=False),stamp()))
        columns={r[1] for r in db.execute('PRAGMA table_info(events)')}
        if 'camera_id' not in columns:
            db.execute('ALTER TABLE events ADD COLUMN camera_id INTEGER REFERENCES cameras(id) ON DELETE SET NULL')
        db.execute('CREATE INDEX IF NOT EXISTS events_camera_time ON events(camera_id,occurred_at)')

def decode(row):
    config=json.loads(row['config']);config.update(id=row['id'],kind=row['kind'],position=row['position'])
    return config

def list_cameras():
    with connect() as db:
        return [decode(r) for r in db.execute("SELECT * FROM cameras ORDER BY CASE kind WHEN 'Giriş' THEN 0 ELSE 1 END,position,id")]

def get_camera(ident):
    with connect() as db:row=db.execute('SELECT * FROM cameras WHERE id=?',(ident,)).fetchone()
    if not row:raise LookupError('Kamera bulunamadı.')
    return decode(row)

def configured(config):return bool(config.get('url' if config.get('mode')=='rtsp' else 'onvif_url'))

def primary_camera():
    rows=list_cameras()
    return next((c for c in rows if configured(c)),rows[0])

def save_camera(ident,config):
    kind=config.get('kind','Giriş')
    if kind not in KINDS:raise ValueError('Kamera türü Giriş veya Çıkış olmalı.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM cameras WHERE id=?',(ident,)).fetchone()
        if not old:raise LookupError('Kamera bulunamadı.')
        position=old['position']
        if kind!=old['kind']:
            position=db.execute('SELECT coalesce(max(position),0)+1 FROM cameras WHERE kind=?',(kind,)).fetchone()[0]
        output=dict(config,kind=kind,position=position);output.pop('id',None)
        db.execute('UPDATE cameras SET kind=?,position=?,config=? WHERE id=?',(kind,position,json.dumps(output,ensure_ascii=False),ident))
    return get_camera(ident)

def add_camera(kind):
    if kind not in KINDS:raise ValueError('Giriş veya çıkış kamerası seçin.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT count(*) FROM cameras').fetchone()[0]>=MAX_CAMERAS:raise ValueError(f'En fazla {MAX_CAMERAS} kamera eklenebilir.')
        position=db.execute('SELECT coalesce(max(position),0)+1 FROM cameras WHERE kind=?',(kind,)).fetchone()[0]
        config=blank(kind,position)
        ident=db.execute('INSERT INTO cameras(kind,position,config,created_at) VALUES (?,?,?,?)',
          (kind,position,json.dumps(config,ensure_ascii=False),stamp())).lastrowid
    return get_camera(ident)

def clear_camera(ident):
    old=get_camera(ident)
    return save_camera(ident,blank(old['kind'],old['position']))

def public_camera(config):
    result=dict(config);result['password_set']=bool(result.pop('password',''))
    result['configured']=configured(config)
    return result
