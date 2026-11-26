#!/bin/bash
# Run after installing CUDA-compatible NVIDIA cards and the distro-recommended driver.
# This never installs or replaces a kernel driver.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo 'sudo bash /opt/facepass/enable-gpu.sh ile çalıştırın.'; exit 1; fi
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi -L; then
  echo 'Çalışan NVIDIA sürücüsü ve uyumlu GPU bulunamadı. Önce yeni kartın desteklenen sürücüsü kurulmalı.'; exit 1
fi
driver_branch=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1 | cut -d. -f1)
if [ "$driver_branch" -lt 525 ]; then
  echo 'Bu sürücü CUDA 11.x kullanıyor. GiysunPASS yerleşik CUDA karşılaştırmasıyla çalışır.'
  echo 'Yeni CUDA 12 yüz şablonu çalışma ortamı bu sürücüyle uyumlu değildir; kurulmadı.'
  exit 0
fi
python3 -m venv --system-site-packages /opt/facepass/venv
/opt/facepass/venv/bin/python -m pip install --upgrade pip
/opt/facepass/venv/bin/python -m pip install 'onnxruntime-gpu[cuda,cudnn]'
/opt/facepass/venv/bin/python -c 'import onnxruntime as ort; ort.preload_dlls(); assert "CUDAExecutionProvider" in ort.get_available_providers(); print(ort.get_available_providers())'
systemctl restart facepass
echo 'GPU çalışma ortamı kuruldu. Kullanılabilen bütün NVIDIA GPU’lar servis açılışında keşfedilir.'
echo 'Panel > Sistem ayarları bölümünden gerçekten etkinleşen GPU’ları kontrol edin.'
