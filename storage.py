"""V6 durable, user-scoped storage. Session secrets encrypted using SESSION_KEY."""
import json
import sqlite3
import time
from cryptography.fernet import Fernet

DEFAULTS = dict(mode='turbo', workers=2, connections=4, delay=0.1, poll=10,
                caption='', replacements=[], type_filters=['text','photo','video','document','audio','voice','animation','sticker','poll'],
                filter='all', watch=[], auto=True, sender='Bot')

class Store:
    def __init__(self, path, owner, key=None):
        self.owner = owner
        self.cipher = Fernet(key.encode() if isinstance(key, str) else key) if key else None
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS users(uid INTEGER PRIMARY KEY,name TEXT,username TEXT,joined_at REAL,banned INTEGER DEFAULT 0,role TEXT DEFAULT 'user',total_ok INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS user_settings(uid INTEGER,key TEXT,value TEXT,PRIMARY KEY(uid,key));
        CREATE TABLE IF NOT EXISTS global_settings(key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.migrate()
    def migrate(self):
        if self.global_get('migration_v6', False):
            return
        old = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        if old:
            rows = self.db.execute('SELECT key,value FROM settings').fetchall()
            for k, raw in rows:
                if k in ('session','user_session','string_session'):
                    k = 'session'
                    if not raw:
                        continue
                    if not self.cipher:
                        raise RuntimeError('SESSION_KEY is required to migrate legacy login safely')
                    value = raw
                else:
                    try:
                        value = json.loads(raw)
                    except (ValueError, TypeError):
                        value = raw
                if self.db.execute('SELECT 1 FROM user_settings WHERE uid=? AND key=?',(self.owner,k)).fetchone() is None:
                    self.set(self.owner,k,value)
            # Remove plaintext legacy session copies only after successful encrypted migration.
            self.db.execute('DELETE FROM settings')
            self.db.commit()
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.db.execute('VACUUM')
        self.global_set('migration_v6', True)
    def touch(self, uid, name='', username=''):
        self.db.execute('INSERT INTO users(uid,name,username,joined_at,role) VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET name=excluded.name,username=excluded.username',
                        (uid,name,username,time.time(),'owner' if uid == self.owner else 'user'))
        self.db.commit()
    def allowed(self, uid):
        if uid == self.owner:
            return True
        row = self.db.execute('SELECT banned,role FROM users WHERE uid=?',(uid,)).fetchone()
        return bool(row and not row[0] and (self.global_get('access','open') == 'open' or row[1] == 'approved'))
    def get(self, uid, key, default=None):
        row = self.db.execute('SELECT value FROM user_settings WHERE uid=? AND key=?',(uid,key)).fetchone()
        if not row:
            value = DEFAULTS.get(key, default)
            return json.loads(json.dumps(value))
        value = json.loads(row[0])
        if key == 'session' and value:
            if not self.cipher:
                raise RuntimeError('SESSION_KEY missing')
            return self.cipher.decrypt(value.encode()).decode()
        return value
    def set(self, uid, key, value):
        if key == 'session' and value:
            if not self.cipher:
                raise RuntimeError('Set SESSION_KEY before storing logins')
            value = self.cipher.encrypt(value.encode()).decode()
        self.db.execute('INSERT INTO user_settings VALUES(?,?,?) ON CONFLICT(uid,key) DO UPDATE SET value=excluded.value',(uid,key,json.dumps(value)))
        self.db.commit()
    def global_get(self, key, default=None):
        row = self.db.execute('SELECT value FROM global_settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def global_set(self,key,value):
        self.db.execute('INSERT INTO global_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
        self.db.commit()
    def reset(self,uid,keep=True):
        session = self.get(uid,'session') if keep else None
        self.db.execute('DELETE FROM user_settings WHERE uid=?',(uid,))
        self.db.commit()
        if session:
            self.set(uid,'session',session)
    def users(self):
        return self.db.execute('SELECT uid,name,username,banned,role,total_ok FROM users ORDER BY joined_at').fetchall()
    def flag(self,uid,key,value):
        if key not in ('banned','role'):
            raise ValueError('Invalid user flag')
        self.db.execute(f'UPDATE users SET {key}=? WHERE uid=?',(value,uid))
        self.db.commit()
    def success(self,uid,n=1):
        self.db.execute('UPDATE users SET total_ok=total_ok+? WHERE uid=?',(n,uid))
        self.db.commit()
    def close(self):
        self.db.close()
