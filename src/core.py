"""DIVI deterministic local core. No model and no arbitrary command execution."""
import datetime as dt
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
VERSION = '0.1.0'

def db():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / 'divi.sqlite', timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.executescript('''
    CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS facts(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, message TEXT NOT NULL, due TEXT NOT NULL, fired INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, intent TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, device TEXT, intent TEXT, result TEXT, duration_ms INTEGER);
    CREATE TABLE IF NOT EXISTS sessions(id_hash TEXT PRIMARY KEY, device TEXT NOT NULL, csrf_hash TEXT NOT NULL, expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, last_seen TEXT, revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS login_attempts(ip TEXT, at INTEGER);
    CREATE TABLE IF NOT EXISTS downloads(id INTEGER PRIMARY KEY, url TEXT, filename TEXT, digest TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS web_history(id INTEGER PRIMARY KEY, query TEXT, url TEXT, title TEXT, at TEXT);
    ''')
    return con

def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def config():
    p = ROOT / 'config' / 'default.json'
    return json.loads(p.read_text())

def audit(intent, result, start, device='local'):
    with db() as c:
        c.execute('INSERT INTO audit(at,device,intent,result,duration_ms) VALUES(?,?,?,?,?)',
                  (stamp(), device[:80], intent[:80], str(result)[:200], int((time.monotonic()-start)*1000)))
        c.execute('DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 10000)')

class Title(HTMLParser):
    inside = False
    title = ''
    def handle_starttag(self, tag, attrs):
        if tag == 'title': self.inside = True
    def handle_endtag(self, tag):
        if tag == 'title': self.inside = False
    def handle_data(self, data):
        if self.inside: self.title += data

def allowed_url(url):
    u = urllib.parse.urlsplit(url)
    cfg = config()['internet']
    if not cfg['enabled'] or cfg['mode'] == 'offline': raise ValueError('Internet is disabled')
    if u.scheme not in (('https', 'http') if cfg['allowHttp'] else ('https',)) or not u.hostname or u.username or u.password:
        raise ValueError('Only approved HTTP(S) URLs are supported')
    if u.port not in (None, 80, 443): raise ValueError('Port blocked')
    host = u.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.local', '.internal', '.localhost')): raise ValueError('Private host blocked')
    for item in socket.getaddrinfo(host, u.port or 443, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global: raise ValueError('Private/nonpublic address blocked')
    return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect blocked; inspect the destination URL explicitly')

def fetch(url, limit=512_000):
    allowed_url(url)
    req = urllib.request.Request(url, headers={'User-Agent':'DIVI/0.1 personal assistant'})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as r:
        if int(r.headers.get('Content-Length', 0)) > limit: raise ValueError('Response too large')
        raw = r.read(limit+1)
        if len(raw) > limit: raise ValueError('Response too large')
        return r.status, r.headers.get('Content-Type',''), raw

def interpret(command):
    s = command.strip()
    if not s or len(s) > 1000: return ('unknown', {})
    low = s.lower()
    with db() as c:
        row = c.execute('SELECT intent FROM aliases WHERE alias=?', (low,)).fetchone()
    if row: return (row['intent'], {})
    patterns = [
        (r'^(?:show |what(?: is|\'s) (?:my |the )?|how much )(?:battery|battery is left|power level)(?: status)?\??$|^battery$', 'device.battery'),
        (r'^(?:internet status|check if internet is working)$', 'internet.status'),
        (r'^(?:show (?:my )?notes|notes)$', 'notes.list'),
        (r'^save note (.+)$', 'notes.add'),
        (r'^search notes (.+)$', 'notes.search'),
        (r'^delete note (\d+)$', 'notes.delete'),
        (r'^remember (.+?) is (.+)$', 'memory.set'),
        (r'^what is my (.+?)\??$', 'memory.get'),
        (r'^forget (?:my )?(.+)$', 'memory.delete'),
        (r'^(?:remind me at|remind at) (\d{1,2})(?::(\d\d))?\s*(am|pm)?\s+to (.+)$', 'reminder.create'),
        (r'^(?:show (?:my )?reminders|reminders)$', 'reminder.list'),
        (r'^(?:backup (?:my )?divi(?: project)?|backup)$', 'backup.create'),
        (r'^(?:show battery status)$', 'device.battery'),
        (r'^(?:search internet for|search the internet for) (.+)$', 'internet.search'),
        (r'^(?:check (?:this )?website|fetch) (https?://\S+)$', 'internet.fetch'),
    ]
    for pattern, intent in patterns:
        m = re.fullmatch(pattern, low, re.I)
        if m: return intent, {'groups':m.groups(), 'original':s}
    return 'unknown', {}

def backup():
    (ROOT/'backups').mkdir(exist_ok=True)
    name='divi-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.gz'
    path=ROOT/'backups'/name
    with db() as c:
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    with tarfile.open(path, 'w:gz') as t:
        for rel in ('data/divi.sqlite','config/default.json'):
            p=ROOT/rel
            if p.exists(): t.add(p, arcname=rel)
    with tarfile.open(path) as t:
        assert all(x.isfile() and not x.name.startswith('/') for x in t.getmembers())
    return {'file':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def execute(intent, params=None, device='local', confirmed=False):
    start=time.monotonic(); params=params or {}; g=params.get('groups',())
    try:
        if intent == 'device.battery':
            binary=shutil.which('termux-battery-status')
            if not binary: result={'error':'Install Termux:API app and pkg install termux-api'}
            else: result=json.loads(subprocess.run([binary], capture_output=True, text=True, timeout=8, check=True).stdout)
        elif intent == 'device.storage':
            d=shutil.disk_usage(ROOT); result={'free_bytes':d.free,'total_bytes':d.total}
        elif intent == 'internet.status':
            try: allowed_url('https://example.com'); socket.getaddrinfo('example.com',443); result={'dns':'ok','mode':config()['internet']['mode']}
            except Exception as e: result={'internet':'unavailable','reason':str(e)}
        elif intent == 'internet.fetch':
            url=params.get('url') or g[0]; status, mime, content=fetch(url)
            title=Title(); title.feed(content.decode('utf-8','replace')[:100000])
            result={'status':status,'content_type':mime,'title':title.title.strip(),'preview':re.sub(r'<[^>]*>',' ',content.decode('utf-8','replace'))[:1000]}
        elif intent == 'internet.search':
            query=params.get('query') or g[0]; url='https://www.google.com/search?'+urllib.parse.urlencode({'q':query})
            result={'query':query,'search_url':url,'notice':'Open the search URL in your browser; automatic result parsing requires a configured permitted provider.'}
            with db() as c: c.execute('INSERT INTO web_history(query,url,title,at) VALUES(?,?,?,?)',(query,url,'Search',stamp()))
        elif intent == 'notes.add':
            value=params.get('text') or params['original'][len('save note '):]
            with db() as c: result={'id':c.execute('INSERT INTO notes(text,created) VALUES(?,?)',(value,stamp())).lastrowid}
        elif intent == 'notes.list':
            with db() as c: result=[dict(r) for r in c.execute('SELECT * FROM notes ORDER BY id DESC LIMIT 100')]
        elif intent == 'notes.search':
            value=params.get('query') or g[0]
            with db() as c: result=[dict(r) for r in c.execute('SELECT * FROM notes WHERE text LIKE ? ESCAPE "\\" LIMIT 100',('%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',))]
        elif intent == 'notes.delete':
            if not confirmed: result={'confirmation_required':True,'intent':intent,'params':{'id':int(g[0]) if g else int(params['id'])}}
            else:
                with db() as c: result={'deleted':c.execute('DELETE FROM notes WHERE id=?',(int(params.get('id') or g[0]),)).rowcount}
        elif intent == 'memory.set':
            key,value=(params['key'],params['value']) if 'key' in params else (g[0], params['original'].split(' is ',1)[1])
            with db() as c: c.execute('INSERT OR REPLACE INTO facts VALUES(?,?)',(key,value))
            result={'saved':key}
        elif intent in ('memory.get','memory.delete'):
            key=params.get('key') or g[0]
            with db() as c:
                if intent=='memory.get':
                    row=c.execute('SELECT value FROM facts WHERE key=?',(key,)).fetchone(); result={'key':key,'value':row['value'] if row else None}
                elif not confirmed: result={'confirmation_required':True,'intent':intent,'params':{'key':key}}
                else: result={'deleted':c.execute('DELETE FROM facts WHERE key=?',(key,)).rowcount}
        elif intent == 'reminder.create':
            hour=int(g[0]); minute=int(g[1] or 0); suffix=g[2]; message=params['original'].split(' to ',1)[1]
            if hour > (12 if suffix else 23) or hour < 1 or minute>59: raise ValueError('Invalid time')
            if suffix: hour=hour%12+(12 if suffix.lower()=='pm' else 0)
            zone=dt.datetime.now().astimezone().tzinfo
            due=dt.datetime.now(zone).replace(hour=hour,minute=minute,second=0,microsecond=0)
            if due<=dt.datetime.now(zone): due+=dt.timedelta(days=1)
            with db() as c: result={'id':c.execute('INSERT INTO reminders(message,due) VALUES(?,?)',(message,due.isoformat())).lastrowid,'due':due.isoformat()}
        elif intent == 'reminder.list':
            with db() as c: result=[dict(r) for r in c.execute('SELECT * FROM reminders WHERE fired=0 ORDER BY due LIMIT 100')]
        elif intent == 'backup.create': result=backup()
        elif intent == 'unknown': result={'error':"I don't understand that command yet.", 'examples':['battery','save note physics exam Monday','remind me at 8 pm to study physics','backup divi']}
        else: result={'error':'Tool not registered'}
    except Exception as e: result={'error':str(e)[:300]}
    audit(intent, 'error' if isinstance(result,dict) and 'error' in result else 'ok',start,device)
    return {'intent':intent,'result':result}

def run(command, device='local'):
    intent,params=interpret(command)
    return execute(intent,params,device)
