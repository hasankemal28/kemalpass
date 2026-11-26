import csv
import functools
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import time
import zipfile
import shutil
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote
from zoneinfo import ZoneInfo

os.environ.setdefault('OPENCV_LOG_LEVEL','SILENT')
os.environ.setdefault('OPENCV_FFMPEG_LOGLEVEL','-8')
os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS','rtsp_transport;tcp')
import cv2
from flask import Flask, request, session, jsonify, render_template, send_from_directory, send_file, redirect, Response, g
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from db import ROOT, init, connect, setting, put_setting
from engine import FaceEngine, now, save_crop, record, samples, rank
from camera import CameraWorker, CameraManager, profiles, resolve, open_capture
import camera_store as cameras
from metrics import SystemMetrics
from accounts import init_accounts, load_user, can, me_data, authorize_request, accounts

os.umask(0o077)
init()
init_accounts()
cameras.init_cameras()
secret_path=ROOT/'secret'
if not secret_path.exists(): secret_path.write_text(secrets.token_hex(48))
app=Flask(__name__)
app.config.update(SECRET_KEY=secret_path.read_text(),MAX_CONTENT_LENGTH=24*1024*1024,
    SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
engine=FaceEngine()
manager=CameraManager(engine,start=os.environ.get("FACEPASS_NO_WORKER")!="1")
worker=manager.primary()
metrics=SystemMetrics(start=os.environ.get('FACEPASS_NO_WORKER')!='1')

attempts=defaultdict(deque)
attempt_lock=threading.Lock()
camera_test_lock=threading.Lock()

def fail(message,status=400):
    return jsonify(error=message),status

def auth(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        if not getattr(g,'current_user',None): return fail('Oturum açın.',401)
        return fn(*args,**kwargs)
    return wrapped

@app.before_request
def protect():
    load_user()
    denied=authorize_request()
    if denied is not None:return denied
    if request.method in ('POST','PATCH','DELETE','PUT') and request.path!='/api/login':
        expected=session.get('csrf','')
        if not expected or not hmac.compare_digest(expected,request.headers.get('X-CSRF-Token','')):
            return fail('Oturum doğrulaması başarısız. Sayfayı yenileyin.',403)
    if request.path=='/api/login' and request.method=='POST':
        origin=request.headers.get('Origin')
        if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
            return fail('Geçersiz istek kaynağı.',403)

@app.after_request
def headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers['Cache-Control']='no-store'
    if request.method in ('POST','PATCH','DELETE') and response.status_code<400 and session.get('user'):
        try:
            with connect() as db:
                db.execute('INSERT INTO audit(at,actor,action,target) VALUES (?,?,?,?)',(now(),session['user'],request.method,request.path))
        except sqlite3.Error: pass
    return response

@app.errorhandler(Exception)
def error(exc):
    if type(exc) is LookupError:return fail(str(exc),404)
    if isinstance(exc,ValueError): return fail(str(exc))
    if isinstance(exc,HTTPException):
        message='En fazla 24 MB yükleyebilirsiniz.' if exc.code==413 else 'İstek işlenemedi.'
        return fail(message,exc.code)
    app.logger.error('Request failed: %s',type(exc).__name__)
    return fail('İşlem tamamlanamadı. Lütfen tekrar deneyin.',500)

@app.get('/')
def index():
    return render_template('index.html' if getattr(g,'current_user',None) else 'login.html')

@app.post('/api/login')
def login():
    data=request.get_json(silent=True) or {}; address=request.remote_addr
    with attempt_lock:
        q=attempts[address]; current=time.monotonic()
        while q and current-q[0]>300: q.popleft()
        if len(q)>=10: return fail('Çok fazla deneme. 5 dakika sonra yeniden deneyin.',429)
        q.append(current)
    username=str(data.get('username','')).strip().lower()
    with connect() as db:user=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    password=user['password_hash'] if user else generate_password_hash('invalid-login-comparison')
    valid=check_password_hash(password,str(data.get('password','')))
    if not user or not user['active'] or not valid:
        return fail('Kullanıcı adı veya şifre yanlış.',401)
    with attempt_lock: attempts.pop(address,None)
    session.clear(); session.permanent=True; session['user']=user['username']; session['session_version']=user['session_version']; session['csrf']=secrets.token_urlsafe(32)
    with connect() as db:db.execute('UPDATE users SET last_login=? WHERE id=?',(now(),user['id']))
    return jsonify(ok=True)

@app.post('/api/logout')
@auth
def logout():
    session.clear(); return jsonify(ok=True)

@app.get('/api/me')
@auth
def me(): return jsonify(me_data())

@app.post('/api/password')
@auth
def password():
    data=request.get_json() or {}
    if not check_password_hash(g.current_user['password_hash'],data.get('current','')): return fail('Mevcut şifre yanlış.')
    new=data.get('password','')
    if not 8<=len(new)<=256: return fail('Yeni şifre 8–256 karakter olmalı.')
    with connect() as db:
        db.execute('UPDATE users SET password_hash=?,session_version=session_version+1 WHERE id=?',(generate_password_hash(new),g.current_user['id']))
    session['session_version']=g.current_user['session_version']+1
    session['csrf']=secrets.token_urlsafe(32)
    return jsonify(ok=True,csrf=session['csrf'])

@app.get('/api/status')
@auth
def status():
    midnight=datetime.now(ZoneInfo('Europe/Istanbul')).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat(timespec='seconds')
    with connect() as db:
        stats={
          'people':db.execute('SELECT count(*) FROM people').fetchone()[0],
          'samples':db.execute('SELECT count(*) FROM samples').fetchone()[0],
          'pending':db.execute("SELECT count(*) FROM unknowns WHERE state='pending'").fetchone()[0],
          'today':db.execute("SELECT count(*) FROM events WHERE source='camera' AND occurred_at>=?",(midnight,)).fetchone()[0],
        }
    disk=shutil.disk_usage(ROOT)
    visible=can('monitor') or can('settings')
    return jsonify(stats={key:(value if can('dashboard') or (key in ('people','samples') and can('people')) or (key=='pending' and can('unknowns')) or (key=='today' and (can('events') or can('live'))) else None) for key,value in stats.items()},
       camera=worker.status() if any(can(m) for m in ('dashboard','live','camera')) else {'state':'waiting','message':'','inference_ms':0},
       engine=dict(name='YuNet + SFace',opencv=cv2.__version__,**engine.gpu.status()) if visible else None,
       storage={'free_gb':round(disk.free/1024**3,1),'used_gb':round(disk.used/1024**3,1),'total_gb':round(disk.total/1024**3,1),'percent':round(disk.used/disk.total*100,1) if disk.total else None,'low':disk.free<2*1024**3} if visible else None,
       system=metrics.snapshot() if can('monitor') else None,server_time=now())

@app.get('/api/people')
@auth
def people():
    with connect() as db:
        rows=db.execute('''SELECT p.*,count(s.id) AS sample_count,
          (SELECT image FROM samples WHERE person_id=p.id ORDER BY id LIMIT 1) AS image,
          (SELECT max(occurred_at) FROM events WHERE person_id=p.id AND source='camera') AS last_seen
          FROM people p LEFT JOIN samples s ON s.person_id=p.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE''').fetchall()
    return jsonify(people=[dict(r) for r in rows])

@app.get('/api/person-options')
@auth
def person_options():
    with connect() as db:rows=db.execute('SELECT id,name FROM people ORDER BY name COLLATE NOCASE').fetchall()
    return jsonify(people=[dict(r) for r in rows])

@app.get('/api/live-info')
@auth
def live_info():
    primary=cameras.primary_camera()
    return jsonify(name=primary['name'],status=manager.worker(primary['id']).status(),cameras=manager.statuses())

def extract_uploads():
    files=request.files.getlist('photos')
    files=[f for f in files if f.filename]
    if not files or len(files)>12: raise ValueError('1–12 fotoğraf seçin.')
    results=[]
    for f in files:
        try: results.append(engine.photo(f.read()))
        except ValueError as exc: raise ValueError(f'{Path(f.filename).name[:80]}: {exc}') from None
    return results

def person_name(value):
    name=str(value or '').strip()
    if not name or len(name)>100: raise ValueError('1–100 karakterlik bir isim girin.')
    return name

@app.post('/api/people')
@auth
def add_person():
    name=person_name(request.form.get('name')); note=request.form.get('note','').strip()[:1000]
    faces=extract_uploads(); written=[]
    try:
        with connect() as db:
            pid=db.execute('INSERT INTO people(name,note,created_at) VALUES (?,?,?)',(name,note,now())).lastrowid
            for face in faces:
                image=save_crop(face['crop']); written.append(image)
                db.execute('INSERT INTO samples(person_id,image,embedding,created_at,quality,pose) VALUES (?,?,?,?,?,?)',(pid,image,face['feature'].tobytes(),now(),face['quality'],face.get('pose',0)))
    except Exception:
        for image in written: (ROOT/image).unlink(missing_ok=True)
        raise
    return jsonify(id=pid,name=name,samples=len(faces)),201

@app.post('/api/people/<int:pid>/photos')
@auth
def add_photos(pid):
    faces=extract_uploads(); written=[]
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM people WHERE id=?',(pid,)).fetchone(): return fail('Kişi bulunamadı.',404)
            count=db.execute('SELECT count(*) FROM samples WHERE person_id=?',(pid,)).fetchone()[0]
            if count+len(faces)>setting('recognition').get('sample_limit',30): return fail(f'Kişi fotoğraf sınırı doluyor. Şu an {count} fotoğraf var; ayarlardan sınırı değiştirebilirsiniz.')
            for face in faces:
                image=save_crop(face['crop']); written.append(image)
                db.execute('INSERT INTO samples(person_id,image,embedding,created_at,quality,pose) VALUES (?,?,?,?,?,?)',(pid,image,face['feature'].tobytes(),now(),face['quality'],face.get('pose',0)))
    except Exception:
        for image in written: (ROOT/image).unlink(missing_ok=True)
        raise
    return jsonify(ok=True,samples=len(faces))

@app.get('/api/people/<int:pid>')
@auth
def person_detail(pid):
    with connect() as db:
        p=db.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not p: return fail('Kişi bulunamadı.',404)
        photos=db.execute('SELECT id,image,created_at,origin FROM samples WHERE person_id=? ORDER BY id',(pid,)).fetchall()
    return jsonify(person=dict(p),photos=[dict(r) for r in photos])

@app.patch('/api/people/<int:pid>')
@auth
def edit_person(pid):
    data=request.get_json(); name=person_name(data.get('name')); note=str(data.get('note',''))[:1000]
    with connect() as db:
        if not db.execute('SELECT id FROM people WHERE id=?',(pid,)).fetchone(): return fail('Kişi bulunamadı.',404)
        db.execute('UPDATE people SET name=?,note=? WHERE id=?',(name,note,pid))
        db.execute('UPDATE events SET person_name=? WHERE person_id=?',(name,pid))
    return jsonify(ok=True)

@app.delete('/api/people/<int:pid>')
@auth
def delete_person(pid):
    with connect() as db:
        db.execute('DELETE FROM people WHERE id=?',(pid,))
    purge_orphans(); worker.refresh()
    return jsonify(ok=True)

@app.delete('/api/photos/<int:sid>')
@auth
def delete_photo(sid):
    with connect() as db: db.execute('DELETE FROM samples WHERE id=?',(sid,))
    purge_orphans(); return jsonify(ok=True)

def event_query():
    clauses=[]; args=[]
    pid=request.args.get('person_id','')
    if pid:
        clauses.append('e.person_id=?'); args.append(int(pid))
    camera_id=request.args.get('camera_id')
    if camera_id:clauses.append('e.camera_id=?');args.append(int(camera_id))
    kind=request.args.get('kind')
    if kind in cameras.KINDS:clauses.append('e.kind=?');args.append(kind)
    source=request.args.get('source','camera')
    if source in ('camera','test'): clauses.append('e.source=?'); args.append(source)
    if request.args.get('state')=='unknown': clauses.append('e.person_id IS NULL')
    for field,operator,hour in [('from','>=','00:00:00'),('to','<','00:00:00')]:
        value=request.args.get(field)
        if value:
            try:
                date=datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=ZoneInfo('Europe/Istanbul'))
                if field=='to': date+=timedelta(days=1)
                clauses.append('e.occurred_at '+operator+' ?'); args.append(date.astimezone(timezone.utc).isoformat(timespec='seconds'))
            except ValueError: raise ValueError('Tarih biçimi geçersiz.')
    return (' WHERE '+' AND '.join(clauses)) if clauses else '',args

@app.get('/api/events')
@auth
def events():
    where,args=event_query() if can('events') else (" WHERE e.source='camera'",[])
    page=max(1,int(request.args.get('page',1))) if can('events') else 1; limit=30 if can('events') else 10
    with connect() as db:
        total=db.execute('SELECT count(*) FROM events e'+where,args).fetchone()[0]
        rows=db.execute('''SELECT e.id,e.person_id,e.person_name,e.unknown_id,e.occurred_at,e.last_seen,
          e.image,e.similarity,e.kind,e.source,e.camera,e.camera_id,e.method FROM events e'''+where+' ORDER BY e.occurred_at DESC,e.id DESC LIMIT ? OFFSET ?',args+[limit,(page-1)*limit]).fetchall()
    return jsonify(events=[dict(r) for r in rows],total=total,page=page,pages=max(1,(total+limit-1)//limit))

@app.get('/api/recent-people')
@auth
def recent_people():
    with connect() as db:
        rows=db.execute('''SELECT id,person_id,person_name,image,occurred_at,kind FROM
          (SELECT *,row_number() OVER(PARTITION BY person_id ORDER BY occurred_at DESC,id DESC) AS rn
           FROM events WHERE person_id IS NOT NULL AND source='camera')
          WHERE rn=1 ORDER BY occurred_at DESC LIMIT 16''').fetchall()
    return jsonify(people=[dict(r) for r in rows])

@app.get('/api/events.csv')
@auth
def export_csv():
    where,args=event_query()
    with connect() as db:
        rows=db.execute('SELECT e.* FROM events e'+where+' ORDER BY occurred_at DESC LIMIT 100000',args).fetchall()
    out=io.StringIO(); writer=csv.writer(out,delimiter=';')
    writer.writerow(['Kişi','Tarih / Türkiye saati','Son görülme','Olay','Kamera','Kaynak','Doğrulama','Benzerlik'])
    def clean(value):
        text=str(value or '')
        return "'"+text if text[:1] in ('=','+','-','@','\t','\r') else text
    for r in rows:
        writer.writerow([clean(r['person_name'] or 'Bilinmeyen'),local_time(r['occurred_at']),local_time(r['last_seen']),clean(r['kind']),clean(r['camera']),r['source'],r['method'],round(r['similarity'] or 0,4)])
    return send_file(io.BytesIO(('\ufeff'+out.getvalue()).encode()),mimetype='text/csv',as_attachment=True,download_name='gecis-kayitlari.csv')

def local_time(value): return datetime.fromisoformat(value).astimezone(ZoneInfo('Europe/Istanbul')).strftime('%d.%m.%Y %H:%M:%S')

@app.get('/api/unknowns')
@auth
def unknowns():
    with connect() as db:
        rows=db.execute('''SELECT u.id,u.image,u.first_seen,u.last_seen,
          (SELECT count(*) FROM events WHERE unknown_id=u.id AND source='camera') AS sightings,
          (SELECT count(*) FROM events WHERE unknown_id=u.id AND source='test') AS tests
          FROM unknowns u WHERE u.state='pending' ORDER BY u.last_seen DESC LIMIT 200''').fetchall()
    return jsonify(unknowns=[dict(r) for r in rows])

@app.post('/api/unknowns/<int:uid>/resolve')
@auth
def resolve_unknown(uid):
    data=request.get_json(); pid=data.get('person_id'); name=data.get('name')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        u=db.execute("SELECT * FROM unknowns WHERE id=? AND state='pending'",(uid,)).fetchone()
        if not u: return fail('Bekleyen yüz bulunamadı.',404)
        if pid:
            p=db.execute('SELECT * FROM people WHERE id=?',(int(pid),)).fetchone()
            if not p: return fail('Kişi bulunamadı.',404)
            pid=p['id']; name=p['name']
        else:
            name=person_name(name)
            pid=db.execute('INSERT INTO people(name,note,created_at) VALUES (?,?,?)',(name,'Bekleyen yüzden oluşturuldu',now())).lastrowid
        count=db.execute('SELECT count(*) FROM samples WHERE person_id=?',(pid,)).fetchone()[0]
        if count<setting('recognition').get('sample_limit',30): db.execute("INSERT INTO samples(person_id,image,embedding,created_at,origin) VALUES (?,?,?,?,'confirmed')",(pid,u['image'],u['embedding'],now()))
        db.execute("UPDATE unknowns SET state='resolved',person_id=? WHERE id=?",(pid,uid))
        count=db.execute("UPDATE events SET person_id=?,person_name=?,method='manual' WHERE unknown_id=?",(pid,name,uid)).rowcount
    return jsonify(ok=True,person_id=pid,name=name,updated_events=count)

@app.post('/api/unknowns/<int:uid>/ignore')
@auth
def ignore_unknown(uid):
    with connect() as db: db.execute("UPDATE unknowns SET state='ignored' WHERE id=?",(uid,))
    return jsonify(ok=True)

@app.post('/api/events/<int:eid>/correct')
@auth
def correct_event(eid):
    data=request.get_json(); pid=int(data.get('person_id',0))
    with connect() as db:
        event=db.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        person=db.execute('SELECT * FROM people WHERE id=?',(pid,)).fetchone()
        if not event or not person: return fail('Kayıt veya kişi bulunamadı.',404)
        db.execute("UPDATE events SET person_id=?,person_name=?,method='manual' WHERE id=?",(pid,person['name'],eid))
    return jsonify(ok=True)

@app.post('/api/recognize')
@auth
def test_recognition():
    f=request.files.get('photo')
    if not f: return fail('Bir fotoğraf seçin.')
    face=engine.photo(f.read()); config=setting('recognition')
    match=rank(face['feature'],samples(),config['threshold'],config['margin'])
    if request.form.get('record')=='true':
        result=record(face,'test',identity=match)
    else: result={'person_id':match[0],'name':match[1],'similarity':match[2]}
    return jsonify(result=result,note='Fotoğraf testi, gerçek kamera geçişlerinden ayrı tutulur.')

def public_camera(config):
    out=dict(config); out['password_set']=bool(out.pop('password','')); return out

def camera_config(data, camera_id=None):
    old=cameras.get_camera(camera_id) if camera_id is not None else cameras.primary_camera(); config=dict(old)
    for key in ('mode','url','onvif_url','username','profile','name','kind'):
        if key in data: config[key]=str(data[key]).strip()[:2000]
    if data.get('clear_password'): config['password']=''
    elif data.get('password'): config['password']=str(data['password'])[:500]
    if config['mode'] not in ('rtsp','onvif'): raise ValueError('Bağlantı türü geçersiz.')
    if config['kind'] not in ('Giriş','Çıkış'): raise ValueError('Olay etiketi geçersiz.')
    if not config['name'] or len(config['name'])>100: raise ValueError('Kamera adı 1–100 karakter olmalı.')
    if config['url']:
        p=urlsplit(config['url'])
        if p.scheme not in ('rtsp','rtsps') or not p.hostname: raise ValueError('RTSP adresi geçersiz.')
        if p.username is not None:
            config['username']=unquote(p.username); config['password']=unquote(p.password or '')
            host=p.hostname
            if ':' in host: host='['+host+']'
            if p.port: host+=':'+str(p.port)
            config['url']=urlunsplit((p.scheme,host,p.path,p.query,''))
    if config['onvif_url']:
        p=urlsplit(config['onvif_url'])
        if p.scheme not in ('http','https') or not p.hostname or p.username: raise ValueError('ONVIF servis adresi geçersiz.')
    return config

@app.get('/api/settings')
@auth
def settings():
    return jsonify(camera=cameras.public_camera(cameras.primary_camera()) if can('camera') else None,cameras=[cameras.public_camera(c) for c in cameras.list_cameras()] if can('camera') else None,recognition=setting('recognition') if can('settings') else None,retention_days=setting('retention_days',90) if can('settings') else None)

@app.post('/api/cameras/<int:camera_id>')
@app.post('/api/camera')
@auth
def save_camera(camera_id=None):
    config=camera_config(request.get_json() or {},camera_id)
    config['enabled']=False
    cameras.save_camera(config['id'],config); manager.refresh(config['id'])
    return jsonify(camera=public_camera(config),message='Ayarlar kaydedildi. Bağlantıyı test edip izlemeyi başlatabilirsiniz.')

@app.post('/api/cameras/<int:camera_id>/toggle')
@app.post('/api/camera/toggle')
@auth
def toggle_camera(camera_id=None):
    config=cameras.get_camera(camera_id) if camera_id is not None else cameras.primary_camera(); enabled=bool((request.get_json() or {}).get('enabled'))
    if enabled and not config.get('url' if config['mode']=='rtsp' else 'onvif_url'): return fail('Önce kamera adresini kaydedin.')
    config['enabled']=enabled; cameras.save_camera(config['id'],config); manager.refresh(config['id'])
    return jsonify(ok=True,enabled=enabled)

@app.post('/api/cameras/<int:camera_id>/profiles')
@app.post('/api/camera/profiles')
@auth
def camera_profiles(camera_id=None):
    config=camera_config(request.get_json() or {},camera_id)
    _,items=profiles(config)
    return jsonify(profiles=items)

@app.post('/api/cameras/<int:camera_id>/test')
@app.post('/api/camera/test')
@auth
def camera_test(camera_id=None):
    if not camera_test_lock.acquire(blocking=False): return fail('Bir bağlantı testi zaten çalışıyor.',409)
    capture=None
    try:
        config=camera_config(request.get_json() or {},camera_id)
        capture=open_capture(resolve(config))
        if not capture.isOpened(): return fail('Yayın açılamadı. Kamera adresini, ağ bağlantısını ve şifreyi kontrol edin.')
        ok,frame=capture.read()
        if not ok: return fail('Bağlantı kuruldu ancak görüntü alınamadı.')
        faces=engine.extract(frame,setting('recognition')['min_face'])
        path=ROOT/f"camera-test-{config['id']}.jpg"; cv2.imwrite(str(path),frame)
        return jsonify(ok=True,width=frame.shape[1],height=frame.shape[0],faces=len(faces),preview=f"/api/cameras/{config['id']}/test-image?t="+secrets.token_hex(4))
    finally:
        if capture is not None: capture.release()
        camera_test_lock.release()

@app.get('/api/cameras/<int:camera_id>/test-image')
@app.get('/api/camera/test-image')
@auth
def camera_test_image(camera_id=None):
    config=cameras.get_camera(camera_id) if camera_id is not None else cameras.primary_camera()
    return send_file(ROOT/f"camera-test-{config['id']}.jpg",mimetype='image/jpeg')

@app.get('/api/cameras/<int:camera_id>/frame')
@app.get('/api/camera/frame')
@auth
def camera_frame(camera_id=None):
    worker=manager.worker(camera_id) if camera_id is not None else manager.primary()
    with worker.lock: image=worker.preview
    if not image: return '',204
    return send_file(io.BytesIO(image),mimetype='image/jpeg')

@app.get('/api/cameras/<int:camera_id>/stream')
@app.get('/api/camera/stream')
@auth
def camera_stream(camera_id=None):
    worker=manager.worker(camera_id) if camera_id is not None else manager.primary()
    with worker.lock: ready=worker.preview is not None
    if not ready:return '',204
    user_id=g.current_user['id'];version=g.current_user['session_version']
    def frames():
        previous=None;deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            with worker.lock: jpg=worker.preview; state=worker.state['state']
            if state!='online' or jpg is None:break
            with connect() as db: user=db.execute('SELECT active,session_version FROM users WHERE id=?',(user_id,)).fetchone()
            if not user or not user['active'] or user['session_version']!=version:break
            if jpg is not previous:
                yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpg)).encode()+b'\r\n\r\n'+jpg+b'\r\n'
                previous=jpg
            time.sleep(.15)
    response=Response(frames(),mimetype='multipart/x-mixed-replace; boundary=frame')
    response.headers['X-Accel-Buffering']='no'
    return response

@app.post('/api/recognition-settings')
@auth
def recognition_settings():
    data=request.get_json(); config=setting('recognition')
    limits={'threshold':(.363,.85,float),'margin':(.02,.25,float),'interval':(.4,5,float),'cooldown':(5,600,int),'min_face':(35,200,int),'confirmations':(2,6,int),'sample_limit':(10,100,int)}
    for key,(lo,hi,kind) in limits.items():
        if key in data:
            value=kind(data[key])
            if not lo<=value<=hi: return fail(f'{key} izin verilen aralıkta değil.')
            config[key]=value
    if 'auto_learn' in data: config['auto_learn']=bool(data['auto_learn'])
    if 'retention_days' in data:
        days=int(data['retention_days'])
        if not 1<=days<=3650: return fail('Saklama süresi 1–3650 gün olmalı.')
        put_setting('retention_days',days)
    put_setting('recognition',config)
    return jsonify(ok=True)

@app.get('/api/audit')
@auth
def audit_list():
    with connect() as db: rows=db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 60').fetchall()
    return jsonify(audit=[dict(r) for r in rows])

@app.post('/api/people/<int:pid>/merge')
@auth
def merge_person(pid):
    target=int((request.get_json() or {}).get('target_id',0))
    if target==pid: return fail('Farklı bir kişi seçin.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        people=db.execute('SELECT id,name FROM people WHERE id IN (?,?)',(pid,target)).fetchall()
        if len(people)!=2: return fail('Kişiler bulunamadı.',404)
        name=next(r['name'] for r in people if r['id']==target)
        count=db.execute('SELECT count(*) FROM samples WHERE person_id=?',(target,)).fetchone()[0]
        room=max(0,setting('recognition').get('sample_limit',30)-count)
        ids=[r[0] for r in db.execute('SELECT id FROM samples WHERE person_id=? ORDER BY quality DESC,id LIMIT ?',(pid,room))]
        for ident in ids: db.execute('UPDATE samples SET person_id=? WHERE id=?',(target,ident))
        db.execute('UPDATE events SET person_id=?,person_name=? WHERE person_id=?',(target,name,pid))
        db.execute('UPDATE unknowns SET person_id=? WHERE person_id=?',(target,pid))
        db.execute('DELETE FROM people WHERE id=?',(pid,))
    worker.refresh();purge_orphans()
    return jsonify(ok=True,name=name)

@app.get('/media/<folder>/<filename>')
@auth
def media(folder,filename):
    if folder not in ('faces','snapshots') or not filename.endswith('.jpg'): return fail('Fotoğraf bulunamadı.',404)
    if not can('admin'):
        image=folder+'/'+filename; allowed=False
        with connect() as db:
            if can('people'):allowed=bool(db.execute('SELECT 1 FROM samples WHERE image=? LIMIT 1',(image,)).fetchone())
            if can('events'):allowed=allowed or bool(db.execute('SELECT 1 FROM events WHERE image=? LIMIT 1',(image,)).fetchone())
            if can('live'):allowed=allowed or bool(db.execute("SELECT 1 FROM (SELECT image FROM events WHERE source='camera' ORDER BY occurred_at DESC,id DESC LIMIT 10) WHERE image=?",(image,)).fetchone())
            if can('unknowns'):allowed=allowed or bool(db.execute("SELECT 1 FROM unknowns WHERE image=? AND state='pending' LIMIT 1",(image,)).fetchone())
        if not allowed:return fail('Bu fotoğrafa erişim yetkiniz yok.',403)
    return send_from_directory(ROOT/folder,filename)

def purge_orphans():
    with connect() as db:
        references={r[0] for r in db.execute('SELECT image FROM samples UNION SELECT image FROM events UNION SELECT image FROM unknowns')}
    for folder in ('faces','snapshots'):
        for path in (ROOT/folder).glob('*.jpg'):
            if folder+'/'+path.name not in references and time.time()-path.stat().st_mtime>60: path.unlink(missing_ok=True)

def cleanup():
    cutoff=(datetime.now(timezone.utc)-timedelta(days=setting('retention_days',90))).isoformat(timespec='seconds')
    with connect() as db:
        db.execute('DELETE FROM events WHERE last_seen<?',(cutoff,))
        db.execute('DELETE FROM unknowns WHERE last_seen<? AND NOT EXISTS(SELECT 1 FROM events WHERE unknown_id=unknowns.id)',(cutoff,))
    purge_orphans()

def maintenance():
    while True:
        time.sleep(3600)
        try: cleanup()
        except Exception: app.logger.error('Maintenance failed')

if os.environ.get('FACEPASS_NO_WORKER')!='1': threading.Thread(target=maintenance,daemon=True).start()

@app.post('/api/test-records/clear')
@auth
def clear_tests():
    with connect() as db:
        db.execute("DELETE FROM events WHERE source='test'")
        db.execute('DELETE FROM unknowns WHERE NOT EXISTS(SELECT 1 FROM events WHERE unknown_id=unknowns.id)')
    purge_orphans(); return jsonify(ok=True)

@app.get('/api/backup')
@auth
def backup():
    # Online SQLite backup produces a coherent database even during writes.
    with tempfile.TemporaryDirectory(prefix='facepass-backup-') as tmp:
        dbpath=Path(tmp)/'facepass.sqlite3'
        with connect() as source:
            target=sqlite3.connect(dbpath); source.backup(target); target.close()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(dbpath,'facepass.sqlite3')
            for folder in ('faces','snapshots'):
                for file in (ROOT/folder).glob('*.jpg'):
                    if file.exists(): archive.write(file,folder+'/'+file.name)
        out.seek(0)
    return send_file(out,mimetype='application/zip',as_attachment=True,download_name='facepass-yedek-'+datetime.now().strftime('%Y%m%d')+'.zip')

@app.get('/healthz')
def health():
    with connect() as db: db.execute('SELECT 1').fetchone()
    return jsonify(status='ok')

app.register_blueprint(accounts)

@app.route('/api/cameras',methods=['GET','POST'])
@auth
def camera_collection():
    if request.method=='POST':
        config=cameras.add_camera((request.get_json() or {}).get('kind','Giriş'))
        return jsonify(camera=cameras.public_camera(config)),201
    return jsonify(cameras=[dict(cameras.public_camera(c),status=manager.camera_status(c)['status']) for c in cameras.list_cameras()],max_cameras=cameras.MAX_CAMERAS)

if __name__=='__main__':
    from waitress import serve
    serve(app,host='0.0.0.0',port=8080,threads=12,channel_timeout=45,max_request_body_size=24*1024*1024)

