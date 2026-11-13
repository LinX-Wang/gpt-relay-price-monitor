"""Isolated, read-only Sub2API usage client; one instance per connected site."""
from __future__ import annotations
import json, math, os, threading, time, secrets, socket, ipaddress, http.client, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler
from urllib.error import HTTPError, URLError
from cryptography.fernet import Fernet, InvalidToken

BASE = 'https://luluai.win'
SITE_ID = 'lulu'
SITE_NAME = '噜噜 AI'
from settings import DATA_DIR
PRIVATE = DATA_DIR / 'usage-private'
LOCK = threading.RLock()
REFRESH_LOCK = threading.RLock()
CACHE = {}
RANGES = {}
RANGE_LOCK = threading.RLock()
WARM_POOL = ThreadPoolExecutor(max_workers=1)
WARMING = set()
SHANGHAI = timezone(timedelta(hours=8))
META = None
SESSION = None
NEWAPI_LOCK = threading.RLock()
NEWAPI_STATE = {}

class UsageError(Exception):
    def __init__(self, message, status=502, code='UPSTREAM_ERROR'):
        super().__init__(message); self.status=status; self.code=code

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UsageError('站点返回了跳转地址，已停止传递登录信息。')

def public_addresses(host,port):
    try:addresses=list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    except OSError:raise UsageError('站点域名暂时无法解析，请稍后重试。',502,'DNS_ERROR') from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise UsageError('站点必须解析到公开网络地址，已停止发送登录令牌。',400,'INVALID_SITE')
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        addresses=public_addresses(self.host,self.port)
        # Pin the socket to a validated IP; keep the domain for TLS validation/SNI.
        last=None
        for address in addresses:
            try:
                self.sock=socket.create_connection((address,self.port),self.timeout,self.source_address)
                self.sock=self._context.wrap_socket(self.sock,server_hostname=self.host)
                return
            except OSError as error:
                last=error
                if self.sock:self.sock.close()
        raise last


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self,req):
        return self.do_open(PublicHTTPSConnection,req,context=self._context)


def request(path, token='', payload=None):
    session=load_session()
    if session and session.get('panel')=='newapi':
        import usage_newapi
        return usage_newapi.request(sys.modules[__name__],path,token or session['access_token'],payload)
    return upstream_request(path,token,payload)


def upstream_request(path, token='', payload=None):
    from urllib.parse import urlsplit
    allowed={'/api/user/self','/api/user/self/groups','/api/log/self','/api/status'}
    if ('..' in path or not path.startswith('/') or path.startswith('//')
        or (not path.startswith('/api/v1/') and urlsplit(path).path not in allowed)):
        raise UsageError('不支持的接口', 400)
    if not path.startswith('/api/v1/') and payload is not None:
        raise UsageError('New API 连接仅允许只读查询。',400,'INVALID_INPUT')
    headers={'Accept':'application/json','Content-Type':'application/json','User-Agent':'Mozilla/5.0 LinX-Usage/1.0',
             'Origin':BASE,'Referer':BASE+'/usage'}
    if token: headers['Authorization']='Bearer '+token
    body=json.dumps(payload).encode() if payload is not None else None
    try:
        with build_opener(ProxyHandler({}),NoRedirect,PublicHTTPSHandler).open(Request(BASE+path,headers=headers,data=body),timeout=15) as r:
            raw=r.read(8_000_001)
            if len(raw)>8_000_000: raise UsageError('站点返回的数据过大，请减少每页条数。')
            data=json.loads(raw)
    except HTTPError as e:
        if e.code==401: raise UsageError('站点登录已过期，请重新连接账户。',502,'RECONNECT_REQUIRED') from None
        if e.code==429: raise UsageError('站点限制了查询频率，请稍后刷新。',429,'RATE_LIMITED') from None
        if e.code in (400,403): raise UsageError('站点拒绝了请求（HTTP '+str(e.code)+'）。请核对官网登录令牌；若 API 也被 Cloudflare 拦截，登录令牌仍不能绕过该验证。',502,'UPSTREAM_DENIED') from None
        raise UsageError('站点接口返回 HTTP '+str(e.code)+'，请稍后刷新。',502,'UPSTREAM_NOT_FOUND' if e.code in (404,405) else 'UPSTREAM_ERROR') from None
    except (URLError,TimeoutError,OSError):
        raise UsageError('连接站点超时或网络异常，请稍后刷新。') from None
    except (ValueError,UnicodeError):
        raise UsageError('站点返回的不是有效的用量数据。',502,'INVALID_UPSTREAM_DATA') from None
    if isinstance(data,dict) and 'code' in data:
        if data['code'] not in (0,200,'0'):
            raise UsageError('站点未接受此次查询，请检查账户连接。')
        return data.get('data')
    return data


def verify_profile(token,panel='auto'):
    if panel not in ('auto','sub2api','newapi'):
        raise UsageError('不支持的面板类型。',400,'INVALID_INPUT')
    if panel=='auto' and (load_session() or {}).get('panel')=='newapi':panel='newapi'
    if panel!='newapi':
        try:
            profile=request('/api/v1/auth/me',token)
            if isinstance(profile,dict) and profile.get('id'):
                current=load_session()
                return profile,(current or {}).get('panel','sub2api')
            if panel=='sub2api':raise UsageError('无法确认站点账户身份。')
        except UsageError as error:
            if panel!='auto' or error.code not in ('UPSTREAM_NOT_FOUND','INVALID_UPSTREAM_DATA'):raise
    import usage_newapi
    return usage_newapi.profile(sys.modules[__name__],token,include_balance=False),'newapi'

def cipher():
    PRIVATE.mkdir(mode=0o700,parents=True,exist_ok=True); os.chmod(PRIVATE,0o700)
    p=PRIVATE/'key'
    if not p.exists():
        try:
            fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError: pass
        else:
            with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
    os.chmod(p,0o600)
    return Fernet(p.read_bytes())

def load_session():
    global SESSION
    if SESSION is not None:return SESSION
    p=PRIVATE/'connection.enc'
    if not p.exists():return None
    try:SESSION=json.loads(cipher().decrypt(p.read_bytes()))
    except (InvalidToken,ValueError,OSError):raise UsageError('账户连接配置无法读取，请重新连接。',409,'RECONNECT_REQUIRED') from None
    return SESSION

def save_session(value):
    global SESSION
    encrypted=cipher().encrypt(json.dumps(value).encode())
    tmp=PRIVATE/('connection.'+secrets.token_hex(6)+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(encrypted)
    os.replace(tmp,PRIVATE/'connection.enc');SESSION=value

def api(path):
    s=load_session()
    if not s:raise UsageError('请先连接站点 AI 账户，连接后即可读取真实用量。',409,'CONNECT_REQUIRED')
    try:return request(path,s['access_token'])
    except UsageError as e:
        if e.code!='RECONNECT_REQUIRED' or not s.get('refresh_token'):raise
    with REFRESH_LOCK:
        current=load_session()
        if current and current.get('access_token')!=s.get('access_token'):
            s=current
        else:
            try:
                refreshed=request('/api/v1/auth/refresh',payload={'refresh_token':s['refresh_token']})
                if not isinstance(refreshed,dict) or not refreshed.get('access_token'):raise UsageError('登录已过期',502,'RECONNECT_REQUIRED')
                s=dict(s,access_token=refreshed['access_token'],refresh_token=refreshed.get('refresh_token') or s['refresh_token'])
                save_session(s)
            except UsageError:raise UsageError('站点登录已过期，请重新连接账户。',502,'RECONNECT_REQUIRED') from None

    return request(path,s['access_token'])

def connect(payload):
    global META
    mode=payload.get('mode','token')
    with REFRESH_LOCK:
        if mode=='token':
            access=str(payload.get('access_token','')).strip();refresh=str(payload.get('refresh_token','')).strip()
            if access.startswith('Bearer '):access=access[7:].strip()
            if not access or len(access)>16000 or any(c.isspace() for c in access) or access.startswith('sk-'):
                raise UsageError('请填写站点网站的登录令牌（JWT），不是调用模型用的 sk- 密钥。',400,'INVALID_INPUT')
        elif mode=='login':
            email=str(payload.get('email','')).strip();password=str(payload.get('password',''))
            if not email or not password or len(email)>254 or len(password)>2048:raise UsageError('请填写站点账户邮箱和密码。',400,'INVALID_INPUT')
            result=request('/api/v1/auth/login',payload={'email':email,'password':password})
            if not isinstance(result,dict) or result.get('requires_2fa'):
                raise UsageError('站点要求验证码或二次验证，请在站点官网登录后，切换“登录令牌”方式连接。',409,'TWO_FACTOR_REQUIRED')
            access=result.get('access_token','');refresh=result.get('refresh_token','')
            if not access:raise UsageError('站点没有返回登录令牌，请使用登录令牌方式连接。')
        else:raise UsageError('连接方式无效',400,'INVALID_INPUT')
        profile,panel=verify_profile(access,payload.get('panel','auto'))
        if not isinstance(profile,dict) or not profile.get('id'):raise UsageError('无法确认站点账户身份，未保存此次连接。')
        save_session({'access_token':access,'refresh_token':refresh if panel=='sub2api' else '','panel':panel,'user_id':profile['id'],
                      'username':profile.get('username') or profile.get('email') or '站点用户','connected_at':datetime.now().isoformat(timespec='microseconds')})
        CACHE.clear();RANGES.clear();META=None
        with NEWAPI_LOCK:NEWAPI_STATE.clear()
        return status()

def disconnect():
    global SESSION,META
    with REFRESH_LOCK:
        p=PRIVATE/'connection.enc'
        if p.exists():p.unlink()
        SESSION=None;META=None;CACHE.clear();RANGES.clear()
        with NEWAPI_LOCK:NEWAPI_STATE.clear()
    return {'ok':True,'connected':False}

def status():
    # Reading the saved connection never waits for an in-flight usage query.
    s=load_session()
    return {'connected':bool(s),'site_id':SITE_ID,'site_name':SITE_NAME,'base_url':BASE,
            'username':s.get('username') if s else None,'connected_at':s.get('connected_at') if s else None,
            'panel':s.get('panel','sub2api') if s else None}


def number(value):
    try:n=float(value)
    except (ValueError,TypeError):return None
    return n if math.isfinite(n) else None

def normalize_row(row):
    if row.get('_source')=='newapi':
        import usage_newapi
        return usage_newapi.normalize_row(row)
    out={k:row.get(k) for k in ('id','model','created_at','group_id','input_tokens','output_tokens','cache_read_tokens',
         'cache_creation_tokens','actual_cost','total_cost','rate_multiplier','first_token_ms','duration_ms','stream','reasoning_effort')}
    for k in ('input_tokens','output_tokens','cache_read_tokens','cache_creation_tokens'):
        n=number(out[k]);out[k]=max(0,int(n)) if n is not None else None
    for k in ('actual_cost','total_cost','rate_multiplier','first_token_ms','duration_ms'):out[k]=number(out[k])
    group=row.get('group') or {};out['group_name']=str(group.get('name') or row.get('group_name') or '未标注分组') if isinstance(group,dict) else '未标注分组'
    inputs=[out[k] for k in ('input_tokens','cache_read_tokens','cache_creation_tokens')]
    all_tokens=inputs+[out['output_tokens']]
    out['input_total']=sum(inputs) if all(x is not None for x in inputs) else None
    out['total_tokens']=sum(all_tokens) if all(x is not None for x in all_tokens) else None
    out['cache_rate']=out['cache_read_tokens']/out['input_total']*100 if out['input_total'] else None
    return out

def summary(rows):
    result={'requests':len(rows)}
    for field in ('input_tokens','output_tokens','cache_read_tokens','cache_creation_tokens','total_tokens','input_total','actual_cost'):
        values=[r[field] for r in rows]
        result[field]=sum(values) if all(x is not None for x in values) else None
    result['cache_rate']=result['cache_read_tokens']/result['input_total']*100 if result.get('input_total') and result.get('cache_read_tokens') is not None else None
    return result

def period_summary(data):
    if not isinstance(data,dict):return None
    mapping={'requests':'total_requests','input_tokens':'total_input_tokens','output_tokens':'total_output_tokens',
             'cache_read_tokens':'total_cache_read_tokens','cache_creation_tokens':'total_cache_creation_tokens',
             'total_tokens':'total_tokens','actual_cost':'total_actual_cost'}
    out={k:number(data.get(v)) for k,v in mapping.items()}
    vals=[out[k] for k in ('input_tokens','cache_read_tokens','cache_creation_tokens')]
    out['input_total']=number(data.get('total_input_total')) if 'total_input_total' in data else sum(vals) if all(x is not None for x in vals) else None
    out['cache_rate']=out['cache_read_tokens']/out['input_total']*100 if out['input_total'] and out['cache_read_tokens'] is not None else None
    return out

def query_params(params):
    def get(k,default=''):return str(params.get(k,[default])[0]).strip()
    try:
        page=int(get('page','1'));size=int(get('page_size','100'))
    except ValueError:raise UsageError('分页参数无效',400,'INVALID_INPUT')
    if page<1 or page>100000 or size not in (20,50,100):raise UsageError('每页条数应为 20、50 或 100。',400,'INVALID_INPUT')
    today=datetime.now(SHANGHAI).date()
    try:
        start=datetime.strptime(get('start_date',str(today)),'%Y-%m-%d').date()
        end=datetime.strptime(get('end_date',str(today)),'%Y-%m-%d').date()
    except ValueError:raise UsageError('日期格式应为 YYYY-MM-DD。',400,'INVALID_INPUT')
    if start>end or (end-start).days>90:raise UsageError('请选择起止顺序正确、跨度不超过 90 天的日期。',400,'INVALID_INPUT')
    model=get('model');group=get('group_id')
    if len(model)>200 or (group and (not group.isdigit() or len(group)>18)):raise UsageError('模型或分组参数无效',400,'INVALID_INPUT')
    result={'page':page,'page_size':size,'start_date':str(start),'end_date':str(end),'timezone':'Asia/Shanghai','sort_by':'created_at','sort_order':'desc'}
    for field, default in (('start_time','00:00'),('end_time','23:59')):
        value=get(field,default)
        try:datetime.strptime(value,'%H:%M')
        except ValueError:raise UsageError('时间格式应为 HH:MM。',400,'INVALID_INPUT')
        if len(value)!=5:raise UsageError('时间格式应为 HH:MM。',400,'INVALID_INPUT')
        result[field]=value
    if start==end and result['start_time']>result['end_time']:
        raise UsageError('开始时间不能晚于结束时间。',400,'INVALID_INPUT')
    if model:result['model']=model
    if group:result['group_id']=group
    return result

def _metadata_unlocked():
    global META
    if META and time.monotonic()-META[0]<120:return META[1]
    identity=status().get('connected_at')
    warnings=[];groups=[];balance=None
    with ThreadPoolExecutor(max_workers=2) as pool:
        gf=pool.submit(api,'/api/v1/groups/available');pf=pool.submit(api,'/api/v1/auth/me')
        try:
            raw=gf.result()
            if isinstance(raw,list):groups=[{'id':g['id'],'name':str(g.get('name','')),'rate_multiplier':number(g.get('rate_multiplier'))} for g in raw if isinstance(g,dict) and 'id' in g]
        except UsageError as e:warnings.append(str(e))
        try:
            profile=pf.result();balance=number(profile.get('balance')) if isinstance(profile,dict) else None
            if isinstance(profile,dict):
                name=profile.get('username') or profile.get('email') or '站点用户'
                current=load_session()
                if current and current.get('connected_at')==identity and current.get('username')!=name:
                    with REFRESH_LOCK:
                        current=load_session()
                        if current and current.get('connected_at')==identity:save_session(dict(current,username=name))

        except UsageError as e:warnings.append(str(e))
    with REFRESH_LOCK:
        if status().get('connected_at')!=identity:
            raise UsageError('连接已更新，正在重新读取用量。',409,'CONNECTION_CHANGED')
        result=dict(status(),groups=groups,balance=balance,warnings=warnings)
        META=(time.monotonic(),result);return result

def metadata():
    with LOCK:return _metadata_unlocked()

def range_key(q):
    return urlencode({k:v for k,v in q.items() if k not in ('page','page_size','start_time','end_time','model','group_id')})


def range_rows(q):
    key=range_key(q)
    with RANGE_LOCK:
        entry=RANGES.get(key)
        if entry and time.monotonic()-entry[0]<10:return entry
        identity=status().get('connected_at')
        base={k:v for k,v in q.items() if k not in ('page','page_size','start_time','end_time','model','group_id')}
        def fetch_page(page):
            raw=api('/api/v1/usage?'+urlencode(dict(base,page=page,page_size=1000)))
            if not isinstance(raw,dict) or not isinstance(raw.get('items'),list):
                raise UsageError('站点用量列表格式发生变化，请联系维护者。')
            return raw
        first=fetch_page(1);expected=int(first.get('total') or 0)
        if expected>10000:
            raise UsageError('分钟筛选范围超过 10,000 条记录，请缩短日期范围后重试。',400,'RANGE_TOO_LARGE')
        size=int(first.get('page_size') or 1000)
        if not 1<=size<=1000:raise UsageError('上游分页大小无效。')
        batches=[first]
        if expected>size:
            with ThreadPoolExecutor(max_workers=4) as pool:
                batches.extend(pool.map(fetch_page,range(2,math.ceil(expected/size)+1)))
        rows=[]
        for raw in batches:
            if int(raw.get('total') or 0)!=expected:
                raise UsageError('查询期间记录发生变化，请重新查询以获得完整统计。',409,'RANGE_CHANGED')
            rows.extend(normalize_row(r) for r in raw['items'] if isinstance(r,dict))
        if len(rows)!=expected or len({r['id'] for r in rows})!=len(rows):
            raise UsageError('上游分页记录不完整或有重复，请重新查询。',409,'RANGE_CHANGED')
        with REFRESH_LOCK:
            if identity!=status().get('connected_at') or not status()['connected']:
                raise UsageError('账户连接已变化，请重新查询。',409,'CONNECT_REQUIRED')
            if len(RANGES)>4:RANGES.clear()
            entry=(time.monotonic(),rows,datetime.now(SHANGHAI).isoformat(timespec='seconds'))
            RANGES[key]=entry
            return entry


def warm_range(q):
    # Prefetch only small date windows; never delay the visible response.
    if (datetime.fromisoformat(q['end_date'])-datetime.fromisoformat(q['start_date'])).days>6:return
    key=range_key(q)
    if key in WARMING:return
    entry=RANGES.get(key)
    if entry and time.monotonic()-entry[0]<10:return
    WARMING.add(key)
    def run():
        try:range_rows(q)
        except Exception:pass  # Optional warm-up; foreground queries report errors.
        finally:WARMING.discard(key)
    WARM_POOL.submit(run)


def minute_range(q):
    rows=range_rows(q)[1]
    if q.get('model'):rows=[r for r in rows if r['model']==q['model']]
    if q.get('group_id'):rows=[r for r in rows if str(r['group_id'])==q['group_id']]
    start=datetime.fromisoformat(q['start_date']+'T'+q['start_time']).replace(tzinfo=SHANGHAI)
    end=datetime.fromisoformat(q['end_date']+'T'+q['end_time']).replace(tzinfo=SHANGHAI)+timedelta(minutes=1)
    selected=[]
    for row in rows:
        try:
            stamp=datetime.fromisoformat(str(row['created_at']).replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError()
        except (ValueError,TypeError):
            raise UsageError('上游记录缺少有效时区时间，无法准确执行分钟筛选。')
        if start<=stamp<end:selected.append(row)
    selected.sort(key=lambda r:datetime.fromisoformat(r['created_at'].replace('Z','+00:00')),reverse=True)
    offset=(q['page']-1)*q['page_size']
    return selected[offset:offset+q['page_size']],len(selected),summary(selected)


def snapshot(params):
    with LOCK:
        identity=status().get('connected_at')
        q=query_params(params);key=urlencode(q);now=time.monotonic()
        # Short deduplication window; a 10-second refresh always gets fresh usage.
        if key in CACHE and now-CACHE[key][0]<3:return CACHE[key][1]
        upstream={k:v for k,v in q.items() if k not in ('start_time','end_time')}
        filters={k:v for k,v in upstream.items() if k not in ('page','page_size','sort_by','sort_order')}
        warnings=[];period=None
        ready=RANGES.get(range_key(q))
        use_range=bool(ready and time.monotonic()-ready[0]<10)
        if use_range or q['start_time']!='00:00' or q['end_time']!='23:59':
            with ThreadPoolExecutor(max_workers=2) as pool:
                rf=pool.submit(minute_range,q);mf=pool.submit(_metadata_unlocked)
                rows,total,period=rf.result();meta=mf.result()
            raw={'page':q['page'],'total':total}
        else:
            with ThreadPoolExecutor(max_workers=3) as pool:
                uf=pool.submit(api,'/api/v1/usage?'+urlencode(upstream))
                sf=pool.submit(api,'/api/v1/usage/stats?'+urlencode(filters))
                mf=pool.submit(_metadata_unlocked)
                raw=uf.result()
                if not isinstance(raw,dict) or not isinstance(raw.get('items'),list):raise UsageError('站点用量列表格式发生变化，请联系维护者。')
                try:period=period_summary(sf.result())
                except UsageError:warnings.append('所选时段汇总暂不可用；当前页统计仍可查看。')
                meta=mf.result()
            rows=[normalize_row(r) for r in raw['items'] if isinstance(r,dict)]
        result={'connected':True,'username':meta.get('username'),'site_id':SITE_ID,'rows':rows,'page':int(raw.get('page') or q['page']),
                'page_size':q['page_size'],'total':int(raw.get('total') or 0),
                'page_summary':summary(rows),'period_summary':period,'balance':meta.get('balance'),
                'groups':meta['groups'],'warnings':warnings+meta['warnings'],'filters':q,
                'fetched_at':RANGES[range_key(q)][2] if (use_range or q['start_time']!='00:00' or q['end_time']!='23:59') else datetime.now(SHANGHAI).isoformat(timespec='seconds')}
        with REFRESH_LOCK:
            if status().get('connected_at')!=identity:
                raise UsageError('连接已更新，正在重新读取用量。',409,'CONNECTION_CHANGED')
            if not use_range:warm_range(dict(q))
            if len(CACHE)>80:CACHE.clear()
            CACHE[key]=(time.monotonic(),result);return result
