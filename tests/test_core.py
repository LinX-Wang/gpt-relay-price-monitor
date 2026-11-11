"""Exercise portable storage, historical groups and encrypted reconnects in isolation."""
import os,subprocess,sys,tempfile,textwrap,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class CoreTests(unittest.TestCase):
    def run_case(self,source):
        with tempfile.TemporaryDirectory(prefix='linx-test-') as state:
            env={**os.environ,'APP_DATA_DIR':state,'APP_AUTH_REQUIRED':'0'}
            result=subprocess.run([sys.executable,'-c',textwrap.dedent(source)],cwd=ROOT,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
    def test_clean_start_and_storage_paths(self):
        self.run_case('''
            import app,monitor,settings,purity_groups,purity_decisions,usage_dashboard,usage_sites,usage_icons
            assert monitor.load_sites()==[]
            assert monitor.DB_FILE==settings.DB_FILE==purity_groups.DB==purity_decisions.DB
            assert usage_sites.PRIVATE==usage_dashboard.PRIVATE==settings.DATA_DIR/'usage-private'
            assert usage_icons.CACHE==settings.DATA_DIR/'usage-icons'
            monitor.build_reports([])
            assert (settings.REPORTS_DIR/'latest.html').exists()
        ''')
    def test_reprice_preserves_historical_evidence(self):
        self.run_case('''
            import app,monitor,purity_groups,sqlite3
            site={'name':'Fixture','url':'https://relay.example','category':'收费站','balance':0,'welfare_rate':.1}
            monitor.write_sites([site]);monitor.build_reports([site])
            app.save_purity_check({'site_name':'Fixture','model':'GPT-6.1-Sol','match_percent':99.8,'multiplier':.1,'conclusion_status':'auto'})
            group=purity_groups.data()['groups'][0]
            app.save_purity_group_change({'action':'reprice','site_name':'Fixture','group_id':group['id'],'version':group['version'],'new_multiplier':.08})
            group=purity_groups.data()['groups'][0]
            assert group['current_multiplier']==.08
            assert group['latest_check']['multiplier']==.1
            assert set(group['aliases'])=={.1,.08}
        ''')
    def test_connection_is_encrypted_and_invalidates_cache(self):
        self.run_case('''
            import usage_dashboard as u,secrets
            token=secrets.token_urlsafe(48)
            u.request=lambda *args,**kwargs:{'id':1,'email':'demo@example.com'}
            u.CACHE['stale']={};u.RANGES['stale']={}
            result=u.connect({'mode':'token','access_token':token})
            assert result['connected'] and not u.CACHE and not u.RANGES
            assert token.encode() not in (u.PRIVATE/'connection.enc').read_bytes()
            u.SESSION=None
            assert u.load_session()['access_token']==token
            u.disconnect();assert not (u.PRIVATE/'connection.enc').exists()
        ''')
    def test_purity_defaults_and_meow_acceptance(self):
        self.run_case('''
            import importlib.util,purity_decisions as d
            assert d.display_status(d.auto_status(99.8))=='推荐'
            assert d.display_status(d.auto_status(95))=='一般'
            assert d.display_status(d.auto_status(0))=='避雷'
            spec=importlib.util.spec_from_file_location('policy','integrations/meow/linx_policy.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
            def report(model,quality='sufficient'):
                return {'claimed_model':'gpt-6.1-sol','fingerprint':{'model':model,'quality_status':quality,'verdict':'match'}}
            assert p.classify(report('gpt-6.1-sol'))['accepted'] is True
            assert p.classify(report('gpt-6-astra'))['accepted'] is True
            assert p.classify(report('other'))['accepted'] is False
            assert p.classify(report('gpt-6-astra','insufficient'))['accepted'] is None
        ''')
if __name__=='__main__':unittest.main()
