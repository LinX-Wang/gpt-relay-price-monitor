const $ = id => document.getElementById(id);
const state = {token: "", snapshot: null, sessionId: null, timer: null, submitting: false, followLatest: true, followSchedule: false};

async function json(url, options = {}) {
  let response;
  try { response = await fetch(url, {...options, signal: options.signal || (options.method === "POST" ? undefined : AbortSignal.timeout(30000))}); }
  catch {
    const error = Error(uiMessage("backend_disconnected"));
    error.code = "backend_disconnected";
    throw error;
  }
  const value = await response.json();
  if (!response.ok) {
    const error = Error(value.error ? MeowUI.error(value.error, uiMessage) : `HTTP ${response.status}`);
    error.code = value.error?.code;
    throw error;
  }
  return value;
}

function post(url, body) {
  return json(url, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Meow-Token": state.token},
    body: JSON.stringify(body),
  });
}

function packages() {
  return (state.snapshot?.packages || []).filter(item => item.mode === $("mode").value &&
    (item.mode === "chat" || item.publisher === "maintainer" || $("show-reference-packages").checked))
    .sort((a,b)=>b.version.localeCompare(a.version,undefined,{numeric:true}));
}

function options(select, rows, emptyLabel) {
  const previous = select.value;
  select.replaceChildren(...rows.map(([value, label]) => new Option(label, value)));
  if (!rows.length) select.add(new Option(emptyLabel, ""));
  if (rows.some(([value]) => value === previous)) select.value = previous;
}

function selectedPackage() {
  return packages().find(item => `${item.id}|${item.version}` === $("package").value);
}

const displayModel = MeowUI.modelName;

function renderModels() {
  const item = selectedPackage();
  options($("claimed"), (item?.models || []).map(model => [model.id, displayModel(model.id, model.name || model.id)]), t("无可用模型"));
  if(!state.linxDefaultChosen && $("mode").value==="gpt" && item?.models.some(m=>m.id==="gpt-6.1-sol")){ $("claimed").value="gpt-6.1-sol"; state.linxDefaultChosen=true; }
  const model = item?.models.find(model => model.id === $("claimed").value);
  const preset = state.snapshot?.endpoints.find(item => item.id === $("endpoint-preset").value && item.mode === $("mode").value);
  if (!$("request-model").value || state.claimedSelection !== model?.id) {
    MeowUI.autofill($("request-model"), model, $("base-url").value, true);
    if(preset)$("request-model").value=preset.model;
  }
  state.claimedSelection = model?.id;
  updateEstimate();
  updateReady();
  $("benchmark-note").textContent = item?.id.startsWith("synthetic-") ? t("这是合成测试基准，仅用于界面联调，不可判断真实模型。") : item ? "" : t("尚未安装适用基准。请先到基准库导入或下载。");
}

function render() {
  options($("package"), packages().map(item => [
    `${item.id}|${item.version}`, `${MeowUI.baselineName(item)} · ${item.version}`,
  ]), t("没有已安装基准包"));
  const preferred=state.snapshot?.defaults?.[$("mode").value];
  if(!state.defaultApplied?.[$("mode").value] && preferred){$("package").value=preferred.id+"|"+preferred.version;(state.defaultApplied||={})[$("mode").value]=true;}
  renderModels();
  renderPresets();
  $("base-url").disabled = Boolean($("endpoint-preset").value);
  updateReady();
  document.querySelectorAll("[data-mode]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.mode === $("mode").value)));
}

function updateReady() {
  const missing = [];
  const preset = state.snapshot?.endpoints.find(item => item.id === $("endpoint-preset").value);
  if (!selectedPackage()) missing.push(t("基准包"));
  if (!$("base-url").value.trim() && !preset) missing.push(t("API 地址"));
  if (!$("request-model").value.trim()) missing.push(t("实际请求模型"));
  if (!$("key").value.trim() && !preset?.credential_saved) missing.push("API key");
  if ($("run-mode").value === "scheduled" && !preset?.credential_saved) missing.push(t("已保存凭据的 API 连接"));
  const running = state.snapshot?.sessions.some(item => item.kind === "detection" && state.snapshot.active.includes(item.session_id));
  $("readiness").textContent = running ? t("当前检测运行中，请先等待或停止。") : missing.length ?
    t("开始前还需要：{items}", {items: missing.join(" / ")}) : t("准备就绪。");
  $("start").disabled = state.submitting || Boolean(running) || Boolean(missing.length);
}

function renderPresets() {
  const presets = state.snapshot?.endpoints || [];
  options($("endpoint-preset"), [["", t("仅本次手动输入")], ...presets.filter(item => item.mode === $("mode").value).map(item => [item.id, item.name])]);
  options($("preset-edit"), [["", t("添加新连接")], ...presets.map(item => [item.id, item.name])]);
}

function detectionInput() {
  const [id, version] = $("package").value.split("|");
  return {package_id: id, package_version: version, mode: $("mode").value,
    endpoint_id: $("endpoint-preset").value || undefined,
    base_url: $("base-url").value, allow_insecure: $("allow-http").checked, key: $("key").value,
    claimed_model: $("claimed").value, request_model: $("request-model").value,
    site_group: $("site-group").value,
    tier: $("tier").value, runtime: {workers: Number($("workers").value), retry_budget: $("retry-budget").value.trim()==="" ? undefined : Number($("retry-budget").value), retain_raw: $("retain-raw").checked}};
}

let estimateSequence = 0;
async function updateEstimate() {
  const sequence = ++estimateSequence;
  if (!selectedPackage()) { $("request-count").textContent = "—"; $("detect-estimate").textContent = t("请选择基准包。"); return; }
  try {
    const input = detectionInput();
    delete input.key;
    const estimate = await post("/meow/api/run/estimate", input);
    if (sequence === estimateSequence) {
      $("request-count").textContent = estimate.logical_requests;
      $("retry-budget").placeholder = String(Math.ceil(estimate.logical_requests/2));
      $("retry-budget").max = estimate.logical_requests*10;
      $("detect-estimate").textContent = t("含重试最多 {attempts} 次请求，费用由 API 服务商收取。", {attempts: estimate.maximum_http_attempts});
    }
  } catch (error) { if (sequence === estimateSequence) $("detect-estimate").textContent = errorMessage(error); }
}

function renderReportNote(report) {
  const box=$("report-note"), fp=report.fingerprint, cells=Object.entries(fp.cells || {});
  const running=["prepared","running","stopping"].includes(report.operational_status);
  const missing=running ? [] : cells.filter(([,cell])=>cell.valid<cell.minimum);
  const valid=report.progress?.valid_samples || 0;
  const make=(tag,text,cls)=>{const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node;};
  const details=box.dataset.reportId===report.session_id ? box.querySelector("details") || make("details","") : make("details","");
  box.dataset.reportId=report.session_id;
  box.replaceChildren();box.hidden=false;box.className="report-note"+(running ? " running" : "");
  const heading=make("div","","report-note-heading");
  heading.append(make("strong",t(running ? "检测中" : !valid ? "尚无有效样本" : missing.length ? "有效样本不足" : "检测明细")));
  if(!running)heading.append(make("span",uiMessage(report.operational_status),"report-note-status"));
  box.append(heading);
  const reasons=running ? [] : (fp.reasons || []).filter(code=>!["samples_incomplete","no_weighted_family"].includes(code));
  if(reasons.length)box.append(make("p",reasons.map(uiMessage).join(" / ")));
  const partial=MeowUI.partialNote(fp,running,locale);
  if(partial)box.append(make("p",partial));
  const failures=(report.events || []).filter(event=>event.event==="attempt_decision").reduce((all,event)=>{const text=event.error ? MeowUI.error(event.error,uiMessage) : uiMessage(event.code);all[text]=(all[text]||0)+1;return all;},{});
  if(missing.length || Object.keys(failures).length || report.failure){
    details.replaceChildren();details.append(make("summary",t("查看明细")));
    for(const [id,cell] of missing)details.append(make("p",t("{name}：{valid}/{planned}，至少{minimum}",{name:id,valid:cell.valid,planned:cell.planned,minimum:cell.minimum})));
    if(report.failure)details.append(make("p",uiMessage(report.failure)));
    for(const [text,count] of Object.entries(failures))details.append(make("p",text+" × "+count));
    box.append(details);
  }
  box.hidden=running ? !Object.keys(failures).length : !(partial || missing.length || Object.keys(failures).length || report.failure || reasons.length);
}
function showReport(report) {
  if (!report.fingerprint) return;
  $("report-placeholder").hidden = true;
  $("verdict").hidden = false;
  $("retention-export").hidden = false;
  const running=["prepared","running","stopping"].includes(report.operational_status);
  const color = running ? "running" : report.linx_result?.color || report.fingerprint.color || "yellow";
  $("verdict").className = `verdict ${color}`;
  $("verdict").textContent = running ? t("检测中") : report.fingerprint.sample_policy?.version === "60-percent-v1" && report.fingerprint.quality_status !== "sufficient" ? t(report.fingerprint.quality_status === "insufficient_valid_samples" ? "有效样本不足" : "部分题目样本不足") : {green: t("强指向申报模型"), red: t("强指向其他候选模型"), yellow: t("证据不足")}[color];
  if(!running && report.linx_result) $("verdict").textContent = report.linx_result.label;
  renderReportNote(report);
  if(report.progress)progress(report.progress);
  $("report-summary").textContent=MeowUI.reportMeta({model:displayModel(report.claimed_model),requestModel:report.request_model,url:report.endpoint,group:report.site_group},locale);
  $("report").textContent = JSON.stringify(report, null, 2);
  const models=state.snapshot.packages.find(p=>p.id===report.benchmark.id&&p.version===report.benchmark.version)?.models||[];
  MeowUI.matches($("match-bars"),report.fingerprint,{models,display:displayModel,valid:report.progress?.valid_samples,running,language:locale});
  $("meter-bar").parentElement.hidden=!running;
}

function renderHistory(sessions) {
  $("history-count").textContent=sessions.length;
  MeowUI.history($("run-history"),sessions.map(s=>({
    id:s.session_id,created:s.created_at,model:displayModel(s.claimed_model),requestModel:s.request_model,
    url:s.safe_endpoint,group:s.site_group,done:s.progress.logical_completed,valid:s.progress.valid_samples,planned:s.progress.planned,
    status:["prepared","running","stopping"].includes(s.status)?t("检测中"):s.quality_status==='insufficient_valid_samples'?t("有效样本不足"):s.quality_status==='cell_samples_incomplete'?t("部分题目样本不足"):s.linx_result?s.linx_result.label:s.color?{green:t("强指向申报模型"),red:t("强指向其他候选模型"),yellow:t("证据不足")}[s.color]:uiMessage(s.status),
    color:["prepared","running","stopping"].includes(s.status)?"running":s.linx_result?.color||s.color||"",resumable:["paused","error"].includes(s.status)
  })),{selected:state.sessionId,language:locale,empty:t(historyQuery?"没有匹配的检测记录":"还没有检测记录"),
    open:async id=>{
      state.followLatest=false;state.sessionId=id;showWorkspace("detect-view");
      try {const report=await json("/meow/api/report/"+encodeURIComponent(id));if(state.sessionId!==id)return;progress({...report.progress,status:report.operational_status});showReport(report);
        if(matchMedia("(max-width:800px)").matches)$("verdict").scrollIntoView({block:"start"});}
      catch(error){$("progress").textContent=errorMessage(error);}
    },
    resume:async id=>{
      try{
        if(!$("key").value&&!$("endpoint-preset").value)throw Error(t("请先输入该原线路的临时key或选择原连接预设。"));
        const result=await post("/meow/api/run/start",{...detectionInput(),resume_id:id});
        state.sessionId=result.session_id;state.followSchedule=false;state.followLatest=true;
      }catch(error){showStartError(error);}
    }
  });
}

function progress(value) {
  $("progress").textContent=MeowUI.progress({done:value.logical_completed||0,valid:value.valid_samples||0,planned:value.planned||0,attempts:value.http_attempts||0,retries:value.retries||0,budget:value.retry_budget},locale);
  $("meter-bar").style.width = value.planned ? `${Math.round(value.logical_completed / value.planned * 100)}%` : "0%";
}

async function poll() {
  if(state.polling)return;state.polling=true;
  try {
    const snapshot = await json("/meow/api/status", {cache: "no-store"});
    Object.assign(state.snapshot, snapshot);
    $("service-state").textContent = t("已连接");
    const schedule = snapshot.schedule;
    $("schedule-pause").hidden = !schedule?.enabled;
    updateReady();
    $("schedule-status").textContent = schedule ? `${t(schedule.enabled ? "计划运行中" : "计划已暂停")} · ${t("已完成 {count} 轮", {count: schedule.completed_rounds})}` +
      (schedule.next_due ? ` · ${t("下一轮 {time}", {time: new Date(schedule.next_due * 1000).toLocaleTimeString()})}` : "") +
      (schedule.error ? ` · ${uiMessage(schedule.error)}` : "") : "";
    if (!$("detect-view").hidden) await refreshHistory();
    const activeRun = snapshot.sessions.find(item => item.kind === "detection" && snapshot.active.includes(item.session_id));
    $("stop").disabled = !activeRun;
    $("stop").hidden = !activeRun;
    if (state.followLatest) state.sessionId = activeRun?.session_id || (state.followSchedule ? schedule?.last_session_id : null) || state.sessionId;
    if (!state.sessionId) return;
    const session = snapshot.sessions.find(item => item.session_id === state.sessionId);
    if (session) progress(session);
    const identity=state.sessionId;
    const report = await json(`/meow/api/report/${encodeURIComponent(identity)}`, {cache: "no-store"});
    if(state.sessionId===identity)showReport(report);
  } catch (error) {
    if (["backend_disconnected", "session_token_required"].includes(error.code)) {
      document.querySelectorAll('input[type="password"]').forEach(input => { input.value = ""; });
      updateReady();
    }
    $("progress").textContent = t("读取状态失败：{error}", {error: errorMessage(error)});
  } finally {
    state.polling=false;
  }
}

function showStartError(error) {
  $("start-error").textContent = errorMessage(error);
  $("start-error").scrollIntoView({block: "nearest", behavior: "instant"});
  $("start-error").focus({preventScroll: true});
}

$("mode").addEventListener("change", render);
document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
  $("mode").value = button.dataset.mode; $("base-url").disabled = false; render();
}));
for (const id of ["base-url", "key", "request-model"]) $(id).addEventListener("input", updateReady);
$("claimed").addEventListener("change", () => {
  const model = selectedPackage()?.models.find(item => item.id === $("claimed").value);
  MeowUI.autofill($("request-model"),model,$("base-url").value,true);
  state.claimedSelection = model?.id;
  updateReady();
});
$("run-mode").addEventListener("change", () => {
  $("schedule-controls").hidden = $("schedule-help").hidden = $("run-mode").value !== "scheduled";
  updateReady();
});
$("package").addEventListener("change", renderModels);
$("show-reference-packages").addEventListener("change", render);
$("tier").addEventListener("change", updateEstimate);
$("retry-budget").addEventListener("input", updateEstimate);
$("endpoint-preset").addEventListener("change", () => {
  const preset = state.snapshot.endpoints.find(item => item.id === $("endpoint-preset").value);
  $("base-url").disabled = Boolean(preset);
  if (preset) { $("allow-http").checked = preset.allow_insecure === true; $("base-url").value = preset.base_url; $("request-model").value = preset.model; $("key").value = ""; }
  updateReady();
});
$("detect-form").addEventListener("submit", async event => {
  event.preventDefault();
  if(state.submitting || $("start").disabled)return;
  $("start-error").textContent = "";
  $("start").disabled = true;
  state.submitting = true;
  try {
    const input = detectionInput();
    state.followLatest = true;
    if ($("run-mode").value === "scheduled") {
      if (!input.endpoint_id) throw Error(t("定时检测需要已保存凭据的连接。"));
      delete input.key;
      await post("/meow/api/schedule/start", {detection: input, interval_seconds: Number($("schedule-minutes").value) * 60,
        round_limit: $("schedule-rounds").value ? Number($("schedule-rounds").value) : null});
      state.followSchedule = true;
    } else {
      const result = await post("/meow/api/run/start", input);
      state.followSchedule = false;
      state.sessionId = result.session_id;
    }
    await poll();
  } catch (error) {
    showStartError(error);
  } finally {
    state.submitting = false;
    updateReady();
  }
});

$("stop").addEventListener("click", async () => {
  try {
    const activeRun = state.snapshot.sessions.find(item => item.kind === "detection" && state.snapshot.active.includes(item.session_id));
    if (activeRun) {
      state.sessionId = activeRun.session_id;
      state.followLatest = false;
      await post("/meow/api/run/stop", {session_id: activeRun.session_id});
      await poll();
    }
  } catch (error) {
    $("progress").textContent = errorMessage(error);
  }
});

$("schedule-pause").addEventListener("click", async () => {
  try { await post("/meow/api/schedule/pause", {}); await poll(); }
  catch (error) { $("schedule-status").textContent = errorMessage(error); }
});

(async () => {
  try {
    const bootstrap = await json("/meow/api/bootstrap");
    state.token = bootstrap.token;
    state.tierDefaults = bootstrap.tier_defaults;
    translatePage(new URLSearchParams(location.search).get("lang") || bootstrap.locale);
    state.snapshot = await json("/meow/api/snapshot", {cache: "no-store"});
    state.followSchedule = Boolean(state.snapshot.schedule?.enabled);
    $("service-state").textContent = t("已连接");
    render();
    state.timer = setInterval(poll, 2000);
    window.dispatchEvent(new Event("workspace-ready"));
  } catch (error) {
    $("service-state").textContent = errorMessage(error);
  }
})();
MeowUI.theme($("theme-choice"));
$("base-url").addEventListener("input",()=>MeowUI.autofill($("request-model"),selectedPackage()?.models.find(m=>m.id===$("claimed").value),$("base-url").value));
