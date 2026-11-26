import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(os.environ.get('FACEPASS_DATA', '/var/lib/facepass'))

@contextmanager
def connect():
    db = sqlite3.connect(ROOT / 'facepass.sqlite3', timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in ('faces', 'snapshots'):
        (ROOT / directory).mkdir(exist_ok=True, mode=0o700)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS people(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS samples(
          id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
          image TEXT NOT NULL, embedding BLOB NOT NULL, created_at TEXT NOT NULL,
          origin TEXT NOT NULL DEFAULT 'manual', quality REAL NOT NULL DEFAULT 0, pose REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS unknowns(
          id INTEGER PRIMARY KEY, image TEXT NOT NULL, embedding BLOB NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
          person_name TEXT, unknown_id INTEGER REFERENCES unknowns(id) ON DELETE SET NULL,
          occurred_at TEXT NOT NULL, last_seen TEXT NOT NULL, image TEXT NOT NULL,
          similarity REAL, kind TEXT NOT NULL, source TEXT NOT NULL, camera TEXT NOT NULL,
          method TEXT NOT NULL, embedding BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS events_time ON events(occurred_at DESC);
        CREATE INDEX IF NOT EXISTS events_person ON events(person_id,occurred_at);
        CREATE INDEX IF NOT EXISTS events_unknown ON events(unknown_id);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL,
          actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
        ''')
        defaults = {
          'camera': {'mode':'rtsp','url':'','onvif_url':'','username':'','password':'',
                     'profile':'','enabled':False,'name':'Giriş kamerası','kind':'Giriş'},
          'recognition': {'threshold':0.50,'margin':0.08,'interval':0.8,
                          'cooldown':30,'min_face':55,'confirmations':2,'auto_learn':True,'sample_limit':30},
        }
        for key,value in defaults.items():
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,json.dumps(value)))
        columns={r[1] for r in db.execute('PRAGMA table_info(samples)')}
        for name,ddl in [('origin',"TEXT NOT NULL DEFAULT 'manual'"),('quality','REAL NOT NULL DEFAULT 0'),('pose','REAL NOT NULL DEFAULT 0')]:
            if name not in columns: db.execute(f'ALTER TABLE samples ADD COLUMN {name} {ddl}')

def setting(key, default=None):
    with connect() as db:
        row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default

def put_setting(key, value):
    with connect() as db:
        db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key,json.dumps(value)))
