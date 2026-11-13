// ISOLATED world: forward to the extension, never to the monitor document.
(()=>{
 'use strict';if(window.__linxPanelBridge)return;window.__linxPanelBridge=true;
 window.addEventListener('message',e=>{
  if(e.source!==window||e.origin!==location.origin||e.data?.type!=='linx-panel-access'||e.data.origin!==location.origin)return;
  if(typeof e.data.authorization!=='string'||e.data.authorization.length>16020)return;
  try{chrome.runtime.sendMessage({type:'panel-access',origin:location.origin,path:e.data.path,authorization:e.data.authorization}).catch(()=>{})}catch{}
 });
 window.postMessage({type:'linx-request-panel-access'},location.origin);
})();
