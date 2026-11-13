// MAIN world, only on explicitly granted station origins. No Cookie/storage reads.
(()=>{
 'use strict';
 if(window.__linxPanelObserver)return;window.__linxPanelObserver=true;
 const paths=new Set(['/api/user/self','/api/user/self/groups','/api/user/models','/api/log/self','/api/log/self/stat']);
 let last=null;
 function publish(url,authorization){
  try{const u=new URL(url,location.href);if(u.origin!==location.origin||!paths.has(u.pathname)||typeof authorization!=='string'||!/^Bearer[ ]/i.test(authorization)||authorization.length>16020)return;
   last={type:'linx-panel-access',origin:location.origin,path:u.pathname,authorization};window.postMessage(last,location.origin);
  }catch{}
 }
 // Do not consume request/response bodies or change the site's own auth lifecycle.
 const originalFetch=window.fetch;
 window.fetch=function(input,init){
  try{const url=typeof input==='string'||input instanceof URL?String(input):input.url;
   const headers=new Headers(init?.headers??(input instanceof Request?input.headers:undefined));publish(url,headers.get('Authorization'));
  }catch{}
  return Reflect.apply(originalFetch,this,arguments);
 };
 const proto=XMLHttpRequest.prototype,open=proto.open,set=proto.setRequestHeader,send=proto.send,requests=new WeakMap();
 proto.open=function(method,url){requests.set(this,{url:String(url),authorization:null});return Reflect.apply(open,this,arguments)};
 proto.setRequestHeader=function(name,value){const r=requests.get(this);if(r&&String(name).toLowerCase()==='authorization')r.authorization=String(value);return Reflect.apply(set,this,arguments)};
 proto.send=function(){const r=requests.get(this);if(r)publish(r.url,r.authorization);return Reflect.apply(send,this,arguments)};
 window.addEventListener('message',e=>{if(e.source===window&&e.origin===location.origin&&e.data?.type==='linx-request-panel-access'&&last)window.postMessage(last,location.origin)});
})();
