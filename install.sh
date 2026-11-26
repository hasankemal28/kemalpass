#!/bin/bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo 'sudo bash install.sh ile çalıştırın.'; exit 1; fi
cd "$(dirname "$0")"
mode=${1:-}
[ -z "$mode" ] || [ "$mode" = --prepare-only ] || { echo "Geçersiz seçenek"; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-opencv python3-flask python3-waitress python3-pil python3-requests python3-venv sqlite3 ffmpeg curl ca-certificates fonts-dejavu-core
id facepass >/dev/null 2>&1 || useradd --system --home-dir /var/lib/facepass --shell /usr/sbin/nologin facepass
install -d -m 0755 /opt/facepass
cp -a app.py accounts.py camera_store.py metrics.py overlay.py db.py engine.py camera.py backend.py cuda_matcher.py bootstrap.py bootstrap-default.hash launch.sh backup.py enable-gpu.sh download-models.py templates static tests /opt/facepass/
install -d -m 0755 /opt/facepass/models
if [ -d models ]; then cp -a models/. /opt/facepass/models/; else python3 /opt/facepass/download-models.py; fi
chown -R root:root /opt/facepass
chmod -R a+rX /opt/facepass
install -d -o facepass -g facepass -m 0700 /var/lib/facepass
install -m 0644 facepass.service facepass-backup.service facepass-backup.timer /etc/systemd/system/
systemctl daemon-reload
if [ "$mode" = --prepare-only ]; then
  echo 'Dosyalar ve bağımlılıklar kuruldu. Şifre seçmek için: sudo giysunpass-kur'
  exit 0
fi
runuser -u facepass -- env FACEPASS_DATA=/var/lib/facepass python3 /opt/facepass/bootstrap.py
systemctl enable --now facepass.service facepass-backup.timer
systemctl restart facepass.service
echo 'GiysunPASS hazır: http://SUNUCU_IP:8080 · Kullanıcı: giysunpass'
