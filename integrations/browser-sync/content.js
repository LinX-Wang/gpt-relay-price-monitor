'use strict';
// Only injected into the paired monitor origin, never into relay pages.
if(window.__linxCaptureBridgeVersion!==chrome.runtime.getManifest().version){
 window.__linxCaptureBridgeVersion=chrome.runtime.getManifest().version;
 chrome.runtime.onMessage?.addListener(message=>{if(message?.type==='linx-capture-progress'&&typeof message.request_id==='string'&&typeof message.text==='string')window.postMessage(message,location.origin)});
 window.addEventListener('message',async event=>{
  const m=event.data;
  if(event.source===window&&event.origin===location.origin&&m?.type==='linx-open-extensions'&&['chrome://extensions/','edge://extensions/'].includes(m.url)&&typeof m.id==='string'&&m.id.length<100){
   try{const result=await chrome.runtime.sendMessage({type:'open-extensions',url:m.url});window.postMessage({type:'linx-open-extensions-result',id:m.id,...result},location.origin)}catch{window.postMessage({type:'linx-open-extensions-result',id:m.id,ok:false,error:'扩展已更新，请刷新本页后重试，或复制地址到地址栏打开。'},location.origin)}return;
  }
  if(event.source!==window||event.origin!==location.origin||m?.type!=='linx-capture-request'||typeof m.request_id!=='string'||m.request_id.length>100)return;
  window.postMessage({type:'linx-capture-ack',request_id:m.request_id},location.origin);
  try{const result=await chrome.runtime.sendMessage({type:'capture',request_id:m.request_id,site_id:m.site_id,origin:m.origin});window.postMessage({type:'linx-capture-result',request_id:m.request_id,...result},location.origin)}
  catch{window.postMessage({type:'linx-capture-result',request_id:m.request_id,ok:false,error:'扩展已更新，请刷新用量页面后重试。'},location.origin)}
 });
}
