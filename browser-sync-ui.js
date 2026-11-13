/* Management shares the authenticated dashboard; no relay secrets enter this page. */
(()=>{
  'use strict';
  const button=document.getElementById('browserSyncBtn');if(!button)return;
  const style=document.createElement('style');style.textContent=
  '#browserSyncBtn{margin-top:8px;border:1px solid var(--line);color:var(--sub);background:var(--surface);border-radius:10px;padding:9px 12px;font-size:12px;text-align:center}#browserSyncBtn:hover{color:var(--brand);border-color:var(--brand)}'+
  '#browserSyncModal{width:min(610px,calc(100vw - 28px));max-height:86dvh;overflow:auto;padding:24px;border:1px solid var(--line);border-radius:18px;background:var(--surface);color:var(--ink)}'+
  '#browserSyncModal::backdrop{background:rgb(0 0 0 / .4)}#browserSyncModal h2{font-size:20px;margin:0 0 8px}#browserSyncModal p,#browserSyncModal li{color:var(--sub);font-size:13px;line-height:1.8}#browserSyncModal ol{padding-left:22px}#browserSyncModal code{font-size:12px;overflow-wrap:anywhere}#browserSyncModal .sync-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0}#browserSyncModal button,#browserSyncModal a.sync-download{border:1px solid var(--line);border-radius:9px;padding:8px 13px;background:var(--bg);color:var(--ink);font:inherit;font-size:13px;text-decoration:none;cursor:pointer}#browserSyncModal .sync-download{background:var(--soft)!important;color:var(--brand)!important}#browserSyncModal input{width:100%;box-sizing:border-box;padding:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;font-size:13px}#browserSyncModal .sync-device{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 0;border-bottom:1px solid var(--line)}#browserSyncModal .sync-device small{display:block;color:var(--sub);font-size:12px}#browserSyncModal .sync-message{white-space:pre-line;overflow-wrap:anywhere}#browserSyncModal .sync-note{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:11px 13px}#browserSyncModal [hidden]{display:none!important}';document.head.append(style);
  const modal=document.createElement('dialog');modal.id='browserSyncModal';
  // Compact control surface; installation details remain available on demand.
  style.textContent+='#browserSyncModal{padding:0;width:min(560px,calc(100vw - 24px));border-radius:20px}#browserSyncModal .sync-head{display:flex;gap:12px;align-items:center;padding:24px 24px 18px;border-bottom:1px solid var(--line)}#browserSyncModal .sync-mark{width:42px;height:42px;padding:8px;border-radius:12px;background:var(--soft);box-sizing:border-box}#browserSyncModal .sync-head h2{margin:0;font-size:19px}#browserSyncModal .sync-head p{margin:3px 0 0;font-size:12px}#browserSyncModal .sync-body{padding:20px 24px}#browserSyncModal .sync-endpoint{display:flex;justify-content:space-between;gap:12px;align-items:center;color:var(--sub);font-size:12px;padding:10px 12px;background:var(--bg);border-radius:9px}#browserSyncModal .sync-endpoint code{font-family:inherit;color:var(--ink)}#browserSyncModal .sync-actions{margin:16px 0;gap:8px}#browserSyncModal button.sync-primary{background:var(--brand);color:var(--surface);border-color:var(--brand);font-weight:650}#browserSyncModal .sync-section-title{display:flex;justify-content:space-between;align-items:center;margin:22px 0 10px}#browserSyncModal h3{font-size:13px;margin:0}#browserSyncModal .sync-count{font-size:11px;color:var(--brand);background:var(--soft);border-radius:20px;padding:2px 8px}#browserSyncModal .sync-device{padding:13px;border:1px solid var(--line);border-radius:11px;margin:8px 0;background:var(--bg)}#browserSyncModal .sync-device strong{font-size:13px}#browserSyncModal .sync-device small{margin-top:3px;font-size:11px}#browserSyncModal .sync-device button{background:transparent;color:var(--sub);font-size:12px;padding:5px 9px}#browserSyncModal .sync-empty{padding:20px 12px;text-align:center;color:var(--sub);font-size:13px;background:var(--bg);border-radius:10px}#browserSyncModal details{border-top:1px solid var(--line);margin-top:18px;padding-top:14px}#browserSyncModal summary{cursor:pointer;font-size:12px;color:var(--sub)}#browserSyncModal details li{margin:8px 0;font-size:12px}#browserSyncModal .sync-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 24px;border-top:1px solid var(--line);background:var(--bg)}#browserSyncModal .sync-foot span{font-size:11px;color:var(--sub)}#browserSyncModal .sync-message{font-size:12px;margin:12px 0 0}#browserSyncModal .sync-message:empty{display:none}#browserSyncModal #syncPairBox{border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--bg)}#browserSyncModal #syncPairBox label{font-size:12px;color:var(--sub)}#browserSyncModal #syncPairBox input{margin-top:8px;background:var(--surface);font-family:monospace}@media(max-width:450px){#browserSyncModal .sync-head,#browserSyncModal .sync-body{padding:18px}#browserSyncModal .sync-endpoint{align-items:flex-start;flex-direction:column;gap:4px}#browserSyncModal .sync-foot{padding:12px 18px}}';
  modal.innerHTML='<div class="sync-head"><img class="sync-mark" src="/assets/linx-mark.svg" alt=""><div><h2>自动同步登录</h2><p>官网保持登录，连接自动更新。</p></div></div><div class="sync-body">'+
    '<div class="sync-endpoint"><span>监测服务器</span><code id="syncMonitorOrigin"></code></div>'+
    '<div class="sync-actions"><button class="sync-primary" id="syncPairCreate" type="button">＋ 配对浏览器</button><a class="sync-download" href="/api/cache/browser-sync/extension.zip">↓ 下载扩展</a><button id="syncDeviceRefresh" type="button">刷新状态</button></div>'+
    '<div id="syncPairBox" hidden><label for="syncPairCode">一次性配对码 · 5 分钟有效</label><input id="syncPairCode" readonly autocomplete="off" spellcheck="false"><div class="sync-actions"><button id="syncPairCopy" type="button">复制配对码</button></div></div>'+
    '<div class="sync-section-title"><h3>已连接浏览器</h3><span class="sync-count" id="syncDeviceCount">检查中</span></div><div id="syncDeviceList"></div><p id="syncManageMessage" class="sync-message" role="status"></p>'+
    '<details><summary>首次安装与使用说明</summary><ol><li>下载扩展，解压到固定文件夹。</li><li>打开 <code>chrome://extensions</code> 或 <code>edge://extensions</code>，开启开发者模式，点击「加载已解压的扩展程序」，选择解压后的 linx-browser-sync 文件夹。</li><li>点击「配对浏览器」，把配对码粘贴到扩展中。监测地址已预填，也可以修改。</li><li>点击扩展里的「授权站点」，保持官网页面打开并登录即可。</li></ol><p>每分钟检查一次，只在登录令牌变化时同步。新增站点需在扩展授权；退出登录后请先在官网重新登录。密码、模型 API Key 和刷新令牌不会上传。</p></details></div>'+
    '<div class="sync-foot"><span>令牌加密保存 · 仅同步已授权站点</span><button id="syncModalClose" type="button">完成</button></footer>';
  style.textContent+='#browserSyncModal{width:min(520px,calc(100vw - 28px));border-radius:22px}#browserSyncModal .sync-head{padding:26px 28px 0;border:0;align-items:flex-start}#browserSyncModal .sync-mark{width:34px;height:34px;padding:5px;border-radius:9px;background:var(--bg)}#browserSyncModal .sync-head h2{font-size:20px;line-height:1.6}#browserSyncModal .sync-head p{font-size:12px;margin:3px 0 0}#browserSyncModal .sync-body{padding:20px 28px 24px}#browserSyncModal .sync-endpoint{background:transparent;border-radius:0;padding:0 0 20px;justify-content:flex-start;gap:10px;border-bottom:1px solid var(--line);font-size:11px}#browserSyncModal .sync-endpoint code{color:var(--sub)}#browserSyncModal .sync-actions{margin:20px 0;gap:10px}#browserSyncModal button.sync-primary{background:var(--ink);border-color:var(--ink);color:var(--surface);height:39px;border-radius:9px;padding:0 16px}#browserSyncModal a.sync-download{background:transparent!important;color:var(--ink)!important;height:39px;display:inline-flex;align-items:center;padding:0 14px}#browserSyncModal #syncDeviceRefresh{border:0;background:transparent;color:var(--sub);padding:0 4px;font-size:11px}#browserSyncModal .sync-section-title{margin:24px 0 0}#browserSyncModal .sync-section-title h3{font-size:11px;color:var(--sub);font-weight:500}#browserSyncModal .sync-count{background:transparent;color:var(--sub);padding:0;font-size:10px}#browserSyncModal .sync-device{background:transparent;border:0;border-radius:0;margin:0;padding:18px 0;border-bottom:1px solid var(--line);gap:12px}#browserSyncModal .sync-device strong{font-size:13px;font-weight:600}#browserSyncModal .sync-device small{margin-top:5px;font-size:11px}#browserSyncModal .sync-device button{border:0;font-size:11px;padding:0;color:var(--sub);margin-left:auto}#browserSyncModal .sync-device-icon{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;background:var(--bg);color:var(--sub);flex:none}#browserSyncModal .sync-device-icon svg{width:19px;height:19px;stroke:currentColor;fill:none;stroke-width:1.5}#browserSyncModal details{border:0;margin:22px 0 0;padding:0}#browserSyncModal summary{font-size:11px}#browserSyncModal .sync-foot{padding:15px 28px;background:transparent}#browserSyncModal .sync-foot span{font-size:10px}#browserSyncModal .sync-foot button{border:0;background:var(--soft);color:var(--brand);font-size:12px;padding:0 16px}#browserSyncModal .sync-message{font-size:11px}#browserSyncModal::backdrop{backdrop-filter:blur(4px);background:rgb(8 18 27 / .48)}@media(max-width:450px){#browserSyncModal .sync-head{padding:22px 22px 0}#browserSyncModal .sync-body{padding:18px 22px 22px}#browserSyncModal .sync-foot{padding:14px 22px}#browserSyncModal .sync-endpoint{flex-direction:row;flex-wrap:wrap}}';
  modal.querySelector('.sync-head p').textContent='官网保持登录，新令牌自动送达用量面板。';
  modal.querySelector('summary').textContent='第一次使用？查看安装指南';
  const guide=modal.querySelector('details');guide.className='sync-guide';guide.innerHTML='<summary><span class="guide-summary-icon">?</span><span>安装与使用指南<small>只需配置一次，之后自动同步</small></span><span class="guide-chevron">⌄</span></summary><div class="guide-content"><ol class="guide-steps"><li><span class="guide-number">01</span><div><strong>下载并解压</strong><p>将扩展解压到固定文件夹，后续更新覆盖原目录即可。</p></div></li><li><span class="guide-number">02</span><div><strong>打开浏览器扩展管理</strong><div class="guide-browser-links"><a href="chrome://extensions/" data-extension-page="chrome://extensions/">Chrome <span>chrome://extensions ↗</span></a><a href="edge://extensions/" data-extension-page="edge://extensions/">Edge <span>edge://extensions ↗</span></a></div><p>选择当前使用的浏览器，开启「开发者模式」，点击「加载已解压的扩展程序」，选中 linx-browser-sync 文件夹。</p><div id="guideLinkMessage" role="status"></div></div></li><li><span class="guide-number">03</span><div><strong>连接当前监测网站</strong><p>点击上方「连接新浏览器」，将配对码粘贴到扩展中。监测地址已预填，也可修改。</p></div></li><li><span class="guide-number">04</span><div><strong>授权站点，开始同步</strong><p>在扩展中点击「授权站点」，保持对应官网已打开并登录。新站可在添加窗口点击「获取并连接」，授权后会自动继续。</p></div></li></ol><div class="guide-note"><strong>日常使用</strong><p>每分钟检查，也会在切换标签页时检查；各站并行处理。官网退出登录时，先在官网重新登录。</p><small>仅同步授权站点的登录令牌，不上传密码、模型 API Key 或刷新令牌。</small></div></div>';
  style.textContent+='#browserSyncModal .sync-guide{margin-top:22px;border:1px solid var(--line);border-radius:13px;padding:0;background:var(--bg);overflow:hidden}#browserSyncModal .sync-guide>summary{display:flex;align-items:center;gap:10px;padding:14px;list-style:none;color:var(--ink);font-size:12px;font-weight:600}#browserSyncModal .sync-guide>summary::-webkit-details-marker{display:none}#browserSyncModal .guide-summary-icon{width:25px;height:25px;border-radius:8px;display:grid;place-items:center;background:var(--soft);color:var(--brand);font-size:13px;flex:none}#browserSyncModal .sync-guide summary small{display:block;color:var(--sub);font-size:10px;font-weight:400;margin-top:3px}#browserSyncModal .guide-chevron{margin-left:auto;color:var(--sub);transition:transform .15s}#browserSyncModal .sync-guide[open] .guide-chevron{transform:rotate(180deg)}#browserSyncModal .guide-content{padding:0 14px 14px}#browserSyncModal .guide-steps{list-style:none;padding:0;margin:0}#browserSyncModal .guide-steps li{display:flex;align-items:flex-start;gap:12px;margin:0;padding:15px 0;border-top:1px solid var(--line)}#browserSyncModal .guide-number{flex:none;font:500 11px var(--linx-number-font,monospace);color:var(--brand);padding-top:2px;opacity:.8}#browserSyncModal .guide-steps li>div{min-width:0;flex:1}#browserSyncModal .guide-steps strong{font-size:12px;font-weight:600;color:var(--ink)}#browserSyncModal .guide-steps p,#browserSyncModal .guide-note p{font-size:11px;line-height:1.9;margin:6px 0 0}#browserSyncModal .guide-browser-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:10px 0}html body#cache-dashboard #browserSyncModal .guide-browser-links a{display:flex;flex-direction:column;gap:5px;padding:10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);color:var(--ink);text-decoration:none;font-size:12px;font-weight:600;min-width:0}#browserSyncModal .guide-browser-links a span{font-size:9px;overflow-wrap:anywhere;color:var(--brand);font-family:var(--linx-number-font,monospace);font-weight:400}html body#cache-dashboard #browserSyncModal .guide-browser-links a:hover{border-color:var(--brand);background:var(--soft)}#browserSyncModal .guide-note{padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}#browserSyncModal .guide-note strong{font-size:11px;color:var(--brand)}#browserSyncModal .guide-note small{display:block;font-size:10px;color:var(--sub);line-height:1.8;margin-top:8px}#browserSyncModal #guideLinkMessage{font-size:10px;line-height:1.8;color:var(--brand);overflow-wrap:anywhere}#browserSyncModal #guideLinkMessage:empty{display:none}#browserSyncModal .guide-browser-links a:focus-visible{outline:2px solid var(--brand);outline-offset:2px}@media(max-width:380px){#browserSyncModal .guide-browser-links{grid-template-columns:1fr}}';
  guide.querySelector('.guide-note p').textContent='支持 Sub2API 与 New API。New API 首次连接可能刷新官网一次；日常同步无需手动更新令牌。官网退出登录时，先在官网重新登录。';
  guide.querySelectorAll('[data-extension-page]').forEach(link=>link.onclick=event=>{
    event.preventDefault();const url=link.dataset.extensionPage,id=crypto.randomUUID(),status=guide.querySelector('#guideLinkMessage');
    status.textContent='正在请求扩展打开管理页…';
    const timer=setTimeout(()=>{status.replaceChildren(document.createTextNode('扩展未响应，浏览器不允许网页直接打开内部设置。'));const copy=document.createElement('button');copy.type='button';copy.textContent='复制 '+url;copy.onclick=async()=>{try{await navigator.clipboard.writeText(url);copy.textContent='已复制，请粘贴到地址栏打开'}catch{status.append(document.createTextNode('请手动复制：'+url))}};status.append(copy)},4000);
    function receive(e){if(e.source!==window||e.origin!==location.origin||e.data?.type!=='linx-open-extensions-result'||e.data.id!==id)return;clearTimeout(timer);window.removeEventListener('message',receive);status.textContent=e.data.ok?'已打开扩展管理页。':e.data.error||'请将 '+url+' 粘贴到地址栏打开。'}
    window.addEventListener('message',receive);setTimeout(()=>window.removeEventListener('message',receive),20000);window.postMessage({type:'linx-open-extensions',url,id},location.origin);
  });
  const copyIcon='<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="10" rx="2"/><path d="M12 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/></svg>';
  for(const link of guide.querySelectorAll('[data-extension-page]')){const card=document.createElement('div');card.className='guide-browser-card';link.before(card);card.append(link);const b=document.createElement('button');b.type='button';b.className='guide-copy-icon';b.title='复制地址';b.setAttribute('aria-label','复制 '+link.textContent.split(' ')[0]+' 扩展管理地址');b.innerHTML=copyIcon;b.onclick=async()=>{const url=link.dataset.extensionPage,status=guide.querySelector('#guideLinkMessage');try{await navigator.clipboard.writeText(url);b.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8"/></svg>';b.classList.add('copied');b.title='已复制';status.textContent='地址已复制，可粘贴到浏览器地址栏打开。';setTimeout(()=>{b.innerHTML=copyIcon;b.classList.remove('copied');b.title='复制地址'},1800)}catch{status.textContent='请复制到地址栏：'+url}};card.append(b)}
  style.textContent+='#browserSyncModal .guide-browser-card{display:flex;align-items:center;min-width:0;border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:3px;transition:border-color .15s}#browserSyncModal .guide-browser-card:hover{border-color:var(--brand)}html body#cache-dashboard #browserSyncModal .guide-browser-card a{flex:1;border:0;background:transparent!important;padding:11px 7px 11px 9px;font-size:12px}html body#cache-dashboard #browserSyncModal .guide-copy-icon{display:grid;place-items:center;flex:none;width:28px;height:28px;padding:6px;margin-right:4px;border:0;border-radius:7px;background:transparent;color:var(--sub)}html body#cache-dashboard #browserSyncModal .guide-copy-icon:hover,html body#cache-dashboard #browserSyncModal .guide-copy-icon.copied{background:var(--soft);color:var(--brand)}#browserSyncModal .guide-copy-icon svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}#browserSyncModal .guide-copy-icon:focus-visible{outline:2px solid var(--brand);outline-offset:2px}';
  const state=document.getElementById('connectionState'),identity=document.querySelector('.account-identity');
  if(state&&identity){const row=document.createElement('div');row.className='account-status-row';const label=identity.querySelector('.account-label');identity.prepend(row);if(label)row.append(label);row.append(state)}
  style.textContent+='html body#cache-dashboard .account-status-row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:5px}html body#cache-dashboard .account-status-row .account-label{margin:0}html body#cache-dashboard .account-status-row .connection-state{margin:0;flex:none}html body#cache-dashboard .site-heading>div{flex:1;min-width:0}html body#cache-dashboard #siteName{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;overflow-wrap:anywhere;line-height:1.5}@media(max-width:650px){html body#cache-dashboard .account-status-row{float:right;margin:0 0 0 6px}html body#cache-dashboard #siteName{max-height:3em;overflow:auto!important}}';
  style.textContent+='#browserSyncModal .guide-copy-actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}#browserSyncModal .guide-copy-actions button{font-size:11px;padding:6px 10px;border-radius:8px}html body#cache-dashboard #sitePickerMenu .site-menu-option{margin:0 0 7px!important;padding:10px!important;min-height:50px;flex-shrink:0}html body#cache-dashboard #sitePickerMenu .site-menu-option:last-child{margin-bottom:0!important}html body#cache-dashboard #sitePickerMenu{padding:10px!important}';
  modal.querySelector('#syncPairCreate').textContent='＋ 连接新浏览器';
  style.textContent+='html body#cache-dashboard #browserSyncModal #syncDeviceRefresh{display:inline-flex;align-items:center;gap:6px;height:37px;padding:0 12px;border:1px solid var(--line)!important;border-radius:9px;background:var(--bg)!important;color:var(--ink)!important;font-size:12px;font-weight:500}html body#cache-dashboard #browserSyncModal .sync-device button{display:inline-flex;align-items:center;gap:5px;height:31px;padding:0 10px;border:1px solid #d6747940!important;border-radius:8px;color:#d67479!important;background:#d674790c!important;font-size:11px;flex:none}html body#cache-dashboard #browserSyncModal .sync-device button:hover{background:#d6747924!important;border-color:#d6747970!important}html body#cache-dashboard #browserSyncModal #syncModalClose{display:inline-flex;align-items:center;gap:6px;height:35px;padding:0 18px;border:1px solid var(--brand)!important;border-radius:9px;background:var(--brand)!important;color:var(--surface)!important;font-weight:650;font-size:12px}html body#cache-dashboard #browserSyncModal #syncPairCreate{background:var(--ink)!important;color:var(--surface)!important;border-color:var(--ink)!important}#browserSyncModal #syncDeviceRefresh svg,#browserSyncModal .sync-device button svg,#browserSyncModal #syncModalClose svg{width:14px;height:14px;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}#browserSyncModal button:hover{transform:none;filter:brightness(1.06)}#browserSyncModal button:focus-visible{outline:2px solid var(--brand);outline-offset:3px}#browserSyncModal .sync-foot{align-items:center}';
  modal.querySelector('#syncDeviceRefresh').innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 7a6 6 0 1 0 0 6M16 3v4h-4"/></svg><span>刷新状态</span>';
  modal.querySelector('#syncModalClose').innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8"/></svg><span>完成</span>';
  style.textContent+='html body#cache-dashboard #browserSyncModal #syncModalClose span{color:inherit!important;font-size:12px;font-weight:650}';
  const connectionPanel=document.createElement('section');connectionPanel.className='sync-connection-panel';
  const body=modal.querySelector('.sync-body');
  for(const child of [...body.children])if(child!==guide)connectionPanel.append(child);
  body.prepend(connectionPanel);
  style.textContent+=`
    #browserSyncModal{width:min(1040px,calc(100vw - 32px));max-height:90dvh;box-sizing:border-box}
    #browserSyncModal .sync-head{padding:24px 28px 0;align-items:center}
    #browserSyncModal .sync-body{display:grid;grid-template-columns:280px minmax(0,1fr);gap:26px;padding:24px 28px}
    #browserSyncModal .sync-connection-panel{min-width:0;padding-right:24px;border-right:1px solid var(--line)}
    #browserSyncModal .sync-endpoint{flex-direction:column;align-items:flex-start;gap:6px;padding-bottom:18px}
    #browserSyncModal .sync-endpoint code{overflow-wrap:anywhere}
    #browserSyncModal .sync-actions{gap:8px}
    #browserSyncModal .sync-actions #syncPairCreate{flex:1}
    #browserSyncModal .sync-actions #syncDeviceRefresh{width:100%;justify-content:center}
    #browserSyncModal .sync-device{flex-wrap:wrap;gap:10px}
    #browserSyncModal .sync-device>div{flex:1;min-width:0;overflow-wrap:anywhere}
    #browserSyncModal .sync-guide{margin:0;align-self:start}
    #browserSyncModal .guide-content{padding:0 16px 16px}
    #browserSyncModal .sync-guide>summary{padding:16px}
    #browserSyncModal .guide-steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px}
    #browserSyncModal .guide-steps li{padding:13px 0;gap:10px}
    #browserSyncModal .guide-steps li:nth-child(-n+2){grid-column:1/-1}
    #browserSyncModal .guide-steps p,#browserSyncModal .guide-note p{line-height:1.75;font-size:12px}
    #browserSyncModal .guide-note{padding:10px 12px}
    #browserSyncModal .guide-note small{margin-top:5px;line-height:1.7}
    #browserSyncModal .sync-foot{position:sticky;bottom:0;background:var(--surface);z-index:1}
    @media(max-width:860px){
      #browserSyncModal{width:min(720px,calc(100vw - 24px))}
      #browserSyncModal .sync-body{grid-template-columns:minmax(0,1fr);gap:18px}
      #browserSyncModal .sync-connection-panel{padding:0;border:0}
      #browserSyncModal .sync-endpoint{flex-direction:row;flex-wrap:wrap;align-items:center}
      #browserSyncModal .sync-actions #syncDeviceRefresh{width:auto}
      #browserSyncModal .sync-section-title{margin-top:18px}
      #browserSyncModal .sync-device{padding:12px 0}
    }
    @media(max-width:480px){
      #browserSyncModal .sync-head{padding:18px 16px 0}
      #browserSyncModal .sync-body{padding:18px 16px}
      #browserSyncModal .sync-head h2{font-size:18px}
      #browserSyncModal .guide-steps{grid-template-columns:minmax(0,1fr)}
      #browserSyncModal .guide-steps li{grid-column:1}
      #browserSyncModal .guide-content{padding:0 12px 12px}
      #browserSyncModal .sync-foot{padding:12px 16px}
      #browserSyncModal .sync-foot span{max-width:65%;line-height:1.6}
    }
  `;
  document.body.append(modal);
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
    const list=get('syncDeviceList');list.replaceChildren();get('syncDeviceCount').textContent=d.devices.length+' 台设备';
    if(!d.devices.length){const empty=document.createElement('div');empty.className='sync-empty';empty.textContent='还未连接浏览器，点击上方开始配对';list.append(empty);return}
    for(const device of d.devices){
      const row=document.createElement('div');row.className='sync-device';const text=document.createElement('div');
      const name=document.createElement('strong');name.textContent=device.name;const detail=document.createElement('small');
      detail.textContent=device.last_seen?'上次连接 '+new Date(device.last_seen*1000).toLocaleString():'等待首次同步';text.append(name,detail);
      const remove=document.createElement('button');remove.type='button';remove.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M7 5V3h6v2M5 5l1 12h8l1-12M8 8v6m4-6v6"/></svg><span>移除</span>';remove.onclick=async()=>{
        remove.disabled=true;try{await call('revoke',{device_id:device.id});await devices()}catch(e){get('syncManageMessage').textContent=e.message}finally{remove.disabled=false}
      };const icon=document.createElement('span');icon.className='sync-device-icon';icon.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/></svg>';row.append(icon,text,remove);list.append(row);
    }
    }finally{deviceRequest=false}
  }
  button.onclick=async()=>{modal.showModal();get('syncManageMessage').textContent='';try{await devices()}catch(e){get('syncManageMessage').textContent=e.message}};
  get('syncModalClose').onclick=()=>modal.close();
  modal.addEventListener('close',()=>{get('syncPairCode').value='';get('syncPairBox').hidden=true;pairingDeviceIds=null});
  setInterval(()=>{if(modal.open&&!document.hidden)devices().catch(e=>{get('syncManageMessage').textContent=e.message})},3000);
  window.addEventListener('focus',()=>{if(modal.open)devices().catch(()=>{})});
  get('syncDeviceRefresh').onclick=async()=>{const b=get('syncDeviceRefresh');b.disabled=true;try{await devices();get('syncManageMessage').textContent='设备状态已更新'}catch(e){get('syncManageMessage').textContent=e.message}finally{b.disabled=false}};
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

(()=>{
 'use strict';
 const form=document.getElementById('addSiteForm'),token=document.getElementById('siteToken');if(!form||!token)return;
 const box=document.createElement('div');box.className='capture-connect';box.innerHTML='<div class="capture-title"><span>浏览器快速连接</span><small>官网已登录？无需复制令牌</small></div><button type="button" id="captureLogin">↗ 从已打开的官网获取并连接</button><p id="captureMessage" role="status">使用已配对的 LinX 登录同步扩展，仅获取所选站点。</p>';
 const label=token.closest('label')||token;label.before(box);
 const style=document.createElement('style');style.textContent='html body#cache-dashboard .capture-connect{padding:14px;border:1px solid var(--line);background:var(--soft);border-radius:12px;margin:14px 0}.capture-title{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:11px}.capture-title span{font-size:12px;font-weight:650;color:var(--ink)}.capture-title small{font-size:10px;color:var(--sub)}html body#cache-dashboard #captureLogin{width:100%;height:38px;border:1px solid var(--brand)!important;background:var(--brand)!important;color:var(--surface)!important;border-radius:9px;font-size:12px;font-weight:650}.capture-connect p{font-size:10px;line-height:1.8;color:var(--sub);margin:9px 0 0}.capture-connect button:disabled{opacity:.65;cursor:wait}@media(max-width:450px){.capture-title{display:block}.capture-title small{display:block;margin-top:5px}}';document.head.append(style);
 const button=box.querySelector('button'),message=box.querySelector('p');let active=false;
 async function request(path,body){const r=await fetch('/api/cache/browser-sync/'+path,{method:body?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok)throw Error(d.error||'自动连接暂时不可用');return d}
 button.onclick=async()=>{
  if(active)return;active=true;button.disabled=true;const save=document.getElementById('saveSiteBtn');save.disabled=true;
  try{
   const choice=editorSiteRows.find(x=>x.key===selectedEditorSiteKey);if(!choice)throw Error('请先在上方搜索并选中站点');
   message.textContent='正在准备 '+choice.name+' 的浏览器连接…';let sid=choice.existing?.site_id;
   if(!sid){const created=await api('sites',{name:choice.name.slice(0,32),base_url:choice.origin});sid=created.site_id;choice.existing=created}
   const job=await request('create-capture',{site_id:sid});
   const result=await new Promise((resolve,reject)=>{
    let polling=false;function cleanup(){clearTimeout(missing);clearTimeout(timeout);clearInterval(poll);window.removeEventListener('message',listener)}
    const missing=setTimeout(()=>{cleanup();reject(Error('未检测到新版扩展。请在「自动同步登录」下载 1.1.4，更新扩展并配对，刷新本页后重试。'))},2500);
    const timeout=setTimeout(()=>{cleanup();reject(Error('官网登录验证未及时完成，请检查该站官网状态后重试。'))},45000);
    const poll=setInterval(async()=>{if(polling)return;polling=true;try{const s=await request('capture-status/'+encodeURIComponent(job.request_id));if(s.state==='done'){cleanup();resolve({ok:true,connection:s.connection})}}catch{}finally{polling=false}},1500);
    function listener(event){if(event.source!==window||event.origin!==location.origin||event.data?.request_id!==job.request_id)return;if(event.data?.type==='linx-capture-ack'){clearTimeout(missing);return}if(event.data?.type==='linx-capture-progress'){message.textContent=event.data.text;return}if(event.data?.type!=='linx-capture-result')return;cleanup();resolve(event.data)}
    window.addEventListener('message',listener);window.postMessage({type:'linx-capture-request',request_id:job.request_id,site_id:sid,origin:job.origin},location.origin);
    message.textContent='正在从 '+choice.name+' 官网获取并验证登录。首次使用请在扩展中点击「授权站点」。';
   });
   if(!result.ok&&result.code==='PERMISSION_REQUIRED'){
    message.textContent='请打开 LinX 登录同步扩展点击「授权站点」。此页面会在授权后自动继续连接，无需再次点击。';
    const deadline=Date.now()+75000;
    while(true){const pending=await request('capture-status/'+encodeURIComponent(job.request_id));if(pending.state==='done'){result.connection=pending.connection;break}if(pending.state==='failed')throw Error('站点登录验证未通过，请检查官网登录后重试');if(Date.now()>deadline)throw Error('等待授权超时，请授权后重新点击获取');if(!document.getElementById('addSiteModal').open)throw Error('已取消等待');await new Promise(resolve=>setTimeout(resolve,1000))}
   }else if(!result.ok){const saved=await request('capture-status/'+encodeURIComponent(job.request_id));if(saved.state==='done')result.connection=saved.connection;else throw Error(result.error||'未能获取官网登录，请在当前浏览器登录该站后重试')}
   const state=result.connection||(await request('capture-status/'+encodeURIComponent(job.request_id))).connection;if(!state?.connected)throw Error('连接尚未完成，请重试');
   epoch++;clearSiteSnapshots(sid);currentSite=sid;const entry={...state,name:choice.name,base_url:choice.origin};siteList=siteList.some(s=>s.site_id===sid)?siteList.map(s=>s.site_id===sid?{...s,...entry}:s):[...siteList,entry];
   const select=document.getElementById('siteSelect');select.replaceChildren(...siteList.map(s=>{const option=document.createElement('option');option.value=s.site_id;option.textContent=s.name;return option}));select.value=sid;setConnected(entry);try{localStorage.setItem('linx-usage-site',sid)}catch{}
   token.value='';document.getElementById('addSiteModal').close();data=null;dates();capture();render();note('已从官网登录状态连接，正在读取最新用量。');await load();
  }catch(error){message.textContent=error.message}
  finally{active=false;button.disabled=false;save.disabled=!selectedEditorSiteKey}
 };
})();
