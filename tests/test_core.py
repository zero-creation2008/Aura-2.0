import sys
import unittest
import asyncio
import hashlib
import json
import os
import tempfile
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from core import interpret, run, allowed_url, db
from server import make_app

class CoreTests(unittest.TestCase):
    def test_intents(self):
        self.assertEqual(interpret('how much battery is left')[0],'device.battery')
        self.assertEqual(interpret('remind me at 8 pm to study physics')[0],'reminder.create')
        self.assertEqual(interpret('arbitrary shell command')[0],'unknown')
    def test_memory(self):
        self.assertEqual(run('remember test project directory is ~/Projects')['intent'],'memory.set')
        self.assertEqual(run('what is my test project directory')['result']['value'],'~/Projects')
    def test_database(self):
        with db() as c: self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_network_policy(self):
        for u in ('http://127.0.0.1','https://127.0.0.1','https://localhost','file:///etc/passwd'):
            with self.assertRaises(ValueError): allowed_url(u)
    def test_routes(self):
        paths={r.resource.canonical for r in make_app().router.routes()}
        self.assertTrue({'/api/login','/api/command','/ws'}.issubset(paths))

    def test_authenticated_command_and_csrf(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                salt=os.urandom(16); password='test-password-long'
                owner=Path(tmp)/'owner.json'
                owner.write_text(json.dumps({'salt':salt.hex(),'hash':hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()}))
                with patch('server.security_path',return_value=owner):
                    async with TestClient(TestServer(make_app())) as client:
                        denied=await client.get('/api/health')
                        self.assertEqual(denied.status,401)
                        login=await client.post('/api/login',json={'password':password,'device':'test'})
                        self.assertEqual(login.status,200)
                        csrf=(await login.json())['csrf']
                        denied=await client.post('/api/command',json={'command':'battery'})
                        self.assertEqual(denied.status,403)
                        ok=await client.post('/api/command',json={'command':'show notes'},headers={'X-CSRF-Token':csrf})
                        self.assertEqual(ok.status,200)
                        self.assertEqual((await ok.json())['intent'],'notes.list')
        asyncio.run(scenario())

if __name__=='__main__': unittest.main()
