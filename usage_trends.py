"""Daily upstream aggregates, isolated from the records endpoint."""
from datetime import datetime, timedelta
from urllib.parse import urlencode
import threading,time
import usage_sites
from usage_dashboard import UsageError,SHANGHAI,number
CACHE={}
LOCK=threading.Lock()
INFLIGHT={}

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
    filters={'start_date':str(start),'end_date':str(end),'granularity':'day','timezone':'Asia/Shanghai'}
    if model:filters['model']=model
    if group:filters['group_id']=group
    key=(sid,identity,urlencode(filters))
    with LOCK:
        saved=CACHE.get(key)
        if saved and time.monotonic()-saved[0]<60:return saved[1]
        # A month already read by another tab can also serve a shorter range.
        for cached_key,(at,result) in reversed(list(CACHE.items())):
            f=result['filters']
            if (cached_key[:2]==key[:2] and time.monotonic()-at<60
                and f.get('model','')==model and f.get('group_id','')==group
                and f['start_date']<=str(start) and f['end_date']>=str(end)):
                return dict(result,filters=filters,points=[p for p in result['points'] if str(start)<=p['date']<=str(end)])
        flight=INFLIGHT.get(key)
        owner=flight is None
        if owner:
            flight={'event':threading.Event(),'error':None}
            INFLIGHT[key]=flight
    if not owner:
        if not flight['event'].wait(40):raise UsageError('统计读取超时，请稍后重试。',504,'TREND_TIMEOUT')
        if flight['error'] is not None:raise flight['error']
        if client.status().get('connected_at')!=identity:
            raise UsageError('连接已变化，请重新读取趋势。',409,'CONNECT_REQUIRED')
        return flight['result']
    try:
        result=_read(client,sid,identity,filters,start,end)
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


def _read(client,sid,identity,filters,start,end):
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
