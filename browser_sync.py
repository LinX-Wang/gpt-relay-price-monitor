"""Paired browser bridge: accept access tokens only for existing usage accounts."""
from __future__ import annotations
import base64, hashlib, io, json, os, re, secrets, threading, time, zipfile
from pathlib import Path
from urllib.parse import urlsplit
import usage_sites
from usage_dashboard import UsageError

ROOT = Path(__file__).resolve().parent
STATE = usage_sites.PRIVATE / 'browser-sync.json'
LOCK = threading.RLock()
PREFIX = '/api/cache/browser-sync/'
CAPTURES = {}


def create_capture(payload):
    sid = str(payload.get('site_id', ''))
    site = next((s for s in usage_sites.sites() if s['site_id'] == sid), None)
    if not site:
        raise UsageError('请先选择并添加站点。', 404, 'SITE_NOT_FOUND')
    with LOCK:
        for key in list(CAPTURES):
            if CAPTURES[key]['expires'] < time.time():CAPTURES.pop(key)
        if len(CAPTURES) >= 40:
            raise UsageError('连接请求过多，请稍后重试。', 429, 'CAPTURE_LIMIT')
        request_id = secrets.token_urlsafe(24)
        CAPTURES[request_id] = {'site_id':sid,'origin':site['base_url'].rstrip('/'),
                               'expires':time.time()+90,'state':'pending'}
    return {'request_id':request_id,'site_id':sid,'origin':site['base_url'].rstrip('/')}


def capture_status(request_id):
    with LOCK:
        job = CAPTURES.get(request_id)
        if not job or job['expires'] < time.time():
            raise UsageError('获取请求已过期，请重新点击。', 410, 'CAPTURE_EXPIRED')
        return {k:job[k] for k in ('site_id','state')}


def capture_token(payload):
    request_id = str(payload.get('request_id',''))
    with LOCK:
        job = CAPTURES.get(request_id)
        if not job or job['expires'] < time.time() or job['state'] != 'pending':
            raise UsageError('获取请求已过期或已处理，请重新点击。', 409, 'CAPTURE_EXPIRED')
        if payload.get('site_id') != job['site_id'] or payload.get('origin') != job['origin']:
            raise UsageError('令牌来源与选择的站点不一致。', 400, 'ORIGIN_MISMATCH')
        job['state'] = 'connecting'
    try:
        result = sync_token(payload, allow_initial=True)
        with LOCK:job['state'] = 'done'
        return result
    except Exception:
        with LOCK:job['state'] = 'failed'
        raise

def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def _read():
    if not STATE.exists():
        return {'devices': [], 'pairing': None}
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise UsageError('自动同步配置读取失败，请稍后重试。', 503, 'SYNC_STATE_ERROR') from None

def _save(state):
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE.with_name('browser-sync.' + secrets.token_hex(8) + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE)
    os.chmod(STATE, 0o600)

def admin_status():
    with LOCK:
        return {'devices': [{k: d.get(k) for k in ('id', 'name', 'created_at', 'last_seen')}
                            for d in _read()['devices']]}

def create_pairing():
    with LOCK:
        state = _read()
        if len(state['devices']) >= 8:
            raise UsageError('最多连接 8 个浏览器，请先移除不用的设备。', 409, 'DEVICE_LIMIT')
        code = secrets.token_urlsafe(24)
        state['pairing'] = {'hash': _digest(code), 'expires': time.time() + 300}
        _save(state)
        return {'code': code, 'expires_in': 300}

def pair(payload):
    code = str(payload.get('code', ''))
    if not 20 <= len(code) <= 100:
        raise UsageError('配对码无效或已过期，请重新生成。', 403, 'INVALID_PAIRING')
    with LOCK:
        state = _read(); pending = state.get('pairing')
        if not pending or pending['expires'] < time.time() or not secrets.compare_digest(pending['hash'], _digest(code)):
            raise UsageError('配对码无效或已使用，请在用量页面重新生成。', 403, 'INVALID_PAIRING')
        if len(state['devices']) >= 8:
            raise UsageError('浏览器数量已达上限。', 409, 'DEVICE_LIMIT')
        key = secrets.token_urlsafe(32)
        device = {'id': secrets.token_hex(8), 'name': str(payload.get('name') or '我的浏览器')[:40],
                  'key_hash': _digest(key), 'created_at': int(time.time()), 'last_seen': None}
        state['devices'].append(device); state['pairing'] = None; _save(state)
        return {'device_key': key, 'device_id': device['id']}

def authorize(header):
    if not header.startswith('Bearer ') or len(header) > 120:
        raise UsageError('浏览器未配对或已被移除。', 401, 'DEVICE_UNAUTHORIZED')
    digest = _digest(header[7:])
    with LOCK:
        state = _read()
        for device in state['devices']:
            if secrets.compare_digest(device['key_hash'], digest):
                return device['id']
    raise UsageError('浏览器未配对或已被移除，请重新配对。', 401, 'DEVICE_UNAUTHORIZED')

def touch(device_id):
    with LOCK:
        state = _read()
        for d in state['devices']:
            if d['id'] == device_id:
                d['last_seen'] = int(time.time()); _save(state); break

def configured_sites():
    # Do not expose account names, balances or tokens to the extension.
    return {'sites': [{k: s[k] for k in ('site_id', 'name', 'base_url', 'connected')}
                      for s in usage_sites.sites()]}

def sync_token(payload, allow_initial=False):
    sid = str(payload.get('site_id', ''))
    site = next((s for s in usage_sites.sites() if s['site_id'] == sid), None)
    if not site or payload.get('origin') != site['base_url'].rstrip('/'):
        raise UsageError('令牌来源与已添加站点不一致，未保存。', 400, 'ORIGIN_MISMATCH')
    token = str(payload.get('access_token', ''))
    if not 20 <= len(token) <= 16000 or not re.fullmatch(r'[A-Za-z0-9_-]+.[A-Za-z0-9_-]+.[A-Za-z0-9_-]+', token):
        raise UsageError('未找到有效的网页登录 JWT。', 400, 'INVALID_TOKEN')
    try:
        claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
        if not isinstance(claims, dict) or float(claims.get('exp', 0)) <= time.time() + 15:
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise UsageError('网页登录令牌已过期，请先在官网重新登录。', 400, 'EXPIRED_TOKEN') from None
    client = usage_sites.client(sid)
    with client.LOCK:
        old = client.load_session()
        if not old and not allow_initial:
            raise UsageError('该站点尚未连接或已手动断开，请先在用量页连接一次。', 409, 'CONNECT_REQUIRED')
        if not old:
            client.connect({'mode':'token','access_token':token,'refresh_token':''})
            return {'ok':True,'changed':True}
        if secrets.compare_digest(old['access_token'], token):
            return {'ok': True, 'changed': False}
        # Verify against the site's own API; decoding a JWT is not verification.
        profile = client.request('/api/v1/auth/me', token)
        if not isinstance(profile, dict) or not profile.get('id') or str(profile['id']) != str(old.get('user_id')):
            raise UsageError('官网登录了另一个账户，已停止覆盖。需要换账户时请手动连接。', 409, 'ACCOUNT_MISMATCH')
        from datetime import datetime
        client.save_session({'access_token': token, 'refresh_token': '', 'user_id': profile['id'],
                             'username': profile.get('username') or profile.get('email') or '站点用户',
                             'connected_at': datetime.now().isoformat(timespec='microseconds')})
        client.CACHE.clear(); client.RANGES.clear(); client.META = None
    return {'ok': True, 'changed': True}

def extension_archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as out:
        for name in ('manifest.json', 'background.js', 'content.js', 'popup.html', 'popup.js', 'icon.png'):
            out.write(ROOT / 'integrations/browser-sync' / name, 'linx-browser-sync/' + name)
        out.write(ROOT / 'docs/BROWSER_SYNC.md', 'linx-browser-sync/安装说明.md')
    return stream.getvalue()

def handle(handler, path, send_json, payload=None):
    """Separate device credentials from the monitor's normal login session."""
    if not path.startswith(PREFIX):
        return False
    action = path[len(PREFIX):]
    method = handler.command
    try:
        if method == 'POST' and not isinstance(payload, dict):
            raise UsageError('请求格式无效。', 400, 'INVALID_INPUT')
        if (method, action) == ('POST', 'pair'):
            result = pair(payload)
        elif (method, action) in (('GET', 'sites'), ('POST', 'token'), ('POST', 'capture'), ('POST', 'unpair')):
            device_id = authorize(handler.headers.get('Authorization', ''))
            if action == 'unpair':
                with LOCK:
                    state = _read()
                    state['devices'] = [d for d in state['devices'] if d['id'] != device_id]
                    _save(state)
                result = {'ok': True}
            else:
                result = configured_sites() if method == 'GET' else capture_token(payload) if action == 'capture' else sync_token(payload)
            touch(device_id)
        else:
            if not handler.require_auth(api=True):
                return True
            origin = handler.headers.get('Origin', '')
            if origin and urlsplit(origin).netloc != handler.headers.get('Host', ''):
                raise UsageError('请从本站用量页面管理自动同步。', 403, 'INVALID_ORIGIN')
            if (method, action) == ('GET', 'status'):
                result = admin_status()
            elif (method, action) == ('POST', 'create-capture'):
                result = create_capture(payload)
            elif method == 'GET' and action.startswith('capture-status/'):
                result = capture_status(action.split('/',1)[1])
            elif (method, action) == ('POST', 'create-pairing'):
                result = create_pairing()
            elif (method, action) == ('POST', 'revoke'):
                with LOCK:
                    state = _read()
                    state['devices'] = [d for d in state['devices'] if d['id'] != payload.get('device_id')]
                    _save(state)
                result = {'ok': True}
            elif (method, action) == ('GET', 'extension.zip'):
                body = extension_archive()
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/zip')
                handler.send_header('Content-Disposition', 'attachment; filename=linx-browser-sync.zip')
                handler.send_header('Cache-Control', 'no-store')
                handler.send_header('Content-Length', str(len(body)))
                handler.end_headers(); handler.wfile.write(body)
                return True
            else:
                raise UsageError('接口不存在。', 404, 'NOT_FOUND')
        send_json(handler, result)
    except UsageError as e:
        send_json(handler, {'error': str(e), 'code': e.code}, status=e.status)
    except Exception:
        send_json(handler, {'error': '自动同步暂时未完成，请稍后重试。', 'code': 'SYNC_ERROR'}, status=500)
    return True
