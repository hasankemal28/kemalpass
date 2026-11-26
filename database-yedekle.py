#!/usr/bin/env python3
"""Çalışan SQLite veritabanının tutarlı, ayrı bir kopyasını üretir."""
import argparse
import os
import sqlite3
import tempfile
from pathlib import Path


def snapshot(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Hedef dosya zaten var. Yeni bir dosya adı seçin.')
    fd, temporary = tempfile.mkstemp(prefix='.giysunpass-snapshot-', dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src:
            with sqlite3.connect(temporary) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('Veritabanı kontrolü başarısız.')
        os.chmod(temporary, 0o600)
        if os.geteuid() == 0 and os.environ.get('SUDO_UID'):
            os.chown(temporary, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='Yeni .sqlite3 dosyasının yolu')
    parser.add_argument('--source', default='/var/lib/facepass/facepass.sqlite3')
    args = parser.parse_args()
    snapshot(args.source, args.output)
    print('Veritabanı kopyası:', args.output)
    print('Bu kopya fotoğraf dosyalarını içermez; tam yedek için panelde Sistem ayarları > Yedek kullanın.')
