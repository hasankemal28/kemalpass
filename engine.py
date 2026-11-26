import io
import threading
import uuid
import shutil
from datetime import datetime, timezone
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from db import ROOT, connect, setting
from backend import GPUBackend

MODEL_ROOT=Path(__file__).parent / 'models'
Image.MAX_IMAGE_PIXELS=20_000_000
cv2.setNumThreads(1)
_matching_backend=None

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def vector(blob):
    return np.frombuffer(blob,dtype=np.float32)

def rank(feature, samples, threshold, margin):
    scores={}
    names={}
    gpu_scores=_matching_backend.similarities(feature,samples) if _matching_backend else None
    for index,row in enumerate(samples):
        pid=row['person_id']; names[pid]=row['name']
        score=float(gpu_scores[index]) if gpu_scores is not None else float(np.dot(feature,vector(row['embedding'])))
        scores[pid]=max(score,scores.get(pid,-1.0))
    ordered=sorted(scores.items(),key=lambda x:x[1],reverse=True)
    if not ordered: return None,None,0.0
    pid,score=ordered[0]
    runner=ordered[1][1] if len(ordered)>1 else -1
    if score>=threshold and score-runner>=margin: return pid,names[pid],score
    return None,None,score

class FaceEngine:
    def __init__(self):
        global _matching_backend
        self.lock=threading.RLock()
        self.detector=cv2.FaceDetectorYN.create(str(MODEL_ROOT/'yunet.onnx'),'',(320,320),0.88,0.3,1000)
        self.recognizer=cv2.FaceRecognizerSF.create(str(MODEL_ROOT/'sface.onnx'),'')
        self.gpu=GPUBackend(MODEL_ROOT/'sface.onnx')
        _matching_backend=self.gpu.matcher

    def decode(self, data):
        try:
            im=Image.open(io.BytesIO(data))
            if im.format not in ('JPEG','PNG','WEBP'): raise ValueError('JPG, PNG veya WebP yükleyin.')
            im=ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((1600,1600))
            return cv2.cvtColor(np.asarray(im),cv2.COLOR_RGB2BGR)
        except (UnidentifiedImageError,Image.DecompressionBombError,OSError):
            raise ValueError('Fotoğraf okunamadı veya boyutu çok büyük.')

    def extract(self, frame, minimum=40):
        with self.lock:
            h,w=frame.shape[:2]
            scale=min(1.0,960/max(w,h))
            small=cv2.resize(frame,(round(w*scale),round(h*scale))) if scale<1 else frame
            self.detector.setInputSize((small.shape[1],small.shape[0]))
            _,faces=self.detector.detect(small)
            result=[]
            if faces is None: return result
            for row in faces:
                face=row.copy(); face[:14]/=scale
                x,y,fw,fh=face[:4]
                if min(fw,fh)<minimum: continue
                aligned=self.recognizer.alignCrop(frame,face)
                x1=max(0,int(x-fw*.22)); y1=max(0,int(y-fh*.28))
                x2=min(w,int(x+fw*1.22)); y2=min(h,int(y+fh*1.22))
                crop=frame[y1:y2,x1:x2]
                gray=cv2.cvtColor(aligned,cv2.COLOR_BGR2GRAY)
                quality=float(cv2.Laplacian(gray,cv2.CV_64F).var())
                eye_distance=max(1.0,abs(float(face[4]-face[6])))
                pose=float((face[8]-(face[4]+face[6])/2)/eye_distance)
                result.append({'aligned':aligned.copy(),'crop':crop.copy(),'box':[int(x),int(y),int(fw),int(fh)],'quality':quality,'pose':pose})
        features=self.gpu.features([r['aligned'] for r in result]) if result else []
        if features is None:
            with self.lock: features=[self.recognizer.feature(r['aligned']).flatten().copy() for r in result]
        valid=[]
        for row,feature in zip(result,features):
            feat=np.asarray(feature,dtype=np.float32).reshape(-1); norm=np.linalg.norm(feat)
            if not np.isfinite(norm) or norm<1e-6: continue
            row['feature']=feat/norm; del row['aligned']; valid.append(row)
        return valid

    def photo(self,data):
        frame=self.decode(data)
        faces=self.extract(frame,40)
        if not faces: raise ValueError('Net bir yüz bulunamadı. Daha yakın ve önden çekilmiş fotoğraf yükleyin.')
        if len(faces)>1: raise ValueError('Fotoğrafta birden fazla yüz var. Tek kişinin fotoğrafını yükleyin.')
        if faces[0]['quality']<12: raise ValueError('Yüz çok bulanık. Daha net bir fotoğraf yükleyin.')
        return faces[0]

def save_crop(crop, folder='faces'):
    if shutil.disk_usage(ROOT).free<512*1024*1024:
        raise ValueError('Diskte yeterli boş alan kalmadı. Eski kayıtları temizleyin veya diski genişletin.')
    filename=f'{folder}/{uuid.uuid4().hex}.jpg'
    ok,encoded=cv2.imencode('.jpg',crop,[cv2.IMWRITE_JPEG_QUALITY,91])
    if not ok: raise ValueError('Fotoğraf kaydedilemedi.')
    (ROOT/filename).write_bytes(encoded.tobytes())
    return filename

def samples():
    with connect() as db:
        return db.execute('SELECT s.person_id,s.embedding,p.name FROM samples s JOIN people p ON p.id=s.person_id').fetchall()

def auto_learn(face,pid):
    config=setting('recognition')
    if not config.get('auto_learn',True) or face['quality']<45 or min(face['box'][2:])<85: return False
    # A stricter identity check and diversity filter limit self-training drift.
    match=rank(face['feature'],samples(),max(.72,config['threshold']+.15),max(.15,config['margin']))
    if match[0]!=pid: return False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT embedding,pose FROM samples WHERE person_id=?',(pid,)).fetchall()
        if len(existing)>=config.get('sample_limit',30): return False
        if not db.execute('SELECT id FROM people WHERE id=?',(pid,)).fetchone(): return False
        similarity=max((float(face['feature']@vector(r['embedding'])) for r in existing),default=1)
        pose_delta=min((abs(face.get('pose',0)-r['pose']) for r in existing),default=0)
        if similarity>.985 or (similarity>.94 and pose_delta<.10): return False
        latest=db.execute("SELECT max(created_at) FROM samples WHERE person_id=? AND origin='automatic'",(pid,)).fetchone()[0]
        if latest and (datetime.now(timezone.utc)-datetime.fromisoformat(latest)).total_seconds()<120: return False
        image=save_crop(face['crop'])
        db.execute('INSERT INTO samples(person_id,image,embedding,created_at,origin,quality,pose) VALUES (?,?,?,?,?,?,?)',
                   (pid,image,face['feature'].tobytes(),now(),'automatic',face['quality'],face.get('pose',0)))
    return True

def record(face, source='camera', camera=None, identity=None):
    camera=camera or setting('camera')
    config=setting('recognition')
    pid,name,score=identity or rank(face['feature'],samples(),config['threshold'],config['margin'])
    stamp=now(); unknown_id=None
    image=save_crop(face['crop'],'snapshots')
    with connect() as db:
        if pid is None:
            # Only group highly similar unlabelled sightings; no identity is inferred here.
            best=None; similarity=0.68
            rows=db.execute("SELECT * FROM unknowns WHERE state='pending' ORDER BY id DESC LIMIT 500").fetchall()
            for row in rows:
                s=float(np.dot(face['feature'],vector(row['embedding'])))
                if s>similarity: best=row; similarity=s
            if best:
                unknown_id=best['id']
                db.execute('UPDATE unknowns SET last_seen=? WHERE id=?',(stamp,unknown_id))
            else:
                cur=db.execute('INSERT INTO unknowns(image,embedding,first_seen,last_seen) VALUES (?,?,?,?)',
                    (image,face['feature'].tobytes(),stamp,stamp))
                unknown_id=cur.lastrowid
        cur=db.execute('''INSERT INTO events(person_id,person_name,unknown_id,occurred_at,last_seen,image,
          similarity,kind,source,camera,method,embedding,camera_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (pid,name,unknown_id,stamp,stamp,image,float(score),camera['kind'] if source=='camera' else 'Test',
           source,camera['name'],'automatic' if pid else 'pending',face['feature'].tobytes(),camera.get('id')))
        event_id=cur.lastrowid
    return {'id':event_id,'person_id':pid,'name':name,'unknown_id':unknown_id,'similarity':score,'image':image}
