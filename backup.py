"""Daily local backup: coherent SQLite + referenced photos, last seven copies."""
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.umask(0o077)
source=Path('/var/lib/facepass'); backups=Path('/var/backups/facepass')
backups.mkdir(parents=True,exist_ok=True,mode=0o700)
if shutil.disk_usage(backups).free<2*1024**3: raise SystemExit('Not enough free disk space for a backup')
stamp=datetime.now(ZoneInfo('Europe/Istanbul')).strftime('%Y-%m-%d')
path=backups/('facepass-'+stamp+'.zip'); temporary=path.with_suffix('.partial')
with tempfile.TemporaryDirectory(prefix='facepass-backup-') as temp:
    snapshot=Path(temp)/'facepass.sqlite3'
    src=sqlite3.connect(source/'facepass.sqlite3'); dst=sqlite3.connect(snapshot)
    src.backup(dst);src.close()
    references={r[0] for r in dst.execute('SELECT image FROM samples UNION SELECT image FROM events UNION SELECT image FROM unknowns')};dst.close()
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot,'facepass.sqlite3')
        for name in references:
            file=source/name
            if file.is_file():
                try:archive.write(file,name)
                except FileNotFoundError:pass
        archive.write(source/'secret','secret')
    temporary.replace(path)
for old in sorted(backups.glob('facepass-*.zip'),reverse=True)[7:]:old.unlink()
print('Backup complete:',path.name)
