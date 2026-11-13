"""Daily upstream aggregates, isolated from the records endpoint."""
from datetime import datetime, timedelta
from urllib.parse import urlencode
import threading,time,json,hashlib,os,secrets
from concurrent.futures import ThreadPoolExecutor
import usage_sites
from usage_dashboard import UsageError,SHANGHAI,number
CACHE={}
LOCK=threading.Lock()
INFLIGHT={}
DAILY_CACHE={}
LOADED_SCOPES=set()

def _scope_file(scope):
    return usage_sites.PRIVATE/'trend-cache'/(hashlib.sha256(json.dumps(scope).encode()).hexdigest()+'.json')

def _load_days(scope):
    # Only aggregates, never tokens. Identity separates reconnects and accounts.
    if scope in LOADED_SCOPES:return
    LOADED_SCOPES.add(scope)
    try:
        data=json.loads(_scope_file(scope).read_text())
        for date,(at,point) in data.items():
            age=max(0,time.time()-at)
            if age<7*86400:DAILY_CACHE[scope+(date,)]=(time.monotonic()-age,point)
    except (OSError,ValueError,TypeError):pass

def _save_days(scope):
    path=_scope_file(scope);tmp=path.with_suffix('.'+secrets.token_hex(5)+'.tmp')
    try:
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        data={k[-1]:[time.time()-max(0,time.monotonic()-at),p] for k,(at,p) in DAILY_CACHE.items() if k[:-1]==scope}
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(data,f)
        os.replace(tmp,path)
    except OSError:pass
    finally:
        try:tmp.unlink(missing_ok=True)
        except OSError:pass

def trend(params):
    get=lambda key,default='':str(params.get(key,[default])[0]).strip()
    sid=get('site_id','lulu');client=usage_sites.client(sid)
    today=datetime.now(SHANGHAI).date()
    try:
        start=datetime.strptime(get('start_date',str(today-timedelta(days=6))),'%Y-%m-%d').date()
        end=datetime.strptime(get('end_date',str(today)),'%Y-%m-%d').date()
    except ValueError:raise UsageError('趋势日期格式无效。',400,'INVALID_INPUT') from None
    if start>end or (end-start).days>90:raise UsageError('趋势日期跨度不能超过 90 天。',400,'INVALID_INPUT')
    model=get('model');group=get('group_id')
    if len(model)>200 or (group and (not group.isdigit() or len(group)>18)):
        raise UsageError('模型或分组参数无效。',400,'INVALID_INPUT')
    identity=client.status().get('connected_at')
    if not identity:raise UsageError('请先连接站点账户。',409,'CONNECT_REQUIRED')
    granularity=get('granularity','day')
    if granularity not in ('day','hour') or (granularity=='hour' and start!=end):
        raise UsageError('小时趋势仅支持单日查询。',400,'INVALID_INPUT')
    filters={'start_date':str(start),'end_date':str(end),'granularity':granularity,'timezone':'Asia/Shanghai'}
    if model:filters['model']=model
    if group:filters['group_id']=group
    key=(sid,identity,urlencode(filters))
    stale=None
    with LOCK:
        saved=CACHE.get(key)
        if saved and time.monotonic()-saved[0]<(10 if granularity=='hour' else 60):return saved[1]
        # A month already read by another tab can also serve a shorter range.
        for cached_key,(at,result) in reversed(list(CACHE.items())):
            f=result['filters']
            if (granularity=='day' and cached_key[:2]==key[:2] and time.monotonic()-at<60
                and f.get('model','')==model and f.get('group_id','')==group and f.get('granularity','day')==granularity
                and f['start_date']<=str(start) and f['end_date']>=str(end)):
                return dict(result,filters=filters,points=[p for p in result['points'] if str(start)<=p['date']<=str(end)])
        if granularity=='day':
            scope=(sid,identity,model,group);_load_days(scope)
            cached=[];ages=[];day=start;expired=False
            while day<=end:
                item=DAILY_CACHE.get(scope+(str(day),))
                if not item:break
                age=time.monotonic()-item[0];ages.append(age);cached.append(item[1])
                ttl=21600 if day<today and item[1].get('requests') is not None else 60
                expired=expired or age>=ttl;day+=timedelta(days=1)
            if day>end and expired and ages and max(ages)<7*86400:
                stale={'site_id':sid,'points':cached,'filters':filters,'stale':True,
                       'fetched_at':(datetime.now(SHANGHAI)-timedelta(seconds=max(ages))).isoformat(timespec='seconds')}
        flight=INFLIGHT.get(key)
        owner=flight is None
        if owner:
            flight={'event':threading.Event(),'error':None}
            INFLIGHT[key]=flight
    if stale and not owner:return stale
    def execute():
        try:
            result=_hourly(client,sid,identity,filters,start) if granularity=='hour' else _read(client,sid,identity,filters,start,end)
            with LOCK:
                if len(CACHE)>80:CACHE.clear()
                CACHE[key]=(time.monotonic(),result)
                flight['result']=result
            return result
        except Exception as error:
            flight['error']=error
            raise
        finally:
            with LOCK:
                INFLIGHT.pop(key,None)
                flight['event'].set()
    if stale:
        def update():
            try:execute()
            except Exception:pass  # Keep the dated snapshot; retry on next read.
        threading.Thread(target=update,daemon=True).start()
        return stale
    if not owner:
        if not flight['event'].wait(40):raise UsageError('统计读取超时，请稍后重试。',504,'TREND_TIMEOUT')
        if flight['error'] is not None:raise flight['error']
        if client.status().get('connected_at')!=identity:
            raise UsageError('连接已变化，请重新读取趋势。',409,'CONNECT_REQUIRED')
        return flight['result']
    return execute()


def _hourly(client,sid,identity,filters,day):
    # One complete range read, shared with the records page, not 24 API calls.
    q={k:v for k,v in filters.items() if k!='granularity'}
    q.update(page=1,page_size=100,start_time='00:00',end_time='23:59')
    rows=client.range_rows(q)[1]
    by_hour={}
    for row in rows:
        if filters.get('model') and row['model']!=filters['model']:continue
        if filters.get('group_id') and str(row['group_id'])!=filters['group_id']:continue
        try:
            stamp=datetime.fromisoformat(str(row['created_at']).replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError()
            stamp=stamp.astimezone(SHANGHAI)
        except (ValueError,TypeError):raise UsageError('记录缺少有效时区时间，无法生成小时趋势。') from None
        if stamp.date()==day:by_hour.setdefault(stamp.hour,[]).append(row)
    now=datetime.now(SHANGHAI);points=[]
    fields=('requests','input_tokens','output_tokens','cache_creation_tokens','cache_read_tokens','input_total','total_tokens','actual_cost','cache_rate')
    for hour in range(24):
        stamp=datetime.combine(day,datetime.min.time()).replace(tzinfo=SHANGHAI)+timedelta(hours=hour)
        if hour in by_hour:values=client.summary(by_hour[hour])
        elif stamp<=now:values={k:0 if k!='cache_rate' else None for k in fields}
        else:values={k:None for k in fields}
        points.append(dict(date=stamp.isoformat(timespec='minutes'),**values))
    if client.status().get('connected_at')!=identity:raise UsageError('连接已变化，请重新读取趋势。',409,'CONNECT_REQUIRED')
    return {'site_id':sid,'points':points,'filters':filters,'fetched_at':now.isoformat(timespec='seconds')}


def _read(client,sid,identity,filters,start,end):
    # Closed days rarely change. Reuse verified daily aggregates across 7/30-day
    # views instead of downloading all of their request logs every minute.
    now=time.monotonic();today=datetime.now(SHANGHAI).date()
    scope=(sid,identity,filters.get('model',''),filters.get('group_id',''))
    points={};missing=[];day=start
    with LOCK:
        while day<=end:
            date=str(day);saved=DAILY_CACHE.get(scope+(date,))
            ttl=21600 if day<today and saved and saved[1].get('requests') is not None else 60
            if saved and now-saved[0]<ttl:points[date]=saved[1]
            else:missing.append(day)
            day+=timedelta(days=1)
    ranges=[]
    for day in missing:
        if ranges and day==ranges[-1][1]+timedelta(days=1):ranges[-1]=(ranges[-1][0],day)
        else:ranges.append((day,day))
    def fetch(bounds):
        first,last=bounds
        f=dict(filters,start_date=str(first),end_date=str(last))
        return _read_upstream(client,sid,identity,f,first,last)
    if len(ranges)==1:results=[fetch(ranges[0])]
    elif ranges:
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(fetch,ranges))
    else:results=[]
    with LOCK:
        if len(DAILY_CACHE)>10000:DAILY_CACHE.clear()
        for result in results:
            for point in result['points']:
                points[point['date']]=point
                DAILY_CACHE[scope+(point['date'],)]=(time.monotonic(),point)
        if results:_save_days(scope)
    if client.status().get('connected_at')!=identity:
        raise UsageError('连接已变化，请重新读取趋势。',409,'CONNECT_REQUIRED')
    return {'site_id':sid,'points':[points[d] for d in sorted(points)],'filters':filters,'fetched_at':datetime.now(SHANGHAI).isoformat(timespec='seconds')}


def _read_upstream(client,sid,identity,filters,start,end):
    raw=client.api('/api/v1/usage/dashboard/trend?'+urlencode(filters))
    if not isinstance(raw,dict) or not isinstance(raw.get('trend'),list):
        raise UsageError('该站暂未提供兼容的逐天趋势统计。',502,'TREND_UNAVAILABLE')
    by_date={}
    for item in raw['trend']:
        if not isinstance(item,dict):continue
        date=str(item.get('date',''))[:10]
        if not str(start)<=date<=str(end):continue
        if date in by_date:raise UsageError('站点逐天统计存在重复日期，暂不展示。',502,'TREND_INVALID')
        row={'date':date}
        for k in ('requests','input_tokens','output_tokens','cache_creation_tokens','cache_read_tokens','total_tokens','actual_cost'):
            row[k]=number(item.get(k))
        vals=[row[k] for k in ('input_tokens','cache_creation_tokens','cache_read_tokens')]
        inputs=number(item.get('input_total')) if 'input_total' in item else sum(vals) if all(v is not None for v in vals) else None
        row['input_total']=inputs
        row['cache_rate']=100*row['cache_read_tokens']/inputs if inputs and row['cache_read_tokens'] is not None and 0<=row['cache_read_tokens']<=inputs else None
        by_date[date]=row
    points=[];day=start
    while day<=end:
        date=str(day)
        points.append(by_date.get(date,{'date':date,**{k:None for k in ('requests','total_tokens','actual_cost','cache_rate')}}))
        day+=timedelta(days=1)
    if client.status().get('connected_at')!=identity:
        raise UsageError('连接已变化，请重新读取趋势。',409,'CONNECT_REQUIRED')
    return {'site_id':sid,'points':points,'filters':filters,'fetched_at':datetime.now(SHANGHAI).isoformat(timespec='seconds')}
