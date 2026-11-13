'use strict';
// Only injected into the paired monitor origin, never into relay pages.
if(!window.__linxCaptureBridge){
 window.__linxCaptureBridge=true;
 window.addEventListener('message',async event=>{
  const m=event.data;
  if(event.source!==window||event.origin!==location.origin||m?.type!=='linx-capture-request'||typeof m.request_id!=='string'||m.request_id.length>100)return;
  window.postMessage({type:'linx-capture-ack',request_id:m.request_id},location.origin);
  try{const result=await chrome.runtime.sendMessage({type:'capture',request_id:m.request_id,site_id:m.site_id});window.postMessage({type:'linx-capture-result',request_id:m.request_id,...result},location.origin)}
  catch{window.postMessage({type:'linx-capture-result',request_id:m.request_id,ok:false,error:'扩展已更新，请刷新用量页面后重试。'},location.origin)}
 });
}
