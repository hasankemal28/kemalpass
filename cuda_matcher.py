"""CUDA driver-API face-template comparisons, including Kepler / CUDA 11.4.

Detection and SFace embedding extraction stay on their existing backends. This
module offloads only dot products between already-normalized face templates.
No CUDA toolkit, cuDNN, or Python GPU framework is required.
"""
import ctypes as C
import threading
import numpy as np

PTX=b'''.version 6.4
.target sm_35
.address_size 64
.visible .entry template_dot(
 .param .u64 bank, .param .u64 query, .param .u64 scores,
 .param .u32 rows, .param .u32 dimensions) {
 .reg .pred %p<3>;
 .reg .b32 %r<10>;
 .reg .b64 %rd<10>;
 .reg .f32 %f<4>;
 ld.param.u64 %rd1, [bank];
 ld.param.u64 %rd2, [query];
 ld.param.u64 %rd3, [scores];
 ld.param.u32 %r1, [rows];
 ld.param.u32 %r2, [dimensions];
 mov.u32 %r3, %tid.x;
 mov.u32 %r4, %ctaid.x;
 mov.u32 %r5, %ntid.x;
 mad.lo.u32 %r3, %r4, %r5, %r3;
 setp.ge.u32 %p1, %r3, %r1;
 @%p1 bra DONE;
 mov.u32 %r6, 0;
 mov.f32 %f0, 0f00000000;
 LOOP:
 setp.ge.u32 %p2, %r6, %r2;
 @%p2 bra STORE;
 mad.lo.u32 %r7, %r6, %r1, %r3;
 mul.wide.u32 %rd4, %r7, 4;
 add.u64 %rd5, %rd1, %rd4;
 mul.wide.u32 %rd6, %r6, 4;
 add.u64 %rd7, %rd2, %rd6;
 ld.global.f32 %f1, [%rd5];
 ld.global.f32 %f2, [%rd7];
 mul.f32 %f3, %f1, %f2;
 add.f32 %f0, %f0, %f3;
 add.u32 %r6, %r6, 1;
 bra LOOP;
 STORE:
 mul.wide.u32 %rd8, %r3, 4;
 add.u64 %rd9, %rd3, %rd8;
 st.global.f32 [%rd9], %f0;
 DONE: ret;
}
'''

class CUDAError(RuntimeError):pass

class Driver:
    def __init__(self):
        self.lib=C.CDLL('libcuda.so.1')
        definitions={
          'cuInit':[C.c_uint], 'cuDriverGetVersion':[C.POINTER(C.c_int)],
          'cuDeviceGetCount':[C.POINTER(C.c_int)],'cuDeviceGet':[C.POINTER(C.c_int),C.c_int],
          'cuDeviceGetName':[C.c_char_p,C.c_int,C.c_int],
          'cuDeviceComputeCapability':[C.POINTER(C.c_int),C.POINTER(C.c_int),C.c_int],
          'cuCtxCreate_v2':[C.POINTER(C.c_void_p),C.c_uint,C.c_int],
          'cuCtxPushCurrent_v2':[C.c_void_p], 'cuCtxPopCurrent_v2':[C.POINTER(C.c_void_p)],
          'cuModuleLoadData':[C.POINTER(C.c_void_p),C.c_void_p],
          'cuModuleGetFunction':[C.POINTER(C.c_void_p),C.c_void_p,C.c_char_p],
          'cuMemAlloc_v2':[C.POINTER(C.c_uint64),C.c_size_t], 'cuMemFree_v2':[C.c_uint64],
          'cuMemcpyHtoD_v2':[C.c_uint64,C.c_void_p,C.c_size_t],
          'cuMemcpyDtoH_v2':[C.c_void_p,C.c_uint64,C.c_size_t],
          'cuCtxSynchronize':[],
          'cuLaunchKernel':[C.c_void_p,C.c_uint,C.c_uint,C.c_uint,C.c_uint,C.c_uint,C.c_uint,C.c_uint,C.c_void_p,C.POINTER(C.c_void_p),C.POINTER(C.c_void_p)],
        }
        for name,types in definitions.items():
            fn=getattr(self.lib,name);fn.argtypes=types;fn.restype=C.c_int
        self.call('cuInit',0)
        value=C.c_int();self.call('cuDriverGetVersion',C.byref(value));self.version=value.value

    def call(self,name,*args):
        result=getattr(self.lib,name)(*args)
        if result:raise CUDAError(f'{name}: CUDA error {result}')

class DeviceMatcher:
    def __init__(self,driver,index):
        self.driver=driver;self.index=index;self.lock=threading.Lock()
        self.bank_key=None;self.buffers=[];self.shape=None;self.calls=0;self.active=True
        dev=C.c_int();driver.call('cuDeviceGet',C.byref(dev),index)
        name=C.create_string_buffer(256);driver.call('cuDeviceGetName',name,256,dev)
        self.name=name.value.decode()
        major=C.c_int();minor=C.c_int();driver.call('cuDeviceComputeCapability',C.byref(major),C.byref(minor),dev)
        self.capability=f'{major.value}.{minor.value}'
        if major.value*10+minor.value<35:raise CUDAError('Compute capability 3.5 or newer required')
        self.context=C.c_void_p();driver.call('cuCtxCreate_v2',C.byref(self.context),0,dev)
        try:
            self.module=C.c_void_p();driver.call('cuModuleLoadData',C.byref(self.module),C.cast(C.c_char_p(PTX),C.c_void_p))
            self.function=C.c_void_p();driver.call('cuModuleGetFunction',C.byref(self.function),self.module,b'template_dot')
        finally:
            popped=C.c_void_p();driver.call('cuCtxPopCurrent_v2',C.byref(popped))
        # Validate this device before advertising it as ready.
        query=np.array([1,0,0,0],np.float32)
        bank=[np.array([1,0,0,0],np.float32).tobytes(),np.array([0,1,0,0],np.float32).tobytes()]
        result=self.similarities(query,bank,count_call=False)
        if not np.allclose(result,[1,0],atol=1e-6):raise CUDAError('GPU comparison self-test failed')

    def similarities(self,feature,blobs,count_call=True):
        query=np.ascontiguousarray(feature,dtype=np.float32).reshape(-1)
        key=tuple(blobs);shape=(len(key),query.size)
        if not key:return np.empty(0,np.float32)
        if shape[0]*shape[1]*4>256*1024**2:raise ValueError('CUDA reference bank too large')
        with self.lock:
            d=self.driver;d.call('cuCtxPushCurrent_v2',self.context)
            try:
                if shape!=self.shape:
                    for memory in self.buffers:d.call('cuMemFree_v2',memory)
                    self.buffers=[];self.shape=None;self.bank_key=None
                    for size in (shape[0]*shape[1]*4,shape[1]*4,shape[0]*4):
                        memory=C.c_uint64();d.call('cuMemAlloc_v2',C.byref(memory),size);self.buffers.append(memory)
                    self.shape=shape
                bank_memory,query_memory,out_memory=self.buffers
                if key!=self.bank_key:
                    matrix=np.ascontiguousarray(np.stack([np.frombuffer(blob,dtype=np.float32) for blob in key]).T)
                    if matrix.shape!=(shape[1],shape[0]):raise ValueError('Face feature dimensions differ')
                    d.call('cuMemcpyHtoD_v2',bank_memory,C.c_void_p(matrix.ctypes.data),matrix.nbytes)
                    self.bank_key=key
                d.call('cuMemcpyHtoD_v2',query_memory,C.c_void_p(query.ctypes.data),query.nbytes)
                n=C.c_uint(shape[0]);dimensions=C.c_uint(shape[1])
                arguments=(C.c_void_p*5)(*[C.cast(C.pointer(value),C.c_void_p) for value in (bank_memory,query_memory,out_memory,n,dimensions)])
                d.call('cuLaunchKernel',self.function,(n.value+127)//128,1,1,128,1,1,0,None,arguments,None)
                out=np.empty(n.value,np.float32)
                d.call('cuMemcpyDtoH_v2',C.c_void_p(out.ctypes.data),out_memory,out.nbytes)
                if not np.all(np.isfinite(out)):raise CUDAError('GPU produced invalid similarity values')
                if count_call:self.calls+=1
                return out
            finally:
                popped=C.c_void_p();d.call('cuCtxPopCurrent_v2',C.byref(popped))

    def status(self):
        return {'id':self.index,'name':self.name,'backend':'CUDA karşılaştırma','status':'ready',
          'compute_capability':self.capability,'comparisons':self.calls}

class CUDAMatcher:
    def __init__(self):
        self.nodes=[];self.cursor=0;self.lock=threading.Lock();self.error=None;self.version=None
        try:
            driver=Driver();self.version=driver.version
            count=C.c_int();driver.call('cuDeviceGetCount',C.byref(count))
            for index in range(count.value):
                try:self.nodes.append(DeviceMatcher(driver,index))
                except Exception:self.error='Bir GPU karşılaştırma için başlatılamadı.'
        except Exception:pass

    def similarities(self,feature,rows):
        if not rows:return np.empty(0,np.float32)
        with self.lock:
            active=[node for node in self.nodes if node.active]
            if not active:return None
            node=active[self.cursor%len(active)];self.cursor+=1
        try:return node.similarities(feature,[row['embedding'] for row in rows])
        except CUDAError:
            node.active=False;self.error='GPU karşılaştırması başarısız; CPU’ya dönüldü.';return None
        except Exception:return None

    def devices(self):return [node.status() for node in self.nodes if node.active]
