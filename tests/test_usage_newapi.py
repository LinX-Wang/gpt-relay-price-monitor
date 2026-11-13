"""New API contracts use fixture profiles, logs and tokens, never live accounts."""
import unittest
import test_core


FIXTURE='''
import usage_dashboard as u,usage_newapi as n,usage_trends as t
from urllib.parse import urlsplit,parse_qs
from datetime import datetime
calls=[];identity={'id':12,'email':'fixture@example.com','quota':75000000}
records=[
 {'id':1,'type':2,'created_at':int(datetime(2026,11,10,12,30,tzinfo=u.SHANGHAI).timestamp()),'model_name':'gpt-6.1-sol','group':'default','prompt_tokens':1000,'completion_tokens':200,'quota':500,'use_time':2,'is_stream':True,'other':{'cache_tokens':900,'frt':120,'reasoning_effort':'high'}},
 {'id':2,'type':2,'created_at':int(datetime(2026,11,10,13,0,tzinfo=u.SHANGHAI).timestamp()),'model_name':'gpt-6.1-sol','group':'vip','prompt_tokens':100,'completion_tokens':50,'quota':250,'use_time':3,'other':{'usage_semantic':'anthropic','cache_tokens':900,'cache_creation_tokens':0,'frt':100}},
 {'id':3,'type':2,'created_at':int(datetime(2026,11,11,13,0,tzinfo=u.SHANGHAI).timestamp()),'model_name':'gpt-other','group':'default','prompt_tokens':20,'completion_tokens':10,'quota':10,'other':{}},
]
def raw(path,token='',payload=None):
    calls.append(path);p=urlsplit(path);q=parse_qs(p.query)
    assert payload is None,'a write endpoint was called'
    if p.path=='/api/v1/auth/me':raise u.UsageError('not found',502,'UPSTREAM_NOT_FOUND')
    if p.path=='/api/user/self':return {'success':True,'data':dict(identity)}
    if p.path=='/api/status':return {'success':True,'data':{'quota_per_unit':500000}}
    if p.path=='/api/user/self/groups':return {'success':True,'data':{'default':{'ratio':1},'vip':{'ratio':.8}}}
    assert p.path=='/api/log/self',path
    assert q['type']==['2']
    rows=[r for r in records if int(q['start_timestamp'][0])<=r['created_at']<=int(q['end_timestamp'][0])]
    if q.get('group'):rows=[r for r in rows if r['group']==q['group'][0]]
    if q.get('model_name'):rows=[r for r in rows if r['model_name']==q['model_name'][0]]
    rows.sort(key=lambda r:r['created_at'],reverse=True);page=int(q['p'][0]);size=int(q['page_size'][0])
    return {'success':True,'data':{'page':page,'page_size':size,'total':len(rows),'items':rows[(page-1)*size:page*size]}}
u.upstream_request=raw
u.connect({'access_token':'fixture-newapi-access-token'})
q={k:[v] for k,v in {'start_date':'2026-11-10','end_date':'2026-11-11','page_size':'20'}.items()}
'''


class NewAPITests(unittest.TestCase):
    run_case=test_core.CoreTests.run_case

    def test_auto_detect_shared_dashboard_groups_filters_and_units(self):
        self.run_case(FIXTURE+'''
assert u.status()['panel']=='newapi' and u.status()['connected']
r=u.snapshot(q)
assert r['total']==3 and r['balance']==150
assert len(r['groups'])==2 and r['groups'][1]['rate_multiplier']==.8
byid={v['id']:v for v in r['rows']}
assert byid[1]['input_tokens']==100 and byid[1]['input_total']==1000
assert byid[1]['cache_rate']==90 and byid[1]['total_tokens']==1200
assert byid[1]['duration_ms']==2000 and byid[1]['first_token_ms']==120
assert byid[1]['actual_cost']==.001 and byid[1]['reasoning_effort']=='high'
assert byid[2]['input_tokens']==100 and byid[2]['input_total']==1000
assert byid[2]['cache_rate']==90 and byid[2]['total_tokens']==1050
assert byid[3]['cache_read_tokens'] is None and byid[3]['cache_rate'] is None
assert byid[3]['total_tokens']==30
assert r['period_summary']['total_tokens']==2280
assert r['period_summary']['cache_rate'] is None,'unknown cache was treated as zero'
filtered=u.snapshot(dict(q,model=['gpt-6.1-sol'],group_id=[str(n.group_id('vip'))]))
assert filtered['total']==1 and filtered['rows'][0]['id']==2
minute=u.snapshot(dict(q,start_time=['12:30'],end_date=['2026-11-10'],end_time=['12:59']))
assert minute['total']==1 and minute['rows'][0]['id']==1
u.WARM_POOL.shutdown(wait=True)
''')

    def test_trends_use_same_semantics_and_do_not_double_count_cache(self):
        self.run_case(FIXTURE+'''
r=t.trend(dict(q,site_id=['lulu'],model=['gpt-6.1-sol']))
p=r['points'][0]
assert p['requests']==2 and p['total_tokens']==2250
assert p['input_total']==2000 and p['cache_rate']==90
assert abs(p['actual_cost']-.0015)<1e-9
assert r['points'][1]['total_tokens']==0 and r['points'][1]['cache_rate'] is None
''')

    def test_pagination_is_complete_deduplicated_and_shared_concurrently(self):
        self.run_case(FIXTURE+'''
from concurrent.futures import ThreadPoolExecutor
import threading,time
records[:]=[dict(records[0],id=i+1) for i in range(205)]
started=threading.Event();release=threading.Event();original=raw
def delayed(path,*a,**kw):
    if path.startswith('/api/log/self?'):
        started.set();assert release.wait(2)
    return original(path,*a,**kw)
u.upstream_request=delayed
path='/api/v1/usage?start_date=2026-11-10&end_date=2026-11-10&page=2&page_size=100'
with ThreadPoolExecutor(max_workers=2) as pool:
    first=pool.submit(u.api,path);assert started.wait(1)
    second=pool.submit(u.api,path.replace('/usage?','/usage/stats?'))
    time.sleep(.05);release.set();a=first.result();b=second.result()
assert a['total']==205 and len(a['items'])==100 and b['total_requests']==205
assert len([p for p in calls if p.startswith('/api/log/self?')])==3
u.NEWAPI_STATE.clear()
def duplicate(path,*a,**kw):
    r=original(path,*a,**kw)
    if path.startswith('/api/log/self?') and 'p=2&' in path:r['data']['items'][0]['id']=1
    return r
u.upstream_request=duplicate
try:u.api(path)
except u.UsageError as e:assert e.code=='RANGE_CHANGED'
else:raise AssertionError('duplicate pagination accepted')
''')

    def test_newapi_browser_sync_verifies_origin_account_and_revocation(self):
        self.run_case(FIXTURE+'''
import browser_sync as b
payload={'site_id':'lulu','origin':'https://luluai.win','panel':'newapi','access_token':'fixture-newapi-new-access-token'}
identity['id']=999
try:b.sync_token(payload)
except u.UsageError as e:assert e.code=='ACCOUNT_MISMATCH'
else:raise AssertionError('account switched')
identity['id']=12
assert b.sync_token(payload)['changed'] and u.load_session()['panel']=='newapi'
assert not u.load_session()['refresh_token']
assert payload['access_token'].encode() not in (u.PRIVATE/'connection.enc').read_bytes()
try:b.sync_token(dict(payload,origin='https://other.example'))
except u.UsageError as e:assert e.code=='ORIGIN_MISMATCH'
else:raise AssertionError('origin changed')
try:b.sync_token(dict(payload,access_token='sk-fixture-model-key-invalid'))
except u.UsageError as e:assert e.code=='INVALID_TOKEN'
else:raise AssertionError('model key accepted')
u.disconnect()
try:b.sync_token(payload)
except u.UsageError as e:assert e.code=='CONNECT_REQUIRED'
else:raise AssertionError('manual disconnect overridden')
''')

    def test_missing_currency_and_ambiguous_cache_are_never_fabricated(self):
        self.run_case(FIXTURE+'''
u.NEWAPI_STATE.clear();original=raw
def no_currency(path,*a,**kw):
    if path=='/api/status':return {'success':True,'data':{}}
    return original(path,*a,**kw)
u.upstream_request=no_currency
r=u.snapshot(q)
assert r['balance'] is None and r['page_summary']['actual_cost'] is None
ambiguous=n.normalize_row({'record':dict(records[0],prompt_tokens=100,other={'cache_tokens':900}),'quota_per_unit':500000})
assert ambiguous['cache_rate'] is None and ambiguous['input_total'] is None
u.WARM_POOL.shutdown(wait=True)
''')
