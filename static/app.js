"use strict";
const $ = id => document.getElementById(id);
const state = {user:null, csrf:"", boot:null, evidence:[], conversation:null, voc:null};
function node(tag, content, cls){const e=document.createElement(tag);if(content!=null)e.textContent=content;if(cls)e.className=cls;return e;}
function notice(message,error=false){$("notice").textContent=message;$("notice").className=error?"error":"";$("notice").hidden=false;}
function guard(action){return async event=>{const b=event?.currentTarget?.tagName==="BUTTON"?event.currentTarget:null;if(b)b.disabled=true;try{await action(event);}catch(e){notice(e.message,true);}finally{if(b)b.disabled=false;}};}
function button(label,action,cls="secondary"){const b=node("button",label,cls);b.type="button";b.onclick=guard(action);return b;}
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":state.csrf},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.error||"요청에 실패했습니다.");return d;}
function fillSelect(select,options,selected){select.replaceChildren(...options.map(([v,t])=>{const o=node("option",t);o.value=v;return o;}));if(selected!=null)select.value=selected;}
function featureOptions(){return Object.entries(state.boot.features).map(([id,f])=>[id,f.name]);}
async function refresh(){
  state.boot=await api("/api/bootstrap");state.user=state.boot.user;state.csrf=state.user.csrf;state.evidence=await api("/api/evidence");
  $("identity").textContent=state.user.email;$("role-name").textContent=state.user.role==="owner"?"지식 소유자":"상품 기획자 · PO";
  $("project-name").textContent=state.user.project_id;$("research-nav").hidden=state.user.role!=="owner";$("voc-owner").hidden=state.user.role!=="owner";
  $("model-warning").hidden=state.boot.model_configured;$("model-state").textContent=state.boot.model_configured?state.boot.model_name+" · 설정됨":"AI 모델 미설정";
  $("model-state").className="pill"+(state.boot.model_configured?"":" pending");$("evidence-count").textContent=state.evidence.length;
  fillSelect($("conversation-select"),state.boot.conversations.map(c=>[c.id,c.title]),state.conversation?.id);
  fillSelect($("insight-feature"),featureOptions());
  fillSelect($("persona-evidence"),state.evidence.map(e=>[e.id,(e.kind==="voc"?"VoC":"인사이트")+" · "+(e.title||e.text).slice(0,55)]));
  renderPersonas();
}
async function page(name){
  if(name==="research"&&state.user.role!=="owner")return;
  document.querySelectorAll("section[id^='page-']").forEach(e=>e.hidden=e.id!=="page-"+name);
  document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("active",b.dataset.page===name));
  const labels={chat:["리서치 채팅","리서치와 고객 근거를 연결하고, 기획의 다음 질문을 찾아보세요."],research:["지식 관리","독점 자료를 보호하며 기획자가 활용할 인사이트를 관리하세요."],voc:["고객 VoC","서비스 기능별 고객 요구를 확인하고 해석의 근거로 활용하세요."],personas:["페르소나","광고주의 목표와 제약을 정의하고 가상 인터뷰를 진행하세요."],prd:["PRD 제안","대화에서 나온 가설과 요구사항을 검토하고 채택하세요."]};
  $("page-title").textContent=labels[name][0];$("page-caption").textContent=labels[name][1];
  if(name==="research")await renderResearch();if(name==="voc")await loadVoc();if(name==="prd")await renderProposals();
}
function showEvidence(id){
  const e=state.evidence.find(x=>x.id===id);$("evidence-detail").replaceChildren();
  if(!e){$("evidence-detail").textContent="근거가 철회되었거나 현재 접근할 수 없습니다.";return;}
  $("evidence-detail").append(node("strong",e.title||"고객 VoC"),node("p",e.text),node("small",e.id.slice(0,8)+" · "+(e.evidence_type==="synthetic"?"합성 자료":e.evidence_type==="real"?"수집 VoC":"리서치 인사이트")));
}
function renderMessages(){
  const container=$("messages");
  if(!state.conversation?.messages?.length){
    container.replaceChildren();const empty=node("div",null,"empty");
    empty.append(node("div","✦","empty-icon"),node("h2","어떤 서비스를 기획하고 있나요?"),node("p","리서치·고객 근거에 대해 질문하세요. @태그로 가상 페르소나를 인터뷰할 수 있습니다."));container.append(empty);return;
  }
  container.replaceChildren(...state.conversation.messages.map(m=>{
    const card=node("article",null,"message"+(m.speaker==="PO"?" user":m.is_synthetic?" persona":""));
    card.append(node("div",m.speaker+(m.is_synthetic?" · 가상 인터뷰":""),"speaker"),node("div",m.text,"bubble"));
    if(m.evidence_ids?.length){const refs=node("div",null,"citation-bar");m.evidence_ids.forEach((id,i)=>refs.append(button("근거 "+(i+1)+" · "+id.slice(0,6),()=>showEvidence(id),"")));card.append(refs);}
    if(m.assumptions?.length)card.append(node("div","추론·가정: "+m.assumptions.join(" / "),"assumptions"));return card;
  }));
  container.scrollTop=container.scrollHeight;$("chat-scope").textContent=state.conversation.mode==="interview"?"가상 인터뷰 · 실제 고객 검증과 구분":"공유된 지식과 가명화된 VoC를 활용합니다.";
}
async function openConversation(id){state.conversation=await api("/api/conversations/"+id);$("conversation-select").value=id;renderMessages();}
async function createConversation(title,mode="research",ids=[]){state.conversation=await api("/api/conversations",{title,mode,persona_ids:ids});await refresh();await openConversation(state.conversation.id);}
function insertTag(alias){const input=$("message-input");input.value=input.value.replace(/@[a-zA-Z0-9가-힣_-]*$/," ").trimEnd()+" @"+alias+" ";$("mentions").hidden=true;input.focus();}
function renderPersonas(){
  $("chat-personas").replaceChildren(...state.boot.personas.map(p=>{const b=button("@"+p.alias,()=>insertTag(p.alias),"persona-chip");b.append(node("small",p.segment));return b;}));
  $("persona-list").replaceChildren(...state.boot.personas.map(p=>{
    const card=node("article",null,"panel persona-card");
    card.append(node("div",p.name.slice(0,1),"avatar"),node("h2","@"+p.alias),node("small",p.segment),node("strong","목표"),node("p",p.goals),node("strong","제약"),node("p",p.constraints),node("small","가정: "+p.assumptions),node("small","근거 "+p.evidence_ids.length+"개 · 가상 페르소나 v"+p.version),button("이 페르소나 인터뷰하기",async()=>{await createConversation(p.name+" 인터뷰","interview",[p.id]);await page("chat");insertTag(p.alias);}));return card;
  }));
  if(!state.boot.personas.length)$("persona-list").append(node("p","근거를 추가한 뒤 페르소나를 생성하세요.","muted"));
}
async function renderResearch(){
  const data=await api("/api/research");fillSelect($("insight-source"),data.sources.map(s=>[s.id,s.title]));
  $("source-list").replaceChildren(...data.sources.map(s=>{
    const row=node("div",null,"record");row.append(node("strong",s.title),node("small",s.filename+" · 소유자 전용 · 추출 완료"));
    const actions=node("div",null,"record-actions");
    actions.append(button("원문 확인",async()=>{const raw=await api("/api/research/raw/"+s.id);$("raw-content").textContent=raw.text;$("raw-panel").hidden=false;}),button("AI 인사이트 초안 추출",async()=>{notice("설정된 모델로 소유자 전용 원문을 분석하고 있습니다.");await api("/api/research/extract",{source_id:s.id});await renderResearch();notice("비공개 인사이트 초안을 만들었습니다. 내용을 검토해 공개 범위를 설정하세요.");}));
    row.append(actions);return row;
  }));
  $("insight-list").replaceChildren(...data.insights.map(i=>{
    const row=node("div",null,"record"),head=node("div",null,"section-head");head.append(node("strong",i.title),node("span",i.published?"PO 활용 허용":"비공개 초안/철회",i.published?"state published":"state"));
    row.append(head,node("p",i.text),node("small",state.boot.features[i.feature].name),button(i.published?"공개 철회":"이 인사이트 공개",async()=>{await api("/api/insights/release",{insight_id:i.id,published:!i.published});await refresh();await renderResearch();notice(i.published?"공유를 철회했습니다. 관련 페르소나·답변은 재확인이 필요합니다.":"공개한 인사이트를 PO 채팅에서 활용할 수 있습니다.");}));return row;
  }));
}
async function loadVoc(){
  state.voc=await api("/api/voc");const v=state.voc;$("voc-scope").textContent=v.scope;
  $("voc-stats").replaceChildren(...[["전체 VoC",v.total],["실제 수집",v.total-v.synthetic_count],["가상 샘플",v.synthetic_count],["미분류",v.counts.unclassified]].map(([label,count])=>{const div=node("div",null,"stat");div.append(node("span",label),node("strong",String(count)));return div;}));
  fillSelect($("voc-filter"),[["all","모든 기능"],...featureOptions()],$("voc-filter").value||"all");renderVoc();
}
function renderVoc(){
  const filter=$("voc-filter").value;
  $("voc-list").replaceChildren(...state.voc.records.filter(r=>filter==="all"||r.feature===filter).map(r=>{
    const card=node("article",null,"voc-card");card.append(node("small",r.segment+" · "+(r.evidence_type==="synthetic"?"합성 VoC":"수집 VoC")),node("p",r.text),node("small",(r.occurred_at||"일자 미지정")+" · "+r.id.slice(0,8)));
    const select=node("select");select.setAttribute("aria-label","VoC "+r.id.slice(0,8)+" 기능 분류");fillSelect(select,featureOptions(),r.feature);
    select.onchange=guard(async()=>{await api("/api/voc/feature",{voc_id:r.id,feature:select.value});await refresh();await loadVoc();notice("서비스 기능 연결을 수정했습니다.");});card.append(select);return card;
  }));
}
async function renderProposals(){
  const proposals=await api("/api/proposals");
  $("proposal-list").replaceChildren(...proposals.map(p=>{
    const card=node("article",null,"panel"),head=node("div",null,"section-head");head.append(node("h2","PRD 변경 제안"),node("span",{draft:"검토 대기",accepted:"채택",held:"보류"}[p.state],"state"));card.append(head,node("div",p.text,"proposal-text"));
    if(p.assumptions.length)card.append(node("p","미검증 가정: "+p.assumptions.join(" / "),"muted"));
    card.append(node("small","근거 "+p.evidence_ids.length+"개 · 원본 메시지 "+p.source_message_ids.length+"개 · "+p.id.slice(0,8)));
    const actions=node("div",null,"record-actions");for(const [value,label] of [["accepted","PRD에 반영할 제안으로 채택"],["held","보류"]])actions.append(button(label,async()=>{await api("/api/proposals/decision",{proposal_id:p.id,state:value});await renderProposals();notice("검토 상태를 저장했습니다.");}));card.append(actions);return card;
  }));
  if(!proposals.length)$("proposal-list").append(node("p","리서치 채팅에서 대화를 진행한 뒤 PRD 변경 제안을 생성하세요.","muted"));
}
async function fileBody(input){
  const file=input.files[0];if(!file)throw new Error("파일을 선택하세요.");if(file.size>3*1024*1024)throw new Error("파일은 3MB 이하이어야 합니다.");
  const bytes=new Uint8Array(await file.arrayBuffer());let binary="";for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return {filename:file.name,content_base64:btoa(binary)};
}
$("login").onsubmit=async event=>{event.preventDefault();const b=event.currentTarget.querySelector("button");b.disabled=true;try{await api("/api/login",{email:$("email").value,password:$("password").value});$("password").value="";await boot();}catch(e){$("login-error").textContent=e.message;}finally{b.disabled=false;}};
$("logout").onclick=guard(async()=>{await api("/api/logout",{});window.location.reload();});
document.querySelectorAll("nav button").forEach(b=>b.onclick=guard(()=>page(b.dataset.page)));
$("new-conversation").onclick=()=>{$("new-conversation-form").hidden=!$("new-conversation-form").hidden;};
$("create-conversation").onclick=guard(async()=>{await createConversation($("conversation-title").value);$("new-conversation-form").hidden=true;});
$("conversation-select").onchange=guard(()=>openConversation($("conversation-select").value));
$("chat-form").onsubmit=guard(async event=>{
  event.preventDefault();const b=$("send-message"),input=$("message-input");b.disabled=true;b.textContent="근거를 바탕으로 답변 중…";
  try{if(!state.conversation)await createConversation(input.value.slice(0,50));state.conversation=await api("/api/chat",{conversation_id:state.conversation.id,message:input.value});input.value="";$("mentions").hidden=true;renderMessages();await refresh();}
  finally{b.disabled=false;b.textContent="질문 보내기 ↑";}
});
$("message-input").oninput=()=>{const match=$("message-input").value.match(/@([a-zA-Z0-9가-힣_-]*)$/);if(!match){$("mentions").hidden=true;return;}const options=state.boot.personas.filter(p=>p.alias.toLowerCase().includes(match[1].toLowerCase()));$("mentions").replaceChildren(...options.map(p=>button("@"+p.alias+" · "+p.segment,()=>insertTag(p.alias))));$("mentions").hidden=!options.length;};
document.querySelectorAll("[data-question]").forEach(b=>b.onclick=()=>{$("message-input").value=b.dataset.question;$("message-input").focus();});
$("go-personas").onclick=guard(()=>page("personas"));
$("make-proposal").onclick=guard(async()=>{if(!state.conversation)throw new Error("대화를 먼저 선택하세요.");notice("대화의 근거와 가설을 PRD 변경 제안으로 정리하고 있습니다.");await api("/api/proposals",{conversation_id:state.conversation.id});await page("prd");notice("변경 제안을 생성했습니다. 내용을 검토해 채택하거나 보류하세요.");});
$("export-chat").onclick=guard(async()=>{if(!state.conversation)throw new Error("내보낼 대화를 선택하세요.");const data=await api("/api/export/"+state.conversation.id);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));const a=node("a");a.href=url;a.download="research-package-"+state.conversation.id.slice(0,8)+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);});
$("research-upload").onsubmit=guard(async event=>{event.preventDefault();const r=await api("/api/research/upload",{...await fileBody($("research-file")),title:$("source-title").value});await renderResearch();notice("원문 '"+r.title+"'을 소유자 전용으로 저장했습니다.");});
$("insight-form").onsubmit=guard(async event=>{event.preventDefault();await api("/api/insights",{source_id:$("insight-source").value,title:$("insight-title").value,text:$("insight-text").value,feature:$("insight-feature").value});$("insight-title").value="";$("insight-text").value="";await renderResearch();notice("비공개 인사이트 초안을 저장했습니다.");});
$("close-raw").onclick=()=>{$("raw-content").textContent="";$("raw-panel").hidden=true;};
$("voc-upload").onsubmit=guard(async event=>{event.preventDefault();const r=await api("/api/voc/upload",{...await fileBody($("voc-file")),source_name:$("voc-source").value});await refresh();await loadVoc();notice("VoC "+r.imported+"건 추가 · 중복 "+r.duplicates+"건 · 오류 "+r.errors.length+"건. "+r.errors.map(e=>e.row+"행: "+e.error).join(" / ")+" "+r.redaction_note);});
$("review-collect").onsubmit=guard(async event=>{event.preventDefault();const r=await api("/api/voc/reviews",{app_id:$("apple-app-id").value});await refresh();await loadVoc();notice(r.imported+"건 추가 · 중복 "+r.duplicates+"건. "+r.scope+(r.has_more?" 추가 리뷰가 있습니다.":""));});
$("voc-filter").onchange=renderVoc;
$("generate-persona").onsubmit=guard(async event=>{event.preventDefault();const b=event.currentTarget.querySelector("button");b.disabled=true;try{await api("/api/personas/generate",{segment:$("target-segment").value});await refresh();notice("근거에 연결된 가상 페르소나를 만들었습니다.");}finally{b.disabled=false;}});
$("persona-form").onsubmit=guard(async event=>{event.preventDefault();await api("/api/personas",{name:$("persona-name").value,segment:$("persona-segment").value,goals:$("persona-goals").value,constraints:$("persona-constraints").value,assumptions:$("persona-assumptions").value,evidence_ids:[...$("persona-evidence").selectedOptions].map(o=>o.value)});await refresh();notice("직접 정의한 가상 페르소나를 저장했습니다.");});
async function boot(){await refresh();$("login-screen").hidden=true;$("workspace").hidden=false;if(state.boot.conversations.length)await openConversation(state.conversation?.id||state.boot.conversations[0].id);await page("chat");}
boot().catch(()=>{$("login-screen").hidden=false;$("workspace").hidden=true;});
