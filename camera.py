import base64
import hashlib
import os
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, quote
from xml.sax.saxutils import escape
import cv2
import requests
from requests.auth import HTTPDigestAuth
from db import setting, connect
from engine import now, rank, record, samples, auto_learn
from overlay import annotate
from camera_store import get_camera,list_cameras,primary_camera,configured

_clock_lock=threading.Lock()
_clock_cache={}

def camera_clock_offset(url):
    """Use the camera UTC clock for WS-Security; event timestamps stay server-side."""
    parts=urlsplit(url);key=(parts.scheme,parts.netloc)
    with _clock_lock:
        cached=_clock_cache.get(key)
        if cached and time.monotonic()-cached[0]<300:return cached[1]
    endpoint=urlunsplit((parts.scheme,parts.netloc,'/onvif/device_service','',''))
    body='<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><tds:GetSystemDateAndTime xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/></s:Body></s:Envelope>'
    offset=0.0
    try:
        start=time.time()
        response=requests.post(endpoint,data=body.encode(),headers={'Content-Type':'application/soap+xml; charset=utf-8; action="http://www.onvif.org/ver10/device/wsdl/GetSystemDateAndTime"'},timeout=(3,4),allow_redirects=False)
        response.raise_for_status();root=ET.fromstring(response.content)
        utc=next(e for e in root.iter() if e.tag.split('}')[-1]=='UTCDateTime')
        values={e.tag.split('}')[-1]:int(e.text) for e in utc.iter() if e.text and e.tag.split('}')[-1] in ('Year','Month','Day','Hour','Minute','Second')}
        camera_time=datetime(values['Year'],values['Month'],values['Day'],values['Hour'],values['Minute'],values['Second'],tzinfo=timezone.utc).timestamp()
        offset=camera_time-(start+time.time())/2
    except (requests.RequestException,ET.ParseError,StopIteration,KeyError,ValueError):pass
    with _clock_lock:_clock_cache[key]=(time.monotonic(),offset)
    return offset

def rtsp_address(config):
    url=config.get('url','').strip()
    p=urlsplit(url)
    if p.scheme not in ('rtsp','rtsps') or not p.hostname: raise ValueError('Geçerli bir rtsp:// veya rtsps:// adresi girin.')
    if config.get('username'):
        host=p.hostname
        if ':' in host: host='['+host+']'
        net=quote(config['username'],safe='')+':'+quote(config.get('password',''),safe='')+'@'+host
        if p.port: net+=':'+str(p.port)
        url=urlunsplit((p.scheme,net,p.path,p.query,''))
    return url

def soap(url,action,body,config):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username: raise ValueError('ONVIF adresi http:// veya https:// ile başlamalıdır.')
    username=config.get('username',''); password=config.get('password','')
    nonce=os.urandom(20)
    offset=camera_clock_offset(config.get('onvif_url') or url) if username else 0
    created=datetime.fromtimestamp(time.time()+offset,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    digest=base64.b64encode(hashlib.sha1(nonce+created.encode()+password.encode()).digest()).decode()
    security=f'''<wsse:Security s:mustUnderstand="1" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken><wsse:Username>{escape(username)}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password><wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>'''
    xml=f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header>{security if username else ""}</s:Header><s:Body>{body}</s:Body></s:Envelope>'
    try:
        response=requests.post(url,data=xml.encode(),headers={'Content-Type':f'application/soap+xml; charset=utf-8; action="{action}"'},auth=HTTPDigestAuth(username,password) if username else None,timeout=(4,8),allow_redirects=False)
        response.raise_for_status()
        root=ET.fromstring(response.content)
        if any(e.tag.split('}')[-1]=='Fault' for e in root.iter()): raise ValueError('ONVIF isteği reddedildi. Hesabı, yetkileri ve cihaz saatini kontrol edin.')
        return root
    except (requests.RequestException,ET.ParseError):
        raise ValueError('ONVIF bağlantısı kurulamadı. Adresi, kullanıcıyı ve şifreyi kontrol edin.') from None

def media_url(config):
    endpoint=config.get('onvif_url','').strip()
    if not endpoint: raise ValueError('ONVIF servis adresini girin.')
    root=soap(endpoint,'http://www.onvif.org/ver10/device/wsdl/GetCapabilities','<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Category>Media</tds:Category></tds:GetCapabilities>',config)
    for e in root.iter():
        if e.tag.split('}')[-1]=='Media':
            for child in e:
                if child.tag.split('}')[-1]=='XAddr' and child.text: return child.text
    raise ValueError('Cihazda ONVIF Media servisi bulunamadı.')

def profiles(config):
    url=media_url(config)
    root=soap(url,'http://www.onvif.org/ver10/media/wsdl/GetProfiles','<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',config)
    found=[]
    for e in root.iter():
        if e.tag.split('}')[-1]=='Profiles' and e.get('token'):
            name=next((c.text for c in e if c.tag.split('}')[-1]=='Name'),None)
            found.append({'token':e.get('token'),'name':name or e.get('token')})
    if not found: raise ValueError('ONVIF yayın profili bulunamadı.')
    return url,found

def resolve(config):
    if config.get('mode')!='onvif': return rtsp_address(config)
    url,found=profiles(config)
    token=config.get('profile') or found[0]['token']
    if token not in [x['token'] for x in found]: raise ValueError('Seçilen ONVIF profili cihazda bulunamadı.')
    body=f'''<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{escape(token)}</trt:ProfileToken></trt:GetStreamUri>'''
    root=soap(url,'http://www.onvif.org/ver10/media/wsdl/GetStreamUri',body,config)
    uri=next((e.text for e in root.iter() if e.tag.split('}')[-1]=='Uri' and e.text),None)
    if not uri: raise ValueError('ONVIF cihazı RTSP adresi döndürmedi.')
    return rtsp_address(dict(config,url=uri))

def open_capture(url):
    return cv2.VideoCapture(url,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,6000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,6000])

class CameraWorker:
    def __init__(self,engine,camera_id=None,analysis_gate=None):
        self.engine=engine; self.lock=threading.RLock(); self.changed=threading.Event()
        self.camera_id=camera_id;self.analysis_gate=analysis_gate or threading.Semaphore(1);self.started=False
        self.frame=None; self.frame_time=0; self.sequence=0; self.preview=None
        self.overlay_faces=[]; self.overlay_until=0; self.camera_name='Kamera'
        self.state={'state':'waiting','message':'Kamera bekleniyor','last_frame':None,'last_event':None,'faces':0,'inference_ms':0}
        self.tracks=[]; self.recent={}; self.generation=0

    def start(self):
        with self.lock:
            if self.started:return
            self.started=True
        threading.Thread(target=self.capture_loop,daemon=True,name='camera-reader').start()
        threading.Thread(target=self.analyze_loop,daemon=True,name='face-recognition').start()
        threading.Thread(target=self.preview_loop,daemon=True,name='live-preview').start()

    def config(self):
        return get_camera(self.camera_id) if self.camera_id is not None else setting('camera')

    def refresh(self):
        with self.lock:
            self.generation+=1; self.frame=None; self.preview=None; self.tracks=[]; self.recent={}
            self.overlay_faces=[];self.overlay_until=0
        self.changed.set()

    def status(self):
        with self.lock: return dict(self.state)

    def update(self,**values):
        with self.lock: self.state.update(values)

    def capture_loop(self):
        while True:
            config=self.config(); generation=self.generation
            self.camera_name=config['name']
            if not config['enabled']:
                self.update(state='waiting',message='İzleme durduruldu' if configured(config) else 'Kamera bağlantısı eklenmedi',faces=0)
                self.changed.wait(2); self.changed.clear(); continue
            capture=None
            try:
                self.update(state='connecting',message='Kameraya bağlanılıyor…')
                url=resolve(config)
                capture=open_capture(url)
                if not capture.isOpened(): raise ValueError('Kamera yayını açılamadı. Bağlantı bilgilerini ve kameranın çevrimiçi olduğunu kontrol edin.')
                self.update(state='online',message='Kamera bağlı, yüz tanıma etkin')
                while generation==self.generation:
                    ok,frame=capture.read()
                    if not ok: raise ValueError('Görüntü kesildi. Otomatik yeniden bağlanılacak.')
                    with self.lock:
                        self.frame=frame; self.frame_time=time.monotonic(); self.sequence+=1
                        self.state['last_frame']=now()
            except Exception as exc:
                # Never log a URL containing camera credentials.
                message=str(exc) if isinstance(exc,ValueError) else 'Kamera bağlantısı kesildi. Yeniden denenecek.'
                self.update(state='offline',message=message,faces=0)
                with self.lock: self.frame=None
            finally:
                if capture is not None: capture.release()
            self.changed.wait(8); self.changed.clear()

    def analyze_loop(self):
        last_sequence=-1
        while True:
            config=setting('recognition')
            time.sleep(config['interval'])
            if not self.analysis_gate.acquire(timeout=.2):continue
            with self.lock:
                if self.frame is None or self.sequence==last_sequence or time.monotonic()-self.frame_time>5:
                    self.analysis_gate.release();continue
                frame=self.frame.copy(); last_sequence=self.sequence; generation=self.generation
            try:
                start=time.monotonic()
                faces=self.engine.extract(frame,config['min_face'])
                self.process(faces,config,generation)
                if generation==self.generation:
                    with self.lock:
                        self.overlay_faces=[{key:face[key] for key in ('box','label','confirmed','pending') if key in face} for face in faces]
                        self.overlay_until=time.monotonic()+min(1.5,max(1.0,config['interval']*1.5))
                        self.state.update(faces=len(faces),inference_ms=round((time.monotonic()-start)*1000))
            except Exception:
                self.update(message='Yüz işleme hatası; sonraki karede yeniden denenecek.')
            finally:self.analysis_gate.release()

    def preview_loop(self):
        previous=-1
        while True:
            time.sleep(.2)
            with self.lock:
                if self.frame is None or self.sequence==previous or time.monotonic()-self.frame_time>3:continue
                frame=self.frame.copy();previous=self.sequence;generation=self.generation
                faces=list(self.overlay_faces) if time.monotonic()<self.overlay_until else []
                name=self.camera_name
            try:
                rendered=annotate(frame,faces,name)
                ok,jpg=cv2.imencode('.jpg',rendered,[cv2.IMWRITE_JPEG_QUALITY,92])
                if ok and generation==self.generation:
                    with self.lock:self.preview=jpg.tobytes()
            except Exception:pass

    def process(self,faces,config,generation):
        stamp=time.monotonic(); bank=samples(); used=set()
        self.tracks=[t for t in self.tracks if stamp-t['seen']<8]
        camera=self.config()
        for face in faces:
            if face['quality']<12: continue
            pid,name,score=rank(face['feature'],bank,config['threshold'],config['margin'])
            face['person_id']=pid
            best=None; best_score=0.58
            for t in self.tracks:
                if id(t) in used: continue
                sim=float(face['feature'] @ t['feature'])
                if sim>best_score: best=t; best_score=sim
            identity=pid or 'unknown'
            if best is None:
                best={'feature':face['feature'],'seen':stamp,'identity':identity,'votes':0,'event':None}
                self.tracks.append(best)
            used.add(id(best)); best['seen']=stamp; best['feature']=face['feature']
            if best['identity']!=identity:
                best['identity']=identity; best['votes']=0
            best['votes']+=1
            confirmed=bool(pid and best['votes']>=config['confirmations'])
            face['confirmed']=confirmed;face['pending']=bool(pid and not confirmed)
            face['label']=name if confirmed else 'Doğrulanıyor…' if pid else 'Bilinmeyen · Bu kim?'
            if pid and best['votes']>=max(3,config['confirmations']) and not best.get('learned') and generation==self.generation:
                if auto_learn(face,pid): best['learned']=True
            if best['event']:
                # A continuous sighting remains one event even after manual labelling.
                if stamp-best.get('updated',0)>5:
                    with connect() as db: db.execute('UPDATE events SET last_seen=? WHERE id=?',(now(),best['event']))
                    best['updated']=stamp
                if pid: self.recent[pid]=(stamp,best['event'])
                continue
            if best['votes']<config['confirmations'] or generation!=self.generation or not camera['enabled']: continue
            if pid and pid in self.recent and stamp-self.recent[pid][0]<config['cooldown']:
                best['event']=self.recent[pid][1]; self.recent[pid]=(stamp,best['event']); continue
            event=record(face,'camera',camera,(pid,name,score))
            best['event']=event['id']
            if pid: self.recent[pid]=(stamp,event['id'])
            self.update(last_event=now())

class CameraManager:
    def __init__(self,engine,start=True):
        self.engine=engine;self.workers={};self.lock=threading.RLock()
        self.analysis_gate=threading.Semaphore(1);self.started=start
        self.sync()

    def worker(self,ident):
        get_camera(ident)
        with self.lock:
            if ident not in self.workers:self.workers[ident]=CameraWorker(self.engine,ident,self.analysis_gate)
            return self.workers[ident]

    def primary(self):return self.worker(primary_camera()['id'])

    def sync(self):
        for config in list_cameras():
            if config['enabled']:
                worker=self.worker(config['id'])
                if self.started:worker.start()

    def refresh(self,ident=None):
        targets=[ident] if ident is not None else list(self.workers)
        for target in targets:self.worker(target).refresh()
        self.sync()

    def camera_status(self,config):
        worker=self.workers.get(config['id'])
        state=worker.status() if worker else {'state':'waiting','message':'İzleme durduruldu' if configured(config) else 'Kamera bağlantısı eklenmedi',
          'last_frame':None,'last_event':None,'faces':0,'inference_ms':0}
        return {'id':config['id'],'name':config['name'],'kind':config['kind'],'position':config['position'],
          'enabled':config['enabled'],'configured':configured(config),'status':state}

    def statuses(self):return [self.camera_status(c) for c in list_cameras()]
