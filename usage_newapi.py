"""Read-only New API adapter for the shared Sub2API-shaped dashboard contract.

Only access tokens are used. Cookies, refresh and model endpoints are never sent.
New API's OpenAI prompt count includes cache; Anthropic's excludes it.
"""
from __future__ import annotations
import hashlib, json, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

SHANGHAI=timezone(timedelta(hours=8))
MAX_ROWS=10000


def number(value):
    import math
    if value is None or isinstance(value,bool):return None
    try:value=float(value)
    except (ValueError,TypeError):return None
    return value if math.isfinite(value) else None


def read(c,path,token):
    raw=c.upstream_request(path,token)
    if not isinstance(raw,dict) or raw.get('success') is not True:
        raise c.UsageError('New API 未接受此次查询，请检查官网登录或访问权限。',502,'NEWAPI_DENIED')
    return raw.get('data')


def profile(c,token,include_balance=True):
    raw=read(c,'/api/user/self',token)
    if not isinstance(raw,dict) or not raw.get('id'):
        raise c.UsageError('无法确认 New API 账户身份，未保存连接。',502,'INVALID_PROFILE')
    out={k:raw.get(k) for k in ('id','username','email','display_name')}
    # Currency conversion is optional; an unavailable status never invalidates a login.
    current=c.load_session()
    unit=currency(c,token,optional=True) if include_balance and current and current.get('panel')=='newapi' else None
    out['balance']=number(raw.get('quota'))/unit if unit and number(raw.get('quota')) is not None else None
    return out


def currency(c,token,optional=False):
    with c.NEWAPI_LOCK:
        saved=c.NEWAPI_STATE.get('currency')
        if saved and time.monotonic()-saved[0]<300:return saved[1]
    try:
        raw=read(c,'/api/status',token)
        unit=number(raw.get('quota_per_unit')) if isinstance(raw,dict) else None
        if not unit or unit<=0:raise c.UsageError('New API 未提供有效的额度换算比例，费用暂不展示。',502,'NEWAPI_CURRENCY')
    except c.UsageError:
        if optional:return None
        raise
    with c.NEWAPI_LOCK:c.NEWAPI_STATE['currency']=(time.monotonic(),unit)
    return unit


def group_id(name):
    # Stable, numeric and safe in JavaScript; preserves the existing group selector.
    return int(hashlib.sha256(str(name).encode()).hexdigest()[:13],16)


def groups(c,token):
    key=('groups',hashlib.sha256(token.encode()).hexdigest())
    with c.NEWAPI_LOCK:
        saved=c.NEWAPI_STATE.get(key)
        if saved and time.monotonic()-saved[0]<120:return saved[1]
    raw=read(c,'/api/user/self/groups',token)
    if not isinstance(raw,dict):raise c.UsageError('New API 分组列表格式不兼容。')
    result=[]
    for name,value in raw.items():
        ratio=number(value.get('ratio')) if isinstance(value,dict) else number(value)
        result.append({'id':group_id(name),'name':str(name),'rate_multiplier':ratio})
    with c.NEWAPI_LOCK:c.NEWAPI_STATE[key]=(time.monotonic(),result)
    return result


def normalize_row(row):
    raw=row['record'];unit=row.get('quota_per_unit')
    other=raw.get('other') or {}
    if isinstance(other,str):
        try:other=json.loads(other)
        except (ValueError,TypeError):other={}
    if not isinstance(other,dict):other={}
    def count(value):
        n=number(value)
        return int(n) if n is not None and n>=0 else None
    prompt=count(raw.get('prompt_tokens'));output=count(raw.get('completion_tokens'))
    cache=count(other.get('cache_tokens'))
    creation=count(other.get('cache_creation_tokens'))
    creation_parts=[count(other.get(k)) for k in ('cache_creation_tokens_5m','cache_creation_tokens_1h','cache_creation_tokens_12h')]
    if creation is None and any(v is not None for v in creation_parts):creation=sum(v or 0 for v in creation_parts)
    anthropic=other.get('usage_semantic')=='anthropic' or other.get('claude') is True
    if anthropic:
        uncached=prompt
        # Missing cache components are unknown, never fabricated as zero.
        inputs=prompt+cache+creation if all(v is not None for v in (prompt,cache,creation)) else None
    else:
        inputs=prompt
        uncached=prompt-cache if prompt is not None and cache is not None and cache<=prompt else None
        if cache is not None and prompt is not None and cache>prompt:inputs=None
    total=inputs+output if inputs is not None and output is not None else None
    cost=number(raw.get('quota'));cost=cost/unit if cost is not None and unit else None
    duration=number(raw.get('use_time'));frt=number(other.get('frt'))
    stamp=number(raw.get('created_at'))
    try:created=datetime.fromtimestamp(stamp,SHANGHAI).isoformat(timespec='seconds') if stamp is not None else None
    except (ValueError,OverflowError,OSError):created=None
    group=str(raw.get('group') or other.get('group') or '')
    ratio=number(other.get('user_group_ratio'))
    if ratio is None or ratio<0:ratio=number(other.get('group_ratio'))
    reasoning=other.get('reasoning_effort')
    if reasoning is None and isinstance(other.get('reasoning'),dict):reasoning=other['reasoning'].get('effort')
    return {'id':raw.get('id'),'model':raw.get('model_name'),'created_at':created,
            'group_id':group_id(group),'group_name':group or '未标注分组',
            'input_tokens':uncached,'output_tokens':output,'cache_read_tokens':cache,'cache_creation_tokens':creation,
            'input_total':inputs,'total_tokens':total,'cache_rate':cache/inputs*100 if inputs and cache is not None else None,
            'actual_cost':cost,'total_cost':None,'rate_multiplier':ratio,
            'first_token_ms':frt if frt is not None and frt>=0 else None,
            'duration_ms':duration*1000 if duration is not None and duration>=0 else None,
            'stream':raw.get('is_stream'),'reasoning_effort':reasoning}


def _range(c,q,token):
    def get(k,default=''):return str(q.get(k,[default])[0])
    try:
        start=datetime.fromisoformat(get('start_date')+'T'+get('start_time','00:00')).replace(tzinfo=SHANGHAI)
        end=datetime.fromisoformat(get('end_date')+'T'+get('end_time','23:59')).replace(tzinfo=SHANGHAI)+timedelta(minutes=1)
    except ValueError:raise c.UsageError('New API 查询日期无效。',400,'INVALID_INPUT') from None
    params={'type':2,'start_timestamp':int(start.timestamp()),'end_timestamp':int(end.timestamp())-1}
    if get('model'):params['model_name']=get('model')
    selected=get('group_id')
    if selected:
        names={str(g['id']):g['name'] for g in groups(c,token)}
        if selected not in names:raise c.UsageError('所选 New API 分组已变化，请重新选择。',400,'INVALID_INPUT')
        params['group']=names[selected]
    # Deduplicate the list, summary and chart calls without holding locks during I/O.
    key=('range',hashlib.sha256(token.encode()).hexdigest(),urlencode(params))
    with c.NEWAPI_LOCK:
        saved=c.NEWAPI_STATE.get(key)
        if saved and time.monotonic()-saved[0]<10:return saved[1]
        pending_key=('pending',key);flight=c.NEWAPI_STATE.get(pending_key)
        owner=flight is None
        if owner:
            flight={'event':threading.Event()};c.NEWAPI_STATE[pending_key]=flight
    if not owner:
        if not flight['event'].wait(45):raise c.UsageError('New API 用量读取超时，请缩短日期范围后重试。',504,'NEWAPI_TIMEOUT')
        if 'error' in flight:raise flight['error']
        return flight['result']
    try:
        def page(n):
            raw=read(c,'/api/log/self?'+urlencode(dict(params,p=n,page_size=100)),token)
            if not isinstance(raw,dict) or not isinstance(raw.get('items'),list):
                raise c.UsageError('New API 用量列表格式发生变化。',502,'NEWAPI_FORMAT')
            return raw
        first=page(1)
        try:total=int(first['total']);size=int(first.get('page_size') or 100)
        except (ValueError,TypeError,KeyError):raise c.UsageError('New API 分页数据无效。',502,'NEWAPI_FORMAT') from None
        if total<0 or size<1 or size>100:raise c.UsageError('New API 分页数据无效。',502,'NEWAPI_FORMAT')
        if total>MAX_ROWS:raise c.UsageError('New API 所选时段超过 10,000 条记录，请缩短日期范围以保证统计完整。',400,'RANGE_TOO_LARGE')
        batches=[first]
        if total>size:
            with ThreadPoolExecutor(max_workers=4) as pool:batches.extend(pool.map(page,range(2,(total+size-1)//size+1)))
        records=[]
        for batch in batches:
            if int(batch.get('total',-1))!=total:raise c.UsageError('New API 查询期间记录发生变化，请重新读取。',409,'RANGE_CHANGED')
            records.extend(r for r in batch['items'] if isinstance(r,dict) and r.get('type')==2)
        if len(records)!=total or len({r.get('id') for r in records})!=total:
            raise c.UsageError('New API 分页记录不完整或重复，暂不展示汇总。',409,'RANGE_CHANGED')
        unit=currency(c,token,optional=True)
        result=[{'_source':'newapi','record':r,'quota_per_unit':unit} for r in records]
        result.sort(key=lambda r:number(r['record'].get('created_at')) or 0,reverse=True)
        with c.NEWAPI_LOCK:
            # A reconnect/disconnect clears pending_key; do not repopulate its cache.
            if c.NEWAPI_STATE.get(pending_key) is flight:
                if len(c.NEWAPI_STATE)>100:
                    for old in list(c.NEWAPI_STATE):
                        if isinstance(old,tuple) and old[0]=='range':c.NEWAPI_STATE.pop(old,None)
                c.NEWAPI_STATE[key]=(time.monotonic(),result)
            flight['result']=result
        return result
    except Exception as error:flight['error']=error;raise
    finally:
        with c.NEWAPI_LOCK:
            if c.NEWAPI_STATE.get(pending_key) is flight:c.NEWAPI_STATE.pop(pending_key,None)
            flight['event'].set()


def statistics(c,records):
    total=c.summary([normalize_row(r) for r in records])
    return {'total_requests':total['requests'],'total_input_tokens':total['input_tokens'],
            'total_output_tokens':total['output_tokens'],'total_cache_read_tokens':total['cache_read_tokens'],
            'total_cache_creation_tokens':total['cache_creation_tokens'],'total_tokens':total['total_tokens'],
            'total_actual_cost':total['actual_cost'],'total_input_total':total['input_total']}


def request(c,path,token,payload=None):
    if payload is not None:raise c.UsageError('New API 使用官网登录令牌连接，请在官网完成登录。',400,'INVALID_INPUT')
    parsed=urlsplit(path);q=parse_qs(parsed.query)
    if parsed.path=='/api/v1/auth/me':return profile(c,token)
    if parsed.path=='/api/v1/groups/available':return groups(c,token)
    if parsed.path not in ('/api/v1/usage','/api/v1/usage/stats','/api/v1/usage/dashboard/trend'):
        raise c.UsageError('New API 暂不支持此查询。',400,'INVALID_INPUT')
    rows=_range(c,q,token)
    if parsed.path=='/api/v1/usage':
        page=int(q.get('page',['1'])[0]);size=int(q.get('page_size',['100'])[0]);offset=(page-1)*size
        return {'page':page,'page_size':size,'total':len(rows),'items':rows[offset:offset+size]}
    if parsed.path=='/api/v1/usage/stats':return statistics(c,rows)
    days={}
    for row in rows:
        norm=normalize_row(row)
        if not norm['created_at']:raise c.UsageError('New API 记录缺少有效时间，暂不展示逐天统计。')
        days.setdefault(norm['created_at'][:10],[]).append(norm)
    # The complete log range was verified, so absent closed days are known empty.
    # Keep future dates unknown rather than manufacturing zero usage.
    first=datetime.fromisoformat(q['start_date'][0]).date()
    last=min(datetime.fromisoformat(q['end_date'][0]).date(),datetime.now(SHANGHAI).date())
    while first<=last:
        days.setdefault(str(first),[]);first+=timedelta(days=1)
    return {'trend':[dict(date=day,**c.summary(items)) for day,items in sorted(days.items())]}
