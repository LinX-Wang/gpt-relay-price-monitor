import sys,secrets,urllib.request,urllib.error,os
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from settings import load_environment
load_environment()
PUBLIC_ORIGIN=os.environ.get('APP_PUBLIC_URL','http://127.0.0.1:8765').rstrip('/')
INTERNAL_ORIGIN=os.environ.get('APP_INTERNAL_URL','http://127.0.0.1:'+os.environ.get('APP_PORT','8765')).rstrip('/')
sys.path.insert(0,str(ROOT/'vendor/meow'))
from gpt56_vnext.server import create_server,Handler
from gpt56_vnext.errors import AppError
from linx_policy import classify
class HostedHandler(Handler):
    def _authorize(self):
        if self.headers.get('Host')!=urlsplit(PUBLIC_ORIGIN).netloc:
            self._send({'error':{'code':'invalid_host'}},403);return False
        origin=self.headers.get('Origin')
        if origin and origin!=PUBLIC_ORIGIN:
            self._send({'error':{'code':'invalid_origin'}},403);return False
        try:
            req=urllib.request.Request(INTERNAL_ORIGIN+'/api/detector/auth',headers={'Cookie':self.headers.get('Cookie','')})
            with urllib.request.urlopen(req,timeout=5) as r: allowed=r.status==204
        except urllib.error.HTTPError as e:
            allowed=False
            if e.code not in (401,403):self._send({'error':{'code':'auth_unavailable'}},503);return False
        except Exception:
            self._send({'error':{'code':'auth_unavailable'}},503);return False
        if not allowed:
            if self.command=='GET' and urlsplit(self.path).path=='/meow/':
                self.send_response(302);self.send_header('Location','/login');self.send_header('Cache-Control','no-store');self.end_headers()
            else:self._send({'error':{'code':'login_required'}},401)
            return False
        return True
    def _prepare(self):
        if not self._authorize():return False
        if not self.path.startswith('/meow/'):
            self._send({'error':{'code':'not_found'}},404);return False
        self.path=self.path[len('/meow'):]
        if self.command=='POST' and urlsplit(self.path).path in ('/api/program/exit','/api/program/install-update'):
            self._send({'error':{'code':'server_managed_deployment'}},409);return False
        return True
    def _check(self,authenticated=True):
        if authenticated:
            from http.cookies import SimpleCookie
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            candidate=self.headers.get('X-Meow-Token') or (cookie[self.server.cookie_name].value if self.server.cookie_name in cookie else '')
            if not secrets.compare_digest(candidate,self.server.token):raise AppError('session_token_required',status=403)
    def do_GET(self):
        if self._prepare():super().do_GET()
    def do_POST(self):
        if self._prepare():super().do_POST()
    def _send(self,value,*args,**kwargs):
        if isinstance(value,dict):
            if 'fingerprint' in value:value={**value,'linx_result':classify(value)}
            if isinstance(value.get('items'),list):
                for item in value['items']:
                    if isinstance(item,dict) and item.get('session_id'):
                        report=self.server.state.store.report(item['session_id'])
                        if report:item['linx_result']=classify(report)
        return super()._send(value,*args,**kwargs)
    def send_header(self,key,value):
        if key.lower()=='set-cookie':value=value.replace('Path=/','Path=/meow/'+('; Secure' if PUBLIC_ORIGIN.startswith('https://') else ''))
        return super().send_header(key,value)
server=create_server(port=8790,runs_root=os.environ.get('MEOW_DATA_DIR',str(ROOT/'vendor/meow/runs')),locale='zh-CN')
server.RequestHandlerClass=HostedHandler
print('LinX Meow ready on 127.0.0.1:8790',flush=True)
try:server.serve_forever()
finally:server.server_close()
