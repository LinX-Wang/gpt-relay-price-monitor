const el = (tag,text,cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; };
function action(id, operation, notice="library-notice") {
 const button=$(id); button.addEventListener("click",async()=>{if(button.disabled)return;button.disabled=true;
 try{await operation();}catch(e){$(notice).textContent=errorMessage(e);if(notice==='program-status')updateNotice(errorMessage(e));}finally{button.disabled=false;}});
}
function download(value,name) {
 const link=el("a"), url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));
 link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function showWorkspace(identity) {
  $("jump-to-start").hidden = identity !== "detect-view";
  if(identity==="detect-view")refreshHistory().catch(e=>$("history-notice").textContent=errorMessage(e));
  document.querySelectorAll(".workspace").forEach(view => { view.hidden = view.id !== identity; });
  document.querySelectorAll("[data-view]").forEach(button => {
    if (button.dataset.view === identity) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
}
$("jump-to-start").addEventListener("click", () => {
  $("tier").closest(".setup-panel").scrollIntoView({block: "start", behavior: "instant"});
});
document.querySelectorAll("[data-view],[data-jump]").forEach(button => button.addEventListener("click", () => showWorkspace(button.dataset.view || button.dataset.jump)));
$("save-current-connection").addEventListener("click", () => {
  $("preset-edit").value = "";
  $("preset-mode").value = $("mode").value;
  $("preset-url").value = $("base-url").value;
  $("preset-http").checked = $("allow-http").checked;
  $("preset-model").value = $("request-model").value;
  $("preset-key").value = $("key").value;
  $("preset-name").value = "";
  $("preset-notice").textContent = t("给这条 API 连接起个名字，确认后保存。");
  showWorkspace("settings-view"); $("preset-name").focus();
});

$("preset-edit").addEventListener("change", () => {
  const item = state.snapshot.endpoints.find(item => item.id === $("preset-edit").value);
  $("preset-name").value = item?.name || "";
  $("preset-mode").value = item?.mode || "gpt";
  $("preset-url").value = item?.base_url || "";
  $("preset-http").checked = item?.allow_insecure === true;
  $("preset-model").value = item?.model || "";
  $("preset-key").value = "";
});
action("preset-save", async () => {
  await post("/meow/api/endpoint/save", {preset: {id: $("preset-edit").value || undefined,
    name: $("preset-name").value, mode: $("preset-mode").value,
    base_url: $("preset-url").value, allow_insecure: $("preset-http").checked, model: $("preset-model").value}, key: $("preset-key").value || undefined});
  $("preset-key").value = "";
  await refreshWorkbench(); renderPresets();
  $("preset-notice").textContent = t("连接已保存；key只写入系统凭据库。");
}, "preset-notice");
action("preset-delete", async () => {
  if (!$("preset-edit").value) throw Error(t("请选择要删除的连接。"));
  if (!confirm(t("删除这个连接及它独占的系统凭据？已有定时计划不受影响。"))) return;
  await post("/meow/api/endpoint/delete", {id: $("preset-edit").value});
  await refreshWorkbench(); renderPresets();
  $("preset-notice").textContent = t("连接及对应凭据已删除。");
}, "preset-notice");
action("preset-export", () => download(state.snapshot.endpoints.map(item => ({name: item.name, mode: item.mode,
  base_url: item.base_url, model: item.model})), "meow-connections-no-keys.json"), "preset-notice");
action("schedule-delete", async () => {
  if (!confirm(t("删除已停止的定时计划及其独立凭据？检测报告仍保留。"))) return;
  await post("/meow/api/schedule/delete", {});
  await poll();
}, "preset-notice");


action("retention-export", async () => {
  if (!state.sessionId) throw Error(t("请先打开一份检测报告。"));
  const sessionId = state.sessionId;
  const records = [];
  let after = 0, bytes = 0, coverage;
  while (true) {
    const page = await json(`/meow/api/retention/${encodeURIComponent(sessionId)}?after=${after}`);
    coverage = page.coverage;
    if (!page.records.length) break;
    bytes += new TextEncoder().encode(JSON.stringify(page.records)).length;
    if (bytes > 32 * 1024 * 1024) throw Error(t("留存超过32MiB浏览器导出限制；原数据仍在本机数据库，未截断或删除。"));
    records.push(...page.records);
    after = page.records.at(-1).attempt_id;
  }
  if (!records.length) throw Error(t("本次没有留存正文；未开启留存或请求在响应前中断。"));
  download({session_id: sessionId, coverage, records}, `meow-evidence-${sessionId}.json`);
}, "progress");
let historyRevision=0, historyBefore=null, historyQuery="", historyPaged=false;
const historyRows=new Map();
async function refreshHistory(older=false) {
 const revision=historyRevision, params=new URLSearchParams({q:historyQuery});
 if(older && historyBefore)params.set("before",historyBefore);
 const page=await json("/meow/api/reports?"+params);
 if(revision!==historyRevision)return;
 if(!older&&!historyPaged)historyRows.clear();
 for(const row of page.items)historyRows.set(row.session_id,row);
 if(older || !historyPaged)historyBefore=page.before;
 if(older)historyPaged=true;
 renderHistory([...historyRows.values()].sort((a,b)=>b.sequence-a.sequence));
 $("history-more").hidden=!historyBefore;
}
$("history-search").addEventListener("submit",e=>{e.preventDefault();searchHistory($("history-query").value);});
$("history-search").addEventListener("reset",()=>searchHistory(""));
function searchHistory(query) { historyQuery=query.trim();historyRevision++;historyRows.clear();historyPaged=false;historyBefore=null; refreshHistory().catch(e=>$("history-notice").textContent=errorMessage(e)); }
action("history-more",()=>refreshHistory(true),"history-notice");
MeowUI.models({button:$("fetch-models"),input:$("request-model"),list:$("supported-models"),notice:$("models-notice"),
 fields:["base-url","key","mode","endpoint-preset"].map($),
 connection:()=>({base_url:$("base-url").value,key:$("key").value,endpoint_id:$("endpoint-preset").value||undefined,allow_insecure:$("allow-http").checked}),
 fetchModels:value=>post("/meow/api/models",value)});
async function refreshWorkbench() {
 state.snapshot=await json("/meow/api/snapshot");renderPresets();
 for(const [target,items,remote] of [["local-packages",state.snapshot.packages,false],["remote-packages",state.snapshot.catalog.packages||[],true]]) {
  $(target).replaceChildren();
  for(const item of items) {
   if(item.withdrawn)continue;
   if(remote && state.snapshot.packages.some(p=>p.id===item.id&&p.version===item.version || item.publisher==='maintainer'&&p.publisher==='maintainer'&&(p.id===item.id || item.mode!=='chat'&&p.mode===item.mode)&&!newerVersion(item.version,p.version)))continue;
   const card=el("div",undefined,"package-row"); card.append(el("strong",MeowUI.baselineName(item)),el("p",item.version));
   const button=el("button",t(remote?"安装":"导出"));
   button.onclick=async()=>{try{
    if(remote){await post("/meow/api/catalog/install",{id:item.id,version:item.version});await refreshWorkbench();render();}
    else download(await post("/meow/api/package/export",{id:item.id,version:item.version}),item.id+"-"+item.version+".meow.json");
   }catch(e){$("library-notice").textContent=errorMessage(e);}};
   card.append(button);
   if(!remote){const use=el("button",t("设为默认"));use.onclick=async()=>{
    try{await post("/meow/api/catalog/default",{id:item.id,version:item.version});$("mode").value=item.mode;render();$("package").value=item.id+"|"+item.version;renderModels();$("library-notice").textContent=t("已设为默认");}
    catch(e){$("library-notice").textContent=errorMessage(e);}
   };card.append(use);}
   $(target).append(card);
  }
  if(!$(target).children.length)$(target).append(el('p',t(remote?'暂无新的基准':'尚未安装基准'),'muted'));
 }
}
action("catalog-refresh",async()=>{await post("/meow/api/catalog/refresh",{});await refreshWorkbench();});
$("package-import").addEventListener("change",async e=>{try{
 if(!e.target.files.length)return;await post("/meow/api/package/import",{package:JSON.parse(await e.target.files[0].text())});await refreshWorkbench();render();
}catch(failure){$("library-notice").textContent=errorMessage(failure);}});
action("agent-copy",async()=>{if(!$("agent-url").reportValidity()||!$("agent-models").reportValidity())return;const result=await post("/meow/api/agent-prompt",{base_url:$("agent-url").value,models:$("agent-models").value.split("\n").map(x=>x.trim()).filter(Boolean),mode:$("agent-mode").value});
 await navigator.clipboard.writeText(result.prompt);$("library-notice").textContent=t("已复制，发给你的本地代理即可。");});
action("legacy-export",async()=>download(await json("/meow/api/legacy-work"),"meow-legacy-work.json"));
let programUpdate=null;
function newerVersion(next, current) {
  const parse = value => /^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?$/.exec(value || "");
  const a=parse(next), b=parse(current);
  if(!a || !b)return false;
  for(let i=1;i<=3;i++)if(Number(a[i])!==Number(b[i]))return Number(a[i])>Number(b[i]);
  if(!a[4] || !b[4])return Boolean(!a[4] && b[4]);
  return a[4].localeCompare(b[4],"en",{numeric:true})>0;
}
function baselineUpdates(){
 const local=state.snapshot.packages||[], remote=state.snapshot.catalog.packages||[];
 const latest=new Map();
 for(const item of remote){if(item.publisher!=="maintainer"||item.withdrawn)continue;
  const current=local.filter(p=>p.id===item.id || item.mode!=="chat"&&p.mode===item.mode&&p.publisher==="maintainer");
  const group=item.mode==="chat"?item.id:item.mode;
  if((current.length || item.mode==="chat") && current.every(p=>newerVersion(item.version,p.version)) && (!latest.has(group)||newerVersion(item.version,latest.get(group).version)))latest.set(group,item);
 }return [...latest.values()];
}
function updateNotice(message) {$("global-update-status").textContent=message;$("startup-updates").hidden=false;}
async function checkUpdates(manual=false){
 if(manual)updateNotice(t("正在检查更新…"));
 const results=await Promise.allSettled([post("/meow/api/program/check-update",{locale}),post("/meow/api/catalog/refresh",{})]);
 if(results[0].status==="fulfilled"){programUpdate=results[0].value;$("program-status").textContent=programUpdate.available?programUpdate.current_version+" → "+programUpdate.latest_version:t("已是最新版本");$("program-notes").textContent=programUpdate.notes;}
 else $("program-status").textContent=t("自动检查暂不可用，可手动重试。");
 if(results[1].status==="fulfilled"){state.snapshot.catalog=results[1].value;}
 $("startup-program-update").hidden=!programUpdate?.available;
 $("startup-baseline-update").hidden=!baselineUpdates().length;
 $("startup-updates").hidden=$("startup-program-update").hidden&&$("startup-baseline-update").hidden;
 $("program-download").disabled=!programUpdate?.available;
 if(!$("startup-updates").hidden)updateNotice(t("有更新可用"));
 else if(manual)updateNotice(t(results.some(r=>r.status==='rejected')?"检查暂不可用，请稍后重试。":"已是最新版本"));
}
async function installProgram(){
 if(!programUpdate?.available)return;
 const result=await post("/meow/api/program/install-update",{locale,version:programUpdate.latest_version,confirmed:true});
 $("program-status").textContent=t("正在准备更新，当前任务结束后自动重启。");updateNotice($("program-status").textContent);
 watchUpdate();
}
function watchUpdate(){
 const timer=setInterval(async()=>{try{
 const bootstrap=await json("/meow/api/bootstrap");
 if(bootstrap.version!==state.snapshot.version){clearInterval(timer);location.reload();return;}
 const status=await json("/meow/api/program/update-status");
 $("program-status").textContent=status.message||status.stage;
 updateNotice($("program-status").textContent);
 if(["failed","complete"].includes(status.stage)){clearInterval(timer);if(status.stage==="complete")location.reload();}
 }catch{}},2000);
}
action("quick-update",()=>checkUpdates(true),"program-status");
action("program-check",()=>checkUpdates(true),"program-status");
action("program-exit",async()=>{
 if(!confirm(t("停止本地服务？历史报告会保留。")))return;
 await post("/meow/api/program/exit",{confirmed:true});
 document.querySelectorAll('input[type="password"]').forEach(input=>input.value='');
 $("program-status").textContent=t("本地服务已停止，下次运行启动器即可打开。");
},"program-status");
action("program-download",installProgram,"program-status");
action("startup-program-update",installProgram,"program-status");
action("startup-baseline-update",async()=>{
 try{await post("/meow/api/catalog/update",{packages:baselineUpdates().map(p=>({id:p.id,version:p.version}))});}
 catch(error){if(error.code!=="program_update_required")throw error;await checkUpdates();if(!programUpdate?.available)throw error;await installProgram();return;}
 await refreshWorkbench();await checkUpdates();render();
});
window.addEventListener("workspace-ready",async()=>{await refreshWorkbench();await checkUpdates();await refreshHistory();setInterval(checkUpdates,86400000);});
