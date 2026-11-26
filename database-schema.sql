-- GiysunPASS SQLite schema; oluşturulan boş şemadan çıkarıldı.
PRAGMA foreign_keys=ON;

CREATE TABLE audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL,
          actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);

CREATE TABLE cameras(
          id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,position INTEGER NOT NULL,
          config TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(kind,position));

CREATE TABLE events(
          id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
          person_name TEXT, unknown_id INTEGER REFERENCES unknowns(id) ON DELETE SET NULL,
          occurred_at TEXT NOT NULL, last_seen TEXT NOT NULL, image TEXT NOT NULL,
          similarity REAL, kind TEXT NOT NULL, source TEXT NOT NULL, camera TEXT NOT NULL,
          method TEXT NOT NULL, embedding BLOB NOT NULL, camera_id INTEGER REFERENCES cameras(id) ON DELETE SET NULL);

CREATE TABLE people(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);

CREATE TABLE samples(
          id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
          image TEXT NOT NULL, embedding BLOB NOT NULL, created_at TEXT NOT NULL,
          origin TEXT NOT NULL DEFAULT 'manual', quality REAL NOT NULL DEFAULT 0, pose REAL NOT NULL DEFAULT 0);

CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE unknowns(
          id INTEGER PRIMARY KEY, image TEXT NOT NULL, embedding BLOB NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);

CREATE TABLE users(
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
          permissions TEXT NOT NULL DEFAULT '{}', session_version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, last_login TEXT);

CREATE INDEX events_camera_time ON events(camera_id,occurred_at);

CREATE INDEX events_person ON events(person_id,occurred_at);

CREATE INDEX events_time ON events(occurred_at DESC);

CREATE INDEX events_unknown ON events(unknown_id);
