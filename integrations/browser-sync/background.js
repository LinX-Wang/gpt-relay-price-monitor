'use strict';
const ALARM='linx-login-sync';
let running=false;
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
  return tokens.length?{origin:location.origin,...tokens[0]}:null;
}
async function fingerprint(value){
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
}
async function sync(){
  if(running)return;running=true;
  try{
    const config=await settings();if(!config?.key||config.paused)return;
    const roster=await request(config,'sites');
    const saved=await chrome.storage.local.get(['fingerprints','lastSuccess']);
    const hashes=saved.fingerprints||{},successes=saved.lastSuccess||{};
    const tabs=await chrome.tabs.query({});const results=[];
    for(const site of roster.sites){
      const origin=new URL(site.base_url).origin;
      const result={site_id:site.site_id,name:site.name,at:Date.now(),lastSuccess:successes[site.site_id]||null};
      try{
        if(!site.connected){result.message='尚未连接 / 已手动断开';results.push(result);continue}
        if(!await chrome.permissions.contains({origins:[origin+'/*']})){result.message='待授权，请点击「授权站点」';results.push(result);continue}
        const matching=tabs.filter(t=>{try{return new URL(t.url).origin===origin}catch{return false}});
        const found=[];
        for(const tab of matching){
          try{
            const values=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readLogin,args:[origin]});
            const value=values[0]?.result;if(value?.origin===origin)found.push(value);
          }catch{/* Tab may have closed, navigated or been discarded. */}
        }
        found.sort((a,b)=>b.expires-a.expires);const login=found[0];
        if(!login){result.message=matching.length?'未检测到有效登录，请在官网登录':'请保持该站官网页面打开';results.push(result);continue}
        const hash=await fingerprint(login.token);
        if(hashes[site.site_id]!==hash){
          const updated=await request(config,'token',{site_id:site.site_id,origin,access_token:login.token});
          hashes[site.site_id]=hash;successes[site.site_id]=Date.now();
          result.lastSuccess=successes[site.site_id];result.message=updated.changed?'新令牌已同步':'连接已是最新';
        }else result.message='连接已是最新';
        result.ok=true;
      }catch(error){result.message=error.message||'同步未完成，将自动重试'}
      results.push(result);
    }
    await chrome.storage.local.set({fingerprints:hashes,lastSuccess:successes,results,lastCheck:Date.now(),lastError:''});
    await chrome.action.setBadgeText({text:results.some(x=>!x.ok)?'!':''});
    await chrome.action.setBadgeBackgroundColor({color:'#997024'});
  }catch(error){
    await chrome.storage.local.set({lastError:error.message||'无法连接监测服务器',lastCheck:Date.now()});
    await chrome.action.setBadgeText({text:'!'});
  }finally{running=false}
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
 const site=(await request(config,'sites')).sites.find(s=>s.site_id===message.site_id);if(!site)throw Error('所选站点不存在，请重新选择');
 const origin=new URL(site.base_url).origin;
 if(!await chrome.permissions.contains({origins:[origin+'/*']}))throw Error('请先打开 LinX 登录同步扩展，点击「授权站点」，再回来重试');
 const tabs=await chrome.tabs.query({}),matching=tabs.filter(t=>{try{return new URL(t.url).origin===origin}catch{return false}}),found=[];
 for(const tab of matching){try{const values=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readLogin,args:[origin]});const login=values[0]?.result;if(login?.origin===origin)found.push(login)}catch{}}
 found.sort((a,b)=>b.expires-a.expires);const login=found[0];if(!login)throw Error(matching.length?'未找到有效登录令牌，请先在该站官网登录后重试':'请在当前浏览器打开该站官网并登录后重试');
 await request(config,'capture',{request_id:message.request_id,site_id:site.site_id,origin,access_token:login.token});
 return {ok:true};
}
async function start(){await chrome.alarms.create(ALARM,{periodInMinutes:1});await installCaptureBridge();await sync()}
chrome.runtime.onInstalled.addListener(start);
chrome.runtime.onStartup.addListener(start);
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name===ALARM)sync()});
chrome.tabs.onUpdated.addListener((_id,change)=>{if(change.status==='complete')sync()});
chrome.tabs.onActivated.addListener(()=>sync());
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message?.type==='capture'){capture(message,sender).then(sendResponse,error=>sendResponse({ok:false,error:error.message||'自动连接未完成，请重试'}));return true}
  if(message?.type!=='sync')return;
  start().then(()=>sendResponse({ok:true}),()=>sendResponse({ok:false}));return true;
});
