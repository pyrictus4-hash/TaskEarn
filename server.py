from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3, json, os, secrets, hashlib, hmac, time

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'taskeearn.db')
SESSION_DAYS = 7


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    c = db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'worker' CHECK(role IN ('worker','client','admin')),
      balance_cents INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      expires_at INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      client_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      category TEXT NOT NULL,
      reward_cents INTEGER NOT NULL CHECK(reward_cents > 0),
      est_minutes INTEGER NOT NULL CHECK(est_minutes > 0),
      quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
      completed_count INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed','paused')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(client_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS submissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id INTEGER NOT NULL,
      worker_id INTEGER NOT NULL,
      answer TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
      reward_cents INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      reviewed_at TEXT,
      UNIQUE(task_id, worker_id),
      FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
      FOREIGN KEY(worker_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS ledger (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      amount_cents INTEGER NOT NULL,
      kind TEXT NOT NULL,
      note TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    ''')
    cols=[r['name'] for r in c.execute('PRAGMA table_info(users)').fetchall()]
    if 'is_active' not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

    if c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'] == 0:
        worker_pw = pw_hash('demo123')
        client_pw = pw_hash('demo123')
        c.execute('INSERT INTO users(email,password_hash,role,balance_cents) VALUES(?,?,?,?)',
                  ('worker@taskeearn.demo', worker_pw, 'worker', 1284))
        c.execute('INSERT INTO users(email,password_hash,role,balance_cents) VALUES(?,?,?,?)',
                  ('client@taskeearn.demo', client_pw, 'client', 10000))
        worker_id = c.execute("SELECT id FROM users WHERE email='worker@taskeearn.demo'").fetchone()['id']
        client_id = c.execute("SELECT id FROM users WHERE email='client@taskeearn.demo'").fetchone()['id']
        demo_tasks = [
            ('Search result relevance','Rate whether a search result matches a user query.','ai',45,3,25),
            ('Product category check','Verify an online product is in the correct category.','ai',30,2,40),
            ('Short audio review','Listen to a short recording and check transcription quality.','content',80,5,15),
            ('Image classification','Identify the main object and category shown in an image.','ai',18,1,60),
            ('Shopping habits survey','Answer a short survey about everyday shopping decisions.','survey',75,5,20),
        ]
        for title, desc, cat, reward, mins, qty in demo_tasks:
            budget = reward * qty
            c.execute('INSERT INTO tasks(client_id,title,description,category,reward_cents,est_minutes,quantity) VALUES(?,?,?,?,?,?,?)',
                      (client_id,title,desc,cat,reward,mins,qty))
            c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',
                      (client_id,-budget,'task_funding','Demo task funding'))
        c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',
                  (worker_id,1284,'credit','Demo starting balance'))
        c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',
                  (client_id,10000,'credit','Demo client wallet'))
        # Demo funding ledger entries above are informational; reset client wallet to 5000 for demo.
        c.execute('UPDATE users SET balance_cents=10000 WHERE id=?',(client_id,))
    admin_email=os.getenv('ADMIN_EMAIL','admin@taskeearn.com').strip().lower()
    admin_password=os.getenv('ADMIN_PASSWORD','TaskEarnAdmin#2026!X9')
    if admin_email and '@' in admin_email:
        existing=c.execute('SELECT id FROM users WHERE email=?',(admin_email,)).fetchone()
        if not existing:
            # Creating the admin automatically requires ADMIN_PASSWORD. If it is
            # not configured yet, the admin can still be promoted at first login
            # using the password of an existing account with ADMIN_EMAIL.
            if admin_password:
                c.execute('INSERT INTO users(email,password_hash,role,balance_cents,is_active) VALUES(?,?,?,?,1)',
                          (admin_email,pw_hash(admin_password),'admin',0))
        else:
            # Do not overwrite an existing password unless ADMIN_PASSWORD is set.
            # This prevents a missing Render secret from locking the admin out.
            c.execute("UPDATE users SET role='admin', is_active=1 WHERE email=?", (admin_email,))
            if admin_password:
                c.execute("UPDATE users SET password_hash=? WHERE email=?", (pw_hash(admin_password), admin_email))
    c.commit(); c.close()


def pw_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ':' + key.hex()


def pw_check(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(':')
        salt = bytes.fromhex(salt_hex)
        key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def json_out(h, obj, status=200):
    raw = json.dumps(obj, ensure_ascii=False).encode()
    h.send_response(status)
    h.send_header('Content-Type', 'application/json; charset=utf-8')
    h.send_header('Cache-Control', 'no-store')
    h.send_header('Content-Length', str(len(raw)))
    h.end_headers()
    h.wfile.write(raw)


def body(h):
    n = int(h.headers.get('Content-Length', '0'))
    raw = h.rfile.read(n) if n else b'{}'
    try:
        return json.loads(raw.decode() or '{}')
    except json.JSONDecodeError:
        raise ValueError('Invalid JSON')


def auth(h):
    raw = h.headers.get('Authorization', '')
    if not raw.startswith('Bearer '):
        return None
    token = raw[7:].strip()
    if not token:
        return None
    c = db()
    row = c.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                       WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1''', (token_hash(token), int(time.time()))).fetchone()
    c.close()
    return row


def issue_session(uid):
    token = secrets.token_urlsafe(48)
    expires = int(time.time()) + SESSION_DAYS * 86400
    c = db(); c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (token_hash(token),uid,expires)); c.commit(); c.close()
    return token


def money(cents):
    return f'${cents/100:.2f}'


def safe_user(u):
    if not u: return None
    return {'id':u['id'],'email':u['email'],'role':u['role'],'balance_cents':u['balance_cents'],'is_active':bool(u['is_active']) if 'is_active' in u.keys() else True}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args))

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == '/api/health': return json_out(self, {'ok':True})
            if p == '/api/me': return json_out(self, {'user': safe_user(auth(self))})
            if p == '/api/tasks': return self.tasks()
            if p == '/api/worker/summary': return self.worker_summary()
            if p == '/api/worker/ledger': return self.worker_ledger()
            if p == '/api/client/overview': return self.client_overview()
            if p == '/api/client/tasks': return self.client_tasks()
            if p == '/api/client/submissions': return self.client_submissions()
            if p == '/api/admin/overview': return self.admin_overview()
            if p == '/api/admin/users': return self.admin_users()
            if p == '/api/admin/tasks': return self.admin_tasks()
            if p == '/api/admin/submissions': return self.admin_submissions()
            if p == '/api/admin/ledger': return self.admin_ledger()
            if p in ('/','/styles.css','/app.js'):
                return self.file('index.html' if p=='/' else p.lstrip('/'))
            return json_out(self, {'error':'Not found'},404)
        except Exception as e:
            print('GET error:', repr(e))
            return json_out(self, {'error':'Server error'},500)

    def do_POST(self):
        p = urlparse(self.path).path
        routes = {
            '/api/register': self.register,
            '/api/login': self.login,
            '/api/logout': self.logout,
            '/api/tasks/start': self.start,
            '/api/tasks/submit': self.submit,
            '/api/client/tasks': self.create_task,
            '/api/client/review': self.review,
            '/api/client/test-funds': self.add_test_funds,
            '/api/admin/user-action': self.admin_user_action,
            '/api/admin/task-action': self.admin_task_action,
            '/api/admin/review': self.admin_review,
        }
        if p not in routes:
            return json_out(self, {'error':'Not found'},404)
        try:
            return routes[p]()
        except ValueError as e:
            return json_out(self, {'error':str(e)},400)
        except Exception as e:
            print('POST error:', repr(e))
            return json_out(self, {'error':'Server error'},500)

    def file(self,name):
        path = os.path.join(BASE,name)
        if not os.path.isfile(path): return json_out(self, {'error':'Not found'},404)
        data=open(path,'rb').read()
        ct='text/html; charset=utf-8' if name.endswith('.html') else 'text/css; charset=utf-8' if name.endswith('.css') else 'application/javascript; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type',ct); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

    def register(self):
        d=body(self); email=d.get('email','').strip().lower(); password=d.get('password',''); role=d.get('role','worker')
        if role not in ('worker','client'): role='worker'
        if '@' not in email or len(password)<6: return json_out(self, {'error':'Enter a valid email and a password with at least 6 characters.'},400)
        admin_email=os.getenv('ADMIN_EMAIL','').strip().lower()
        if admin_email and email==admin_email:
            return json_out(self, {'error':'This email is reserved for the admin. Use Log in with the configured admin password.'},409)
        c=db()
        try:
            c.execute('INSERT INTO users(email,password_hash,role,is_active) VALUES(?,?,?,1)',(email,pw_hash(password),role))
            uid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
            c.commit()
        except sqlite3.IntegrityError:
            c.close(); return json_out(self, {'error':'That email is already registered.'},409)
        c.close()
        return self._login_uid(uid)

    def login(self):
        d=body(self)
        email=d.get('email','').strip().lower()
        password=d.get('password','')

        # Admin login is authoritative for the configured ADMIN_EMAIL.
        # If ADMIN_PASSWORD is configured, it is accepted and becomes the stored
        # admin password. If it is not configured, an already-existing account's
        # password can be used once to promote that account to admin.
        admin_email=os.getenv('ADMIN_EMAIL','admin@taskeearn.com').strip().lower()
        admin_password=os.getenv('ADMIN_PASSWORD','TaskEarnAdmin#2026!X9')
        if admin_email and email==admin_email:
            c=db(); u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if u:
                valid = (bool(admin_password) and password==admin_password) or pw_check(password,u['password_hash'])
                if not valid:
                    c.close(); return json_out(self, {'error':'Invalid admin password.'},401)
                if admin_password and password==admin_password:
                    stored_hash=pw_hash(admin_password)
                else:
                    stored_hash=u['password_hash']
                c.execute("UPDATE users SET role='admin', password_hash=?, is_active=1 WHERE id=?", (stored_hash,u['id']))
                uid=u['id']
            else:
                if not admin_password or password!=admin_password:
                    c.close(); return json_out(self, {'error':'Admin account is not initialized. Set ADMIN_PASSWORD in Render, then log in again.'},503)
                c.execute('INSERT INTO users(email,password_hash,role,is_active) VALUES(?,?,?,1)',
                          (email,pw_hash(admin_password),'admin'))
                uid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']
            c.commit(); c.close()
            return self._login_uid(uid)

        c=db(); u=c.execute('SELECT * FROM users WHERE email=? AND is_active=1',(email,)).fetchone(); c.close()
        if not u or not pw_check(password,u['password_hash']):
            return json_out(self, {'error':'Invalid email or password.'},401)
        return self._login_uid(u['id'])

    def _login_uid(self,uid):
        token=issue_session(uid); c=db(); u=c.execute('SELECT id,email,role,balance_cents,is_active FROM users WHERE id=?',(uid,)).fetchone(); c.close()
        return json_out(self, {'token':token,'user':dict(u)})

    def logout(self):
        raw=self.headers.get('Authorization','')
        token=raw[7:].strip() if raw.startswith('Bearer ') else ''
        if token:
            c=db(); c.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),)); c.commit(); c.close()
        return json_out(self, {'ok':True})

    def tasks(self):
        u=auth(self); wid=u['id'] if u and u['role']=='worker' else -1
        cat=parse_qs(urlparse(self.path).query).get('category',['all'])[0]
        c=db()
        q='''SELECT t.*, EXISTS(SELECT 1 FROM submissions s WHERE s.task_id=t.id AND s.worker_id=?) taken
             FROM tasks t WHERE t.status='open' AND t.completed_count<t.quantity'''
        args=[wid]
        if cat!='all': q+=' AND t.category=?'; args.append(cat)
        q+=' ORDER BY t.created_at DESC, t.id DESC'
        rows=[dict(r) for r in c.execute(q,args).fetchall()]; c.close()
        for r in rows:
            r['reward']=money(r['reward_cents']); r['slots_left']=r['quantity']-r['completed_count']
            r.pop('reward_cents',None); r.pop('client_id',None)
        return json_out(self, {'tasks':rows})

    def start(self):
        u=auth(self)
        if not u or u['role']!='worker': return json_out(self, {'error':'Worker login required.'},401)
        tid=body(self).get('task_id')
        c=db(); t=c.execute("SELECT * FROM tasks WHERE id=? AND status='open' AND completed_count<quantity",(tid,)).fetchone()
        if not t: c.close(); return json_out(self, {'error':'Task unavailable.'},404)
        existing=c.execute('SELECT id FROM submissions WHERE task_id=? AND worker_id=?',(tid,u['id'])).fetchone(); c.close()
        if existing: return json_out(self, {'error':'You already submitted this task.'},409)
        return json_out(self, {'ok':True,'task':dict(t)})

    def submit(self):
        u=auth(self)
        if not u or u['role']!='worker': return json_out(self, {'error':'Worker login required.'},401)
        d=body(self); tid=d.get('task_id'); answer=d.get('answer','').strip()
        if not answer: return json_out(self, {'error':'Answer is required.'},400)
        c=db(); t=c.execute("SELECT * FROM tasks WHERE id=? AND status='open' AND completed_count<quantity",(tid,)).fetchone()
        if not t: c.close(); return json_out(self, {'error':'Task is no longer accepting submissions.'},404)
        try:
            c.execute('INSERT INTO submissions(task_id,worker_id,answer,reward_cents) VALUES(?,?,?,?)',(tid,u['id'],answer,t['reward_cents']))
            c.commit()
        except sqlite3.IntegrityError:
            c.close(); return json_out(self, {'error':'You already submitted this task.'},409)
        c.close(); return json_out(self, {'ok':True,'status':'pending'})

    def worker_summary(self):
        u=auth(self)
        if not u or u['role']!='worker': return json_out(self, {'error':'Worker login required.'},401)
        c=db();
        approved=c.execute("SELECT COALESCE(SUM(reward_cents),0) n FROM submissions WHERE worker_id=? AND status='approved'",(u['id'],)).fetchone()['n']
        pending=c.execute("SELECT COALESCE(SUM(reward_cents),0) n FROM submissions WHERE worker_id=? AND status='pending'",(u['id'],)).fetchone()['n']
        done=c.execute("SELECT COUNT(*) n FROM submissions WHERE worker_id=? AND status='approved'",(u['id'],)).fetchone()['n']
        c.close(); return json_out(self, {'balance_cents':u['balance_cents'],'approved_cents':approved,'pending_cents':pending,'completed_tasks':done})

    def worker_ledger(self):
        u=auth(self)
        if not u or u['role']!='worker': return json_out(self, {'error':'Worker login required.'},401)
        c=db(); rows=[dict(r) for r in c.execute('SELECT amount_cents,kind,note,created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],)).fetchall()]; c.close()
        for r in rows: r['amount']=money(r['amount_cents'])
        return json_out(self, {'entries':rows})

    def add_test_funds(self):
        u=auth(self)
        if not u or u['role']!='client':
            return json_out(self, {'error':'Client login required.'},401)
        d=body(self)
        try:
            cents=int(round(float(d.get('amount',10))*100))
        except Exception:
            return json_out(self, {'error':'Invalid test fund amount.'},400)
        if cents < 100 or cents > 10000:
            return json_out(self, {'error':'Test funding must be between $1 and $100.'},400)
        c=db(); c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE users SET balance_cents=balance_cents+? WHERE id=? AND role='client'",(cents,u['id']))
        c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',(u['id'],cents,'test_funding','Test wallet credit — no real payment processed'))
        c.commit()
        new_balance=c.execute('SELECT balance_cents FROM users WHERE id=?',(u['id'],)).fetchone()['balance_cents']
        c.close()
        return json_out(self, {'ok':True,'added_cents':cents,'balance_cents':new_balance,'test_only':True})

    def create_task(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required.'},401)
        d=body(self)
        try:
            reward=max(1,int(round(float(d.get('reward',0))*100))); mins=max(1,int(d.get('est_minutes',1))); qty=max(1,int(d.get('quantity',1)))
        except Exception:
            return json_out(self, {'error':'Invalid reward, time, or quantity.'},400)
        title=d.get('title','').strip(); desc=d.get('description','').strip(); cat=d.get('category','ai')
        if not title or not desc: return json_out(self, {'error':'Title and description are required.'},400)
        total=reward*qty
        c=db(); c.execute('BEGIN IMMEDIATE')
        client=c.execute('SELECT balance_cents FROM users WHERE id=?',(u['id'],)).fetchone()
        if not client or client['balance_cents']<total:
            c.rollback(); c.close(); return json_out(self, {'error':f'Insufficient client balance. You need {money(total)}.'},402)
        c.execute('UPDATE users SET balance_cents=balance_cents-? WHERE id=?',(total,u['id']))
        c.execute('INSERT INTO tasks(client_id,title,description,category,reward_cents,est_minutes,quantity) VALUES(?,?,?,?,?,?,?)',(u['id'],title,desc,cat,reward,mins,qty))
        c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',(u['id'],-total,'task_funding',f'Funded task: {title}'))
        tid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']; c.commit(); c.close()
        return json_out(self, {'ok':True,'task_id':tid,'funded_cents':total})

    def client_overview(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required.'},401)
        c=db();
        tasks=c.execute('SELECT COUNT(*) n FROM tasks WHERE client_id=?',(u['id'],)).fetchone()['n']
        open_tasks=c.execute("SELECT COUNT(*) n FROM tasks WHERE client_id=? AND status='open'",(u['id'],)).fetchone()['n']
        pending=c.execute('''SELECT COUNT(*) n FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE t.client_id=? AND s.status='pending' ''',(u['id'],)).fetchone()['n']
        spent=c.execute("SELECT COALESCE(-SUM(amount_cents),0) n FROM ledger WHERE user_id=? AND kind='task_funding'",(u['id'],)).fetchone()['n']
        c.close(); return json_out(self, {'balance_cents':u['balance_cents'],'tasks':tasks,'open_tasks':open_tasks,'pending_submissions':pending,'funded_cents':spent})

    def client_tasks(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required.'},401)
        c=db(); ts=[dict(r) for r in c.execute('SELECT * FROM tasks WHERE client_id=? ORDER BY id DESC',(u['id'],)).fetchall()]; c.close()
        for x in ts: x['reward']=money(x['reward_cents']); x['budget']=money(x['reward_cents']*x['quantity']); x['slots_left']=x['quantity']-x['completed_count']
        return json_out(self, {'tasks':ts})

    def client_submissions(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required.'},401)
        c=db(); rows=[dict(r) for r in c.execute('''SELECT s.*,t.title,u.email worker_email FROM submissions s
            JOIN tasks t ON t.id=s.task_id JOIN users u ON u.id=s.worker_id
            WHERE t.client_id=? ORDER BY s.id DESC LIMIT 100''',(u['id'],)).fetchall()]; c.close()
        for x in rows: x['reward']=money(x['reward_cents'])
        return json_out(self, {'submissions':rows})

    def review(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required.'},401)
        d=body(self); sid=d.get('submission_id'); decision=d.get('decision')
        if decision not in ('approve','reject'): return json_out(self, {'error':'Invalid decision.'},400)
        c=db(); c.execute('BEGIN IMMEDIATE')
        row=c.execute('''SELECT s.*,t.client_id,t.title,t.quantity,t.completed_count FROM submissions s
                         JOIN tasks t ON t.id=s.task_id WHERE s.id=? AND t.client_id=?''',(sid,u['id'])).fetchone()
        if not row: c.rollback(); c.close(); return json_out(self, {'error':'Submission not found.'},404)
        if row['status']!='pending': c.rollback(); c.close(); return json_out(self, {'error':'Submission already reviewed.'},409)
        if decision=='approve':
            c.execute("UPDATE submissions SET status='approved',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,))
            c.execute('UPDATE users SET balance_cents=balance_cents+? WHERE id=?',(row['reward_cents'],row['worker_id']))
            c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',(row['worker_id'],row['reward_cents'],'credit','Task approved: '+row['title']))
            new_count=row['completed_count']+1
            new_status='closed' if new_count>=row['quantity'] else 'open'
            c.execute('UPDATE tasks SET completed_count=?,status=? WHERE id=?',(new_count,new_status,row['task_id']))
        else:
            c.execute("UPDATE submissions SET status='rejected',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,))
        c.commit(); c.close(); return json_out(self, {'ok':True,'status':'approved' if decision=='approve' else 'rejected'})


    def _require_admin(self):
        u=auth(self)
        if not u or u['role']!='admin':
            json_out(self, {'error':'Admin login required.'},401)
            return None
        return u

    def admin_overview(self):
        if not self._require_admin(): return
        c=db()
        users=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
        workers=c.execute("SELECT COUNT(*) n FROM users WHERE role='worker'").fetchone()['n']
        clients=c.execute("SELECT COUNT(*) n FROM users WHERE role='client'").fetchone()['n']
        active=c.execute('SELECT COUNT(*) n FROM users WHERE is_active=1').fetchone()['n']
        tasks=c.execute('SELECT COUNT(*) n FROM tasks').fetchone()['n']
        open_tasks=c.execute("SELECT COUNT(*) n FROM tasks WHERE status='open'").fetchone()['n']
        submissions=c.execute('SELECT COUNT(*) n FROM submissions').fetchone()['n']
        pending=c.execute("SELECT COUNT(*) n FROM submissions WHERE status='pending'").fetchone()['n']
        credited=c.execute("SELECT COALESCE(SUM(amount_cents),0) n FROM ledger WHERE amount_cents>0 AND kind='credit'").fetchone()['n']
        funded=c.execute("SELECT COALESCE(-SUM(amount_cents),0) n FROM ledger WHERE kind='task_funding'").fetchone()['n']
        c.close()
        return json_out(self, {'users':users,'workers':workers,'clients':clients,'active_users':active,'tasks':tasks,'open_tasks':open_tasks,'submissions':submissions,'pending_submissions':pending,'credited_cents':credited,'funded_cents':funded})

    def admin_users(self):
        if not self._require_admin(): return
        c=db(); rows=[dict(r) for r in c.execute('SELECT id,email,role,balance_cents,is_active,created_at FROM users ORDER BY id DESC LIMIT 250').fetchall()]; c.close()
        for r in rows: r['balance']=money(r['balance_cents'])
        return json_out(self, {'users':rows})

    def admin_tasks(self):
        if not self._require_admin(): return
        c=db(); rows=[dict(r) for r in c.execute("SELECT t.*,u.email client_email FROM tasks t JOIN users u ON u.id=t.client_id ORDER BY t.id DESC LIMIT 250").fetchall()]; c.close()
        for r in rows:
            r['reward']=money(r['reward_cents']); r['budget']=money(r['reward_cents']*r['quantity']); r['slots_left']=r['quantity']-r['completed_count']
        return json_out(self, {'tasks':rows})

    def admin_submissions(self):
        if not self._require_admin(): return
        c=db(); rows=[dict(r) for r in c.execute("SELECT s.*,t.title,t.client_id,cu.email client_email,wu.email worker_email FROM submissions s JOIN tasks t ON t.id=s.task_id JOIN users cu ON cu.id=t.client_id JOIN users wu ON wu.id=s.worker_id ORDER BY s.id DESC LIMIT 250").fetchall()]; c.close()
        for r in rows: r['reward']=money(r['reward_cents'])
        return json_out(self, {'submissions':rows})

    def admin_ledger(self):
        if not self._require_admin(): return
        c=db(); rows=[dict(r) for r in c.execute("SELECT l.*,u.email FROM ledger l JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 250").fetchall()]; c.close()
        for r in rows: r['amount']=money(r['amount_cents'])
        return json_out(self, {'entries':rows})

    def admin_user_action(self):
        if not self._require_admin(): return
        d=body(self); uid=d.get('user_id'); action=d.get('action')
        if action not in ('disable','enable'): return json_out(self, {'error':'Invalid user action.'},400)
        c=db(); target=c.execute('SELECT id,email,role,is_active FROM users WHERE id=?',(uid,)).fetchone()
        if not target: c.close(); return json_out(self, {'error':'User not found.'},404)
        if target['role']=='admin': c.close(); return json_out(self, {'error':'Admin accounts cannot be disabled here.'},400)
        active=1 if action=='enable' else 0
        c.execute('UPDATE users SET is_active=? WHERE id=?',(active,uid))
        if not active: c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        c.commit(); c.close(); return json_out(self, {'ok':True,'is_active':bool(active)})

    def admin_task_action(self):
        if not self._require_admin(): return
        d=body(self); tid=d.get('task_id'); action=d.get('action')
        if action not in ('pause','resume','close'): return json_out(self, {'error':'Invalid task action.'},400)
        c=db(); t=c.execute('SELECT id,status,completed_count,quantity FROM tasks WHERE id=?',(tid,)).fetchone()
        if not t: c.close(); return json_out(self, {'error':'Task not found.'},404)
        status={'pause':'paused','resume':'open','close':'closed'}[action]
        if action=='resume' and t['completed_count']>=t['quantity']:
            c.close(); return json_out(self, {'error':'Completed tasks cannot be reopened.'},400)
        c.execute('UPDATE tasks SET status=? WHERE id=?',(status,tid)); c.commit(); c.close(); return json_out(self, {'ok':True,'status':status})

    def admin_review(self):
        if not self._require_admin(): return
        d=body(self); sid=d.get('submission_id'); decision=d.get('decision')
        if decision not in ('approve','reject'): return json_out(self, {'error':'Invalid decision.'},400)
        c=db(); c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT s.*,t.title,t.quantity,t.completed_count FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.id=?",(sid,)).fetchone()
        if not row: c.rollback(); c.close(); return json_out(self, {'error':'Submission not found.'},404)
        if row['status']!='pending': c.rollback(); c.close(); return json_out(self, {'error':'Submission already reviewed.'},409)
        if decision=='approve':
            c.execute("UPDATE submissions SET status='approved',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,))
            c.execute('UPDATE users SET balance_cents=balance_cents+? WHERE id=?',(row['reward_cents'],row['worker_id']))
            c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',(row['worker_id'],row['reward_cents'],'credit','Admin approved task: '+row['title']))
            n=row['completed_count']+1; status='closed' if n>=row['quantity'] else 'open'
            c.execute('UPDATE tasks SET completed_count=?,status=? WHERE id=?',(n,status,row['task_id']))
        else:
            c.execute("UPDATE submissions SET status='rejected',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,))
        c.commit(); c.close(); return json_out(self, {'ok':True,'status':'approved' if decision=='approve' else 'rejected'})

if __name__=='__main__':
    init_db()
    port=int(os.getenv('PORT','8091'))
    print(f'TaskEarn running at http://127.0.0.1:{port}')
    ThreadingHTTPServer(('0.0.0.0',port),H).serve_forever()
