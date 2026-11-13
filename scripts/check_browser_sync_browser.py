"""Check bridge scripts and expired-dashboard recovery in an isolated real browser.

Chrome extension APIs are controlled mocks; relay profiles and JWTs are fixtures.
This does not install an extension in, or read credentials from, a user profile.
"""
import argparse,base64,json,os,secrets,sys,tempfile,threading,time
from urllib.parse import urlparse,parse_qs
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
                    window.fixture={stored:{config},relay:{},allowed:false,listeners:[],uploads:0,registered:[]};
                    const event={addListener(fn){fixture.listeners.push(fn)}};
                    const api={
                        storage:{local:{async get(keys){if(typeof keys==='string')keys=[keys];return Object.fromEntries(keys.map(k=>[k,fixture.stored[k]]))},async set(v){Object.assign(fixture.stored,v)}}},
                        permissions:{async contains(){return fixture.allowed}},
                        tabs:{async create(options){fixture.openedTab=options;return {id:71,...options}},async query(){return [{id:1,url:'https://luluai.win/dashboard'}]},onUpdated:event,onActivated:event},
                        scripting:{async getRegisteredContentScripts({ids}={}){return ids?fixture.registered.filter(s=>ids.includes(s.id)):fixture.registered},async unregisterContentScripts({ids}){fixture.registered=fixture.registered.filter(s=>!ids.includes(s.id))},async registerContentScripts(s){fixture.registered.push(...s)},async executeScript({func,args,files}){
                            if(files)return [];
                            const store={getItem:k=>fixture.relay[k]||null};
                            const run=new Function('location','localStorage','sessionStorage','args','return ('+func.toString()+')(...args)');
                            return [{result:run({origin:'https://luluai.win'},store,{getItem:()=>null},args)}]
                        }},
                        action:{async setBadgeText(){},async setBadgeBackgroundColor(){}},
                        alarms:{async create(){},onAlarm:event},runtime:{onInstalled:event,onStartup:event,onMessage:{addListener(fn){fixture.runtimeListener=fn}}}
                    };Object.defineProperty(window,'chrome',{value:api,configurable:true});
                }''',{'origin':origin,'key':pair['device_key']})
                worker.add_script_tag(content=(ROOT/'integrations/browser-sync/background.js').read_text(encoding='utf-8'))
                opened=worker.evaluate('''async()=>{
                    const m={type:'open-extensions',url:'edge://extensions/'};
                    const result=await new Promise(resolve=>fixture.runtimeListener(m,{url:fixture.stored.config.origin+'/cache.html',tab:{id:99}},resolve));
                    if(!result.ok)throw Error(result.error);return fixture.openedTab;
                }''')
                assert opened['url']=='chrome://extensions/' and opened['active'],'wrong-browser internal URL was not normalized'
                worker.evaluate('t=>fixture.relay.auth_token=t',second)
                worker.evaluate('sync()')
                assert client.load_session()['access_token']==first,'permission was bypassed'
                worker.evaluate('fixture.allowed=true;sync()')
                assert client.load_session()['access_token']==second
                worker.evaluate('installCaptureBridge()')
                assert worker.evaluate('fixture.registered.find(s=>s.id==="linx-capture").matches[0]')==origin+'/*'
                assert worker.evaluate('fixture.registered.find(s=>s.id==="linx-capture").js[0]')=='content.js'
                before=len(calls);worker.evaluate('sync()');assert len(calls)==before,'duplicate token uploaded'
                assert second not in worker.evaluate('JSON.stringify(fixture.stored)'),'JWT persisted in extension storage'
                # Real dashboard management and recovery after an expired connection.
                page=context.new_page()
                snapshot={'site_id':'lulu','username':'demo@example.com','connected':True,'rows':[],'groups':[],
                          'page':1,'page_size':100,'total':0,'warnings':[],'fetched_at':'2026-11-12T12:00:00+08:00',
                          'page_summary':{},'period_summary':{},'balance':0}
                page.route('**/api/cache/snapshot?**',lambda route:route.fulfill(json=snapshot))
                trend_points=[{'date':'2026-11-'+str(6+i).zfill(2),'total_tokens':v,'cache_rate':r,'requests':12+i*5,'actual_cost':.1+i*.07,'input_tokens':100-r,'cache_read_tokens':r,'cache_creation_tokens':0} for i,(v,r) in enumerate([(2400000,82),(3800000,91),(3100000,89),(5900000,96),(4200000,93),(7800000,98),(6200000,97)])]
                trend_calls=[]
                trend_points[0].update(input_total=100,input_tokens=None,cache_creation_tokens=None)
                def trend_response(route):
                    q=parse_qs(urlparse(route.request.url).query)
                    trend_calls.append(q)
                    if q.get('granularity')==['hour']:
                        pts=[dict(trend_points[0],date=q['start_date'][0]+'T'+str(h).zfill(2)+':00+08:00',total_tokens=h*100) for h in range(24)]
                        route.fulfill(json={'points':pts,'filters':{'granularity':'hour'},'site_id':'lulu'});return
                    pts=[] if q.get('model')==['fixture-other'] else [p for p in trend_points if q['start_date'][0]<=p['date']<=q['end_date'][0]]
                    route.fulfill(json={'points':pts,'site_id':q.get('site_id',['lulu'])[0]})
                page.route('**/api/cache/trend?**',trend_response)
                page.route('**/api/cache/icon?**',lambda route:route.fulfill(content_type='image/png',body=(ROOT/'docs/screenshot-assets/lulu.png').read_bytes()))
                page.goto(origin+'/cache.html');page.wait_for_function('typeof siteList!=="undefined" && siteList.length && !busy')
                assert not page.locator('#usageTrend').is_visible()
                page.wait_for_function('document.querySelector("#utContext").textContent.includes("全部分组")')
                assert len(trend_calls)==1,'initial trend should issue one range read'
                from datetime import date
                initial=trend_calls[0]
                assert (date.fromisoformat(initial['end_date'][0])-date.fromisoformat(initial['start_date'][0])).days==6,'7-day trend fetched unnecessary history'
                page.locator('#usageTrendOpen').click()
                page.wait_for_function('document.querySelector("#utContext").textContent.includes("全部分组")')
                assert page.locator('#utSummaryRate').inner_text()!='—','known input_total was ignored'
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-light.png'))
                page.evaluate("document.documentElement.dataset.theme='dark'")
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-dark.png'))
                page.locator('#usageTrend canvas').hover()
                assert page.locator('#usageTrend .ut-tip').is_visible()
                page.locator('[data-days="1"]').click()
                page.wait_for_function('document.querySelector("#usageTrend canvas").getAttribute("aria-label").includes("24")')
                assert trend_calls[-1].get('granularity')==['hour']
                assert trend_calls[-1]['start_date']==trend_calls[-1]['end_date']
                assert '逐小时' in page.locator('.ut-foot').inner_text()
                assert page.locator('#utSummaryPeak').inner_text()=='23:00'
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-hourly.png'))
                page.locator('[data-days="7"]').click()
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
                # Calendar uses day aggregates; reports preserve missing values and comparisons.
                page.locator('#usageTrendOpen').click()
                page.locator('#uiTab-calendar').click()
                page.wait_for_selector('.ui-day')
                page.locator('#utPickerButton').click()
                page.locator('#utPickerMenu [data-value="total_tokens"]').click()
                page.locator('.ui-day[aria-label^="2026-11-11"]').click()
                assert '7.8M' in page.locator('.ui-layout .ui-card').inner_text()
                assert '98.00%' in page.locator('.ui-layout .ui-card').inner_text()
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-calendar-dark.png'))
                page.locator('.ui-actions button').click()
                page.wait_for_selector('.ui-report-grid')
                assert '+85.7%' in page.locator('.ui-report-grid').inner_text()
                assert '+5.00' in page.locator('.ui-report-grid').inner_text()
                assert len([q for q in trend_calls if q.get('granularity')!=['hour']])==2,'calendar and report triggered duplicate upstream reads'
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-report-dark.png'))
                page.locator('.ui-date').fill('2026-11-05')
                page.wait_for_function('document.querySelector(".ui-report-grid")?.textContent.includes("暂无可用统计")')
                page.locator('.ui-date').fill('2026-11-11')
                page.wait_for_function('document.querySelector(".ui-report-grid")?.textContent.includes("7.8M")')
                assert len([q for q in trend_calls if q.get('granularity')!=['hour']])==2,'changing the report day should reuse month aggregates'
                original_model=page.evaluate('applied.model')
                page.evaluate("applied.model='fixture-other'")
                page.wait_for_function('document.querySelector(".ui-report-grid")?.textContent.includes("暂无可用统计")')
                assert '7.8M' not in page.locator('#uiInsights').inner_text(),'previous model data leaked into new filter'
                page.evaluate('model=>applied.model=model',original_model)
                page.wait_for_function('document.querySelector(".ui-report-grid")?.textContent.includes("7.8M")')
                page.evaluate("document.documentElement.dataset.theme='light'")
                page.wait_for_timeout(250)
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-report-light.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#uiTab-calendar').click()
                page.wait_for_selector('.ui-day')
                assert page.locator('#uiInsights').evaluate('n=>n.scrollWidth<=n.clientWidth')
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-calendar-mobile.png'))
                page.set_viewport_size({'width':320,'height':740})
                assert page.locator('#uiInsights').evaluate('n=>n.scrollWidth<=n.clientWidth')
                page.locator('#uiTab-report').click()
                assert page.locator('#uiInsights').evaluate('n=>n.scrollWidth<=n.clientWidth')
                assert not page.locator('.ui-method').evaluate('n=>n.open')
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#uiTab-trend').click()
                page.keyboard.press('Escape')
                page.set_viewport_size({'width':1440,'height':1000})
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
                # First-time connection via selected station, content bridge and real server job.
                profile['id']=123;client.disconnect()
                await_token=jwt();worker.evaluate('t=>fixture.relay.auth_token=t',await_token)
                page.route('**/api/sites',lambda route:route.fulfill(json={'sites':[{'name':'噜噜 AI','url':'https://luluai.win'}]}))
                page.expose_function('fixtureCapture',lambda m:worker.evaluate('''m=>capture(m,{url:fixture.stored.config.origin+'/cache.html',tab:{id:99}}).catch(e=>({ok:false,error:e.message}))''',m))
                page.evaluate('window.chrome={runtime:{getManifest:()=>({version:"fixture"}),sendMessage:m=>window.fixtureCapture(m)}}')
                page.add_script_tag(content=(ROOT/'integrations/browser-sync/content.js').read_text(encoding='utf-8'))
                page.locator('#addSiteBtn').click()
                page.locator('.editor-site-option').click()
                assert page.locator('#siteToken').input_value()==''
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-capture-connect.png'))
                page.evaluate("document.documentElement.dataset.theme='dark'")
                page.wait_for_timeout(250)
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-capture-connect-dark.png'))
                page.set_viewport_size({'width':320,'height':740})
                assert page.locator('#addSiteModal').evaluate('n=>n.scrollWidth<=n.clientWidth')
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-capture-connect-mobile.png'))
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('#captureLogin').click()
                page.wait_for_function('!document.querySelector("#addSiteModal").open && !busy')
                assert client.load_session()['access_token']==await_token
                assert await_token not in page.content(),'token exposed to monitor page'
                # Lost extension result must recover from server completion,
                # rather than forcing a timeout and a manual retry.
                client.disconnect();polled_token=jwt()
                worker.evaluate('t=>fixture.relay.auth_token=t',polled_token)
                page.evaluate('''()=>{chrome.runtime.sendMessage=m=>{
                    const result=window.fixtureCapture(m);
                    return m.type==='capture'?result.then(()=>new Promise(()=>{})):result;
                }}''')
                page.locator('#addSiteBtn').click();page.locator('.editor-site-option').click()
                page.locator('#captureLogin').click()
                page.wait_for_function('!document.querySelector("#addSiteModal").open && !busy',timeout=8000)
                assert client.load_session()['access_token']==polled_token
                await_token=polled_token
                page.evaluate('chrome.runtime.sendMessage=m=>window.fixtureCapture(m)')
                # An unpermitted site must not be read, even for explicit first connect.
                worker.evaluate('fixture.allowed=false')
                job=bridge.create_capture({'site_id':'lulu'})
                denied=worker.evaluate('''m=>capture(m,{url:fixture.stored.config.origin+'/cache.html',tab:{id:99}}).catch(e=>({ok:false,error:e.message}))''',{'request_id':job['request_id'],'site_id':'lulu'})
                assert not denied['ok'] and '授权站点' in denied['error']
                assert bridge.capture_status(job['request_id'])['state']=='pending'
                worker.evaluate('fixture.allowed=true')
                client.disconnect()
                worker.evaluate('sync(true)')
                assert bridge.capture_status(job['request_id'])['state']=='done','authorization did not resume pending first connection'
                assert client.load_session()['access_token']==await_token
                # A slow relay must not block another site's result, or the popup response.
                parallel=worker.evaluate('''async()=>{
                    const original=request,originalRoster=getRoster,originalRead=readSiteLogin;
                    let releaseSlow;const gate=new Promise(resolve=>releaseSlow=resolve),started=[];
                    try{
                        getRoster=async()=>({sites:[{site_id:'fast',name:'Fast fixture',base_url:'https://fast.example',connected:true},{site_id:'slow',name:'Slow fixture',base_url:'https://slow.example',connected:true}]});
                        readSiteLogin=async origin=>({matching:1,login:{token:origin,expires:9999999999}});
                        request=async(_config,path,body)=>{started.push(body.site_id);if(body.site_id==='slow')await gate;return {changed:true}};
                        const run=sync(true);
                        await new Promise(resolve=>setTimeout(resolve,100));
                        const early=fixture.stored.results.map(x=>({...x}));
                        releaseSlow();await run;
                        return {started,early,done:fixture.stored.results};
                    }finally{releaseSlow();request=original;getRoster=originalRoster;readSiteLogin=originalRead}
                }''')
                assert parallel['started']==['fast','slow']
                assert parallel['early'][0]['ok'] and parallel['early'][1]['checking']
                assert all(item['ok'] for item in parallel['done'])
                # Exercise actual fetch/XHR observation in a real document. The
                # site owns these fixture tokens; no user browser is inspected.
                panel=context.new_page()
                panel.route('https://newapi.example/**',lambda r:r.fulfill(content_type='text/html',body='<title>Fixture</title>'))
                panel.goto('https://newapi.example/dashboard')
                panel.evaluate('window.observations=[];window.chrome={runtime:{async sendMessage(m){observations.push(m)}}}')
                panel.add_script_tag(content=(ROOT/'integrations/browser-sync/panel-bridge.js').read_text(encoding='utf-8'))
                panel.add_script_tag(content=(ROOT/'integrations/browser-sync/newapi-observer.js').read_text(encoding='utf-8'))
                panel.evaluate('''async()=>{
                    const r=await fetch('/api/user/self',{headers:{Authorization:'Bearer fixture-browser-access-token'}});
                    if(!r.ok)throw Error('fetch behavior changed');
                    await fetch('/v1/responses',{headers:{Authorization:'Bearer sk-model-must-be-ignored'}});
                    await new Promise(resolve=>{const x=new XMLHttpRequest();x.open('GET','/api/log/self');x.setRequestHeader('Authorization','Bearer fixture-xhr-access-token');x.onload=resolve;x.send()});
                }''')
                panel.wait_for_function('observations.length===2')
                observations=panel.evaluate('observations')
                assert [x['path'] for x in observations]==['/api/user/self','/api/log/self']
                assert all(x['type']=='panel-access' for x in observations)
                panel.close()
                # New API memory-only login: explicit capture reloads the selected
                # tab once, observes only its own Bearer request and uses real APIs.
                import usage_sites
                newsite={'site_id':'site-newapi-fixture','name':'New API fixture','base_url':'https://newapi.example'}
                usage_sites._save([newsite]);newclient=usage_sites.client(newsite['site_id'])
                def newapi_request(path,*_a,**_k):
                    if path=='/api/user/self':data={'id':456,'email':'newapi@example.com','quota':500000}
                    elif path=='/api/status':data={'quota_per_unit':500000}
                    elif path=='/api/user/self/groups':data={'default':{'ratio':1}}
                    else:raise AssertionError('unexpected New API endpoint: '+path)
                    return {'success':True,'data':data}
                newclient.upstream_request=newapi_request
                newtoken=jwt();newjob=bridge.create_capture({'site_id':newsite['site_id']})
                result=worker.evaluate('''async arg=>{
                    fixture.originalTabs=chrome.tabs.query;fixture.originalScript=chrome.scripting.executeScript;
                    fixture.newToken=arg.token;fixture.reloads=0;
                    chrome.tabs.query=async()=>[{id:2,url:arg.origin+'/dashboard'}];
                    chrome.scripting.executeScript=async({func,args})=>{
                        const store={getItem:()=>null};
                        const run=new Function('location','localStorage','sessionStorage','args','return ('+func.toString()+')(...args)');
                        return [{result:run({origin:arg.origin},store,{getItem:()=>null},args)}];
                    };
                    chrome.tabs.reload=async id=>{
                        if(id!==2)throw Error('wrong tab reloaded');fixture.reloads++;
                        await observePanelRequest({tabId:2,url:arg.origin+'/api/user/self',requestHeaders:[{name:'Authorization',value:'Bearer '+fixture.newToken},{name:'Cookie',value:'do-not-read-or-upload'}]});
                    };
                    return capture(arg.job,{url:fixture.stored.config.origin+'/cache.html',tab:{id:99}});
                }''',{'origin':newsite['base_url'],'token':newtoken,'job':dict(newjob)})
                assert result['ok'] and result['connection']['panel']=='newapi'
                assert worker.evaluate('fixture.reloads')==1
                assert newclient.load_session()['access_token']==newtoken
                assert newtoken not in worker.evaluate('JSON.stringify(fixture.stored)')
                assert 'do-not-read-or-upload' not in worker.evaluate('JSON.stringify(fixture.stored)')
                refreshed=jwt()
                worker.evaluate('''async arg=>{
                    await getRoster(await settings(),true);
                    await observePanelRequest({tabId:2,url:arg.origin+'/api/log/self',requestHeaders:[{name:'Authorization',value:'Bearer '+arg.token}]});
                    if(running)await running;
                }''',{'origin':newsite['base_url'],'token':refreshed})
                assert newclient.load_session()['access_token']==refreshed
                assert worker.evaluate('fixture.reloads')==1,'background sync reloaded the website'
                worker.evaluate('''async()=>{
                    const before=observedLogins.size;
                    await observePanelRequest({tabId:3,url:'https://unconfigured.example/api/user/self',requestHeaders:[{name:'Authorization',value:'Bearer '+fixture.newToken}]});
                    if(observedLogins.size!==before)throw Error('unconfigured origin read');
                    chrome.tabs.query=fixture.originalTabs;chrome.scripting.executeScript=fixture.originalScript;
                }''')
                # Popup script loads without inline script/CSP or syntax assumptions.
                popup=context.new_page();popup.goto(origin+'/_popup-fixture')
                popup.set_content((ROOT/'integrations/browser-sync/popup.html').read_text(encoding='utf-8').replace('<script src="popup.js"></script>',''))
                popup.evaluate('''()=>{window.chrome={storage:{local:{async get(){return {}}}},runtime:{},permissions:{},alarms:{},action:{}}}''')
                popup.add_script_tag(content=(ROOT/'integrations/browser-sync/popup.js').read_text(encoding='utf-8'))
                assert popup.locator('#pair').is_visible()
                assert popup.locator('#origin').input_value()=='https://linxprice.bot.cd'
                popup.evaluate("chrome.storage.local.get=async()=>({preferredOrigin:'https://custom.example'});render()")
                assert popup.locator('#origin').input_value()=='https://custom.example'
                popup.evaluate('''()=>{
                    window.popupState={config:{origin:'https://monitor.example',key:'fixture'},sites:[],results:[],checking:false};
                    chrome.storage.local.get=async()=>popupState;
                    chrome.runtime.sendMessage=async()=>({ok:true,started:true});render();
                }''')
                popup.locator('#sync').click()
                assert popup.locator('#message').inner_text().startswith('正在检查')
                popup.evaluate('popupState.checking=true;render()')
                assert popup.locator('#sync').inner_text()=='检查中…'
                popup.evaluate('popupState.checking=false;popupState.lastCheck=Date.now();render()')
                assert popup.locator('#sync').is_enabled()
                assert popup.locator('#message').inner_text().startswith('检查完成')
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#usageTrendOpen').click()
                trend_box=page.locator('#usageTrend').bounding_box()
                assert trend_box['x']>=0 and trend_box['x']+trend_box['width']<=390
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-usage-trends-mobile.png'))
                page.keyboard.press('Escape')
                page.locator('#browserSyncBtn').click()
                assert not page.locator('#browserSyncModal details').evaluate('node=>node.open')
                page.locator('#browserSyncModal summary').click()
                assert page.locator('.guide-browser-card .guide-copy-icon').count()==2
                assert page.locator('.account-status-row #connectionState').count()==1
                assert page.locator('.guide-steps li').count()==4
                assert page.locator('.guide-browser-links a').nth(0).get_attribute('href')=='chrome://extensions/'
                assert page.locator('.guide-browser-links a').nth(1).get_attribute('href')=='edge://extensions/'
                page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{async writeText(text){window.copiedGuideAddress=text}}})")
                page.locator('.guide-copy-icon').first.click()
                assert page.evaluate('window.copiedGuideAddress')=='chrome://extensions/'
                assert page.locator('.guide-copy-icon').first.evaluate('n=>n.classList.contains("copied")')
                page.evaluate('''()=>{window.chrome.runtime.sendMessage=async m=>{if(m.type!=='open-extensions')return window.fixtureCapture(m);await new Promise(resolve=>setTimeout(resolve,1200));return {ok:true}}}''')
                page.locator('.guide-browser-links a').first.click()
                page.wait_for_function('document.querySelector("#guideLinkMessage").textContent.includes("已打开")')
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-sync-guide-mobile.png'))
                assert page.locator('#browserSyncModal').evaluate('n=>n.scrollWidth<=n.clientWidth')
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('#browserSyncModal').evaluate('n=>n.scrollTop=0')
                panel=page.locator('.sync-connection-panel').bounding_box()
                guide_box=page.locator('.sync-guide').bounding_box()
                assert panel['x']+panel['width']<=guide_box['x'],'desktop guide should sit beside connection panel'
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-sync-guide-dark.png'))
                page.evaluate("document.documentElement.dataset.theme='light'")
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-sync-guide-light.png'))
                box=page.locator('#browserSyncModal').bounding_box()
                assert box['x']>=0 and box['x']+box['width']<=1440
                page.screenshot(path=str(Path(tempfile.gettempdir())/'linx-browser-sync-dialog-mobile.png'))
                assert not errors,errors
                browser.close()
                print('PASS: permitted-origin reads, actual token API and encryption, unchanged-token dedupe, account protection, management UI, popup script and expired-dashboard recovery (extension APIs mocked).')
        finally:server.shutdown();server.server_close()

if __name__=='__main__':main()
