"""Daily upstream aggregates, isolated from the records endpoint."""
from datetime import datetime, timedelta
from urllib.parse import urlencode
import threading,time
import usage_sites
from usage_dashboard import UsageError,SHANGHAI,number
CACHE={}
LOCK=threading.Lock()

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
        inputs=sum(vals) if all(v is not None for v in vals) else None
        row['cache_rate']=100*row['cache_read_tokens']/inputs if inputs and 0<=row['cache_read_tokens']<=inputs else None
        by_date[date]=row
    points=[];day=start
    while day<=end:
        date=str(day)
        points.append(by_date.get(date,{'date':date,**{k:None for k in ('requests','total_tokens','actual_cost','cache_rate')}}))
        day+=timedelta(days=1)
    if client.status().get('connected_at')!=identity:
        raise UsageError('连接已变化，请重新读取趋势。',409,'CONNECT_REQUIRED')
    result={'site_id':sid,'points':points,'filters':filters,'fetched_at':datetime.now(SHANGHAI).isoformat(timespec='seconds')}
    with LOCK:
        if len(CACHE)>80:CACHE.clear()
        CACHE[key]=(time.monotonic(),result)
    return result
