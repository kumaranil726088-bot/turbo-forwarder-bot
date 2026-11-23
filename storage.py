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
        self.db.execute('INSERT INTO users(uid,name,username,joined_at,role) VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET name=excluded.name,username=excluded.username WHERE users.name<>excluded.name OR users.username<>excluded.username',
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
        self.db.execute('INSERT INTO user_settings VALUES(?,?,?) ON CONFLICT(uid,key) DO UPDATE SET value=excluded.value WHERE user_settings.value<>excluded.value',(uid,key,json.dumps(value)))
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

    def snapshot(self, owner):
        """Authenticated encrypted logical snapshot, independent of SQLite WAL layout."""
        if not self.cipher:
            raise ValueError('SESSION_KEY is required for encrypted backups')
        if owner != self.owner:
            raise ValueError('Owner mismatch')
        rows = self.db.execute('SELECT uid,name,username,joined_at,banned,role,total_ok FROM users ORDER BY uid').fetchall()
        # Do not back up a stale broadcast lock or in-memory confirmation tokens.
        globals_ = self.db.execute("SELECT key,value FROM global_settings WHERE key!='broadcast_active'").fetchall()
        data = dict(format='turbo-backup-1',owner=owner,created=time.time(),users=rows,
                    user_settings=self.db.execute('SELECT uid,key,value FROM user_settings').fetchall(),global_settings=globals_)
        encrypted=self.cipher.encrypt(json.dumps(data,ensure_ascii=False,allow_nan=False).encode())
        if len(encrypted)>10*1024*1024:
            raise ValueError('Backup exceeds supported 10 MiB; use external database backup tooling')
        return encrypted

    def decode_snapshot(self, blob, owner):
        if not self.cipher:
            raise ValueError('SESSION_KEY is required for restore')
        if len(blob)>10*1024*1024:
            raise ValueError('Backup exceeds 10 MiB')
        try:
            plain=self.cipher.decrypt(blob)
            data=json.loads(plain)
            self.validate_snapshot(data,owner)
            return data
        except Exception as exc:
            raise ValueError('Invalid backup, wrong owner, or different SESSION_KEY') from exc

    def validate_snapshot(self, data, owner):
        import math
        if not isinstance(data,dict) or data.get('format')!='turbo-backup-1' or data.get('owner')!=owner or owner!=self.owner:
            raise ValueError('Backup format/owner mismatch')
        if not isinstance(data.get('created'),(int,float)) or not math.isfinite(data['created']):
            raise ValueError('Invalid backup timestamp')
        for key in ('users','user_settings','global_settings'):
            if not isinstance(data.get(key),list) or len(data[key])>100000:
                raise ValueError('Invalid backup table')
        ids=set()
        for row in data['users']:
            if not isinstance(row,(list,tuple)) or len(row)!=7:
                raise ValueError('Invalid users row')
            uid,name,username,joined,banned,role,total=row
            if type(uid)!=int or uid<=0 or uid in ids or not isinstance(name,str) or not isinstance(username,str):
                raise ValueError('Invalid user identity')
            if not isinstance(joined,(int,float)) or not math.isfinite(joined) or banned not in (0,1) or role not in ('user','approved','owner') or type(total)!=int or total<0:
                raise ValueError('Invalid user metadata')
            if (role=='owner') != (uid==owner):
                raise ValueError('Invalid owner role')
            ids.add(uid)
        if owner not in ids:
            raise ValueError('Owner missing from backup')
        seen=set()
        for row in data['user_settings']:
            if not isinstance(row,(list,tuple)) or len(row)!=3:
                raise ValueError('Invalid settings row')
            uid,key,raw=row
            if uid not in ids or not isinstance(key,str) or not key or not isinstance(raw,str) or (uid,key) in seen:
                raise ValueError('Invalid settings identity')
            seen.add((uid,key))
            value=json.loads(raw)
            if key=='session' and value:
                self.cipher.decrypt(value.encode())
            if key=='watch' and (not isinstance(value,list) or any(not isinstance(r,dict) or not all(k in r for k in ('id','src','target')) for r in value)):
                raise ValueError('Invalid route snapshot')
        seen=set()
        for row in data['global_settings']:
            if not isinstance(row,(list,tuple)) or len(row)!=2:
                raise ValueError('Invalid global row')
            key,raw=row
            if not isinstance(key,str) or not isinstance(raw,str) or key in seen:
                raise ValueError('Invalid global setting')
            json.loads(raw);seen.add(key)

    def restore_snapshot(self, data, owner):
        self.validate_snapshot(data,owner)
        # One atomic DB transaction: either all snapshot rows replace current state, or none do.
        with self.db:
            for table in ('user_settings','global_settings','users'):
                self.db.execute('DELETE FROM '+table)
            self.db.executemany('INSERT INTO users(uid,name,username,joined_at,banned,role,total_ok) VALUES(?,?,?,?,?,?,?)',data['users'])
            self.db.executemany('INSERT INTO user_settings VALUES(?,?,?)',data['user_settings'])
            self.db.executemany('INSERT INTO global_settings VALUES(?,?)',data['global_settings'])
            self.db.execute("INSERT INTO global_settings VALUES('broadcast_active','false') ON CONFLICT(key) DO UPDATE SET value='false'")
