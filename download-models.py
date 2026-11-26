import hashlib
import urllib.request
from pathlib import Path
root=Path(__file__).parent/'models';root.mkdir(exist_ok=True)
models=[('face_detection_yunet','face_detection_yunet_2023mar.onnx','yunet.onnx','8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),('face_recognition_sface','face_recognition_sface_2021dec.onnx','sface.onnx','0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79')]
for folder,name,dest,sha in models:
    path=root/dest
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
        data=urllib.request.urlopen(f'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{folder}/{name}',timeout=180).read()
        if hashlib.sha256(data).hexdigest()!=sha:raise SystemExit('Model checksum mismatch: '+dest)
        path.write_bytes(data)
    license=urllib.request.urlopen(f'https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/{folder}/LICENSE',timeout=30).read()
    (root/(folder+'-LICENSE')).write_bytes(license)
    print(dest,'verified')
