"""Optional multi-NVIDIA-GPU SFace inference; CPU detection/alignment always remains active."""
import importlib.util
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from cuda_matcher import CUDAMatcher

class GPUBackend:
    def __init__(self,model):
        self.devices=[]; self.sessions=[]; self.cursor=0; self.lock=threading.Lock(); self.error=None
        self.matcher=CUDAMatcher()
        self.runtime_installed=importlib.util.find_spec('onnxruntime') is not None
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=index,name','--format=csv,noheader'],capture_output=True,text=True,timeout=8)
            if result.returncode: return
            cards=[line.split(',',1) for line in result.stdout.strip().splitlines() if ',' in line]
            if not cards: return
            if self.matcher.devices() and self.matcher.version<12000:
                self.error='CUDA 11.x eşleştirme etkin. Yüz bulma ve yüz şablonu çıkarma CPU üzerinde çalışır.';return
            if not self.runtime_installed:
                self.error='CUDA eşleştirme etkin; yüz şablonlarını da GPU’ya taşımak için uygun CUDA çalışma ortamı gerekir.' if self.matcher.devices() else 'GPU algılandı; GPU çalışma ortamı hazır değil.';return
            import onnxruntime as ort
            if hasattr(ort,'preload_dlls'): ort.preload_dlls()
            if 'CUDAExecutionProvider' not in ort.get_available_providers():
                self.error='CUDA sağlayıcısı kullanılamıyor; CPU etkin.'; return
            for index,name in cards:
                try:
                    options=ort.SessionOptions(); options.intra_op_num_threads=1; options.inter_op_num_threads=1; options.log_severity_level=3
                    session=ort.InferenceSession(str(model),sess_options=options,providers=[('CUDAExecutionProvider',{'device_id':int(index),'gpu_mem_limit':1024**3,'cudnn_conv_algo_search':'HEURISTIC'}),'CPUExecutionProvider'])
                    if 'CUDAExecutionProvider' not in session.get_providers(): continue
                    session.run(None,{session.get_inputs()[0].name:np.zeros((1,3,112,112),dtype=np.float32)})
                    self.sessions.append((session,threading.Lock()))
                    self.devices.append({'id':int(index),'name':name.strip(),'backend':'CUDA','status':'ready'})
                except Exception:
                    self.error='Bazı GPU’lar başlatılamadı. Uyumlu GPU’lar ve CPU kullanılmaya devam ediyor.'
            if self.sessions: self.pool=ThreadPoolExecutor(max_workers=len(self.sessions),thread_name_prefix='cuda')
        except (FileNotFoundError,subprocess.SubprocessError): pass
        except Exception: self.error='GPU çalışma ortamı başlatılamadı; CPU etkin.'

    def _run(self,pair,aligned):
        session,lock=pair
        # SFace expects unscaled RGB float32 NCHW, as in OpenCV FaceRecognizerSF.
        tensor=aligned[:,:,::-1].transpose(2,0,1)[None].astype(np.float32)
        with lock: return session.run(None,{session.get_inputs()[0].name:tensor})[0].reshape(-1)

    def features(self,images):
        if not self.sessions: return None
        jobs=[]
        with self.lock:
            for image in images:
                pair=self.sessions[self.cursor%len(self.sessions)]; self.cursor+=1
                jobs.append(self.pool.submit(self._run,pair,image))
        try: return [job.result(timeout=20) for job in jobs]
        except Exception:
            self.error='GPU çıkarımı başarısız; bu işlem CPU’ya aktarıldı.'
            return None

    def status(self):
        matching=self.matcher.devices()
        devices=self.devices or matching
        backend='CPU + '+str(len(self.devices))+' GPU' if self.sessions else 'CPU + CUDA eşleştirme' if matching else 'CPU'
        distribution='Yüz bulma CPU; yüz şablonları GPU; karşılaştırma CUDA.' if self.sessions else 'Yüz bulma ve yüz şablonu CPU; kayıtlı yüzlerle karşılaştırma CUDA.' if matching else 'Yüz bulma, yüz şablonu ve karşılaştırma CPU.'
        return {'backend':backend,'devices':devices,'gpu_runtime_installed':bool(self.runtime_installed or matching),
                'embedding_runtime_installed':self.runtime_installed,'gpu_note':self.matcher.error or self.error,
                'distribution':distribution,'cuda_comparison_calls':sum(d['comparisons'] for d in matching)}
