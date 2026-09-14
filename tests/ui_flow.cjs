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
