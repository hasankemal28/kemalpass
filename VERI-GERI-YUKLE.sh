#!/bin/bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'sudo bash VERI-GERI-YUKLE.sh /yedek.zip ile çalıştırın.'; exit 1; }
[ "$#" -eq 1 ] || { echo 'Kullanım: sudo bash VERI-GERI-YUKLE.sh /tam/yol/facepass-yedek.zip'; exit 1; }
id facepass >/dev/null
base=$(cd -- "$(dirname -- "$0")" && pwd)
stage=$(mktemp -d /var/lib/facepass-restore.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
python3 "$base/veri-hazirla.py" "$(realpath "$1")" "$stage"
chown -R facepass:facepass "$stage"
chmod 0700 "$stage"
was_active=$(systemctl is-active facepass.service || true)
timer_active=$(systemctl is-active facepass-backup.timer || true)
panel_active=$(systemctl is-active giysunpass-sqlite-web.service || true)
has_panel=0
if systemctl cat giysunpass-sqlite-web.service >/dev/null 2>&1; then has_panel=1; fi
old="/var/lib/facepass-before-restore-$(date +%Y%m%d-%H%M%S)-$$"
rollback() {
  rc=$?
  trap - ERR
  systemctl stop facepass.service || true
  if [ "$has_panel" -eq 1 ]; then systemctl stop giysunpass-sqlite-web.service || true; fi
  if [ -d "$old" ]; then
    if [ -e /var/lib/facepass ]; then mv /var/lib/facepass "$old-failed"; fi
    mv "$old" /var/lib/facepass
  fi
  if [ "$was_active" = active ]; then systemctl start facepass.service || true; fi
  if [ "$panel_active" = active ]; then systemctl start giysunpass-sqlite-web.service || true; fi
  if [ "$timer_active" = active ]; then systemctl start facepass-backup.timer || true; fi
  exit "$rc"
}
trap rollback ERR
# Açık SQLite/WAL bağlantıları kalmasın: bütün veri yazıcılarını durdur.
systemctl stop facepass-backup.timer
systemctl stop facepass-backup.service
if [ "$has_panel" -eq 1 ]; then systemctl stop giysunpass-sqlite-web.service; fi
systemctl stop facepass.service
if [ -d /var/lib/facepass ]; then mv /var/lib/facepass "$old"; fi
mv "$stage" /var/lib/facepass
systemctl start facepass.service
curl --fail --silent --retry 15 --retry-connrefused --retry-delay 2 http://127.0.0.1:8080/healthz
if [ "$panel_active" = active ]; then systemctl start giysunpass-sqlite-web.service; fi
if [ "$timer_active" = active ]; then systemctl start facepass-backup.timer; fi
trap - ERR
echo "Veri geri yüklendi. Önceki veri korundu: $old"
echo 'Yedekteki mevcut kullanıcı adı ve şifreyle giriş yapın. SQLite Web şifresi ayrıdır.'
