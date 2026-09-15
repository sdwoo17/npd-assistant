"use strict";
// DOM + real HTTP integration. Model responses come from tests/helpers.py.
// This does not exercise browser layout, actual provider quality, or live UAT.
const {test, before, after} = require("node:test");
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {once} = require("node:events");
const {JSDOM} = require("jsdom");
let server, origin, html, script;
const windows = [];

before(async () => {
  server = spawn(process.env.PYTHON || "python3", ["-m", "tests.dom_server"], {stdio:["ignore","pipe","inherit"]});
  origin = await new Promise((resolve,reject) => {
    const timeout = setTimeout(() => reject(new Error("Fixture startup timed out")),10000);
    server.once("error", e => {clearTimeout(timeout); reject(e);});
    server.once("exit", code => {clearTimeout(timeout); reject(new Error("Fixture exited: "+code));});
    server.stdout.once("data", data => {clearTimeout(timeout); resolve(data.toString().trim());});
  });
  [html,script] = await Promise.all(["/","/app.js"].map(path => fetch(origin+path).then(r=>r.text())));
  script += "\n" + await fetch(origin+"/planning.js").then(r=>r.text());
  script += "\n" + await fetch(origin+"/research-workspace.js").then(r=>r.text());
  script += "\n" + await fetch(origin+"/research-flow.js").then(r=>r.text());
});

after(async () => {
  windows.forEach(w=>w.close());
  if(server && server.exitCode===null){const done=once(server,"exit");server.kill();await done;}
});

async function screen(role) {
  const dom = new JSDOM(html,{url:origin,runScripts:"outside-only"});
  const w=dom.window;windows.push(w);
  let cookie="", initialResolve;
  const initialRequest = new Promise(resolve=>initialResolve=resolve);
  w.fetch=async(path,options={})=>{
    const headers=new Headers(options.headers||{});
    if(cookie)headers.set("Cookie",cookie);
    if(options.method==="POST")headers.set("Origin",origin);
    const r=await fetch(new URL(path,origin),{...options,headers});
    const setCookie=r.headers.get("set-cookie");
    if(setCookie)cookie=setCookie.split(";")[0];
    if(path==="/api/bootstrap")initialResolve();
    return r;
  };
  const request = w.eval(script + "\napi;");
  await initialRequest;
  await new Promise(resolve=>setTimeout(resolve,25));
  const $=id=>w.document.getElementById(id);
  $("email").value=role+"@example.test";
  $("password").value=role==="owner"?"Owner-test-pass!":"Planner-test-pass!";
  await submit($("login"));
  assert.equal($("workspace").hidden,false,$("login-error").textContent);
  return {w,$,request};
}
async function submit(element){await element.onsubmit({preventDefault(){},currentTarget:element});}
async function click(element){assert.ok(element,"Button exists");await element.onclick({currentTarget:element});}
function byText(root,text){return [...root.querySelectorAll("button")].find(b=>b.textContent.includes(text));}
function setFile(element,name,content){
  const bytes=Buffer.from(content);
  Object.defineProperty(element,"files",{configurable:true,value:[{
    name,size:bytes.length,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)
  }]});
}

test("owner UI uploads a protected file, writes/releases an insight and views the original", async()=>{
  const {w,$}=await screen("owner");
  assert.equal($("research-nav").hidden,false);
  await click($("research-nav"));
  $("source-title").value="UI 합성 리서치";
  setFile($("research-file"),"synthetic-ui.md","UI-PRIVATE-CANARY-5701\n소재 분석의 근거가 필요한 광고주.");
  await submit($("research-upload"));
  assert.match($("source-list").textContent,/UI 합성 리서치/);
  $("insight-source").value=$("insight-source").options[1].value;
  $("insight-title").value="UI 공개 인사이트";
  $("insight-text").value="소규모 광고주 소재 분석 리포트에는 추천 근거를 보여주는 것이 필요하다는 가설.";
  await submit($("insight-form"));
  const record=[...$("insight-list").children].find(e=>e.textContent.includes("UI 공개 인사이트"));
  assert.match(record.textContent,/비공개/);
  await click(byText(record,"이 인사이트 공개"));
  assert.match($("notice").textContent,/활용할 수/);
  const source=[...$("source-list").children].find(e=>e.textContent.includes("UI 합성 리서치"));
  await click(byText(source,"원문 확인"));
  assert.match($("raw-content").textContent,/UI-PRIVATE-CANARY-5701/);
  await click($("close-raw"));
  assert.equal($("raw-content").textContent,"");
  assert.equal($("raw-panel").hidden,true);
});

test("PO UI filters VoC, interviews tagged personas, cites evidence and accepts a PRD proposal", async()=>{
  const {w,$}=await screen("po");
  assert.equal($("research-nav").hidden,true);
  assert.equal($("voc-owner").hidden,true);
  assert.doesNotMatch(w.document.body.textContent,/PRIVATE-RESEARCH-CANARY|UI-PRIVATE-CANARY/);
  await click(w.document.querySelector("[data-page='voc']"));
  assert.match($("voc-stats").textContent,/가상 샘플1/);
  const feature=$("voc-list").querySelector("select");
  feature.value="budget";
  await feature.onchange({currentTarget:feature});
  $("voc-filter").value="budget";$("voc-filter").onchange();
  assert.equal($("voc-list").children.length,1);
  await click(w.document.querySelector("[data-page='chat']"));
  $("message-input").value="@소";
  $("message-input").oninput();
  assert.equal($("mentions").hidden,false);
  await click(byText($("mentions"),"@소규모광고주"));
  assert.match($("message-input").value,/@소규모광고주 /);
  $("message-input").value="@대행사운영자 @소규모광고주 소재 리포트를 어떻게 개선하면 좋을까요?";
  await submit($("chat-form"));
  assert.equal($("messages").querySelectorAll(".persona").length,2,$("notice").textContent);
  assert.match($("messages").textContent,/가상 인터뷰/);
  await click($("messages").querySelector(".citation-bar button"));
  assert.ok($("evidence-detail").textContent.length>30);
  $("message-input").value="앞서 말한 문제의 이유를 더 자세히 설명해 주세요.";
  await submit($("chat-form"));
  assert.equal($("messages").querySelectorAll(".persona").length,4);
  assert.deepEqual([...$("messages").querySelectorAll(".persona .speaker")].map(e=>e.textContent.split(" · ")[0]),["대행사운영자","소규모광고주","대행사운영자","소규모광고주"]);
  await click($("make-proposal"));
  assert.equal($("page-prd").hidden,false,$("notice").textContent);
  assert.equal($("proposal-list").children.length,1);
  await click(byText($("proposal-list"),"제안으로 채택"));
  assert.equal($("proposal-list").querySelector(".state").textContent,"채택");
  assert.doesNotMatch(w.document.body.textContent,/PRIVATE-RESEARCH-CANARY|UI-PRIVATE-CANARY/);
});

test("a stale PO screen cannot accept another editor's proposal until refreshed", async()=>{
  const reviewer = await screen("po"), editor = await screen("po");
  const conv = await reviewer.request("/api/conversations", {title:"동시 편집 합성 검증"});
  await reviewer.request("/api/chat", {conversation_id:conv.id, message:"소재 리포트 개선"});
  const proposal = await reviewer.request("/api/proposals", {conversation_id:conv.id});
  await click(reviewer.w.document.querySelector("[data-page='prd']"));
  const staleCard = reviewer.$("proposal-list").lastElementChild;
  const changedText = "다른 PO가 정정한 합성 요구사항";
  await editor.request("/api/proposals/update", {
    proposal_id:proposal.id, expected_version:proposal.version,
    changes:proposal.changes.map(c=>({...c, after:changedText})),
  });
  await click(byText(staleCard,"제안으로 채택"));
  assert.match(reviewer.$("notice").textContent,/제안이 변경됐습니다/);
  const unchanged = (await editor.request("/api/prds")).find(p=>p.id===conv.prd_id);
  assert.equal(unchanged.version, proposal.target_prd_version);
  assert.equal((await editor.request("/api/proposals")).find(p=>p.id===proposal.id).state,"draft");
  await click(reviewer.w.document.querySelector("[data-page='prd']"));
  const refreshedCard = reviewer.$("proposal-list").lastElementChild;
  assert.equal(refreshedCard.querySelector('[data-field="after"]').value,changedText);
  await click(byText(refreshedCard,"제안으로 채택"));
  const applied = (await editor.request("/api/prds")).find(p=>p.id===conv.prd_id);
  assert.equal(applied.version,proposal.target_prd_version+1);
  assert.ok(applied.sections.some(s=>s.text.includes(changedText)));
});

test("PO completes the five-stage FGI and checks an external PRD without storing its text", async()=>{
  const {w,$,request}=await screen("po");
  await click(w.document.querySelector("[data-page='studies']"));
  $("study-new-title").value="DOM 합성 연구";
  await submit($("study-create"));
  const field=key=>$("study-detail").querySelector(`[data-field="${key}"]`);
  field("study_objective").value="소재 리포트 분석";
  field("study_questions").value="판단 근거를 어떻게 확인하나요?";
  await submit($("study-detail").querySelector("form"));
  assert.match($("study-detail").querySelector('[aria-current="step"]').textContent,/리크루팅/);
  field("study_criteria").value="소재 리포트 담당 광고주 · 합성 모집";
  field("study_personas").options[0].selected=true;
  await submit($("study-detail").querySelector("form"));
  await click(byText($("study-detail"),"AI로 가이드 생성"));
  assert.equal($("study-detail").querySelectorAll('[data-field^="guide_text_"]').length,8);
  assert.equal(byText($("study-detail"),"검토한 가이드로 세션 시작"),undefined);
  await submit(field("guide_text_0").closest("form"));
  await click(byText($("study-detail"),"검토한 가이드로 세션 시작"));
  await click(byText($("study-detail"),"세션 열기"));
  $("message-input").value="소재 리포트의 근거는 무엇인가요?";
  await submit($("chat-form"));
  assert.equal($("messages").querySelectorAll(".persona").length,1,$("notice").textContent);
  await click(w.document.querySelector("[data-page='studies']"));
  await click(byText($("study-detail"),"디브리프 생성"));
  field("study_review_summary").value="검토 결과는 실제 고객에게 추가 확인한다.";
  await submit(field("study_review_summary").closest("form"));
  await click(byText($("study-detail"),"현재 검토본으로 스터디 완료"));
  assert.match($("study-detail").textContent,/완료 스터디/);
  assert.equal(byText($("study-detail"),"세션 열기"),undefined);
  const study=(await request("/api/studies")).studies.find(r=>r.title==="DOM 합성 연구");
  const exported=await request("/api/export/"+study.conversation_id+"?format=markdown");
  assert.match(exported.text,/디브리프 체크리스트/);
  assert.equal(byText($("study-detail"),"검토본으로 PRD 변경 제안 생성"),undefined);
  await click(byText($("study-detail"),"완료 FGI로 후속 분석 대화 시작"));
  await click(w.document.querySelector("[data-page='prd']"));
  const evidence=await request("/api/evidence");
  $("citation-check-text").value=`PRIVATE-PASTED-PRD [${evidence[0].id}] [${evidence[0].id}] v999 [INS-ADS-UNKNOWN]`;
  await submit($("citation-check-form"));
  assert.match($("citation-check-result").textContent,/인용 3회.*서로 다른 근거 2개.*유효 근거 1개/);
  assert.match($("citation-check-result").textContent,/버전 불일치/);
  assert.match($("citation-check-result").textContent,/접근 불가/);
  assert.doesNotMatch(JSON.stringify(await request("/api/prds")),/PRIVATE-PASTED-PRD/);
  $("project-title").value="FGI 독립 프로젝트";
  await submit($("project-create"));
  for(const id of ["study-detail","study-list","citation-check-result","persona-archive-list"])
    assert.equal($(id).textContent,"",id);
  assert.equal($("citation-check-text").value,"");
});

test("unavailable persona profiles can be archived without exposing their old text", async()=>{
  const {w,$,request}=await screen("owner");
  const evidence=(await request("/api/evidence")).find(e=>e.kind==="insight");
  const person=await request("/api/personas",{name:"접근불가프로필",segment:"합성",goals:"숨겨져야할목표",constraints:"합성",assumptions:["가정"],evidence_ids:[evidence.id]});
  await request("/api/insights/release",{insight_id:evidence.id,published:false});
  const before=(await request("/api/bootstrap")).persona_pool_count;
  await click(w.document.querySelector("[data-page='personas']"));
  assert.doesNotMatch($("persona-archive-list").textContent,/숨겨져야할목표|접근불가프로필/);
  await click(byText($("persona-archive-list"),person.id));
  assert.equal((await request("/api/bootstrap")).persona_pool_count,before-1);
});
