"""Fictional, opt-in data for evaluation and screenshots; never overwrites live data."""
import json,sys,sqlite3
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from settings import SITES_FILE,DATA_DIR,DB_FILE
import monitor,purity_groups as groups,purity_decisions as decisions

def seed():
    if SITES_FILE.exists() and json.loads(SITES_FILE.read_text(encoding='utf-8')):
        raise SystemExit('Existing sites found. Use an empty APP_DATA_DIR for the demo.')
    if DB_FILE.exists():
        with sqlite3.connect(DB_FILE) as c:
            if c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                raise SystemExit('Existing database found. Use an empty APP_DATA_DIR.')
    names=['星河示例站','云帆示例站','青禾示例站','远山示例站','晴川示例站','流光公益示例']
    scores=[99.8,99.2,97.6,89.2,0,None];rates=[.08,.1,.12,.15,.09,.2];sites=[]
    for i,name in enumerate(names):
        sites.append({'name':name,'url':f'https://relay-{i+1}.example','category':'公益站' if i==5 else '收费站',
            'balance':round(12.5+i*8.6,2),'balance_2':None,'welfare_rate':rates[i],
            'plus_rate':round(rates[i]+.04,2),'pro_rate':round(rates[i]+.1,2),'signup_bonus':1 if i<3 else 0,
            'daily_checkin_bonus':.2 if i%2==0 else None,'checkin_mode':'手动' if i%2==0 else '无签到',
            'notes':'虚构演示数据，不代表真实服务或检测结论。','invite_url':'',
            'purity_match':scores[i],'purity_multiplier':rates[i] if scores[i] is not None else None,
            'purity_status':'已验纯' if scores[i] is not None else '',
            'reimbursement_status':'可报销' if i<3 else '','reimbursement_notes':'演示报销信息' if i<3 else ''})
    monitor.write_sites(sites);monitor.build_reports(sites)
    import app
    app.init_purity_checks();groups.initialize();decisions.initialize()
    today=datetime.now().replace(hour=14,minute=30,second=0,microsecond=0)
    with sqlite3.connect(DB_FILE) as c:
        for i,site in enumerate(sites[:5]):
            for d in range(6):
                stamp=(today-timedelta(days=5-d)).isoformat(sep=' ',timespec='minutes')
                score=max(0,min(100,scores[i]-(5-d)*.15)) if scores[i] else 0
                c.execute('INSERT INTO purity_checks(site_name,match_percent,multiplier,checked_at,notes,model) VALUES(?,?,?,?,?,?)',
                    (site['name'],round(score,1),rates[i],stamp,'虚构演示记录','GPT-6.1-Sol'))
                groups.ensure_group(c,site['name'],'GPT-6.1-Sol',rates[i])
            decisions.insert_validated(c,decisions.validate({'site_name':site['name'],'model':'GPT-6.1-Sol',
                'scope':decisions.multiplier_scope(rates[i]),'status':decisions.auto_status(scores[i]),
                'reason':'虚构演示结论','checked_at':today.isoformat(sep=' ',timespec='minutes')}))
    monitor.build_reports(sites)
    (DATA_DIR/'DEMO_DATA.txt').write_text('Fictional examples only. Not real accounts, services, or measurements.\n',encoding='utf-8')
    print(f'Created {len(sites)} fictional sites in {SITES_FILE}.')

if __name__=='__main__':seed()
