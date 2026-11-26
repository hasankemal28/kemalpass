# KemalPass

## Türkçe

KemalPass, yüz tanıma tabanlı yerel geçiş ve kamera yönetim uygulamasıdır. Yüz görüntüleri ve yüz şablonları sunucuda tutulur; yüz tanıma için buluta fotoğraf gönderilmez.

### Özellikler

- YuNet ile yüz algılama ve SFace ile yüz özellikleri çıkarma
- Yerel kullanıcı hesapları, yetkilendirme ve denetim kayıtları
- RTSP ve ONVIF kamera bağlantıları
- Canlı giriş/çıkış takibi ve tanınmayan yüz kuyruğu
- Kişi, referans fotoğrafı ve geçiş kaydı yönetimi
- SQLite veritabanı ve yerel ZIP yedekleri
- CPU ile çalışma; uygun sistemlerde isteğe bağlı CUDA eşleştirme desteği
- Sunucu CPU, RAM, GPU ve disk durumu

### Kurulum

Proje Ubuntu Server üzerinde çalışacak şekilde hazırlanmıştır. Bağımlılıkları kurduktan sonra:

```bash
cd GiysunPASS
sudo bash install.sh
```

Kurulumdan sonra panel varsayılan olarak `http://SUNUCU_IP:8080` adresinde açılır. İlk yönetici parolası kurulum sırasında belirlenir. Kamera bilgilerini ve parolalarını yalnızca yerel kurulumda girin; bunları kaynak koduna veya Git geçmişine yazmayın.

### Güvenlik ve gizlilik

Bu uygulama yüz biyometrisi ve kamera kimlik bilgileri işleyebilir. Erişimi güvenilir yerel ağ ile sınırlayın, güçlü parolalar kullanın, yedekleri şifreli ve erişimi kısıtlı bir yerde saklayın. Üretim ortamında HTTPS/VPN ve uygun yasal/kurumsal izinleri değerlendirin.

### Testler

```bash
cd GiysunPASS
python3 tests/test_system.py
```

### Kaynaklar

- [OpenCV YuNet ve SFace yüz tanıma](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)
- [YuNet modeli ve lisansı](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [SFace modeli ve lisansı](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)

## English

KemalPass is a local face-recognition-based access and camera management application. Face images and embeddings remain on the server; photos are not sent to a cloud service for recognition.

### Features

- Face detection with YuNet and face feature extraction with SFace
- Local accounts, permissions, and audit logging
- RTSP and ONVIF camera connections
- Live entry/exit monitoring and an unknown-face queue
- Person, reference-photo, and event management
- SQLite storage and local ZIP backups
- CPU operation with optional CUDA matching on compatible systems
- CPU, RAM, GPU, and disk monitoring

### Installation

The project is prepared for Ubuntu Server. Install dependencies, then run:

```bash
cd GiysunPASS
sudo bash install.sh
```

After installation, the panel is available at `http://SERVER_IP:8080` by default. Choose the initial administrator password during setup. Enter camera credentials only on the local installation; never commit them to source control or Git history.

### Security and privacy

This application may process biometric face data and camera credentials. Restrict access to a trusted network, use strong passwords, keep backups encrypted and access-controlled, and consider HTTPS/VPN and applicable legal or organizational requirements for production deployments.

### Tests

```bash
cd GiysunPASS
python3 tests/test_system.py
```

### References

- [OpenCV YuNet and SFace face recognition](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)
- [YuNet model and license](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- [SFace model and license](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)

## License

No project-wide license has been declared yet. Check third-party model and dependency licenses before redistribution.
