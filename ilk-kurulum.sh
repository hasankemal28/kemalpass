#!/bin/bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo 'sudo bash ilk-kurulum.sh ile çalıştırın.'; exit 1; }
cd -- "$(dirname -- "$0")"
[ ! -f /var/lib/giysunpass-install/prepared ] || { echo 'GiysunPASS dosyaları hazır. Şifre seçmek için: sudo giysunpass-kur'; exit 0; }
umask 077
install -d -m 0755 /var/lib/giysunpass-install
sha256sum -c SHA256SUMS > /var/lib/giysunpass-install/integrity.log
bash GiysunPASS/install.sh --prepare-only
bash sqlite-web/install.sh
install -m 0755 giysunpass-kur /usr/local/sbin/giysunpass-kur
install -m 0644 giysunpass-ilk-giris.sh /etc/profile.d/giysunpass-ilk-giris.sh
date -u > /var/lib/giysunpass-install/prepared
echo 'GiysunPASS hazır. İlk oturum açılışında panel şifrenizi seçin: sudo giysunpass-kur'
