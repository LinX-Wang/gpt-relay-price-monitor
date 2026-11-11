"""Reproducible screenshots with public station identities and fictional account data."""
import argparse,hashlib,json,os,secrets,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.parse import parse_qs,urlsplit
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--chrome');parser.add_argument('--usage-only',action='store_true');args=parser.parse_args()
    out=ROOT/'docs/screenshots';out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='linx-demo-') as state:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}';password=secrets.token_urlsafe(20);salt=secrets.token_hex(16)
        env={**os.environ,'APP_DATA_DIR':state,'APP_HOST':'127.0.0.1','APP_PORT':str(port),'APP_PUBLIC_URL':base,'APP_AUTH_REQUIRED':'0'}
        subprocess.run([sys.executable,str(ROOT/'scripts/seed_demo.py')],env=env,cwd=ROOT,check=True)
        env.update(APP_AUTH_REQUIRED='1',APP_USERNAME='demo-user',APP_PASSWORD_HASH='pbkdf2_sha256$'+salt+'$'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex(),APP_SESSION_SECRET=secrets.token_hex(32))
        log=tempfile.TemporaryFile();server=subprocess.Popen([sys.executable,str(ROOT/'app.py')],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/login',timeout=1);break
                except OSError:time.sleep(.1)
            with sync_playwright() as p:
                executable=args.chrome or ('C:/Program Files/Google/Chrome/Application/chrome.exe' if os.name=='nt' else None)
                browser=p.chromium.launch(headless=True,executable_path=executable)
                context=browser.new_context(viewport={'width':1680,'height':1080},device_scale_factor=1)
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                assert context.request.get(base+'/api/sites').status==401
                page.goto(base+'/login');page.evaluate('document.fonts.ready')
                if not args.usage_only:page.screenshot(path=str(out/'login.png'))
                page.locator('#username').fill('demo-user');page.locator('#password').fill(password);page.locator('#submit').click();page.wait_for_url(base+'/')
                page.wait_for_function("document.querySelectorAll('#tbody tr').length>=6")
                assert len(context.request.get(base+'/api/sites').json()['sites'])==6
                assert len(context.request.get(base+'/api/purity').json()['history'])==30
                assert len(context.request.get(base+'/api/purity/groups').json()['groups'])==5
                def shot(name,theme):
                    if args.usage_only and name not in ('usage-dark.png','usage-picker-dark.png'):return
                    page.evaluate("t=>{localStorage.setItem('ai-price-monitor:theme-mode',t);document.documentElement.dataset.theme=t;document.documentElement.classList.remove('theme-transitioning','theme-transitioning-active')}",theme)
                    page.evaluate('document.fonts.ready');page.screenshot(path=str(out/name),animations='disabled')
                shot('editor-light.png','light');shot('editor-dark.png','dark')
                page.goto(base+'/reports/latest.html');page.wait_for_function("document.body.innerText.includes('星河示例站')");shot('report-dark.png','dark')
                page.goto(base+'/purity.html');page.wait_for_function("document.querySelectorAll('#sites button').length>0 || document.body.innerText.includes('星河示例站')");shot('purity-light.png','light')
                page.locator('.trend-card').scroll_into_view_if_needed();shot('purity-history-light.png','light')
                page.goto(base+'/calculator.html');shot('calculator-light.png','light')
                now=datetime.now(timezone(timedelta(hours=8)));rows=[]
                for i in range(32):
                    cached=[198000,144000,99600,86000,186000,62000][i%6];fresh=[1200,6000,400,14000,14000,38000][i%6];output=1800+i*37
                    rows.append({'id':i+1,'created_at':(now-timedelta(minutes=i*3)).isoformat(),'model':'gpt-6.1-sol','reasoning_effort':['high','xhigh','medium'][i%3],
                        'group_name':['标准分组','经济分组'][i%2],'group_id':1,'input_tokens':fresh,'cache_read_tokens':cached,'cache_creation_tokens':0,
                        'output_tokens':output,'input_total':fresh+cached,'total_tokens':fresh+cached+output,'cache_rate':cached/(cached+fresh)*100,
                        'actual_cost':round(.0012+i*.00035,6),'first_token_ms':[1500,2600,900,14500,3100,1100][i%6],'duration_ms':9000+i*1300})
                summary={k:sum(r[k] for r in rows) for k in ['input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens','total_tokens','input_total','actual_cost']}
                summary.update(requests=len(rows),cache_rate=summary['cache_read_tokens']/summary['input_total']*100)
                sites=[{'site_id':'lulu','name':'噜噜 AI','base_url':'https://luluai.win','connected':True,'username':'demo@example.com'},
                    {'site_id':'demo-two','name':'万家灯火','base_url':'https://wjapi.vip','connected':True,'username':'demo@example.com'}]
                def cache_route(route):
                    path=urlsplit(route.request.url).path
                    if path.endswith('/icon'):
                        site_id=parse_qs(urlsplit(route.request.url).query).get('site_id',['lulu'])[0]
                        icon='wanjia.png' if site_id=='demo-two' else 'lulu.png'
                        route.fulfill(content_type='image/png',body=(ROOT/'docs/screenshot-assets'/icon).read_bytes());return
                    if path.endswith('/sites'):value={'sites':sites}
                    elif path.endswith('/status'):value=sites[0]
                    elif path.endswith('/snapshot'):value={**sites[0],'rows':rows,'page':1,'page_size':100,'total':32,'warnings':[],
                        'fetched_at':now.isoformat(),'groups':[{'id':1,'name':'标准分组'},{'id':2,'name':'经济分组'}],
                        'page_summary':summary,'period_summary':summary,'balance':128.6}
                    else:value={}
                    route.fulfill(json=value)
                page.route('**/api/cache/**',cache_route)
                page.goto(base+'/cache.html');page.wait_for_function("document.querySelectorAll('#rows tr').length===32")
                page.wait_for_function("document.querySelector('#siteIcon').naturalWidth>0 && document.querySelector('#sitePickerButton img').naturalWidth>0")
                shot('usage-dark.png','dark')
                page.locator('#sitePickerButton').click()
                page.wait_for_function("[...document.querySelectorAll('#sitePickerMenu img')].every(i=>i.complete && i.naturalWidth>0)")
                assert page.locator('#sitePickerMenu').inner_text().count('万家灯火')==1
                shot('usage-picker-dark.png','dark')
                page.keyboard.press('Escape');assert not page.locator('#sitePickerMenu').is_visible()
                page.set_viewport_size({'width':390,'height':844});page.locator('#sitePickerButton').click()
                box=page.locator('#sitePickerMenu').bounding_box();assert box and box['x']>=0 and box['x']+box['width']<=390
                assert not errors,errors
                browser.close()
                print('PASS: login guard, authenticated APIs, seeded history/groups, all page scripts, real station icons, picker Escape and mobile bounds; '+('2' if args.usage_only else '9')+' screenshots generated.')
        finally:
            server.terminate();server.wait(timeout=15);log.close()
if __name__=='__main__':main()
