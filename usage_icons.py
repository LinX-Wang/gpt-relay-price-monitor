"""Public station icons; independent of account credentials and usage requests."""
from __future__ import annotations
import base64, hashlib, json, threading, time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import usage_sites

from settings import DATA_DIR
CACHE = DATA_DIR / 'usage-icons'
LOCKS = {}
LOCK = threading.Lock()
MAX_BYTES = 512 * 1024


def _url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Unsupported icon URL')
    usage_sites._valid_base('https://' + (u.hostname or ''))
    return value


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _url(newurl))


def _fetch(url, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    req = Request(_url(url), headers={'User-Agent': 'Mozilla/5.0 LinX-StationIcon/1.0', 'Accept': 'image/*,text/html,application/json;q=0.9'})
    with build_opener(_Redirect()).open(req, timeout=min(4, remaining)) as r:
        raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Icon response too large')
        return raw, r.geturl()


def _image(raw):
    if not raw or len(raw) > MAX_BYTES:
        return None
    if raw.startswith(bytes([137,80,78,71,13,10,26,10])): return 'image/png'
    if raw.startswith(bytes([255,216,255])): return 'image/jpeg'
    if raw[:6] in (b'GIF87a', b'GIF89a'): return 'image/gif'
    if raw.startswith(bytes([0,0,1,0])): return 'image/x-icon'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP': return 'image/webp'
    head = raw[:1024].decode('utf-8', errors='ignore').lstrip().lower()
    if '<svg' in head and '<html' not in head and '<!doctype html' not in head: return 'image/svg+xml'
    return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.icons = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'link' and any(x in (a.get('rel') or '').lower().split() for x in ('icon','apple-touch-icon','shortcut')) and a.get('href'):
            self.icons.append(a['href'])


def _discover(base):
    deadline = time.monotonic() + 14
    candidates = []
    try:
        raw, _ = _fetch(base + '/api/v1/settings/public', deadline)
        config = json.loads(raw)
        config = config.get('data', config)
        if isinstance(config, dict):
            for key in ('site_logo', 'logo_url', 'logo', 'favicon'):
                value = config.get(key)
                if isinstance(value, str) and value.strip(): candidates.append(value.strip())
    except Exception:
        pass
    # Official branding first; a favicon is the fallback for other panel types.
    def download(values, origin):
        for value in values:
            if time.monotonic() >= deadline: break
            try:
                if value.startswith('data:image/'):
                    meta, data = value.split(',', 1)
                    if ';base64' not in meta: continue
                    raw = base64.b64decode(data, validate=True)
                else:
                    raw, _ = _fetch(urljoin(origin, value), deadline)
                mime = _image(raw)
                if mime: return raw, mime
            except Exception:
                continue
        return None
    found = download(candidates[:4], base + '/')
    if found: return found
    try:
        raw, final = _fetch(base + '/', deadline)
        parser = _Links(); parser.feed(raw.decode('utf-8', errors='replace'))
        found = download(parser.icons[:4], final)
        if found: return found
    except Exception:
        pass
    return download(['/favicon.ico', '/favicon.png'], base + '/')


def get(site_id):
    client = usage_sites.client(site_id)
    base = client.BASE
    key = hashlib.sha256(base.encode()).hexdigest()[:24]
    with LOCK:
        lock = LOCKS.setdefault(key, threading.Lock())
    with lock:
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (key + '.image')
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            raw = path.read_bytes(); mime = _image(raw)
            if mime: return raw, mime
        miss = CACHE / (key + '.miss')
        if miss.exists() and time.time() - miss.stat().st_mtime < 900:
            if path.exists():
                raw = path.read_bytes(); mime = _image(raw)
                if mime: return raw, mime
            return None
        found = _discover(base)
        if found:
            tmp = CACHE / (key + '.tmp'); tmp.write_bytes(found[0]); tmp.replace(path)
            miss.unlink(missing_ok=True)
            return found
        miss.touch()
        if path.exists():
            raw = path.read_bytes(); mime = _image(raw)
            if mime: return raw, mime
        return None
