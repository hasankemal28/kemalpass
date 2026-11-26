"""Read-only host telemetry, sampled off the HTTP request path."""
import os
import re
import subprocess
import threading
import time
from pathlib import Path

class SystemMetrics:
    def __init__(self,start=True):
        self.lock=threading.Lock(); self.previous=None
        self.value={'cpu':{'percent':None,'cores':os.cpu_count()},'ram':{},'gpus':[],'sampled_at':None}
        self.cpu_name='CPU'
        try:
            text=Path('/proc/cpuinfo').read_text()
            self.cpu_name=next((line.split(':',1)[1].strip() for line in text.splitlines() if line.startswith('model name')),'CPU')
        except OSError: pass
        self.fallback_gpus=self.detect_gpus()
        self.sample()
        if start:threading.Thread(target=self.loop,daemon=True,name='server-metrics').start()

    def detect_gpus(self):
        cards=[]
        for card in sorted(Path('/sys/class/drm').glob('card[0-9]*')):
            if not re.fullmatch(r'card\d+',card.name):continue
            device=card/'device'
            try:
                vendor=(device/'vendor').read_text().strip(); code=(device/'device').read_text().strip()
                driver=(device/'driver').resolve().name
                address=device.resolve().name
                name='GeForce GT 730' if (vendor,code)==('0x10de','0x1287') else {'0x10de':'NVIDIA GPU','0x1002':'AMD GPU','0x8086':'Intel GPU'}.get(vendor,'GPU')
                try:
                    details=subprocess.run(['lspci','-s',address],capture_output=True,text=True,timeout=2).stdout.strip()
                    if ': ' in details:name=details.split(': ',1)[1].split(' (rev')[0]
                except (OSError,subprocess.SubprocessError):pass
                cards.append({'id':card.name,'name':name,'driver':driver,'utilization':None,'memory_used_mb':None,'memory_total_mb':None,'temperature':None,'note':'Bu sürücü GPU kullanım ölçümü sağlamıyor.','address':address})
            except OSError:pass
        return cards

    def sample(self):
        value={'cpu':{'name':self.cpu_name,'cores':os.cpu_count(),'percent':None},'ram':{},'gpus':[],'sampled_at':time.time()}
        try:
            values=[int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
            # guest and guest_nice are already included in user and nice.
            total=sum(values[:8]); idle=values[3]+(values[4] if len(values)>4 else 0)
            if self.previous:
                delta=total-self.previous[0]
                if delta>0:value['cpu']['percent']=round(max(0,min(100,100*(1-(idle-self.previous[1])/delta))),1)
            self.previous=(total,idle)
            value['cpu']['load1']=round(os.getloadavg()[0],2)
        except (OSError,ValueError):pass
        try:
            mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
            total=mem.get('MemTotal',0); available=mem.get('MemAvailable',mem.get('MemFree',0)); used=total-available
            value['ram']={'total_mb':round(total/1024**2),'used_mb':round(used/1024**2),'available_mb':round(available/1024**2),'percent':round(used/total*100,1) if total else None,'swap_used_mb':round((mem.get('SwapTotal',0)-mem.get('SwapFree',0))/1024**2)}
        except (OSError,ValueError):pass
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,driver_version','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2)
            if result.returncode==0:
                def number(v):
                    try:return float(v.strip())
                    except ValueError:return None
                for line in result.stdout.splitlines():
                    fields=[v.strip() for v in line.split(',')]
                    if len(fields)!=7:continue
                    index,name,util,used,total,temp,driver=fields
                    value['gpus'].append({'id':index,'name':name,'driver':'NVIDIA '+driver,'utilization':number(util),'memory_used_mb':number(used),'memory_total_mb':number(total),'temperature':number(temp),'note':None})
        except (OSError,subprocess.SubprocessError):pass
        if not value['gpus']:value['gpus']=[dict(g) for g in self.fallback_gpus]
        with self.lock:self.value=value

    def loop(self):
        while True:
            time.sleep(3)
            try:self.sample()
            except Exception:pass

    def snapshot(self):
        with self.lock:return dict(self.value)
