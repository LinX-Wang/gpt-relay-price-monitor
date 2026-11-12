import unittest
import test_core
class TrendTests(unittest.TestCase):
    run_case=test_core.CoreTests.run_case
    def test_weighted_daily_cache_missing_dates_and_cache_isolation(self):
        self.run_case('''
            import usage_trends as t,usage_dashboard as u
            u.request=lambda *a,**k:{'id':1,'email':'demo@example.com'}
            u.connect({'access_token':'fixture-token-for-testing-only'})
            calls=[]
            def upstream(path):
                calls.append(path)
                return {'trend':[{'date':'2026-11-10','input_tokens':100,'cache_read_tokens':900,'cache_creation_tokens':0,'total_tokens':1020,'requests':2,'actual_cost':.1},{'date':'2026-11-12','input_tokens':0,'cache_read_tokens':0,'cache_creation_tokens':0,'total_tokens':0,'requests':0,'actual_cost':0}]}
            u.api=upstream
            q={k:[v] for k,v in {'site_id':'lulu','start_date':'2026-11-10','end_date':'2026-11-12','model':'gpt-6.1-sol','group_id':'1'}.items()}
            result=t.trend(q)
            assert len(result['points'])==3
            assert result['points'][0]['cache_rate']==90
            assert result['points'][1]['total_tokens'] is None
            assert result['points'][2]['cache_rate'] is None
            assert result['points'][2]['total_tokens']==0
            assert 'group_id=1' in calls[0] and 'model=gpt-6.1-sol' in calls[0]
            t.trend(q);assert len(calls)==1
            t.trend(dict(q,model=['other']));assert len(calls)==2
            u.save_session(dict(u.load_session(),connected_at='changed'))
            t.trend(q);assert len(calls)==3
        ''')

    def test_contained_range_and_simultaneous_read_share_upstream(self):
        self.run_case('''
            import usage_trends as t,usage_dashboard as u
            import threading,time
            from concurrent.futures import ThreadPoolExecutor
            u.request=lambda *a,**k:{'id':1,'email':'demo@example.com'}
            u.connect({'access_token':'fixture-token-for-testing-only'})
            calls=[];started=threading.Event();release=threading.Event()
            def upstream(path):
                calls.append(path);started.set();assert release.wait(3)
                return {'trend':[{'date':'2026-11-10','total_tokens':100,'requests':1}]}
            u.api=upstream
            q={k:[v] for k,v in {'site_id':'lulu','start_date':'2026-11-01','end_date':'2026-11-12','model':'gpt-6.1-sol'}.items()}
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(t.trend,q);assert started.wait(2)
                second=pool.submit(t.trend,q);time.sleep(.08);release.set()
                a=first.result();b=second.result();assert a==b and len(calls)==1
            smaller=dict(q,start_date=['2026-11-10'],end_date=['2026-11-11'])
            result=t.trend(smaller);assert len(calls)==1
            assert len(result['points'])==2 and result['points'][0]['total_tokens']==100
            assert result['filters']['start_date']=='2026-11-10'
            t.trend(dict(smaller,group_id=['2']));assert len(calls)==2
            t.trend(dict(smaller,model=['other']));assert len(calls)==3
            u.save_session(dict(u.load_session(),connected_at='changed'))
            t.trend(smaller);assert len(calls)==4
            assert not t.INFLIGHT
        ''')
