"""Bir GiysunPASS veri ZIP'ini boş bir hazırlık dizinine doğrulayarak açar."""
import os
import shutil
import sqlite3
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

source, target = map(Path, sys.argv[1:])
if any(target.iterdir()):
    raise SystemExit('Hazırlık dizini boş olmalı.')
with zipfile.ZipFile(source) as archive:
    seen = set()
    for info in archive.infolist():
        name = PurePosixPath(info.filename)
        if name.is_absolute() or '..' in name.parts or '\\' in info.filename:
            raise SystemExit('Geçersiz yedek yolu.')
        if info.filename in seen or stat.S_ISLNK(info.external_attr >> 16):
            raise SystemExit('Tekrarlanan dosya veya sembolik bağlantı.')
        seen.add(info.filename)
        if info.is_dir():
            continue
        allowed = (info.filename in ('facepass.sqlite3', 'secret') or
                   (len(name.parts) == 2 and name.parts[0] in ('faces', 'snapshots') and name.suffix.lower() == '.jpg'))
        if not allowed:
            raise SystemExit('Desteklenmeyen yedek dosyası: '+info.filename)
        if info.file_size > shutil.disk_usage(target).free - 256*1024**2:
            raise SystemExit('Yedek için yeterli disk alanı yok.')
        destination = target / info.filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, destination.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        destination.chmod(0o600)
database = target / 'facepass.sqlite3'
if not database.is_file():
    raise SystemExit('Yedekte veritabanı bulunamadı.')
with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise SystemExit('Veritabanı bütünlük kontrolünden geçmedi.')
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'settings', 'people', 'samples', 'events', 'unknowns'} <= tables:
        raise SystemExit('GiysunPASS tabloları eksik.')
    print('Yedek doğrulandı. Kişi:', connection.execute('SELECT count(*) FROM people').fetchone()[0])
for name in ('faces', 'snapshots'):
    (target / name).mkdir(exist_ok=True, mode=0o700)
