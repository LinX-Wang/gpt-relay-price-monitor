/* Management shares the authenticated dashboard; no relay secrets enter this page. */
(()=>{
  'use strict';
  const button=document.getElementById('browserSyncBtn');if(!button)return;
  const style=document.createElement('style');style.textContent=
  '#browserSyncBtn{margin-top:8px;border:1px solid var(--line);color:var(--sub);background:var(--surface);border-radius:10px;padding:9px 12px;font-size:12px;text-align:center}#browserSyncBtn:hover{color:var(--brand);border-color:var(--brand)}'+
  '#browserSyncModal{width:min(610px,calc(100vw - 28px));max-height:86dvh;overflow:auto;padding:24px;border:1px solid var(--line);border-radius:18px;background:var(--surface);color:var(--ink)}'+
  '#browserSyncModal::backdrop{background:rgb(0 0 0 / .4)}#browserSyncModal h2{font-size:20px;margin:0 0 8px}#browserSyncModal p,#browserSyncModal li{color:var(--sub);font-size:13px;line-height:1.8}#browserSyncModal ol{padding-left:22px}#browserSyncModal code{font-size:12px;overflow-wrap:anywhere}#browserSyncModal .sync-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0}#browserSyncModal button,#browserSyncModal a.sync-download{border:1px solid var(--line);border-radius:9px;padding:8px 13px;background:var(--bg);color:var(--ink);font:inherit;font-size:13px;text-decoration:none;cursor:pointer}#browserSyncModal .sync-download{background:var(--soft)!important;color:var(--brand)!important}#browserSyncModal input{width:100%;box-sizing:border-box;padding:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;font-size:13px}#browserSyncModal .sync-device{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 0;border-bottom:1px solid var(--line)}#browserSyncModal .sync-device small{display:block;color:var(--sub);font-size:12px}#browserSyncModal .sync-message{white-space:pre-line;overflow-wrap:anywhere}#browserSyncModal .sync-note{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:11px 13px}#browserSyncModal [hidden]{display:none!important}';document.head.append(style);
  const modal=document.createElement('dialog');modal.id='browserSyncModal';
  modal.innerHTML='<h2>自动同步登录</h2><p>官网页面保持登录，新令牌会自动同步到服务器。适用于用量页面已连接的 Sub2API 兼容站点。</p>'+
  '<ol><li><a class="sync-download" href="/api/cache/browser-sync/extension.zip">下载浏览器扩展</a>，解压到固定文件夹。</li><li>在日常使用的 Chrome / Edge 中打开 <code>chrome://extensions</code> 或 <code>edge://extensions</code>，开启开发者模式，选择「加载已解压的扩展程序」，选中解压后的 <code>linx-browser-sync</code> 文件夹。</li><li>点击下方「生成配对码」，打开扩展，填写本站地址和配对码，再点击「授权站点」。以后保持官网页面打开即可。</li></ol>'+
  '<p>本站地址：<code id="syncMonitorOrigin"></code></p><div class="sync-actions"><button id="syncPairCreate" type="button">生成配对码</button><button id="syncDeviceRefresh" type="button">刷新设备</button></div>'+
  '<div id="syncPairBox" hidden><label for="syncPairCode">一次性配对码 · 5 分钟内有效</label><input id="syncPairCode" readonly autocomplete="off" spellcheck="false"><div class="sync-actions"><button id="syncPairCopy" type="button">复制配对码</button></div></div>'+
  '<div class="sync-note"><p style="margin:0">扩展每分钟检查一次，仅读取你授权站点的登录令牌；只在令牌变化时上传。密码、模型 API Key 和刷新令牌不会上传。关闭官网、退出登录、休眠或浏览器完全退出后，无法继续获取新令牌。新增站点后，需要在扩展里授权其官网。</p></div>'+
  '<h3>已配对浏览器</h3><div id="syncDeviceList"></div><p id="syncManageMessage" class="sync-message" role="status"></p><div class="sync-actions"><button id="syncModalClose" type="button">完成</button></div>';document.body.append(modal);
  const get=id=>document.getElementById(id);get('syncMonitorOrigin').textContent=location.origin;
  async function call(path,payload){
    const r=await fetch('/api/cache/browser-sync/'+path,{method:payload?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json'},...(payload?{body:JSON.stringify(payload)}:{})});
    const d=await r.json();if(!r.ok)throw Error(d.error||'自动同步配置读取失败');return d;
  }
  let deviceRequest=false,deviceSignature='',pairingDeviceIds=null;
  async function devices(){
    if(deviceRequest)return;deviceRequest=true;
    try{
    const d=await call('status');
    if(pairingDeviceIds&&d.devices.some(device=>!pairingDeviceIds.includes(device.id))){get('syncPairCode').value='';get('syncPairBox').hidden=true;pairingDeviceIds=null;get('syncManageMessage').textContent='浏览器已配对，扩展会自动检查授权站点的登录状态。'}
    const signature=JSON.stringify(d.devices);if(signature===deviceSignature)return;deviceSignature=signature;
    const list=get('syncDeviceList');list.replaceChildren();
    if(!d.devices.length){list.textContent='暂未配对浏览器';return}
    for(const device of d.devices){
      const row=document.createElement('div');row.className='sync-device';const text=document.createElement('div');
      const name=document.createElement('strong');name.textContent=device.name;const detail=document.createElement('small');
      detail.textContent=device.last_seen?'上次连接 '+new Date(device.last_seen*1000).toLocaleString():'等待首次同步';text.append(name,detail);
      const remove=document.createElement('button');remove.type='button';remove.textContent='移除';remove.onclick=async()=>{
        remove.disabled=true;try{await call('revoke',{device_id:device.id});await devices()}catch(e){get('syncManageMessage').textContent=e.message}finally{remove.disabled=false}
      };row.append(text,remove);list.append(row);
    }
    }finally{deviceRequest=false}
  }
  button.onclick=async()=>{modal.showModal();get('syncManageMessage').textContent='';try{await devices()}catch(e){get('syncManageMessage').textContent=e.message}};
  get('syncModalClose').onclick=()=>modal.close();
  modal.addEventListener('close',()=>{get('syncPairCode').value='';get('syncPairBox').hidden=true;pairingDeviceIds=null});
  setInterval(()=>{if(modal.open&&!document.hidden)devices().catch(e=>{get('syncManageMessage').textContent=e.message})},3000);
  window.addEventListener('focus',()=>{if(modal.open)devices().catch(()=>{})});
  get('syncDeviceRefresh').onclick=async()=>{try{await devices()}catch(e){get('syncManageMessage').textContent=e.message}};
  get('syncPairCreate').onclick=async()=>{
    get('syncPairCreate').disabled=true;try{const baseline=await call('status');const d=await call('create-pairing',{});pairingDeviceIds=baseline.devices.map(device=>device.id);get('syncPairCode').value=d.code;get('syncPairBox').hidden=false;get('syncManageMessage').textContent='配对码只用于连接此监测服务器，请仅粘贴到自己的扩展中。'}catch(e){get('syncManageMessage').textContent=e.message}finally{get('syncPairCreate').disabled=false}
  };
  get('syncPairCopy').onclick=async()=>{try{await navigator.clipboard.writeText(get('syncPairCode').value);get('syncManageMessage').textContent='配对码已复制'}catch{get('syncPairCode').select();get('syncManageMessage').textContent='请复制已选中的配对码'}};
  // Recover even if an expired connection paused the ordinary snapshot refresh.
  let checking=false;
  async function recoverConnection(){
    if(checking||typeof currentSite==='undefined'||!siteList.length||switching||busy||modal.open||document.getElementById('modal').open||document.getElementById('addSiteModal').open)return;
    checking=true;const sid=currentSite;
    try{
      const previous=siteList.find(x=>x.site_id===sid);
      const r=await fetch('/api/cache/status?site_id='+encodeURIComponent(sid),{cache:'no-store'});if(!r.ok)return;const s=await r.json();
      if(currentSite!==sid||busy||switching||!s.connected||!previous||s.connected_at===previous.connected_at)return;
      clearSiteSnapshots(sid);epoch++;setConnected(s);data=null;page=1;pendingLoad=false;refreshRelativeUsageDates();capture();render();
      note('登录连接已自动更新，正在同步最新用量。');await load();
    }catch{/* Ordinary refresh handles user-visible network errors. */}finally{checking=false}
  }
  setInterval(recoverConnection,10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)recoverConnection()});window.addEventListener('online',recoverConnection);
})();
