"use strict";
const $ = id => document.getElementById(id);
const apiBase = document.querySelector('meta[name="kdone-api-base"]')?.content || "api/";
const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const modes = {Normal:"일반", Sleep:"취침", Pleasant:"전열", Auto:"자동"};
const tabs = {overview:["우리 집을 한눈에.","공간의 빛과 온도, 공기의 흐름까지 편안하게 관리하세요.","홈 대시보드"],lights:["빛으로 채우는 공간.","방마다 필요한 조명을 편리하게 관리하세요.","조명"],heating:["우리 집에 맞는 따뜻함.","방별 온도와 외출 모드를 설정하세요.","난방"],ventilation:["매일, 더 신선한 공기.","일반·취침·전열·자동 모드와 풍량을 조절하세요.","환기"],hybrid:["안전하게, 마음 편하게.","가스밸브와 쿡탑을 개별 차단할 수 있습니다.","주방 안전"],schedules:["생활에 맞추는 우리 집.","조명·난방·환기의 켜기, 끄기와 동작 시간을 예약하세요.","예약 관리"],settings:["우리 집과 연결하기.","기기 연결 상태와 월패드 인증을 관리하세요.","연결 설정"]};
let csrf = "", state = {connected:false, devices:{}}, currentTab = "overview", jobs = [], pendingConfirm = null, timer;
let initialized = false, loading = false, externalSettings = {};
const fieldLabels = {OnOffStatus:"조명", PowerStatus:"환기 전원", Heating:"난방 전원", Mode:"모드", WindPower:"풍량", SettingTemp:"설정 온도", GasValveStatus:"가스밸브", InductionStatus:"쿡탑"};

async function api(path, data) {
  const options = {credentials:"same-origin", headers:{}};
  if (data !== undefined) { options.method = "POST"; options.headers = {"Content-Type":"application/json", "X-CSRF-Token":csrf}; options.body = JSON.stringify(data); }
  const target = apiBase.includes("?path=") ? apiBase+encodeURIComponent(path) : apiBase+path;
  const response = await fetch(target, options);
  let result;
  try { result = await response.json(); } catch { throw new Error("서버 응답을 확인할 수 없습니다."); }
  if (!response.ok) {
    if (response.status === 401 && path !== "login") { initialized=false; await bootstrap(); }
    throw new Error(result.error || "요청을 처리하지 못했습니다.");
  }
  return result;
}
function toast(message, error=false) { clearTimeout(timer); $("toast").textContent=message; $("toast").classList.toggle("error",error); $("toast").hidden=false; timer=setTimeout(()=>$("toast").hidden=true,6500); }
function showDialog(id) { const dialog=$(id); if(!dialog.open) dialog.showModal(); }
function confirmAction(title, description) {
  $("confirm-title").textContent=title; $("confirm-description").textContent=description; showDialog("confirm-dialog");
  return new Promise(resolve=>pendingConfirm=resolve);
}
function finishConfirm(value) { $("confirm-dialog").close(); if(pendingConfirm) pendingConfirm(value); pendingConfirm=null; }
$("confirm-ok").addEventListener("click",()=>finishConfirm(true));
$("confirm-cancel").addEventListener("click",()=>finishConfirm(false));
$("confirm-dialog").addEventListener("cancel",event=>{event.preventDefault();finishConfirm(false);});
$("admin-dialog").addEventListener("cancel",event=>event.preventDefault());
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>$(button.dataset.close).close()));

function setTab(tab) {
  currentTab=tab;
  const [title,subtitle,label]=tabs[tab];
  $("page-title").textContent=title; $("page-subtitle").textContent=subtitle; $("breadcrumb").textContent=label;
  $("overview-panel").hidden=tab!=="overview";
  document.querySelectorAll("[data-section]").forEach(section=>section.hidden=tab!=="overview" && section.dataset.section!==tab);
  $("schedules-panel").hidden=tab!=="schedules"; $("settings-panel").hidden=tab!=="settings";
  document.querySelectorAll("[data-tab]").forEach(button=>{const active=button.dataset.tab===tab; button.classList.toggle("active",active); button.setAttribute("aria-current",active?"page":"false");});
  if(tab==="schedules") loadJobs().catch(error=>toast(error.message,true));
}
document.querySelectorAll("[data-tab]").forEach(button=>button.addEventListener("click",()=>setTab(button.dataset.tab)));

function actionData(family, device, field, value) { return {family,group:device.group,sub:device.sub,field,value:String(value)}; }
function attrs(family,device,field,value) { return `data-family="${family}" data-group="${esc(device.group)}" data-sub="${esc(device.sub)}" data-field="${field}" data-value="${esc(value)}"`; }
function toggle(family,device,field) {
  const on=device.states[field]==="On";
  return `<button class="toggle${on?" on":""}" role="switch" aria-checked="${on}" aria-label="${esc(device.name)} ${fieldLabels[field]} ${on?"끄기":"켜기"}" ${attrs(family,device,field,on?"Off":"On")}></button>`;
}
function empty(container,text="기기 연결 후 표시됩니다.") { $(container).innerHTML=`<div class="empty">${esc(text)}</div>`; }
function renderLights() {
  const list=state.devices.lights||[];
  if(!list.length) return empty("lights-grid",state.connected?"조회된 조명이 없습니다.":undefined);
  $("lights-grid").innerHTML=list.map(device=>{const on=device.states.OnOffStatus==="On";return `<article class="device-card light-card${on?" on":""}"><span class="device-icon">${icon("light")}</span>${"OnOffStatus" in device.states?toggle("lights",device,"OnOffStatus"):""}<h3>${esc(device.name)}</h3><div class="device-state">${on?"켜짐":"꺼짐"}</div></article>`;}).join("");
}
function renderHeating() {
  const list=state.devices.heating||[];
  if(!list.length) return empty("heating-grid",state.connected?"조회된 난방 기기가 없습니다.":undefined);
  $("heating-grid").innerHTML=list.map((device,index)=>{const s=device.states,away=s.Mode==="On"; return `<article class="device-card heating-card"><div class="card-top"><h3>${esc(device.name)}</h3>${"Heating" in s?toggle("heating",device,"Heating"):""}</div><div class="room-temp"><strong>${esc(s.CurrentTemp)}</strong><span>℃</span><small>현재 온도</small></div><div class="temperature-control"><button class="step-button" data-step="-1" data-input="temp-${index}" aria-label="설정 온도 1도 낮추기">−</button><div class="temp-value"><input id="temp-${index}" type="number" min="${esc(s.SettingMin)}" max="${esc(s.SettingMax)}" step="1" value="${esc(s.SettingTemp)}" aria-label="${esc(device.name)} 설정 온도">℃</div><button class="step-button" data-step="1" data-input="temp-${index}" aria-label="설정 온도 1도 높이기">+</button></div><div class="room-bottom"><button class="away-button${away?" selected":""}" ${attrs("heating",device,"Mode",away?"Off":"On")}>${away?"외출 중 · 해제":"외출 설정"}</button><button class="small-button" data-temp-input="temp-${index}" ${attrs("heating",device,"SettingTemp",s.SettingTemp)}>온도 적용</button></div></article>`;}).join("");
}
function renderVentilation() {
  const list=state.devices.ventilation||[];
  if(!list.length) return empty("ventilation-grid",state.connected?"조회된 환기 기기가 없습니다.":undefined);
  $("ventilation-grid").innerHTML=list.map(device=>{const s=device.states; return `<article class="device-card vent-card"><div class="vent-heading"><span class="fan-disc">${icon("fan")}</span><div><h3>${esc(device.name)}</h3><div class="inline-status">${toggle("ventilation",device,"PowerStatus")}<span>${s.PowerStatus==="On"?"환기 작동 중":"환기 꺼짐"}</span></div><p>${esc(modes[s.Mode]||s.Mode)} 모드 · ${esc(s.WindPower)}단</p></div></div><div><span class="control-label">운전 모드</span><div class="segments">${Object.entries(modes).map(([value,label])=>`<button class="segment${s.Mode===value?" selected":""}" aria-pressed="${s.Mode===value}" ${attrs("ventilation",device,"Mode",value)}>${label}</button>`).join("")}</div><p class="muted small">취침에서 전열·자동으로 변경 시 일반 모드를 거칩니다.</p></div><div><span class="control-label">풍량</span><div class="segments">${[1,2,3].map(value=>`<button class="segment${String(s.WindPower)===String(value)?" selected":""}" aria-pressed="${String(s.WindPower)===String(value)}" ${s.Mode==="Auto"?"disabled":""} ${attrs("ventilation",device,"WindPower",value)}>${value}단</button>`).join("")}</div><div class="air-meta"><span>필터 ${s.Filter==="Normal"?"정상":esc(s.Filter)}</span><span>공기 ${s.Pollution==="Good"?"좋음":esc(s.Pollution)}</span></div></div></article>`;}).join("");
}
function renderHybrid() {
  const list=state.devices.hybrid||[];
  if(!list.length) return empty("hybrid-grid",state.connected?"조회된 주방 안전 기기가 없습니다.":undefined);
  $("hybrid-grid").innerHTML=list.map(device=>{const s=device.states;return [["GasValveStatus","가스밸브","Close","가스 차단"],["InductionStatus","쿡탑 전원","Off","전원 차단"]].filter(([field])=>field in s).map(([field,label,value,button])=>{const safe=s[field]===value;const name=device.name!==device.source_name?`${esc(device.name)} · ${label}`:label;return `<article class="device-card safety-card"><span class="summary-icon ${safe?"safe-icon":"coral"}">${icon("shield")}</span><div><h3>${name}</h3><p>${safe?"차단됨":field==="GasValveStatus"?"열림":"전원 켜짐"}</p></div><button class="button ${safe?"secondary":"danger-button"}" ${attrs("hybrid",device,field,value)}>${button}</button></article>`;}).join("");}).join("");
}
function render() {
  const light=state.devices.lights||[],heat=state.devices.heating||[],vent=state.devices.ventilation||[];
  const count=light.filter(d=>d.states.OnOffStatus==="On").length;
  $("stat-lights").innerHTML=`${light.length?count:"—"}<small>개</small>`;
  $("stat-lights-note").textContent=light.length?`전체 ${light.length}개 중 켜짐`:"조회 대기 중";
  const temps=heat.map(d=>Number(d.states.CurrentTemp)).filter(Number.isFinite);
  $("stat-temp").innerHTML=`${temps.length?(temps.reduce((a,b)=>a+b,0)/temps.length).toFixed(1):"—"}<small>℃</small>`;
  $("stat-temp-note").textContent=heat.length?`${heat.length}개 공간의 현재 온도`:"조회 대기 중";
  const v=vent[0]?.states;
  $("stat-vent").textContent=v?(v.PowerStatus==="On"?"작동 중":"꺼짐"):"—";
  $("stat-vent-note").textContent=v?`${modes[v.Mode]||v.Mode} 모드 · 풍량 ${v.WindPower}단`:"조회 대기 중";
  $("nav-light-count").textContent=light.length?count:"";
  $("home-name").textContent=state.home_name||state.complex; $("sidebar-complex").textContent=state.complex;
  document.title=`${state.home_name||"우리 집"} · KDOne`;
  $("side-dot").classList.toggle("connected",state.connected);
  $("side-status").innerHTML=`${state.demo?"데모 모드":state.connected?"기기 서버 연결됨":"기기 연결 필요"}<small>NAS HOME SERVER</small>`;
  $("connection-banner").hidden=state.connected;
  $("welcome-state").textContent=state.connected?`지금 켜진 조명 ${count}개, ${heat.length}개 공간을 관리하고 있어요.`:"월패드 인증으로 우리 집과 연결하세요.";
  $("settings-status").textContent=state.demo?"데모 모드":state.connected?"연결됨":"인증 필요";
  $("settings-complex").textContent=state.complex;
  $("connect-settings").disabled=state.connected;
  $("disconnect-settings").disabled=!state.connected||state.demo;
  $("restore-settings").disabled=state.connected||state.demo||!externalSettings.saved_certification;
  const updated=state.updated?new Date(state.updated*1000).toLocaleString("ko-KR",{timeZone:"Asia/Seoul",hour12:false}):"아직 조회하지 않았습니다";
  $("settings-updated").textContent=updated; $("last-update").textContent=`마지막 조회 · ${updated}`;
  if($("settings-auth-updated"))$("settings-auth-updated").textContent=state.last_auth_refresh?new Date(state.last_auth_refresh*1000).toLocaleString("ko-KR",{timeZone:"Asia/Seoul",hour12:false}):"아직 갱신하지 않았습니다";
  $("elevator").disabled=!state.connected;
  const errors=Object.entries(state.errors||{}).map(([family,message])=>`${({lights:"조명",heating:"난방",ventilation:"환기",hybrid:"주방"})[family]}: ${message}`);
  if(state.connection_message) errors.push(state.connection_message);
  if(state.storage_warning) errors.push(state.storage_warning);
  if(state.auth_warning) errors.push(state.auth_warning);
  $("notice").hidden=!errors.length; $("notice").textContent=errors.join(" / ");
  renderLights();renderHeating();renderVentilation();renderHybrid();setTabVisibility();
}
function setTabVisibility(){ $("overview-panel").hidden=currentTab!=="overview"; document.querySelectorAll("[data-section]").forEach(s=>s.hidden=currentTab!=="overview"&&s.dataset.section!==currentTab); }
async function refresh(remote=true) {
  if(loading) return;
  loading=true;$("refresh").disabled=true;
  try { externalSettings=await api("settings");$("saved-code-request").hidden=!externalSettings.saved_account;
    if(!$("complex-query").value||!$("connect-dialog").open)$("complex-query").value=externalSettings.complex_name||"";
    state=await api(`state${remote?"?refresh=1":""}`);render();await loadJobs(); }
  finally {loading=false;$("refresh").disabled=false;}
}
$("refresh").addEventListener("click",()=>refresh().catch(error=>toast(error.message,true)));

document.addEventListener("click",async event=>{
  const step=event.target.closest("[data-step]");
  if(step){const input=$(step.dataset.input);const value=Number(input.value)+Number(step.dataset.step);input.value=String(Math.max(Number(input.min),Math.min(Number(input.max),value)));return;}
  const button=event.target.closest("[data-family]");
  if(!button || button.disabled) return;
  const d=button.dataset;
  const device=(state.devices[d.family]||[]).find(item=>item.group===d.group&&item.sub===d.sub);
  if(!device) return;
  const value=d.tempInput?$(d.tempInput).value:d.value;
  button.disabled=true;
  try {const result=await api("control",{action:actionData(d.family,device,d.field,value),confirm:true});state.devices[d.family]=result.devices;if(!result.warning)state.updated=Date.now()/1000;render();toast(result.warning||result.message,!!result.warning);}
  catch(error){toast(error.message,true);}
  finally{button.disabled=false;}
});
$("elevator").addEventListener("click",async()=>{
  $("elevator").disabled=true;
  try {toast((await api("elevator",{confirm:true})).message);}catch(error){toast(error.message,true);}finally{$("elevator").disabled=false;}
});

function openConnect(){ $("connect-error").textContent="";showDialog("connect-dialog"); }
$("connect-banner").addEventListener("click",openConnect);$("connect-settings").addEventListener("click",openConnect);
$("connection-retry").addEventListener("click",()=>{$("connection-form").hidden=false;$("search-form").hidden=false;$("certify-form").hidden=true;$("kd-code").value="";$("connect-error").textContent="";});
$("restore-settings").addEventListener("click",async()=>{
  $("restore-settings").disabled=true;
  try{toast((await api("connection/saved/restore",{})).message);await refresh();}catch(error){toast(error.message,true);$("restore-settings").disabled=false;}
});
$("saved-code-request").addEventListener("click",async()=>{
  $("saved-code-request").disabled=true;
  try{toast((await api("connection/saved/request",{})).message);$("connection-form").hidden=true;$("search-form").hidden=true;$("certify-form").hidden=false;$("kd-code").focus();}
  catch(error){$("connect-error").textContent=error.message;}finally{$("saved-code-request").disabled=false;}
});
$("disconnect-settings").addEventListener("click",async()=>{
  if(!await confirmAction("기기 연결 해제","예약은 연결 복구까지 대기합니다. 자동 끄기가 실행되지 않을 수 있으니 현재 동작 중인 기기를 확인하세요."))return;
  try{toast((await api("connection/disconnect",{confirm:true})).message);await refresh(false);$("connection-form").hidden=false;$("search-form").hidden=false;$("certify-form").hidden=true;}catch(error){toast(error.message,true);}
});
$("search-form").addEventListener("submit",async event=>{
  event.preventDefault(); const button=event.submitter;button.disabled=true;
  try {const result=await api(`complexes?q=${encodeURIComponent($("complex-query").value)}`);$("complex-select").innerHTML=result.items.length?result.items.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join(""):'<option value="">검색 결과가 없습니다</option>';}
  catch(error){$("connect-error").textContent=error.message;}finally{button.disabled=false;}
});
$("connection-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;$("connect-error").textContent="";
  try {const select=$("complex-select");const result=await api("connection/request",{complex_id:select.value,complex_name:select.selectedOptions[0]?.textContent,username:$("kd-username").value.trim(),password:$("kd-password").value});$("kd-password").value="";$("connection-form").hidden=true;$("search-form").hidden=true;$("certify-form").hidden=false;toast(result.message);$("kd-code").focus();}
  catch(error){$("kd-password").value="";$("connect-error").textContent=error.message;}finally{button.disabled=false;}
});
$("certify-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try {const result=await api("connection/certify",{code:$("kd-code").value});$("kd-code").value="";$("connect-dialog").close();toast(result.warning||result.message,!!result.warning);await refresh();}
  catch(error){$("connect-error").textContent=error.message;$("kd-code").value="";}
  finally{button.disabled=false;}
});
$("admin-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try {const result=await api("login",{password:$("admin-password").value});csrf=result.csrf;$("admin-password").value="";$("admin-dialog").close();initialized=true;await refresh();}
  catch(error){$("admin-error").textContent=error.message;$("admin-password").value="";}finally{button.disabled=false;}
});
$("logout").addEventListener("click",async()=>{
  if(state.demo){toast("데모에서는 실제 계정에 로그인하지 않습니다.");return;}
  if(!await confirmAction("관리자 로그아웃","기기 연결과 저장한 예약은 유지됩니다.")) return;
  try{await api("logout",{});initialized=false;state={connected:false,devices:{},complex:"우리 집"};render();await bootstrap();}catch(error){toast(error.message,true);}
});

function koreaInputTime(date) { const parts=new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date);return parts.replace(" ","T"); }
let sequenceRows=[], editingSchedule=null, savedTimers=[], savedAutomations=[];
const sequenceFields={lights:{OnOffStatus:["On","Off"]},ventilation:{PowerStatus:["On","Off"],Mode:["Normal","Sleep","Pleasant","Auto"],WindPower:["1","2","3"]},heating:{Heating:["On","Off"],Mode:["Off","On"],SettingTemp:null}};
function sequenceDevices(){return ["ventilation","heating","lights"].flatMap(family=>(state.devices[family]||[]).map(device=>({family,...device})));}
function sequenceValueLabel(field,value,family){if(field==="Mode")return family==="heating"?(value==="On"?"외출":"난방"):modes[value];return {On:"켜기",Off:"끄기"}[value]||value;}
function addSequenceRow(type){
  const device=sequenceDevices()[0];if(!device&&type==="action")return;
  sequenceRows.push(type==="wait"?{type,minutes:1}:{type,action:actionData(device.family,device,Object.keys(sequenceFields[device.family])[0],"On")});renderSequence();
}
function renderSequence(){
  const devices=sequenceDevices();
  $("sequence-rows").innerHTML=sequenceRows.map((row,index)=>{
    let content;
    if(row.type==="wait")content=`<label>대기 (분)<input data-step-key="minutes" type="number" min="1" max="10080" step="1" required value="${esc(row.minutes)}"></label>`;
    else{
      const action=row.action,values=sequenceFields[action.family][action.field];
      const device=devices.find(device=>device.family===action.family&&device.group===action.group&&device.sub===action.sub);
      content=`<label>기기<select data-step-key="device">${devices.map((device,i)=>`<option value="${i}" ${device.family===action.family&&device.group===action.group&&device.sub===action.sub?"selected":""}>${esc(device.name)}</option>`).join("")}</select></label><div class="form-row"><label>동작<select data-step-key="field">${Object.keys(sequenceFields[action.family]).map(field=>`<option value="${field}" ${field===action.field?"selected":""}>${esc(fieldLabels[field]||field)}</option>`).join("")}</select></label><label>설정${values?`<select data-step-key="value">${values.map(value=>`<option value="${value}" ${value===action.value?"selected":""}>${esc(sequenceValueLabel(action.field,value,action.family))}</option>`).join("")}</select>`:`<input data-step-key="value" type="number" min="${esc(device?.states.SettingMin||5)}" max="${esc(device?.states.SettingMax||45)}" step="1" required value="${esc(action.value)}">`}</label></div>`;
    }
    return `<article class="sequence-row" data-step-index="${index}"><div class="sequence-heading"><strong>${index+1}. ${row.type==="wait"?"대기":"기기 동작"}</strong><div><button type="button" class="text-button" data-step-move="-1" ${index===0?"disabled":""} aria-label="위로 이동">↑</button><button type="button" class="text-button" data-step-move="1" ${index===sequenceRows.length-1?"disabled":""} aria-label="아래로 이동">↓</button><button type="button" class="text-button" data-step-remove>삭제</button></div></div>${content}</article>`;
  }).join("");
}
$("add-action-row").addEventListener("click",()=>addSequenceRow("action"));
$("add-wait-row").addEventListener("click",()=>addSequenceRow("wait"));
$("timer-on-off-preset").addEventListener("click",()=>{
  const old=sequenceRows.find(row=>row.type==="action");
  if(!old)return;
  const device=old.action,field={lights:"OnOffStatus",ventilation:"PowerStatus",heating:"Heating"}[device.family];
  const minutes=sequenceRows.find(row=>row.type==="wait")?.minutes||1;
  sequenceRows=[{type:"action",action:actionData(device.family,device,field,"On")},{type:"wait",minutes},{type:"action",action:actionData(device.family,device,field,"Off")}];
  renderSequence();
});
function editableSequence(sequence){
  return sequence.flatMap(row=>{
    if(row.type==="wait")return [{type:"wait",minutes:row.minutes}];
    const {setup=[],...action}=row.action;
    const steps=setup.filter(step=>!(step.field==="WindPower"&&setup.some(item=>item.field==="Mode"&&item.value==="Auto")));
    const ordered=action.family==="ventilation"&&action.field==="PowerStatus"&&action.value==="On"?[action,...steps]:[...steps,action];
    return ordered.map(action=>({type:"action",action:{...action}}));
  });
}
function editSchedule(kind,id){
  openSchedule({currentTarget:{dataset:{kind}}});if(!state.connected)return;
  editingSchedule=id;$("schedule-kind").disabled=true;
  $("schedule-title").textContent=kind==="timer"?"타이머 수정":"자동화 수정";
  $("schedule-edit-help").textContent=kind==="timer"?"저장 시 기존의 남은 동작을 교체하고, 지금부터 수정한 순서의 처음부터 실행합니다. 이미 실행한 동작은 되돌리지 않습니다.":"수정 내용은 다음 회차부터 적용합니다. 진행 중인 회차는 기존 순서로 마칩니다.";
  if(kind==="timer"){
    const item=savedTimers.find(item=>item.id===id);
    if(item){$("sequence-name").value=item.label;sequenceRows=editableSequence(item.sequence);}
    else{
      const start=jobs.find(job=>job.id===id),off=jobs.find(job=>job.pair_id===id&&job.id!==id);
      $("sequence-name").value=start.label;
      sequenceRows=editableSequence([...(start.status==="pending"?[{type:"action",action:start.action}]:[]),{type:"wait",minutes:Math.max(1,Math.ceil((off.due-Date.now()/1000)/60))},{type:"action",action:off.action}]);
    }
  }else{
    const item=savedAutomations.find(item=>item.id===id),rule=item.rule;
    $("sequence-name").value=item.label;
    $("schedule-repeat").value=rule.kind;
    $("schedule-clock").value=rule.at||"08:00";
    $("schedule-interval").value=rule.minutes||120;
    if(rule.anchor)$("schedule-time").value=koreaInputTime(new Date(rule.anchor*1000));
    $("schedule-days").querySelectorAll("input").forEach(input=>{input.checked=(rule.days||[]).includes(Number(input.value));});
    sequenceRows=editableSequence(item.action.sequence||[{type:"action",action:item.action},{type:"wait",minutes:item.duration},{type:"action",action:{...item.action,setup:[],field:{lights:"OnOffStatus",ventilation:"PowerStatus",heating:"Heating"}[item.action.family],value:"Off"}}]);
  }
  renderSequence();scheduleOptions();
}
function sequenceSummary(sequence){return sequence.map(step=>step.type==="wait"?`${step.minutes}분 대기`:`${step.name||sequenceDevices().find(device=>device.family===step.action.family&&device.group===step.action.group&&device.sub===step.action.sub)?.name||"기기"}: ${fieldLabels[step.action.field]||step.action.field} ${sequenceValueLabel(step.action.field,step.action.value,step.action.family)}`).join(" → ");}
$("sequence-rows").addEventListener("change",event=>{
  const key=event.target.dataset.stepKey;if(!key)return;
  const row=sequenceRows[Number(event.target.closest("[data-step-index]").dataset.stepIndex)];
  if(key==="minutes")row.minutes=Number(event.target.value);
  else if(key==="device"){
    const device=sequenceDevices()[Number(event.target.value)];row.action=actionData(device.family,device,Object.keys(sequenceFields[device.family])[0],"On");renderSequence();
  }else if(key==="field"){
    row.action.field=event.target.value;row.action.value=sequenceFields[row.action.family][row.action.field]?.[0]||"21";renderSequence();
  }else row.action.value=event.target.value;
});
$("sequence-rows").addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button)return;
  const index=Number(button.closest("[data-step-index]").dataset.stepIndex);
  if(button.hasAttribute("data-step-remove"))sequenceRows.splice(index,1);
  else if(button.hasAttribute("data-step-move")){const target=index+Number(button.dataset.stepMove);[sequenceRows[index],sequenceRows[target]]=[sequenceRows[target],sequenceRows[index]];}
  renderSequence();
});
function scheduleOptions() {
  const automation=$("schedule-kind").value==="automation",repeat=$("schedule-repeat").value;
  for(const id of ["timer-device-label","timer-duration-label","schedule-vent-options","schedule-temp-label"])$(id).hidden=true;
  for(const id of ["schedule-device","schedule-duration","schedule-temp"])$(id).disabled=true;
  $("sequence-options").hidden=false;
  $("timer-on-off-preset").hidden=automation;
  $("sequence-rows").querySelectorAll("input,select").forEach(input=>{input.disabled=false;});
  $("automation-options").hidden=!automation;
  $("schedule-days").hidden=repeat!=="weekdays";
  $("schedule-clock-label").hidden=repeat==="interval";
  $("schedule-interval-options").hidden=repeat!=="interval";
}
function openSchedule(event){
  if(!state.connected){toast("먼저 기기를 연결해 주세요.",true);return;}
  $("schedule-error").textContent="";
  editingSchedule=null;$("schedule-kind").disabled=false;
  $("schedule-title").textContent="타이머 · 자동화 추가";
  $("schedule-edit-help").textContent="대기와 기기 동작을 원하는 순서로 배치하세요. 타이머에는 1분 이상의 대기를 넣어 주세요.";
  $("schedule-form").reset();
  $("schedule-kind").value=event?.currentTarget?.dataset.kind||"timer";
  $("schedule-device").innerHTML=["ventilation","heating","lights"].flatMap(family=>(state.devices[family]||[]).map(device=>`<option data-family="${family}" data-group="${esc(device.group)}" data-sub="${esc(device.sub)}">${esc(({ventilation:"환기",heating:"난방",lights:"조명"})[family])} · ${esc(device.name)}</option>`)).join("");
  $("schedule-time").value=koreaInputTime(new Date(Date.now()+5*60000));
  sequenceRows=[];$("sequence-name").value="";
  if($("schedule-kind").value==="timer")addSequenceRow("wait");
  addSequenceRow("action");
  if($("schedule-kind").value==="timer"&&sequenceRows.at(-1)?.action){sequenceRows.at(-1).action.value="Off";renderSequence();}
  scheduleOptions();showDialog("schedule-dialog");
}
document.querySelectorAll(".schedule-shortcut").forEach(button=>button.addEventListener("click",openSchedule));
for(const id of ["schedule-device","schedule-kind","schedule-repeat","schedule-mode"])$(id).addEventListener("change",scheduleOptions);
$("schedule-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=event.submitter;const option=$("schedule-device").selectedOptions[0];
  const kind=$("schedule-kind").value,repeat=$("schedule-repeat").value;
  const rule=repeat==="interval"?{kind:repeat,minutes:Number($("schedule-interval").value),anchor:new Date(`${$("schedule-time").value}:00+09:00`).getTime()/1000}:{kind:repeat,at:$("schedule-clock").value,days:[...$("schedule-days").querySelectorAll("input:checked")].map(input=>Number(input.value))};
  button.disabled=true;
  const payload={sequence:sequenceRows.map(row=>row.type==="wait"?{type:"wait",minutes:Number(row.minutes)}:{type:"action",action:row.action}),label:$("sequence-name").value,rule};
  const endpoint=editingSchedule?`${kind==="timer"?"timers":"automations"}/${editingSchedule}/edit`:(kind==="timer"?"timers":"automations");
  try {await api(endpoint,payload);$("schedule-dialog").close();toast(editingSchedule?"수정했습니다.":"저장했습니다.");setTab("schedules");}
  catch(error){$("schedule-error").textContent=error.message;}finally{button.disabled=false;}
});
async function loadJobs(){
  const result=await api("schedules");jobs=result.items;savedTimers=result.timers||[];savedAutomations=result.automations;
  const pending=jobs.filter(j=>j.status==="pending").length;$("nav-job-count").textContent=pending||"";
  const labels={pending:result.connected?"대기 중":"연결 대기",running:"실행 중",done:"완료",failed:"실패",cancelled:"취소됨",skipped:"시작 생략"};
  const date=value=>new Date(value*1000).toLocaleString("ko-KR",{timeZone:"Asia/Seoul",hour12:false});
  const timers=jobs.filter(job=>job.kind==="timer"&&!savedTimers.some(item=>item.id===job.id)&&job.id===job.pair_id&&jobs.some(row=>row.pair_id===job.id&&row.id!==job.id&&["pending","running","failed"].includes(row.status)));
  $("timers-list").innerHTML=(savedTimers.map(item=>`<article class="job"><div class="job-body"><h3>${esc(item.label)}</h3><p>${esc(sequenceSummary(item.sequence))}</p><p>${item.next_due?esc(date(item.next_due)):""}</p>${item.error?`<small>${esc(item.error)}</small>`:""}</div><button class="text-button" data-edit-timer="${esc(item.id)}">수정</button><button class="button secondary" data-stop-timer="${esc(item.id)}">취소</button></article>`).join("")+timers.map(job=>{const off=jobs.find(row=>row.pair_id===job.id&&row.id!==job.id);const active=off&&["pending","failed"].includes(off.status);return `<article class="job"><div class="job-body"><h3>${esc(job.label)}</h3><p>종료 ${esc(off?date(off.due):"—")} · ${esc(labels[off?.status]||"대기")}</p>${off?.error?`<small>${esc(off.error)}</small>`:""}</div><button class="text-button" data-edit-timer="${esc(job.id)}">수정</button>${active?`<button class="button secondary" data-stop-timer="${esc(job.id)}">지금 종료</button>`:""}</article>`;}).join(""))||'<div class="empty">등록된 타이머가 없습니다.</div>';
  $("automations-list").innerHTML=result.automations.map(item=>{const rule=item.rule;const repeat=rule.kind==="interval"?`${rule.minutes}분마다`:rule.kind==="daily"?`매일 ${rule.at}`:`${rule.days.map(day=>["월","화","수","목","금","토","일"][day]).join("·")} ${rule.at}`;return `<article class="job"><div class="job-body"><h3>${esc(item.label)}</h3><p>${esc(repeat)} · ${item.action.sequence?esc(sequenceSummary(item.action.sequence)):`${item.duration}분 동작 후 끄기`}</p><p>${item.enabled?`다음 시작 ${esc(date(item.next_due))}`:"일시정지됨"}${item.last_status?` · 최근 실행 ${esc(labels[item.last_status]||item.last_status)}`:""}</p>${item.last_error?`<small>${esc(item.last_error)}</small>`:""}</div><button class="text-button" data-edit-automation="${esc(item.id)}">수정</button><button class="text-button" data-automation="${esc(item.id)}" data-operation="toggle" data-enabled="${!item.enabled}">${item.enabled?"일시정지":"재개"}</button><button class="text-button" data-automation="${esc(item.id)}" data-operation="delete">삭제</button></article>`;}).join("")||'<div class="empty">등록된 자동화가 없습니다.</div>';
  $("jobs-list").innerHTML=jobs.length?jobs.map(job=>{const date=new Date(job.due*1000).toLocaleString("ko-KR",{timeZone:"Asia/Seoul",hour12:false});return `<article class="job"><span class="summary-icon mint">${icon("clock")}</span><div class="job-body"><h3>${esc(job.label)}</h3><p>${esc(date)} · ${esc(fieldLabels[job.action.field]||job.action.field)} ${esc(job.action.value)}</p>${job.error?`<small>${esc(job.error)}</small>`:""}</div><span class="job-state ${esc(job.status)}">${esc(labels[job.status]||job.status)}</span>${job.status==="pending"&&job.kind==="legacy"?`<button class="text-button" data-cancel-job="${esc(job.id)}">취소</button>`:""}</article>`;}).join(""):'<div class="empty">등록된 예약이 없습니다. 첫 번째 예약을 추가해 보세요.</div>';
}
$("timers-list").addEventListener("click",async event=>{
  const edit=event.target.closest("[data-edit-timer]");if(edit){editSchedule("timer",edit.dataset.editTimer);return;}
  const button=event.target.closest("[data-stop-timer]");if(!button)return;button.disabled=true;
  try{await api(`timers/${button.dataset.stopTimer}/stop`,{});await refresh();}catch(error){toast(error.message,true);}finally{button.disabled=false;}
});
$("automations-list").addEventListener("click",async event=>{
  const edit=event.target.closest("[data-edit-automation]");if(edit){editSchedule("automation",edit.dataset.editAutomation);return;}
  const button=event.target.closest("[data-automation]");if(!button)return;button.disabled=true;
  try{await api(`automations/${button.dataset.automation}/${button.dataset.operation}`,{enabled:button.dataset.enabled==="true"});await loadJobs();}catch(error){toast(error.message,true);}finally{button.disabled=false;}
});
$("jobs-list").addEventListener("click",async event=>{
  const button=event.target.closest("[data-cancel-job]");if(!button)return;
  if(!await confirmAction("예약 취소","대기 중인 예약을 취소합니다. 현재 기기 상태는 바뀌지 않습니다. 자동 끄기를 취소하면 기기가 계속 켜져 있을 수 있습니다."))return;
  try {toast((await api(`schedules/${button.dataset.cancelJob}/cancel`,{})).message);await loadJobs();}catch(error){toast(error.message,true);}
});
async function bootstrap(){
  try {const info=await api("session");csrf=info.csrf;$("demo-tag").hidden=!info.demo;
    if(!info.authenticated){showDialog("admin-dialog");return;}
    $("admin-dialog").close();initialized=true;await refresh();
  }catch(error){toast(error.message,true);}
}
$("today").textContent=new Date().toLocaleDateString("ko-KR",{timeZone:"Asia/Seoul",year:"numeric",month:"long",day:"numeric",weekday:"short"});
bootstrap();
setInterval(()=>{if(initialized&&!document.hidden&&!document.querySelector("dialog[open]")&&!document.activeElement?.matches("input,select")&&!loading)refresh().catch(error=>toast(error.message,true));},60000);
setInterval(()=>{if(initialized&&currentTab==="schedules"&&!document.hidden&&!document.querySelector("dialog[open]")&&!loading)loadJobs().catch(error=>toast(error.message,true));},5000);
