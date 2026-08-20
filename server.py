from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3, json, os, secrets, hashlib, hmac, time

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'taskeearn.db')
STATIC = BASE
sessions = {}

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'worker', balance_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
      category TEXT NOT NULL, reward_cents INTEGER NOT NULL, est_minutes INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(client_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS submissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, worker_id INTEGER NOT NULL, answer TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', reward_cents INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      reviewed_at TEXT, UNIQUE(task_id, worker_id), FOREIGN KEY(task_id) REFERENCES tasks(id), FOREIGN KEY(worker_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS ledger (
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
      kind TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')
    # Demo users + tasks
    if c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'] == 0:
        for email, role in [('worker@taskeearn.demo','worker'),('client@taskeearn.demo','client')]:
            c.execute('INSERT INTO users(email,password_hash,role,balance_cents) VALUES(?,?,?,?)', (email, pw_hash('demo123'), role, 1284 if role=='worker' else 0))
        client = c.execute("SELECT id FROM users WHERE email='client@taskeearn.demo'").fetchone()['id']
        demo = [
          ('Search result relevance','Rate whether a search result matches a user query.','ai',45,3),
          ('Product category check','Verify an online product is in the correct category.','ai',30,2),
          ('Short audio review','Listen to a short recording and check transcription quality.','content',80,5),
          ('Image classification','Identify the main object and category shown in an image.','ai',18,1),
          ('Shopping habits survey','Answer a short survey about everyday shopping decisions.','survey',75,5),
        ]
        c.executemany('INSERT INTO tasks(client_id,title,description,category,reward_cents,est_minutes) VALUES(?,?,?,?,?,?)', [(client,*x) for x in demo])
        c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES((SELECT id FROM users WHERE email=?),?,?,?)',('worker@taskeearn.demo',1284,'credit','Demo starting balance'))
    c.commit(); c.close()

def pw_hash(p):
    salt=secrets.token_bytes(16)
    key=hashlib.scrypt(p.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex()+':'+key.hex()

def pw_check(p, stored):
    try:
        salt_hex,key_hex=stored.split(':'); salt=bytes.fromhex(salt_hex)
        key=hashlib.scrypt(p.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception: return False

def json_out(h, obj, status=200):
    b=json.dumps(obj).encode(); h.send_response(status); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)

def body(h):
    n=int(h.headers.get('Content-Length','0')); raw=h.rfile.read(n) if n else b''
    return json.loads(raw.decode() or '{}')

def auth(h):
    token=h.headers.get('Authorization','').replace('Bearer ','')
    uid=sessions.get(token)
    if not uid: return None
    c=db(); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return u

def money(cents): return f'${cents/100:.2f}'

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(), fmt%args))
    def do_GET(self):
        p=urlparse(self.path).path
        if p=='/api/tasks': return self.tasks()
        if p=='/api/me':
            u=auth(self)
            return json_out(self, {'user': ({'id':u['id'],'email':u['email'],'role':u['role'],'balance_cents':u['balance_cents']} if u else None)})
        if p=='/api/client/tasks': return self.client_tasks()
        if p.startswith('/assets/') or p=='/styles.css' or p=='/app.js' or p=='/':
            return self.file('index.html' if p=='/' else p.lstrip('/'))
        return json_out(self, {'error':'Not found'},404)
    def do_POST(self):
        p=urlparse(self.path).path
        routes={'/api/register':self.register,'/api/login':self.login,'/api/logout':self.logout,'/api/tasks/start':self.start,'/api/tasks/submit':self.submit,'/api/client/tasks':self.create_task,'/api/client/review':self.review}
        if p in routes: return routes[p]()
        return json_out(self, {'error':'Not found'},404)
    def file(self,name):
        path=os.path.join(STATIC,name)
        if not os.path.isfile(path): return json_out(self, {'error':'Not found'},404)
        data=open(path,'rb').read(); ct='text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'application/javascript'
        self.send_response(200); self.send_header('Content-Type',ct); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def register(self):
        d=body(self); email=d.get('email','').strip().lower(); password=d.get('password',''); role=d.get('role','worker')
        if role not in ('worker','client'): role='worker'
        if '@' not in email or len(password)<6: return json_out(self, {'error':'Valid email and 6+ character password required'},400)
        c=db()
        try: c.execute('INSERT INTO users(email,password_hash,role) VALUES(?,?,?)',(email,pw_hash(password),role)); uid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']; c.commit()
        except sqlite3.IntegrityError: c.close(); return json_out(self, {'error':'Email already registered'},409)
        c.close(); return self._login_uid(uid)
    def login(self):
        d=body(self); c=db(); u=c.execute('SELECT * FROM users WHERE email=?',(d.get('email','').strip().lower(),)).fetchone(); c.close()
        if not u or not pw_check(d.get('password',''),u['password_hash']): return json_out(self, {'error':'Invalid email or password'},401)
        return self._login_uid(u['id'])
    def _login_uid(self,uid):
        t=secrets.token_urlsafe(32); sessions[t]=uid; c=db(); u=c.execute('SELECT id,email,role,balance_cents FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return json_out(self, {'token':t,'user':dict(u)})
    def logout(self):
        t=self.headers.get('Authorization','').replace('Bearer ',''); sessions.pop(t,None); return json_out(self, {'ok':True})
    def tasks(self):
        u=auth(self); wid=u['id'] if u and u['role']=='worker' else -1; cat=parse_qs(urlparse(self.path).query).get('category',['all'])[0]
        c=db(); q='''SELECT t.*, EXISTS(SELECT 1 FROM submissions s WHERE s.task_id=t.id AND s.worker_id=?) taken FROM tasks t WHERE t.status='open' '''
        args=[wid]
        if cat!='all': q+=' AND t.category=?'; args.append(cat)
        q+=' ORDER BY t.created_at DESC'
        rows=[dict(r) for r in c.execute(q,args).fetchall()]; c.close();
        for r in rows: r['reward']=money(r['reward_cents']); r.pop('reward_cents',None); r.pop('client_id',None)
        return json_out(self, {'tasks':rows})
    def start(self):
        u=auth(self)
        if not u or u['role']!='worker': return json_out(self, {'error':'Worker login required'},401)
        tid=body(self).get('task_id'); c=db(); t=c.execute("SELECT * FROM tasks WHERE id=? AND status='open'",(tid,)).fetchone()
        if not t: c.close(); return json_out(self, {'error':'Task unavailable'},404)
        s=c.execute('SELECT id FROM submissions WHERE task_id=? AND worker_id=?',(tid,u['id'])).fetchone(); c.close()
        if s: return json_out(self, {'error':'Task already started/submitted'},409)
        return json_out(self, {'ok':True,'task':dict(t)})
    def submit(self):
        u=auth(self)
        if not u or u['role']!='worker': return json_out(self, {'error':'Worker login required'},401)
        d=body(self); tid=d.get('task_id'); answer=d.get('answer','').strip()
        if not answer: return json_out(self, {'error':'Answer is required'},400)
        c=db(); t=c.execute("SELECT * FROM tasks WHERE id=? AND status='open'",(tid,)).fetchone()
        if not t: c.close(); return json_out(self, {'error':'Task unavailable'},404)
        try:
            c.execute('INSERT INTO submissions(task_id,worker_id,answer,reward_cents) VALUES(?,?,?,?)',(tid,u['id'],answer,t['reward_cents']))
            c.commit()
        except sqlite3.IntegrityError: c.close(); return json_out(self, {'error':'You already submitted this task'},409)
        c.close(); return json_out(self, {'ok':True,'status':'pending'})
    def create_task(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required'},401)
        d=body(self)
        try: reward=max(1,int(round(float(d.get('reward',0))*100))); mins=max(1,int(d.get('est_minutes',1)))
        except: return json_out(self, {'error':'Invalid reward/time'},400)
        title=d.get('title','').strip(); desc=d.get('description','').strip(); cat=d.get('category','ai')
        if not title or not desc: return json_out(self, {'error':'Title and description required'},400)
        c=db(); c.execute('INSERT INTO tasks(client_id,title,description,category,reward_cents,est_minutes) VALUES(?,?,?,?,?,?)',(u['id'],title,desc,cat,reward,mins)); c.commit(); tid=c.execute('SELECT last_insert_rowid() id').fetchone()['id']; c.close(); return json_out(self, {'ok':True,'task_id':tid})
    def client_tasks(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required'},401)
        c=db(); ts=[dict(r) for r in c.execute('SELECT * FROM tasks WHERE client_id=? ORDER BY id DESC',(u['id'],)).fetchall()]; subs=[dict(r) for r in c.execute('''SELECT s.*,t.title,u.email worker_email FROM submissions s JOIN tasks t ON t.id=s.task_id JOIN users u ON u.id=s.worker_id WHERE t.client_id=? ORDER BY s.id DESC''',(u['id'],)).fetchall()]; c.close();
        for x in ts:x['reward']=money(x['reward_cents'])
        for x in subs:x['reward']=money(x['reward_cents'])
        return json_out(self, {'tasks':ts,'submissions':subs})
    def review(self):
        u=auth(self)
        if not u or u['role']!='client': return json_out(self, {'error':'Client login required'},401)
        d=body(self); sid=d.get('submission_id'); decision=d.get('decision')
        if decision not in ('approve','reject'): return json_out(self, {'error':'Invalid decision'},400)
        c=db(); row=c.execute('''SELECT s.*,t.client_id,t.title FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.id=? AND t.client_id=?''',(sid,u['id'])).fetchone()
        if not row: c.close(); return json_out(self, {'error':'Submission not found'},404)
        if row['status']!='pending': c.close(); return json_out(self, {'error':'Submission already reviewed'},409)
        if decision=='approve':
            c.execute("UPDATE submissions SET status='approved',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,)); c.execute('UPDATE users SET balance_cents=balance_cents+? WHERE id=?',(row['reward_cents'],row['worker_id'])); c.execute('INSERT INTO ledger(user_id,amount_cents,kind,note) VALUES(?,?,?,?)',(row['worker_id'],row['reward_cents'],'credit','Task approved: '+row['title']))
        else: c.execute("UPDATE submissions SET status='rejected',reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,))
        c.commit(); c.close(); return json_out(self, {'ok':True,'status':'approved' if decision=='approve' else 'rejected'})

if __name__=='__main__':
    init_db(); print('TaskEarn running at http://127.0.0.1:8091'); ThreadingHTTPServer(('127.0.0.1',8091),H).serve_forever()
