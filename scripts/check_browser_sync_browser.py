"""Check bridge scripts and expired-dashboard recovery in an isolated real browser.

Chrome extension APIs are controlled mocks; relay profiles and JWTs are fixtures.
This does not install an extension in, or read credentials from, a user profile.
"""
import argparse,base64,json,os,secrets,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--chrome',default='C:/Program Files/Google/Chrome/Application/chrome.exe');args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='linx-sync-check-') as state:
        os.environ.update(APP_DATA_DIR=state,APP_AUTH_REQUIRED='0')
        import app,browser_sync as bridge,usage_dashboard as client
        profile={'id':123,'email':'demo@example.com'};calls=[]
        def request(path,*_args,**_kwargs):calls.append(path);return dict(profile)
        client.request=request
        def jwt():
            payload=base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())+7200,'nonce':secrets.token_hex(12)}).encode()).decode().rstrip('=')
            return 'header.'+payload+'.signature'
        first=jwt();client.connect({'access_token':first})
        pair=bridge.pair({'code':bridge.create_pairing()['code'],'name':'Browser fixture'})
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        origin='http://127.0.0.1:'+str(server.server_address[1])
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=args.chrome)
                context=browser.new_context(viewport={'width':1440,'height':1000});errors=[]
                context.on('page',lambda p:p.on('pageerror',lambda e:errors.append(str(e))))
                worker=context.new_page();worker.goto(origin+'/_sync-fixture')
                second=jwt()
                worker.evaluate('''config=>{
                    window.fixture={stored:{config},relay:{},allowed:false,listeners:[],uploads:0};
                    const event={addListener(fn){fixture.listeners.push(fn)}};
                    const api={
                        storage:{local:{async get(keys){if(typeof keys==='string')keys=[keys];return Object.fromEntries(keys.map(k=>[k,fixture.stored[k]]))},async set(v){Object.assign(fixture.stored,v)}}},
                        permissions:{async contains(){return fixture.allowed}},
                        tabs:{async query(){return [{id:1,url:'https://luluai.win/dashboard'}]},onUpdated:event,onActivated:event},
                        scripting:{async executeScript({func,args}){
                            const store={getItem:k=>fixture.relay[k]||null};
                            const run=new Function('location','localStorage','sessionStorage','args','return ('+func.toString()+')(...args)');
                            return [{result:run({origin:'https://luluai.win'},store,{getItem:()=>null},args)}]
                        }},
                        action:{async setBadgeText(){},async setBadgeBackgroundColor(){}},
                        alarms:{async create(){},onAlarm:event},runtime:{onInstalled:event,onStartup:event,onMessage:event}
                    };Object.defineProperty(window,'chrome',{value:api,configurable:true});
                }''',{'origin':origin,'key':pair['device_key']})
                worker.add_script_tag(content=(ROOT/'integrations/browser-sync/background.js').read_text(encoding='utf-8'))
                worker.evaluate('t=>fixture.relay.auth_token=t',second)
                worker.evaluate('sync()')
                assert client.load_session()['access_token']==first,'permission was bypassed'
                worker.evaluate('fixture.allowed=true;sync()')
                assert client.load_session()['access_token']==second
                before=len(calls);worker.evaluate('sync()');assert len(calls)==before,'duplicate token uploaded'
                assert second not in worker.evaluate('JSON.stringify(fixture.stored)'),'JWT persisted in extension storage'
                # Real dashboard management and recovery after an expired connection.
                page=context.new_page()
                snapshot={'site_id':'lulu','username':'demo@example.com','connected':True,'rows':[],'groups':[],
                          'page':1,'page_size':100,'total':0,'warnings':[],'fetched_at':'2026-11-12T12:00:00+08:00',
                          'page_summary':{},'period_summary':{},'balance':0}
                page.route('**/api/cache/snapshot?**',lambda route:route.fulfill(json=snapshot))
                trend_points=[{'date':'2026-11-'+str(6+i).zfill(2),'total_tokens':v,'cache_rate':r,'requests':12+i*5,'actual_cost':.1+i*.07,'input_tokens':100-r,'cache_read_tokens':r,'cache_creation_tokens':0} for i,(v,r) in enumerate([(2400000,82),(3800000,91),(3100000,89),(5900000,96),(4200000,93),(7800000,98),(6200000,97)])]
                page.route('**/api/cache/trend?**',lambda route:route.fulfill(json={'points':trend_points,'site_id':'lulu'}))
                page.route('**/api/cache/icon?**',lambda route:route.fulfill(content_type='image/png',body=(ROOT/'docs/screenshot-assets/lulu.png').read_bytes()))
                page.goto(origin+'/cache.html');page.wait_for_function('typeof siteList!=="undefined" && siteList.length && !busy')
                assert not page.locator('#usageTrend').is_visible()
                page.locator('#usageTrendOpen').click()
                page.wait_for_function('document.querySelector("#utContext").textContent.includes("全部分组")')
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-light.png'))
                page.evaluate("document.documentElement.dataset.theme='dark'")
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-dark.png'))
                page.locator('#usageTrend canvas').hover()
                assert page.locator('#usageTrend .ut-tip').is_visible()
                page.locator('#utClose').click();assert not page.locator('#usageTrend').is_visible()
                page.locator('#usageTrendOpen').click();assert page.locator('#usageTrend').is_visible()
                page.locator('#utPickerButton').click()
                assert page.locator('#utPickerMenu').is_visible()
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-picker.png'))
                page.locator('#utPickerMenu [data-value="actual_cost"]').click()
                assert not page.locator('#utPickerMenu').is_visible()
                assert page.locator('#utMetricLabel').inner_text()=='实际费用'
                page.locator('#utPickerButton').focus();page.keyboard.press('ArrowDown');page.keyboard.press('ArrowDown');page.keyboard.press('Enter')
                assert page.locator('#utMetricLabel').inner_text()=='请求次数'
                page.keyboard.press('Escape');assert not page.locator('#usageTrend').is_visible()
                page.locator('#browserSyncBtn').click();page.wait_for_selector('#syncDeviceList .sync-device')
                page.locator('#syncPairCreate').click();page.wait_for_function('document.querySelector("#syncPairCode").value.length>20')
                assert len(page.locator('#syncPairCode').input_value())==32
                assert not page.locator('#syncPairBox').is_hidden()
                bridge.pair({'code':page.locator('#syncPairCode').input_value(),'name':'Newly paired fixture'})
                page.wait_for_function('document.querySelectorAll("#syncDeviceList .sync-device").length===2')
                assert page.locator('#syncPairBox').is_hidden()
                assert '浏览器已配对' in page.locator('#syncManageMessage').inner_text()
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-browser-sync-dialog.png'))
                page.locator('#syncModalClose').click();assert page.locator('#syncPairCode').input_value()==''
                page.evaluate('connected=false;reconnectNeeded=true')
                third=jwt();worker.evaluate('t=>fixture.relay.auth_token=t',third);worker.evaluate('sync()')
                page.evaluate('window.dispatchEvent(new Event("online"))')
                page.wait_for_function('connected && !reconnectNeeded && !busy')
                assert client.load_session()['access_token']==third
                # A different account can never silently replace the original.
                profile['id']=999;fourth=jwt();worker.evaluate('t=>fixture.relay.auth_token=t',fourth);worker.evaluate('sync()')
                assert client.load_session()['access_token']==third
                assert '另一个账户' in worker.evaluate('fixture.stored.results[0].message')
                # Popup script loads without inline script/CSP or syntax assumptions.
                popup=context.new_page();popup.goto(origin+'/_popup-fixture')
                popup.set_content((ROOT/'integrations/browser-sync/popup.html').read_text(encoding='utf-8').replace('<script src="popup.js"></script>',''))
                popup.evaluate('''()=>{window.chrome={storage:{local:{async get(){return {}}}},runtime:{},permissions:{},alarms:{},action:{}}}''')
                popup.add_script_tag(content=(ROOT/'integrations/browser-sync/popup.js').read_text(encoding='utf-8'))
                assert popup.locator('#pair').is_visible()
                assert popup.locator('#origin').input_value()=='https://linxprice.bot.cd'
                popup.evaluate("chrome.storage.local.get=async()=>({preferredOrigin:'https://custom.example'});render()")
                assert popup.locator('#origin').input_value()=='https://custom.example'
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#usageTrendOpen').click()
                trend_box=page.locator('#usageTrend').bounding_box()
                assert trend_box['x']>=0 and trend_box['x']+trend_box['width']<=390
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-mobile.png'))
                page.keyboard.press('Escape')
                page.locator('#browserSyncBtn').click()
                assert not page.locator('#browserSyncModal details').evaluate('node=>node.open')
                box=page.locator('#browserSyncModal').bounding_box()
                assert box['x']>=0 and box['x']+box['width']<=390
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-browser-sync-dialog-mobile.png'))
                assert not errors,errors
                browser.close()
                print('PASS: permitted-origin reads, actual token API and encryption, unchanged-token dedupe, account protection, management UI, popup script and expired-dashboard recovery (extension APIs mocked).')
        finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
