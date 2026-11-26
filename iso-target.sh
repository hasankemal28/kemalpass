#!/bin/bash
set -euo pipefail
# Ubuntu Subiquity late-command: /target kurulu sistemdir.
target=${1:-/target}
[ -d "$target/etc" ] || { echo 'Ubuntu hedef dizini bulunamadı'; exit 1; }
base=$(cd -- "$(dirname -- "$0")" && pwd)
install -d "$target/opt/giysunpass-media"
cp -a "$base"/. "$target/opt/giysunpass-media/"
chown -R 0:0 "$target/opt/giysunpass-media"
install -D -m 0755 "$base/giysunpass-kur" "$target/usr/local/sbin/giysunpass-kur"
install -D -m 0644 "$base/giysunpass-ilk-giris.sh" "$target/etc/profile.d/giysunpass-ilk-giris.sh"
install -m 0644 "$base/giysunpass-setup.service" "$target/etc/systemd/system/giysunpass-setup.service"
install -d "$target/etc/systemd/system/multi-user.target.wants"
ln -sf ../giysunpass-setup.service "$target/etc/systemd/system/multi-user.target.wants/giysunpass-setup.service"
