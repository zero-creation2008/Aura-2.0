import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import time
from aiohttp import web
from core import ROOT, VERSION, db, run, execute, config, stamp

def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def security_path(): return ROOT/'secure'/'owner.json'

def authenticate(password):
    info=json.loads(security_path().read_text())
    hashed=hashlib.scrypt(password.encode(),salt=bytes.fromhex(info['salt']),n=16384,r=8,p=1)
    return hmac.compare_digest(hashed.hex(),info['hash'])

@web.middleware
async def protection(request, handler):
    if request.path.startswith('/api/') or request.path=='/ws':
        if request.path == '/api/login': return await handler(request)
        sid=request.cookies.get('divi_session','')
        with db() as c:
            row=c.execute('SELECT * FROM sessions WHERE id_hash=? AND expires>?',(digest(sid),int(time.time()))).fetchone() if sid else None
            if row:
                dev=c.execute('SELECT revoked FROM devices WHERE id=?',(row['device'],)).fetchone()
                if not dev or dev['revoked']: row=None
        if row is None: raise web.HTTPUnauthorized()
        request['device']=row['device']
        if request.method not in ('GET','HEAD'):
            csrf=request.headers.get('X-CSRF-Token','')
            if not csrf or not hmac.compare_digest(digest(csrf),row['csrf_hash']): raise web.HTTPForbidden(text='CSRF token required')
        if request.path == '/ws' and request.headers.get('Origin') != 'http://127.0.0.1:'+str(config()['server']['port']):
            # External TLS gateways forward the public origin, configured explicitly by owner.
            if request.headers.get('Origin') != config()['server'].get('publicOrigin'):
                raise web.HTTPForbidden(text='Origin mismatch')
    return await handler(request)

async def login(request):
    ip=request.remote or 'unknown'; now=int(time.time())
    with db() as c:
        c.execute('DELETE FROM login_attempts WHERE at<?',(now-900,))
        if c.execute('SELECT count(*) FROM login_attempts WHERE ip=?',(ip,)).fetchone()[0]>=5:
            raise web.HTTPTooManyRequests(text='Try again later')
    body=await request.json()
    if not isinstance(body,dict) or len(str(body.get('password','')))>1024: raise web.HTTPBadRequest()
    if not authenticate(str(body.get('password',''))):
        with db() as c: c.execute('INSERT INTO login_attempts(ip,at) VALUES(?,?)',(ip,now))
        await asyncio.sleep(.5)
        raise web.HTTPUnauthorized(text='Invalid credentials')
    sid=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32); device=secrets.token_hex(12)
    name=str(body.get('device','Browser'))[:60]
    with db() as c:
        c.execute('INSERT INTO devices(id,name,last_seen) VALUES(?,?,?)',(device,name,stamp()))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(sid),device,digest(csrf),now+86400))
        c.execute('DELETE FROM login_attempts WHERE ip=?',(ip,))
    resp=web.json_response({'ok':True,'csrf':csrf,'device':device})
    resp.set_cookie('divi_session',sid,httponly=True,secure=bool(config()['server'].get('publicOrigin')),samesite='Strict',max_age=86400,path='/')
    return resp

async def logout(request):
    sid=request.cookies.get('divi_session','')
    with db() as c: c.execute('DELETE FROM sessions WHERE id_hash=?',(digest(sid),))
    resp=web.json_response({'ok':True}); resp.del_cookie('divi_session'); return resp

async def status(request):
    with db() as c:
        count=c.execute('SELECT count(*) FROM reminders WHERE fired=0').fetchone()[0]
    return web.json_response({'version':VERSION,'database':'ok','server':'online','reminders':count,'time':stamp()})

async def command(request):
    body=await request.json()
    if not isinstance(body,dict) or not isinstance(body.get('command'),str): raise web.HTTPBadRequest()
    return web.json_response(run(body['command'],request['device']))

async def confirm(request):
    body=await request.json(); intent=body.get('intent'); params=body.get('params',{})
    if intent not in ('notes.delete','memory.delete') or not isinstance(params,dict): raise web.HTTPBadRequest()
    return web.json_response(execute(intent,params,request['device'],confirmed=True))

async def devices(request):
    with db() as c: rows=[dict(r) for r in c.execute('SELECT id,name,last_seen,revoked FROM devices ORDER BY last_seen DESC')]
    return web.json_response(rows)

async def revoke(request):
    ident=request.match_info['id']
    with db() as c:
        c.execute('UPDATE devices SET revoked=1 WHERE id=?',(ident,))
        c.execute('DELETE FROM sessions WHERE device=?',(ident,))
    return web.json_response({'revoked':ident})

async def ws(request):
    w=web.WebSocketResponse(heartbeat=30); await w.prepare(request)
    await w.send_json({'type':'connected','version':VERSION})
    async for msg in w:
        if msg.type==web.WSMsgType.TEXT:
            await w.send_json({'type':'notice','message':'Use POST /api/command with CSRF for commands.'})
    return w

async def page(request):
    return web.FileResponse(ROOT/'public'/'index.html')

async def scheduler(app):
    while True:
        try:
            with db() as c:
                due=[dict(r) for r in c.execute('SELECT id,message FROM reminders WHERE fired=0')
                     if __import__('datetime').datetime.fromisoformat(r['due']) <= __import__('datetime').datetime.now().astimezone()]
                for row in due: c.execute('UPDATE reminders SET fired=1 WHERE id=?',(row['id'],))
            for row in due:
                if os.environ.get('TERMUX_VERSION'):
                    import subprocess
                    subprocess.run(['termux-notification','--title','DIVI reminder','--content',row['message']],timeout=5,check=False)
        except Exception as e: print('Scheduler:',e,flush=True)
        await asyncio.sleep(30)

async def start_scheduler(app): app['scheduler']=asyncio.create_task(scheduler(app))
async def stop_scheduler(app): app['scheduler'].cancel()

def make_app():
    app=web.Application(middlewares=[protection],client_max_size=64*1024)
    app.router.add_get('/',page)
    app.router.add_post('/api/login',login)
    app.router.add_post('/api/logout',logout)
    app.router.add_get('/api/health',status)
    app.router.add_post('/api/command',command)
    app.router.add_post('/api/confirm',confirm)
    app.router.add_get('/api/devices',devices)
    app.router.add_post('/api/devices/{id}/revoke',revoke)
    app.router.add_get('/ws',ws)
    app.on_startup.append(start_scheduler); app.on_cleanup.append(stop_scheduler)
    return app

if __name__=='__main__':
    if not security_path().exists(): raise SystemExit('Run divi owner setup first')
    db().close(); web.run_app(make_app(),host='127.0.0.1',port=config()['server']['port'])
