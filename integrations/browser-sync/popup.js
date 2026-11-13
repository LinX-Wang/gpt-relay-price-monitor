'use strict';
const $=id=>document.getElementById(id);let config,sites=[];
const DEFAULT_ORIGIN='https://linxprice.bot.cd';
function monitorOrigin(value){
  const u=new URL(value);
  if(u.username||u.password||u.search||u.hash||!(['https:'].includes(u.protocol)||(u.protocol==='http:'&&['localhost','127.0.0.1'].includes(u.hostname))))throw Error('请输入自己的 HTTPS 监测网站地址');
  return u.origin;
}
async function api(path,body){
  const response=await fetch(config.origin+'/api/cache/browser-sync/'+path,{
    method:body?'POST':'GET',credentials:'omit',redirect:'error',cache:'no-store',
    headers:{'Content-Type':'application/json',...(config.key?{Authorization:'Bearer '+config.key}:{})},
    ...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(20000)
  });
  const data=await response.json();if(!response.ok)throw Error(data.error||'无法连接监测服务器');return data;
}
async function render(){
  const state=await chrome.storage.local.get(['config','results','lastError','preferredOrigin']);config=state.config;
  $('setup').hidden=!!config?.key;$('paired').hidden=!config?.key;
  if(!config?.key){$('origin').value=state.preferredOrigin||DEFAULT_ORIGIN;return}
  $('server').textContent=config.origin;$('pause').textContent=config.paused?'恢复同步':'暂停同步';
  $('message').textContent=state.lastError||'';
  $('results').replaceChildren();
  for(const item of state.results||[]){
    const node=document.createElement('div');node.className='site';
    const name=document.createElement('strong');name.textContent=item.name;
    const status=document.createElement('span');status.textContent=item.message;status.className=item.ok?'ok':'';
    node.append(name,status);
    if(item.lastSuccess){const time=document.createElement('small');time.textContent='上次同步 '+new Date(item.lastSuccess).toLocaleString();node.append(time)}
    $('results').append(node);
  }
  try{sites=(await api('sites')).sites}catch(error){$('message').textContent=error.message}
}
$('pair').onclick=async()=>{
  $('pair').disabled=true;
  try{
    const origin=monitorOrigin($('origin').value.trim());
    const code=$('code').value.trim();if(!code)throw Error('请先输入配对码');
    if(!await chrome.permissions.request({origins:[origin+'/*']}))throw Error('需要访问自己的监测服务器才能同步');
    await chrome.storage.local.set({preferredOrigin:origin});
    config={origin};const data=await api('pair',{code,name:'Chrome / Edge 浏览器'});
    await chrome.storage.local.set({config:{origin,key:data.device_key,id:data.device_id,paused:false},fingerprints:{},results:[],lastError:'',lastSuccess:{}});
    await chrome.runtime.sendMessage({type:'sync'});
    $('code').value='';await render();$('message').textContent='已配对。请点击「授权站点」，允许读取已添加站点的登录状态。';
  }catch(error){$('message').textContent=error.message}finally{$('pair').disabled=false}
};
$('grant').onclick=async()=>{
  try{
    const origins=sites.map(s=>new URL(s.base_url).origin+'/*');
    if(!origins.length)throw Error('还没有可授权站点');
    if(!await chrome.permissions.request({origins}))throw Error('尚未授权，将继续保留手动连接方式');
    $('message').textContent='已授权，正在检查…';await chrome.runtime.sendMessage({type:'sync'});await render();
  }catch(error){$('message').textContent=error.message}
};
$('sync').onclick=async()=>{
  $('sync').disabled=true;try{await chrome.runtime.sendMessage({type:'sync'});await render()}catch{$('message').textContent='检查未完成，请重新打开扩展'}finally{$('sync').disabled=false}
};
$('pause').onclick=async()=>{
  config.paused=!config.paused;await chrome.storage.local.set({config});
  if(!config.paused)await chrome.runtime.sendMessage({type:'sync'});await render();
};
$('reset').onclick=async()=>{
  if(!confirm('解除本机配对？服务器用量连接会保留。'))return;
  try{await api('unpair',{})}catch{/* Local deletion must remain possible offline. */}
  const preferredOrigin=config.origin;await chrome.storage.local.clear();await chrome.storage.local.set({preferredOrigin});await chrome.alarms.clear('linx-login-sync');
  const permissions=await chrome.permissions.getAll();if(permissions.origins?.length)await chrome.permissions.remove({origins:permissions.origins});
  await chrome.scripting.unregisterContentScripts({ids:['linx-capture']});
  await chrome.action.setBadgeText({text:''});await render();
};
render();
