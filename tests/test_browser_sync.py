"""Credential bridge checks use temporary storage and fake upstream profiles."""
import unittest
import test_core

class BrowserSyncTests(unittest.TestCase):
    run_case = test_core.CoreTests.run_case

    def test_pairing_is_one_time_and_device_secrets_are_hashed(self):
        self.run_case('''
            import browser_sync as b
            code=b.create_pairing()['code']
            assert code not in b.STATE.read_text()
            device=b.pair({'code':code,'name':'Fixture browser'})
            assert device['device_key'] not in b.STATE.read_text()
            auth='Bearer '+device['device_key']
            assert b.authorize(auth)==device['device_id']
            try:b.pair({'code':code})
            except b.UsageError as e:assert e.code=='INVALID_PAIRING'
            else:raise AssertionError('pairing replay accepted')
            state=b._read();state['devices']=[];b._save(state)
            try:b.authorize(auth)
            except b.UsageError as e:assert e.status==401
            else:raise AssertionError('revoked key accepted')
            code=b.create_pairing()['code'];state=b._read();state['pairing']['expires']=0;b._save(state)
            try:b.pair({'code':code})
            except b.UsageError as e:assert e.code=='INVALID_PAIRING'
            else:raise AssertionError('expired code accepted')
        ''')

    def test_sync_validates_account_source_and_invalidates_cache(self):
        self.run_case('''
            import browser_sync as b,usage_dashboard as u,json,base64,time,secrets
            def jwt(exp):
                encoded=base64.urlsafe_b64encode(json.dumps({'exp':exp,'nonce':secrets.token_hex(8)}).encode()).decode().rstrip('=')
                return 'header.'+encoded+'.signature'
            first=jwt(time.time()+3600);second=jwt(time.time()+7200)
            u.request=lambda *a,**k:{'id':123,'email':'demo@example.com'}
            u.connect({'access_token':first});u.CACHE['stale']={};u.RANGES['stale']={}
            payload={'site_id':'lulu','origin':'https://luluai.win','access_token':second}
            try:b.sync_token(dict(payload,origin='https://wrong.example'))
            except b.UsageError as e:assert e.code=='ORIGIN_MISMATCH'
            else:raise AssertionError('wrong origin accepted')
            u.request=lambda *a,**k:{'id':999,'email':'other@example.com'}
            try:b.sync_token(payload)
            except b.UsageError as e:assert e.code=='ACCOUNT_MISMATCH'
            else:raise AssertionError('account switched')
            assert u.load_session()['access_token']==first
            u.request=lambda *a,**k:{'id':123,'email':'demo@example.com'}
            assert b.sync_token(payload)=={'ok':True,'changed':True}
            assert not u.CACHE and not u.RANGES and u.load_session()['access_token']==second
            assert u.load_session()['refresh_token']==''
            assert second.encode() not in (u.PRIVATE/'connection.enc').read_bytes()
            u.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('duplicate validation'))
            assert b.sync_token(payload)['changed'] is False
            try:b.sync_token(dict(payload,access_token=jwt(1)))
            except b.UsageError as e:assert e.code=='EXPIRED_TOKEN'
            else:raise AssertionError('expired token accepted')
            u.disconnect()
            try:b.sync_token(payload)
            except b.UsageError as e:assert e.code=='CONNECT_REQUIRED'
            else:raise AssertionError('manual disconnect overridden')
        ''')

    def test_device_api_cannot_access_admin_or_other_monitor_apis(self):
        self.run_case('''
            import app,browser_sync as b,threading,urllib.request,urllib.error,json,hashlib,io,zipfile
            app.AUTH_REQUIRED=True;app.AUTH_USERNAME='fixture';app.SESSION_SECRET='temporary-session-secret'
            salt=bytes.fromhex('ab'*16)
            app.AUTH_PASSWORD_HASH='pbkdf2_sha256$'+salt.hex()+'$'+hashlib.pbkdf2_hmac('sha256',b'fixture-password',salt,310000).hex()
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            base='http://127.0.0.1:'+str(server.server_address[1]);cookie=''
            def call(path,body=None,auth='',headers=None):
                hs={'Content-Type':'application/json',**(headers or {})}
                if cookie:hs['Cookie']=cookie
                if auth:hs['Authorization']=auth
                request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=hs)
                try:r=urllib.request.urlopen(request);return r.status,r.read(),r.headers
                except urllib.error.HTTPError as e:return e.code,e.read(),e.headers
            prefix='/api/cache/browser-sync/'
            assert call(prefix+'create-pairing',{})[0]==401
            status,body,headers=call('/api/login',{'username':'fixture','password':'fixture-password'})
            assert status==200
            cookie=headers['Set-Cookie'].split(';')[0]
            assert call(prefix+'create-pairing',{},headers={'Origin':'https://evil.example'})[0]==403
            status,body,_=call(prefix+'create-pairing',{});assert status==200;code=json.loads(body)['code']
            status,body,_=call(prefix+'extension.zip');assert status==200
            archive=zipfile.ZipFile(io.BytesIO(body));assert 'linx-browser-sync/manifest.json' in archive.namelist()
            cookie=''
            status,body,_=call(prefix+'pair',{'code':code});assert status==200
            key='Bearer '+json.loads(body)['device_key']
            assert call(prefix+'sites',auth=key)[0]==200
            assert call(prefix+'sites')[0]==401
            assert call(prefix+'create-pairing',{},auth=key)[0]==401
            assert call(prefix+'create-capture',{'site_id':'lulu'},auth=key)[0]==401
            assert call(prefix+'capture-status/unknown',auth=key)[0]==401
            assert call('/api/sites',auth=key)[0]==401
            assert call(prefix+'unpair',{},auth=key)[0]==200
            assert call(prefix+'sites',auth=key)[0]==401
            server.shutdown();server.server_close()
        ''')

    def test_capture_initial_connection_is_explicit_single_use_and_account_safe(self):
        self.run_case('''
            import browser_sync as b,usage_dashboard as u,json,base64,time
            encoded=base64.urlsafe_b64encode(json.dumps({'exp':time.time()+7200}).encode()).decode().rstrip('=')
            token='header.'+encoded+'.signature'
            u.request=lambda *a,**k:{'id':123,'email':'demo@example.com'}
            job=b.create_capture({'site_id':'lulu'})
            assert b.configured_sites()['sites'][0]['capture_request']==job['request_id']
            payload=dict(job,access_token=token)
            try:b.capture_token(dict(payload,origin='https://wrong.example'))
            except b.UsageError as e:assert e.code=='ORIGIN_MISMATCH'
            else:raise AssertionError('incorrect origin accepted')
            assert not u.load_session()
            assert b.capture_token(payload)['ok']
            assert b.capture_status(job['request_id'])['state']=='done'
            assert 'capture_request' not in b.configured_sites()['sites'][0]
            assert u.load_session()['user_id']==123
            assert token.encode() not in (u.PRIVATE/'connection.enc').read_bytes()
            assert token not in json.dumps(b.CAPTURES)
            try:b.capture_token(payload)
            except b.UsageError as e:assert e.code=='CAPTURE_EXPIRED'
            else:raise AssertionError('job replay accepted')
            second=b.create_capture({'site_id':'lulu'})
            u.request=lambda *a,**k:{'id':999,'email':'other@example.com'}
            changed=base64.urlsafe_b64encode(json.dumps({'exp':time.time()+9000}).encode()).decode().rstrip('=')
            try:b.capture_token(dict(second,access_token='header.'+changed+'.signature'))
            except b.UsageError as e:assert e.code=='ACCOUNT_MISMATCH'
            else:raise AssertionError('existing account replaced')
            assert u.load_session()['user_id']==123
            expired=b.create_capture({'site_id':'lulu'});b.CAPTURES[expired['request_id']]['expires']=0
            try:b.capture_token(dict(expired,access_token=token))
            except b.UsageError as e:assert e.code=='CAPTURE_EXPIRED'
            else:raise AssertionError('expired job accepted')
        ''')

if __name__=='__main__':unittest.main()
