'use strict';
const ALARM='linx-login-sync';
let running=null,rosterCache=null,rosterFlight=null,rosterOwner='';
const observedLogins=new Map(),observedOrigins=new Set(),activeCaptures=new Set();
// Observe only Authorization on known, configured panel APIs. Never read Cookies.
function accessLogin(value,panel,origin){
 if(typeof value!=='string')return null;value=value.replace(/^Bearer\s+/i,'').trim();
 if(value.startsWith('sk-')||value.length<20||value.length>16000||!/^[A-Za-z0-9_.-]+$/.test(value))return null;
 let expires=Date.now()/1000+300;
 if(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value)){
  try{const p=value.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');expires=Number(JSON.parse(atob(p.padEnd(Math.ceil(p.length/4)*4,'='))).exp)}catch{return null}
 }else if(panel!=='newapi')return null;
 return expires*1000>Date.now()+20000?{token:value,expires,origin,panel}:null;
}
async function observePanelRequest(details){
 try{const u=new URL(details.url);if(details.tabId<0||!['/api/user/self','/api/user/self/groups','/api/user/models','/api/log/self','/api/log/self/stat'].includes(u.pathname))return;
  const saved=await chrome.storage.local.get(['config','sites']);if(!saved.config?.key||saved.config.paused)return;
  if(!activeCaptures.has(u.origin)&&!saved.sites?.some(s=>{try{return new URL(s.base_url).origin===u.origin}catch{return false}}))return;
  observedOrigins.add(u.origin);
  const header=details.requestHeaders?.find(h=>h.name.toLowerCase()==='authorization');
  if(!/^Bearer\s/i.test(header?.value||''))return;const login=accessLogin(header.value,'newapi',u.origin);if(!login)return;
  const changed=observedLogins.get(u.origin)?.token!==login.token;observedLogins.set(u.origin,login);
  if(changed&&!activeCaptures.has(u.origin))sync().catch(()=>{});
 }catch{}
}
async function installPanelObserver(origin,tabs){
 const id='linx-panel-'+(await fingerprint(origin)).slice(0,16),ids=[id+'-bridge',id+'-main'];
 const registered=await chrome.scripting.getRegisteredContentScripts({ids});
 if(registered.length!==2){
  if(registered.length)await chrome.scripting.unregisterContentScripts({ids:registered.map(s=>s.id)});
  await chrome.scripting.registerContentScripts([
   {id:ids[0],matches:[origin+'/*'],js:['panel-bridge.js'],runAt:'document_start'},
   {id:ids[1],matches:[origin+'/*'],js:['newapi-observer.js'],runAt:'document_start',world:'MAIN'}
  ]);
 }
 await Promise.all(tabs.filter(t=>{try{return new URL(t.url).origin===origin}catch{return false}}).map(async t=>{
  try{await chrome.scripting.executeScript({target:{tabId:t.id},files:['panel-bridge.js']});await chrome.scripting.executeScript({target:{tabId:t.id},world:'MAIN',files:['newapi-observer.js']})}catch{}
 }));
}
async function settings(){return (await chrome.storage.local.get('config')).config}
async function request(config,path,body){
  const response=await fetch(config.origin+'/api/cache/browser-sync/'+path,{
    method:body?'POST':'GET',credentials:'omit',redirect:'error',cache:'no-store',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+config.key},
    ...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(20000)
  });
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||'服务器暂时无法访问');
  return data;
}
// Runs only inside an explicitly permitted, configured station's top-level page.
function readLogin(expectedOrigin){
  if(location.origin!==expectedOrigin)return null;
  const tokens=[];
  function candidate(value){
    if(typeof value!=='string')return;
    value=value.replace(/^Bearer\s+/i,'').trim();
    if(!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value)||value.length>16000)return;
    try{
      const raw=value.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
      const claims=JSON.parse(atob(raw.padEnd(Math.ceil(raw.length/4)*4,'=')));
      if(Number(claims.exp)*1000>Date.now()+20000)tokens.push({token:value,expires:Number(claims.exp)});
    }catch{}
  }
  for(const store of [localStorage,sessionStorage]){
    for(const key of ['auth_token','access_token','accessToken','token']){
      const value=store.getItem(key);candidate(value);
      try{candidate(JSON.parse(value))}catch{}
    }
    for(const key of ['auth','auth-storage','user','userInfo']){
      try{
        const item=JSON.parse(store.getItem(key));
        for(const obj of [item,item?.state,item?.data]){
          candidate(obj?.access_token);candidate(obj?.accessToken);candidate(obj?.token);
        }
      }catch{}
    }
  }
  tokens.sort((a,b)=>b.expires-a.expires);
  // New API keeps its current access token in memory, not these storage keys.
  const panel=localStorage.getItem('system-config-storage')?'newapi':'sub2api';
  return tokens.length?{origin:location.origin,panel,...tokens[0]}:{origin:location.origin,panel,token:null};
}
async function fingerprint(value){
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
}
async function getRoster(config,force=false){
 const owner=config.origin+'|'+config.key;
 if(rosterCache?.owner===owner&&!force&&Date.now()-rosterCache.at<30000)return rosterCache.data;
 if(rosterFlight&&rosterOwner===owner)return rosterFlight;
 rosterOwner=owner;
 const own=request(config,'sites').then(async data=>{
  rosterCache={owner,data,at:Date.now()};observedOrigins.clear();for(const s of data.sites)observedOrigins.add(new URL(s.base_url).origin);
  for(const origin of observedLogins.keys())if(!observedOrigins.has(origin))observedLogins.delete(origin);
  const scripts=await chrome.scripting.getRegisteredContentScripts();
  const stale=scripts.filter(s=>s.id.startsWith('linx-panel-')&&!s.matches.some(m=>observedOrigins.has(m.replace(/[/][*]$/,''))));
  if(stale.length)await chrome.scripting.unregisterContentScripts({ids:stale.map(s=>s.id)});
  await chrome.storage.local.set({sites:data.sites});return data;
 });rosterFlight=own;
 try{return await own}finally{if(rosterFlight===own)rosterFlight=null}
}
async function readSiteLogin(origin,tabs){
 const matching=tabs.filter(t=>{try{return new URL(t.url).origin===origin}catch{return false}});
 const found=await Promise.all(matching.map(async tab=>{try{const values=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readLogin,args:[origin]});const v=values[0]?.result;return v?.origin===origin?v:null}catch{return null}}));
 const recent=observedLogins.get(origin);if(recent&&recent.expires*1000<=Date.now()+20000)observedLogins.delete(origin);
 const login=observedLogins.get(origin)||found.filter(v=>v?.token).sort((a,b)=>b.expires-a.expires)[0];
 return {login,matching:matching.length,panel:found.some(v=>v?.panel==='newapi')?'newapi':null,tabs:matching};
}
async function acquireLogin(origin,tabs,reload=false,progress=async()=>{}){
 const result=await readSiteLogin(origin,tabs);
 if(result.login||!reload||!result.matching)return result;
 // A panel's optional config-storage key is not proof of its architecture.
 // An explicit capture must also observe memory-only logins without that key.
 await progress('正在刷新所选官网，识别其当前登录方式…');
 await chrome.tabs.reload(result.tabs[0].id);
 const deadline=Date.now()+12000;
 while(Date.now()<deadline){const v=observedLogins.get(origin);if(v&&v.expires*1000>Date.now()+20000){result.login=v;break}await new Promise(resolve=>setTimeout(resolve,100))}
 return result;
}
async function sync(force=false){
 if(running)return running;
 running=runSync(force);try{return await running}finally{running=null}
}
async function runSync(force){
  try{
    const config=await settings();if(!config?.key||config.paused)return;
    await chrome.storage.local.set({checking:true,lastError:''});
    const [roster,saved,tabs]=await Promise.all([getRoster(config,force),chrome.storage.local.get(['fingerprints','lastSuccess']),chrome.tabs.query({})]);
    const hashes=saved.fingerprints||{},successes=saved.lastSuccess||{};
    const results=roster.sites.map(site=>({site_id:site.site_id,name:site.name,message:'正在检查此站…',checking:true,at:Date.now(),lastSuccess:successes[site.site_id]||null}));
    await chrome.storage.local.set({results});
    await Promise.all(roster.sites.map(async(site,index)=>{
      const origin=new URL(site.base_url).origin;
      const result=results[index];
      let ownsCapture=false;
      try{
        if(activeCaptures.has(origin)){result.message='正在从官网获取并连接…';return}
        if(!site.connected&&!site.capture_request){result.message='待首次连接：请在用量页选中此站，点击「获取并连接」';return}
        if(!await chrome.permissions.contains({origins:[origin+'/*']})){result.message='待授权，请点击「授权站点」';return}
        await installPanelObserver(origin,tabs);
        if(site.capture_request){activeCaptures.add(origin);ownsCapture=true}
        const {login,matching,panel}=await acquireLogin(origin,tabs,!!site.capture_request);
        if(!login){result.message=matching?(panel==='newapi'?'已识别 New API，请刷新一次官网以同步当前登录，或在用量页点击「获取并连接」':'未检测到有效登录，请在官网登录'):'请保持该站官网页面打开';return}
        const hash=await fingerprint(login.token);
        if(site.capture_request||hashes[site.site_id]!==hash){
          result.message='已获取登录，正在验证连接…';await chrome.storage.local.set({results});
          const updated=await request(config,site.capture_request?'capture':'token',{site_id:site.site_id,origin,access_token:login.token,panel:login.panel||'sub2api',...(site.capture_request?{request_id:site.capture_request}:{})});
          hashes[site.site_id]=hash;successes[site.site_id]=Date.now();
          result.lastSuccess=successes[site.site_id];result.message=site.capture_request?'首次连接已完成':updated.changed?'新令牌已同步':'连接已是最新';
          if(site.capture_request){delete site.capture_request;site.connected=true}
        }else result.message='连接已是最新';
        result.ok=true;
      }catch(error){result.message=error.message||'同步未完成，将自动重试'}
      finally{if(ownsCapture)activeCaptures.delete(origin);result.checking=false;await chrome.storage.local.set({results})}
    }));
    await chrome.storage.local.set({fingerprints:hashes,lastSuccess:successes,results,lastCheck:Date.now(),lastError:''});
    await chrome.action.setBadgeText({text:results.some(x=>!x.ok)?'!':''});
    await chrome.action.setBadgeBackgroundColor({color:'#997024'});
  }catch(error){
    await chrome.storage.local.set({lastError:error.message||'无法连接监测服务器',lastCheck:Date.now()});
    await chrome.action.setBadgeText({text:'!'});
  }finally{await chrome.storage.local.set({checking:false})}
}
async function installCaptureBridge(){
 const config=await settings();if(!config?.key)return;
 if(!await chrome.permissions.contains({origins:[config.origin+'/*']}))return;
 const scripts=await chrome.scripting.getRegisteredContentScripts({ids:['linx-capture']});
 if(scripts.length&&scripts[0].matches[0]!==config.origin+'/*')await chrome.scripting.unregisterContentScripts({ids:['linx-capture']});
 if(!scripts.length||scripts[0].matches[0]!==config.origin+'/*')await chrome.scripting.registerContentScripts([{id:'linx-capture',matches:[config.origin+'/*'],js:['content.js'],runAt:'document_idle'}]);
 const tabs=await chrome.tabs.query({});for(const tab of tabs){try{if(new URL(tab.url).origin===config.origin)await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']})}catch{}}
}
async function capture(message,sender){
 const config=await settings();if(!config?.key||!sender.tab||new URL(sender.url).origin!==config.origin)throw Error('请先在当前浏览器配对此监测网站');
 const progress=async text=>{try{await chrome.tabs.sendMessage(sender.tab.id,{type:'linx-capture-progress',request_id:message.request_id,text})}catch{}};
 let site;
 if(typeof message.origin==='string'){const u=new URL(message.origin);if(u.protocol!=='https:'||u.origin!==message.origin||u.username||u.password)throw Error('官网地址无效');site={site_id:message.site_id,base_url:message.origin}}
 else{site=(await getRoster(config)).sites.find(s=>s.site_id===message.site_id);if(!site)site=(await getRoster(config,true)).sites.find(s=>s.site_id===message.site_id)}
 if(!site)throw Error('所选站点不存在，请重新选择');
 const origin=new URL(site.base_url).origin;
 await progress('正在读取已打开官网的登录状态…');
 if(!await chrome.permissions.contains({origins:[origin+'/*']}))throw Object.assign(Error('请打开 LinX 登录同步扩展点击「授权站点」，授权后会自动继续连接'),{code:'PERMISSION_REQUIRED'});
 observedOrigins.add(origin);
 if(activeCaptures.has(origin))throw Error('该站正在连接，请稍候查看结果');
 activeCaptures.add(origin);
 try{
 const tabs=await chrome.tabs.query({});await installPanelObserver(origin,tabs);
 const {login,matching,panel}=await acquireLogin(origin,tabs,true,progress);
 // Explicit one-click capture may reload this site's tab once to let its own app
 // bootstrap auth. We never call or rotate the site's refresh endpoint ourselves.
 if(!login)throw Error(matching?'已检查所选官网，但未收到可用的访问令牌。这不代表官网已退出登录；请确认扩展获准访问该站并重新加载，Cookie 登录面板可能尚不兼容。':'请在当前浏览器打开该站官网并登录后重试');
 await progress('已获取登录，服务器正在向该站验证账户…');
 const result=await request(config,'capture',{request_id:message.request_id,site_id:site.site_id,origin,access_token:login.token,panel:login.panel||'sub2api'});
 return {ok:true,connection:result.connection};
 }finally{activeCaptures.delete(origin)}
}
async function start(){await chrome.alarms.create(ALARM,{periodInMinutes:1});await Promise.all([installCaptureBridge(),sync()])}
chrome.runtime.onInstalled.addListener(start);
chrome.runtime.onStartup.addListener(start);
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name===ALARM)sync()});
chrome.tabs.onUpdated.addListener((_id,change)=>{if(change.status==='complete')sync()});
chrome.tabs.onActivated.addListener(()=>sync());
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.type==='panel-access'){
    try{const origin=new URL(sender.url).origin;if(!sender.tab||origin!==message.origin)return;observePanelRequest({tabId:sender.tab.id,url:origin+message.path,requestHeaders:[{name:'Authorization',value:message.authorization}]}).catch(()=>{})}catch{}return;
  }
  if(message?.type==='open-extensions'){
    (async()=>{const config=await settings();if(!config?.key||!sender.tab||new URL(sender.url).origin!==config.origin||!['chrome://extensions/','edge://extensions/'].includes(message.url))throw Error('请从已配对的监测页面打开扩展管理');
     const url=/Edg[/]/.test(navigator.userAgent)?'edge://extensions/':'chrome://extensions/';
     const tab=await chrome.tabs.create({url,active:true});if(!tab?.id)throw Error('浏览器没有创建扩展管理标签页');return {ok:true,url};
    })().then(sendResponse,error=>sendResponse({ok:false,error:error.message}));return true;
  }
  if(message?.type==='capture'){capture(message,sender).then(sendResponse,error=>sendResponse({ok:false,code:error.code,error:error.message||'自动连接未完成，请重试'}));return true}
  if(message?.type==='sites'){settings().then(c=>getRoster(c,true)).then(sendResponse,error=>sendResponse({error:error.message}));return true}
  if(message?.type!=='sync')return;
  // Acknowledge immediately; popup observes per-site progress in storage.
  Promise.all([installCaptureBridge(),sync(true)]).catch(()=>{});sendResponse({ok:true,started:true});
});
