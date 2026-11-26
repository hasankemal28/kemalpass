from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np

@lru_cache(maxsize=12)
def font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'):
        try:return ImageFont.truetype(path,size)
        except OSError:pass
    return ImageFont.load_default(size=size)

def annotate(frame,faces,camera_name='Kamera'):
    h,w=frame.shape[:2]
    if w>1920:
        ratio=1920/w;frame=cv2.resize(frame,(1920,round(h*ratio)),interpolation=cv2.INTER_AREA)
    else:ratio=1
    image=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB));draw=ImageDraw.Draw(image)
    size=max(13,min(20,image.width//40));facefont=font(size)
    for face in faces:
        x,y,bw,bh=[round(v*ratio) for v in face['box']]
        x=max(0,x);y=max(0,y);x2=min(image.width-1,x+bw);y2=min(image.height-1,y+bh)
        color=(83,205,138) if face.get('confirmed') else (99,163,241) if face.get('pending') else (246,187,75)
        label=face.get('label','Bilinmeyen')[:70]
        bounds=draw.textbbox((0,0),label,font=facefont)
        width=min(image.width-8,bounds[2]+16);height=size+16
        lx=min(x,max(0,image.width-width));ly=max(0,y-height)
        draw.rectangle((x,y,x2,y2),outline=color,width=2)
        draw.rounded_rectangle((lx,ly,lx+width,ly+height),radius=4,fill=color)
        draw.text((lx+8,ly+5-bounds[1]),label,fill=(14,35,31),font=facefont)
    caption=camera_name+' · '+datetime.now(ZoneInfo('Europe/Istanbul')).strftime('%d.%m.%Y %H:%M:%S')
    small=font(max(10,size-3));bb=draw.textbbox((0,0),caption,font=small)
    draw.rectangle((0,image.height-27,min(image.width,bb[2]+20),image.height),fill=(16,33,31))
    draw.text((10,image.height-23),caption,font=small,fill=(234,243,237))
    return cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)
