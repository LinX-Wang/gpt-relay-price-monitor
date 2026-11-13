"""Encrypted credentials and isolated Sub2API / New API usage clients."""
from __future__ import annotations
import hashlib, importlib.util, ipaddress, json, os, re, secrets, sys, threading, socket
from pathlib import Path
from urllib.parse import urlsplit
import usage_dashboard as default_client

ROOT=Path(__file__).parent
from settings import DATA_DIR
PRIVATE=DATA_DIR/'usage-private'
REGISTRY=PRIVATE/'site-registry.json'
LOCK=threading.RLock()
CLIENTS={'lulu':default_client}

def _registry():
    PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(PRIVATE,0o700)
    if not REGISTRY.exists():return []
    try:
        data=json.loads(REGISTRY.read_text())
        if not isinstance(data,list):raise ValueError()
        return data
    except (ValueError,OSError):raise default_client.UsageError('站点列表无法读取，请联系维护者。',500,'REGISTRY_ERROR') from None

def _valid_base(value):
    value=str(value or '').strip()
    try:u=urlsplit(value);port=u.port
    except ValueError:raise default_client.UsageError('官网地址无效，请填写 https://域名。',400,'INVALID_SITE') from None
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/') or port not in (None,443):
        raise default_client.UsageError('官网地址仅支持 HTTPS 域名根地址，不要填写路径、端口或令牌。',400,'INVALID_SITE')
    host=u.hostname.rstrip('.').encode('idna').decode('ascii').lower()
    if host in ('localhost','localhost.localdomain') or host.endswith(('.local','.internal','.lan')) or '.' not in host:
        raise default_client.UsageError('官网地址必须是公开 HTTPS 域名。',400,'INVALID_SITE')
    try:ip=ipaddress.ip_address(host)
    except ValueError:ip=None
    if ip:
        if not ip.is_global:raise default_client.UsageError('官网必须使用公开域名。',400,'INVALID_SITE')
        raise default_client.UsageError('请填写站点域名，而不是 IP 地址。',400,'INVALID_SITE')
    try:addresses={ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
    except OSError:raise default_client.UsageError('官网域名暂时无法解析，请检查地址后重试。',400,'INVALID_SITE') from None
    if not addresses or any(not address.is_global for address in addresses):
        raise default_client.UsageError('官网域名必须解析到公开网络地址。',400,'INVALID_SITE')
    return 'https://'+host

def _save(rows):
    tmp=PRIVATE/('site-registry.'+secrets.token_hex(5)+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(rows,f,ensure_ascii=False)
    os.replace(tmp,REGISTRY);os.chmod(REGISTRY,0o600)

def _client(site):
    sid=site['site_id']
    if sid=='lulu':return default_client
    if sid not in CLIENTS:
        spec=importlib.util.spec_from_file_location('_linx_usage_'+sid,default_client.__file__)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);module.UsageError=default_client.UsageError
        module.SITE_ID=sid;module.SITE_NAME=site['name'];module.BASE=site['base_url'];module.PRIVATE=PRIVATE/'sites'/sid
        CLIENTS[sid]=module
    c=CLIENTS[sid];c.SITE_NAME=site['name'];c.BASE=site['base_url'];return c

def sites():
    with LOCK:
        rows=[{'site_id':'lulu','name':'噜噜 AI','base_url':'https://luluai.win'}]+_registry()
        out=[]
        for site in rows:
            c=_client(site);out.append(dict(c.status(),name=site['name'],base_url=site['base_url']))
        return out

def add(payload):
    name=str(payload.get('name','')).strip();base=_valid_base(payload.get('base_url'))
    if not name or len(name)>32 or any(ord(c)<32 for c in name):
        raise default_client.UsageError('站点名称请填写 1–32 个字符。',400,'INVALID_SITE')
    with LOCK:
        rows=_registry()
        if len(rows)>=10:raise default_client.UsageError('最多可添加 10 个额外站点。',400,'SITE_LIMIT')
        if any(x['base_url']==base for x in rows) or base=='https://luluai.win':
            raise default_client.UsageError('这个官网已经添加。',409,'SITE_EXISTS')
        slug=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')[:24]
        sid='site-'+hashlib.sha256((base+'|'+name).encode()).hexdigest()[:12]
        site={'site_id':sid,'name':name,'base_url':base};rows.append(site);_save(rows)
        _client(site)
        return dict(site,connected=False,username=None)

def client(site_id):
    sid=str(site_id or 'lulu')
    with LOCK:
        if sid=='lulu':return default_client
        for site in _registry():
            if site['site_id']==sid:return _client(site)
    raise default_client.UsageError('所选站点不存在或已删除。',404,'SITE_NOT_FOUND')

def remove(site_id):
    sid=str(site_id or '')
    if sid=='lulu':raise default_client.UsageError('默认站点不能移除。',400,'INVALID_SITE')
    with LOCK:
        rows=_registry();site=next((x for x in rows if x['site_id']==sid),None)
        if not site:raise default_client.UsageError('所选站点不存在。',404,'SITE_NOT_FOUND')
        _save([x for x in rows if x['site_id']!=sid])
        c=CLIENTS.pop(sid,None)
        if c:
            with c.LOCK:
                c.disconnect()
                c.WARM_POOL.shutdown(wait=True,cancel_futures=True)
        import shutil
        shutil.rmtree(PRIVATE/'sites'/sid,ignore_errors=True)
        return {'ok':True,'site_id':sid}
