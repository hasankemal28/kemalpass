import getpass
import os
from pathlib import Path
from werkzeug.security import generate_password_hash
from db import init, setting, put_setting

init()
if not setting('admin_password'):
    password = os.environ.pop('FACEPASS_BOOTSTRAP_PASSWORD', None)
    hashed = None
    if password is None:
        print('KemalPass panel yöneticisi: giysunpass')
        print('Belirlediğiniz hazır şifreyi kullanmak için Enter; farklı bir şifre için yeni şifrenizi yazın.')
        while True:
            password = getpass.getpass('Panel şifresi (8–256 karakter, Enter = hazır şifre): ')
            if not password:
                hashed = Path(__file__).with_name('bootstrap-default.hash').read_text().strip()
                if not hashed.startswith('scrypt:32768:8:1$'):
                    raise SystemExit('Hazır şifre özeti geçersiz.')
                break
            if not 8 <= len(password) <= 256:
                print('Şifre 8–256 karakter olmalı.'); continue
            if password != getpass.getpass('Panel şifresi tekrar: '):
                print('Şifreler eşleşmiyor.'); continue
            break
    if hashed is None:
        if not 8 <= len(password) <= 256:
            raise SystemExit('Şifre 8–256 karakter olmalı.')
        hashed = generate_password_hash(password)
    put_setting('admin_password', hashed)
    print('KemalPass panel hesabı oluşturuldu: giysunpass')
else:
    print('Mevcut panel hesabı korundu.')
