"use strict";
// Real Chromium + HTTP + SQLite; provider remains an explicit test double.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { chromium } = require("playwright");
let server, origin, browser;
before(async () => {
  server = spawn(process.env.PYTHON || "python3", ["-m", "tests.dom_server"], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("server startup timeout")),
      15000,
    );
    server.stdout.once("data", (b) => {
      clearTimeout(timer);
      resolve(b.toString().trim());
    });
    server.once("error", reject);
  });
  browser = await chromium.launch({ headless: true, ...(process.env.NPD_TEST_BROWSER_CHANNEL ? {channel:process.env.NPD_TEST_BROWSER_CHANNEL} : {}) });
});
after(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    const done = once(server, "exit");
    server.kill();
    await done;
  }
});
async function login(role, viewport = { width: 1440, height: 1000 }) {
  const ctx = await browser.newContext({ viewport, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(origin);
  await page.fill("#email", role + "@example.test");
  await page.fill(
    "#password",
    role === "owner" ? "Owner-test-pass!" : "Planner-test-pass!",
  );
  await page.locator("#login button").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  return { ctx, page, errors };
}
async function ask(page, text) {
  await page.fill("#message-input", text);
  const done = page.waitForResponse(
    (r) => r.url().endsWith("/api/chat") && r.request().method() === "POST",
  );
  await page.click("#send-message");
  const r = await done;
  assert.equal(r.status(), 201, await r.text());
  await page.waitForFunction(
    () => !document.querySelector("#send-message").disabled,
  );
}
async function nav(page, name) {
  await page.locator('.sidebar [data-page="' + name + '"]').click();
  await page.locator("#page-" + name).waitFor({ state: "visible" });
}

test("browser: only owner can request a model probe and failures never look connected", async () => {
  const po = await login("po");
  assert.equal(await po.page.locator("#model-owner-tools").isVisible(), false);
  await po.ctx.close();
  const owner = await login("owner");
  assert.equal(await owner.page.locator("#model-owner-tools").isVisible(), true);
  await owner.page.click("#test-bedrock");
  await owner.page.waitForFunction(() => document.querySelector("#notice").textContent.includes("Bedrock 제공자"));
  assert.equal(await owner.page.locator("#test-bedrock").isEnabled(), true);
  assert.deepEqual(owner.errors, []);
  await owner.ctx.close();
});

test("browser: eight stored personas keep the desktop composer within reach", async () => {
  const { ctx, page, errors } = await login("po");
  try {
    await page.evaluate(async () => {
      const boot = await (await fetch("/api/bootstrap")).json();
      const evidence = await (await fetch("/api/evidence")).json();
      for (let i = boot.personas.length; i < 8; i++) {
        const response = await fetch("/api/personas", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": boot.user.csrf },
          body: JSON.stringify({ name: "화면검증광고주" + i, segment: "긴 업무 제약을 가진 가상 광고주",
            goals: "소재 리포트 이해", constraints: "복수 계정과 승인 업무", assumptions: ["합성 UI 검증"],
            evidence_ids: [evidence[0].id] }),
        });
        if (!response.ok) throw new Error("Persona setup failed");
      }
    });
    await page.reload();
    await page.locator("#workspace").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelectorAll("#chat-personas button").length === 8);
    const bounds = await page.locator("#send-message").boundingBox();
    assert.ok(bounds.y + bounds.height <= 1000, "Eight personas must not push the composer below the viewport");
    assert.ok(await page.locator(".evidence-panel").evaluate((el) => el.scrollHeight > el.clientHeight));
    await page.locator("#make-debrief").scrollIntoViewIfNeeded();
    assert.ok(await page.locator("#make-debrief").isVisible());
    assert.deepEqual(errors, []);
  } finally {
    await ctx.close();
  }
});

test("browser: PO decisions, group challenge, debrief, real PRD version and Markdown download", async () => {
  const { ctx, page, errors } = await login("po");
  try {
    await page.fill(
      "#decision-text",
      "자동 교체를 제외하고 소재 판단 근거를 제공한다.",
    );
    await page.locator("#decision-form button").click();
    await page
      .locator("#decision-list")
      .getByText("자동 교체를 제외하고 소재 판단 근거를 제공한다.")
      .waitFor();
    const state = page.waitForResponse((r) =>
      r.url().endsWith("/api/conversations/state"),
    );
    await page.selectOption("#round-type", "challenge");
    assert.equal((await state).status(), 201);
    await ask(
      page,
      "@소규모광고주 @대행사운영자 소재 리포트의 추천을 언제 거부하시겠어요?",
    );
    assert.equal(await page.locator("#messages .persona").count(), 2);
    await page.click("#make-debrief");
    await page.locator("#debrief-list form").waitFor();
    for (const label of [
      "공통 요구",
      "의견 차이",
      "기획 가설",
      "근거 부족",
      "실제 고객 확인 질문",
    ])
      assert.ok(
        (await page.locator("#debrief-list").textContent()).includes(label),
      );
    await nav(page, "chat");
    await page.click("#make-proposal");
    await page.locator("#proposal-list fieldset").waitFor();
    await page
      .locator("#proposal-list")
      .getByRole("button", { name: "PRD에 반영할 제안으로 채택" })
      .click();
    await page.waitForFunction(() =>
      document.querySelector("#notice").textContent.includes("실제 반영"),
    );
    assert.match(await page.locator("#prd-list").textContent(), /v2/);
    await nav(page, "chat");
    const download = page.waitForEvent("download");
    await page.click("#export-chat");
    const d = await download;
    const stream = await d.createReadStream();
    let text = "";
    for await (const b of stream) text += b.toString();
    assert.match(d.suggestedFilename(), /\.md$/);
    assert.match(text, /자동 교체를 제외/);
    assert.match(text, /\[[0-9a-f-]{36}\]/);
    assert.doesNotMatch(text, /PRIVATE-RESEARCH-CANARY/);
    assert.deepEqual(errors, []);
  } finally {
    await ctx.close();
  }
});

test("browser: owner file extraction and batch publication, then PO sees only released insights", async () => {
  const { ctx, page, errors } = await login("owner");
  try {
    await nav(page, "research");
    await page.fill("#source-title", "BROWSER 연구");
    await page.setInputFiles("#research-file", {
      name: "browser-private.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("BROWSER-PRIVATE-CANARY\n소재 리포트 가설"),
    });
    await page.locator("#research-upload button").click();
    const source = page
      .locator("#source-list .record")
      .filter({ hasText: "BROWSER 연구" });
    await source.waitFor();
    await source.getByRole("button", { name: "AI 인사이트 초안 추출" }).click();
    const insight = page
      .locator("#insight-list .record")
      .filter({ hasText: "추출 테스트" });
    await insight.waitFor();
    assert.match(await insight.textContent(), /비공개/);
    await insight.locator("input[type=checkbox]").check();
    await page.click("#release-selected");
    await page.waitForFunction(() =>
      [...document.querySelectorAll("#insight-list .record")].some(
        (r) =>
          r.textContent.includes("추출 테스트") &&
          r.textContent.includes("PO 활용 허용"),
      ),
    );
    await source
      .getByRole("button", { name: "원문 확인", exact: true })
      .click();
    await page.locator("#raw-panel").waitFor({ state: "visible" });
    assert.match(
      await page.locator("#raw-content").textContent(),
      /BROWSER-PRIVATE-CANARY/,
    );
    await page.click("#close-raw");
    assert.equal(await page.locator("#raw-content").textContent(), "");
    const po = await login("po");
    try {
      assert.doesNotMatch(
        await po.page.locator("body").textContent(),
        /BROWSER-PRIVATE-CANARY/,
      );
      assert.equal(await po.page.locator("#research-nav").isVisible(), false);
    } finally {
      await po.ctx.close();
    }
    assert.deepEqual(errors, []);
  } finally {
    await ctx.close();
  }
});

test("browser: @ autocomplete, persona created through chat, project isolation and safe no-evidence response", async () => {
  const { ctx, page, errors } = await login("po");
  try {
    await page.selectOption("#chat-action", "create_persona");
    await ask(
      page,
      "소재 리포트 근거가 필요한 광고주 페르소나를 만들어 주세요.",
    );
    await nav(page, "personas");
    await page.locator("#persona-draft-workspace .flow-proposals input[type=checkbox]").first().check();
    await page.getByRole("button", {name:"선택한 초안만 페르소나 풀에 등록"}).first().click();
    await page.locator("#persona-list").getByText("@생성광고주 · v1").waitFor();
    await nav(page, "chat");
    await page.selectOption("#chat-action", "ask");
    await page.fill("#message-input", "@생");
    await page.locator("#mentions button").waitFor({ state: "visible" });
    await page.locator("#mentions button").click();
    assert.match(await page.inputValue("#message-input"), /@생성광고주/);
    await page.locator(".sidebar details summary").click();
    await page.fill("#project-title", "브라우저 독립 프로젝트");
    await page.locator("#project-create button").click();
    await page.waitForFunction(
      () =>
        document.querySelector("#project-name").textContent ===
        "브라우저 독립 프로젝트",
    );
    assert.equal(await page.locator("#persona-list").textContent(), "");
    assert.equal(await page.locator("#raw-content").textContent(), "");
    await ask(page, "이 서비스의 방향은?");
    assert.match(await page.locator("#messages").textContent(), /근거/);
    assert.equal(await page.locator("#messages .persona").count(), 0);
    assert.deepEqual(errors, []);
  } finally {
    await ctx.close();
  }
});

test("browser: responsive workspace and text rendering do not execute markup", async () => {
  const { ctx, page, errors } = await login("owner", {
    width: 390,
    height: 844,
  });
  try {
    await page.locator(".sidebar details summary").click();
    await page.fill("#project-title", "<img src=x onerror=alert(1)>");
    await page.locator("#project-create button").click();
    await page.waitForFunction(() =>
      document.querySelector("#project-name").textContent.includes("<img"),
    );
    assert.equal(await page.locator("#project-name img").count(), 0);
    const sizes = await page.evaluate(() => ({
      width: innerWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    assert.ok(sizes.scroll <= sizes.width + 2, JSON.stringify(sizes));
    assert.deepEqual(errors, []);
  } finally {
    await ctx.close();
  }
});

test("browser: owner approval activates a deferred persona for PO tagging", async () => {
  const owner = await login("owner");
  try {
    await nav(owner.page, "research");
    const row = owner.page.locator("#persona-template-list .record").filter({hasText: "검토프로필"});
    await row.getByText("검토프로필 · 근거 공개 필요").waitFor();
    const insight = owner.page.locator("#insight-list .record").filter({hasText: "템플릿 검토 근거"});
    await insight.getByRole("button", {name: "이 인사이트 공개", exact: true}).click();
    await row.getByRole("button", {name: "검토한 프로필 활성화"}).click();
    await row.getByText("검토프로필 · 활성화됨").waitFor();
    assert.equal(await row.getByRole("button", {name: "검토한 프로필 활성화"}).count(), 0);
    assert.deepEqual(owner.errors, []);
  } finally { await owner.ctx.close(); }
  const po = await login("po");
  try {
    const tag = po.page.locator("#chat-personas button").filter({hasText: "검토프로필"});
    await tag.click();
    assert.match(await po.page.inputValue("#message-input"), /@검토프로필/);
    await ask(po.page, "@검토프로필 소재 리포트 비교 조건을 말씀해 주세요.");
    assert.deepEqual(po.errors, []);
  } finally { await po.ctx.close(); }
});

test("browser: FGI guide review, completed study export and private citation input on mobile", async () => {
  const {ctx, page, errors} = await login("po", {width:390, height:844});
  try {
    await nav(page, "studies");
    await page.fill("#study-new-title", "브라우저 합성 FGI");
    await page.locator("#study-create button").click();
    const field = key => page.locator('#study-detail [data-field="'+key+'"]');
    await field("study_objective").fill("소재 리포트 분석");
    await field("study_questions").fill("판단 근거는 무엇인가요?");
    const designSaved=page.waitForResponse(r=>r.url().endsWith("/api/studies/update")&&r.request().method()==="POST");
    await page.getByRole("button", {name:"설계 저장 · 리크루팅으로", exact:true}).click();
    const designResponse=await designSaved;assert.equal(designResponse.status(),201,await designResponse.text());
    await page.getByRole("button", {name:"리크루팅 저장 · 가이드로", exact:true}).waitFor();
    await field("study_criteria").fill("리포트 담당 광고주 · 가상 모집");
    const first = await field("study_personas").locator("option").first().getAttribute("value");
    await field("study_personas").selectOption(first);
    await page.getByRole("button", {name:"리크루팅 저장 · 가이드로", exact:true}).click();
    await page.getByRole("button", {name:"AI로 가이드 생성", exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[data-field="guide_text_0"]').value.length>0);
    assert.equal(await page.getByRole("button", {name:"검토한 가이드로 세션 시작", exact:true}).count(),0);
    await page.getByRole("button", {name:"가이드 검토·저장", exact:true}).click();
    await page.getByRole("button", {name:"검토한 가이드로 세션 시작", exact:true}).click();
    await page.getByRole("button", {name:"세션 열기", exact:true}).click();
    await ask(page, "소재 리포트 분석 근거는 무엇인가요?");
    await nav(page, "studies");
    await page.getByRole("button", {name:"디브리프 생성", exact:true}).click();
    await field("study_review_summary").fill("실제 고객에게 근거를 추가 확인한다.");
    await page.getByRole("button", {name:"검토본 확정 · 기획 기준으로 사용", exact:true}).click();
    await page.getByRole("button", {name:"현재 검토본으로 스터디 완료", exact:true}).click();
    await page.getByText("검토본을 확정한 완료 스터디입니다.").waitFor();
    assert.equal(await page.getByRole("button", {name:"세션 열기", exact:true}).count(),0);
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button", {name:"Markdown 패키지 내보내기", exact:true}).click();
    const download = await downloaded;
    const chunks=[];
    for await(const chunk of await download.createReadStream()) chunks.push(chunk);
    const exported=Buffer.concat(chunks).toString("utf8");
    assert.match(exported,/디브리프 체크리스트/);
    assert.match(exported,/실제 고객에게 근거를 추가 확인한다/);
    await nav(page,"prd");
    await page.fill("#citation-check-text","<script>window.injection=true</script> [INS-ADS-MISSING]");
    await page.locator("#citation-check-form button").click();
    await page.getByText("미확인 또는 접근 불가",{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>window.injection),undefined);
    const sizes=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(sizes.scroll<=sizes.width+2,JSON.stringify(sizes));
    await page.click("#logout");
    await page.locator("#login-screen").waitFor({state:"visible"});
    assert.equal(await page.inputValue("#citation-check-text"),"");
    assert.equal(await page.locator("#study-detail").textContent(),"");
    assert.deepEqual(errors,[]);
  } finally { await ctx.close(); }
});

test("browser: manual story confirmation preserves exact exported version and draft edits", async()=>{
  const {ctx,page,errors}=await login("po");
  try {
    await page.click('[data-stage="definition"]');
    await page.getByRole("button",{name:"2.2 사용자스토리정의",exact:true}).click();
    await page.locator('[name="actor"]').fill("UI 합성 광고주");
    await page.locator('[name="action"]').fill("기간 조건을 확인한다");
    await page.locator('[name="value"]').fill("비교 오류를 피한다");
    await page.locator('[name="problem"]').fill("기간 조건이 명확하지 않음");
    await page.locator('[name="new_problem"]').check();
    await page.locator('[name="validation_task"]').fill("실제 광고주에게 검증");
    await page.getByRole("button",{name:"수용 기준 추가",exact:true}).click();
    await page.locator('[name="given"]').fill("지표 화면을 열었을 때");
    await page.locator('[name="when"]').fill("기간을 변경하면");
    await page.locator('[name="then"]').fill("선택한 기간을 표시한다");
    await page.getByRole("button",{name:"초안 저장",exact:true}).click();
    await page.getByRole("button",{name:"이 버전 설계안 확정",exact:true}).click();
    await page.getByRole("button",{name:"구조화 설계안 내보내기",exact:true}).waitFor();
    const [download]=await Promise.all([page.waitForEvent("download"),page.getByRole("button",{name:"구조화 설계안 내보내기",exact:true}).click()]);
    const fs=require("node:fs/promises"),payload=JSON.parse(await fs.readFile(await download.path(),"utf8"));
    assert.equal(payload.schema_version,"npd.story-package.v1");
    assert.equal(payload.stories[0].customer_validation,"unverified");assert.equal(payload.stories[0].acceptance_criteria[0].then,"선택한 기간을 표시한다");
    await page.locator('[name="value"]').fill("PO가 수정한 가치");
    await page.getByRole("button",{name:"초안 저장",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.planning-status')?.textContent.includes('설계: draft'));
    await page.screenshot({path:"test-results/stage2-manual-desktop.png",fullPage:true});
    assert.deepEqual(errors,[]);
  } finally {await ctx.close();}
});

test("browser: each AI draft asks for PO intent and creates an independent document", async()=>{
  const {ctx,page,errors}=await login("po");
  try {
    await page.click('[data-stage="definition"]');
    for(const intent of ["UI 광고주 관점 A","UI 운영자 관점 B"]){
      await page.getByRole("button",{name:"AI초안작성",exact:true}).click();
      await page.locator("#draft-prompt-dialog").waitFor({state:"visible"});
      await page.fill("#draft-prompt",intent);
      await page.locator('#draft-prompt-form button[type="submit"]').click();
      await page.locator("#draft-prompt-dialog").waitFor({state:"hidden"});
      assert.equal(await page.locator('[name="document-title"]').inputValue(),intent);
    }
    assert.ok(await page.getByRole("button",{name:/UI 광고주 관점 A · v1 · draft/}).count());
    assert.ok(await page.getByRole("button",{name:/UI 운영자 관점 B · v1 · draft/}).count());
    assert.deepEqual(errors,[]);
  } finally {await ctx.close();}
});

test("browser: mobile planning separates source editing and review without horizontal overflow", async()=>{
  const {ctx,page,errors}=await login("po",{width:390,height:844});
  try {
    await page.click('[data-stage="definition"]');
    await page.getByRole("button",{name:"2.2 사용자스토리정의",exact:true}).click();
    await page.locator('[name="actor"]').waitFor({state:"visible"});
    await page.getByRole("button",{name:"원본",exact:true}).click();
    await page.locator('[name="asset-file"]').waitFor({state:"visible"});
    await page.getByRole("button",{name:"검토·확정",exact:true}).click();
    await page.getByRole("button",{name:"AI초안작성 · 새 후보/재분석",exact:true}).waitFor({state:"visible"});
    const size=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(size.scroll<=size.width+1,JSON.stringify(size));
    await page.screenshot({path:"test-results/stage2-mobile.png",fullPage:true});
    assert.deepEqual(errors,[]);
  } finally {await ctx.close();}
});

test("browser: image analysis creates review questions and links extracted fields to source regions", async()=>{
  const {ctx,page,errors}=await login("po");
  try {
    await page.click('[data-stage="definition"]');
    await page.getByRole("button",{name:"2.2 사용자스토리정의",exact:true}).click();
    await page.locator('[name="asset-title"]').fill("합성 원본 영역 테스트");
    await page.setInputFiles('[name="asset-file"]',{name:"synthetic-sketch.png",mimeType:"image/png",buffer:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACgAAAAUCAIAAABwJOjsAAAANUlEQVR4nO3NwQEAIAwCMWT/nfFrF7g+JAvkJNEGr6xqDDKZvRpjzFVTY4y5amqMMVfp8/gCAeEDJZQenysAAAAASUVORK5CYII=","base64")});
    await page.getByRole("button",{name:"기획 자료 업로드",exact:true}).click();
    await page.getByRole("button",{name:"원본 분석 · AI초안작성",exact:true}).click();
    await page.fill("#draft-prompt","글자·물음표·금지 조건을 확인");
    await page.locator('#draft-prompt-form button[type="submit"]').click();
    await page.locator("#draft-prompt-dialog").waitFor({state:"hidden"});
    await page.getByRole("button",{name:"r1 · text · 광고주",exact:true}).waitFor();
    await page.getByRole("button",{name:"이 분석을 스토리 입력에 추가",exact:true}).click();
    await page.getByRole("button",{name:"AI초안작성 · 새 후보/재분석",exact:true}).click();
    await page.fill("#draft-prompt","소재 운영자 관점으로 제안");
    await page.locator('#draft-prompt-form button[type="submit"]').click();
    await page.locator("#draft-prompt-dialog").waitFor({state:"hidden"});
    const candidate=page.locator('.planning-review details').filter({hasText:"소재 운영자 관점으로 제안"}).last();
    await candidate.locator("summary").click();
    await candidate.getByRole("button",{name:"새 스토리 초안으로 채택",exact:true}).click();
    await page.getByRole("button",{name:"원본 추출 · 원본 영역 보기",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[name="actor"]')?.classList.contains("source-selected"));
    assert.ok((await page.locator('[name="text"]').evaluateAll(nodes=>nodes.map(n=>n.value))).includes("자동 변경은 금지인가?"));
    await page.screenshot({path:"test-results/stage2-image.png",fullPage:true});
    assert.deepEqual(errors,[]);
  } finally {await ctx.close();}
});

async function planningPost(page, path, action){
  const done=page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==="POST");
  await action();const response=await done;assert.equal(response.status(),201,await response.text());return response.json();
}
async function planningIntent(page,label,intent,path){
  await page.getByRole("button",{name:label,exact:true}).click();await page.fill("#draft-prompt",intent);
  const result=await planningPost(page,path,()=>page.locator('#draft-prompt-form button[type="submit"]').click());
  await page.locator("#draft-prompt-dialog").waitFor({state:"hidden"});return result;
}
async function planningImage(page,title,color){
  const data=await page.evaluate(color=>{const c=document.createElement("canvas");c.width=80;c.height=40;const g=c.getContext("2d");g.fillStyle=color;g.fillRect(0,0,80,40);return c.toDataURL("image/png").split(",")[1];},color);
  await page.locator('[name="asset-title"]').fill(title);
  await page.setInputFiles('[name="asset-file"]',{name:"synthetic-"+color+".png",mimeType:"image/png",buffer:Buffer.from(data,"base64")});
  return planningPost(page,"/api/planning-assets",()=>page.getByRole("button",{name:"기획 자료 업로드",exact:true}).click());
}
test("browser: selected crop, immutable region edits and explicit image ordering reach story generation",async()=>{
  const {ctx,page,errors}=await login("po");
  try {
    await page.click('[data-stage="definition"]');await page.getByRole("button",{name:"2.2 사용자스토리정의",exact:true}).click();
    await planningImage(page,"합성 빨간 원본","red");
    await page.waitForFunction(()=>document.querySelector('.source-scroll canvas')?.width===80);
    await page.getByRole("button",{name:"분석 영역 드래그 선택",exact:true}).click();
    const canvasBox=await page.locator('.source-scroll canvas').boundingBox();
    await page.mouse.move(canvasBox.x+canvasBox.width*.25,canvasBox.y+canvasBox.height*.25);await page.mouse.down();
    await page.mouse.move(canvasBox.x+canvasBox.width*.75,canvasBox.y+canvasBox.height*.75);await page.mouse.up();
    assert.equal(await page.locator('[name="crop-x"]').inputValue(),"0.25");assert.equal(await page.locator('[name="crop-width"]').inputValue(),"0.5");
    for(const [key,value] of [["x","0.25"],["y","0.25"],["width","0.5"],["height","0.5"]])await page.locator('[name="crop-'+key+'"]').fill(value);
    await page.getByRole("button",{name:"분석 영역 좌표 적용",exact:true}).click();
    await page.getByRole("button",{name:"원본 회전 90°",exact:true}).click();
    const original=await planningIntent(page,"원본 분석 · AI초안작성","선택 영역 분석","/api/planning-assets/extract");
    assert.deepEqual(original.view.crop,[.25,.25,.5,.5]);assert.equal(original.view.rotation,90);assert.deepEqual(original.regions[0].bbox,[.25,.5,.25,.25]);
    const regionEditor=page.locator('details').filter({has:page.locator('summary').filter({hasText:/^영역·전사 직접 편집$/})});
    await regionEditor.locator("summary").first().click();
    await regionEditor.locator('[name="coords"]').fill("0.1,0.2,0.3,0.4");
    await page.locator('[name="region-note"]').fill("PO가 글자 경계를 직접 검토");
    const edited=await planningPost(page,"/api/planning-assets/regions",()=>page.getByRole("button",{name:"새 영역 검토본 저장",exact:true}).click());
    assert.notEqual(edited.id,original.id);assert.deepEqual(edited.regions[0].bbox,[.1,.2,.3,.4]);
    await page.getByRole("button",{name:"이 분석을 스토리 입력에 추가",exact:true}).click();
    await planningImage(page,"합성 파란 원본","blue");
    const second=await planningIntent(page,"원본 분석 · AI초안작성","두 번째 원본 분석","/api/planning-assets/extract");
    await page.getByRole("button",{name:"이 분석을 스토리 입력에 추가",exact:true}).click();
    await page.locator('.analysis-order-entry').last().getByRole("button",{name:"입력 위로",exact:true}).click();
    assert.deepEqual(await page.locator('.analysis-order-entry').evaluateAll(rows=>rows.map(r=>r.dataset.extractionId)),[second.id,edited.id]);
    const draft=await planningIntent(page,"AI초안작성 · 새 후보/재분석","합성 파란 원본 우선 비교","/api/story-drafts");
    assert.deepEqual(draft.extraction_ids,[second.id,edited.id]);assert.equal(draft.stories[0].provenance.actor.asset_id,second.asset_id);
    await page.screenshot({path:"test-results/stage2-source-order.png",fullPage:true});assert.deepEqual(errors,[]);
  } catch(error){console.error("Planning failure notice:",await page.locator("#notice").textContent());await page.screenshot({path:"test-results/planning-failure-"+Date.now()+".png",fullPage:true});throw error;} finally {await ctx.close();}
});
test("browser: original replacement opens explicit recovery preserving PO edits and historical source",async()=>{
  const {ctx,page,errors}=await login("po");
  try {
    await page.click('[data-stage="definition"]');await page.getByRole("button",{name:"2.2 사용자스토리정의",exact:true}).click();
    const asset=await planningImage(page,"합성 교체 대상 원본","green");
    const run=await planningIntent(page,"원본 분석 · AI초안작성","교체 전 분석","/api/planning-assets/extract");
    await page.getByRole("button",{name:"이 분석을 스토리 입력에 추가",exact:true}).click();
    const draft=await planningIntent(page,"AI초안작성 · 새 후보/재분석","교체 전 스토리 합성","/api/story-drafts");
    const candidate=page.locator('.planning-review details').filter({hasText:"교체 전 스토리 합성"}).last();await candidate.locator("summary").click();
    const story=await planningPost(page,"/api/story-drafts/apply",()=>candidate.getByRole("button",{name:"새 스토리 초안으로 채택",exact:true}).click());
    await page.locator('[name="value"]').fill("보존할 PO 수정 가치");
    const poEdit=await planningPost(page,"/api/stories/update",()=>page.getByRole("button",{name:"초안 저장",exact:true}).click());
    await page.locator('[name="planning-source"]').selectOption(asset.id);
    await page.getByText("원본 교체 · 이전 버전 보존",{exact:true}).click();
    await page.setInputFiles('[name="revision-file"]',{name:"synthetic-revision.md",mimeType:"text/markdown",buffer:Buffer.from("변경된 원본: 자동 변경을 허용하지 않음")});
    await planningPost(page,"/api/planning-assets",()=>page.getByRole("button",{name:"원본 새 버전 저장",exact:true}).click());
    await page.getByRole("button",{name:"재검토 필요 · "+story.id.slice(0,12),exact:true}).click();
    await page.getByRole("button",{name:"이전·현재 원본 비교",exact:true}).click();
    await page.getByText("변경된 원본: 자동 변경을 허용하지 않음",{exact:true}).waitFor();
    await page.locator('[name="source-review-note"]').fill("이전 그림과 현재 변경 조건을 비교");await page.locator('[name="source-review-ack"]').check();
    const recovered=await planningPost(page,"/api/stories/recover",()=>page.getByRole("button",{name:"PO 편집을 유지한 재검토 초안 저장",exact:true}).click());
    assert.equal(recovered.id,story.id);assert.equal(recovered.version,poEdit.version+1);assert.equal(recovered.definition_status,"draft");
    assert.equal(await page.locator('[name="value"]').inputValue(),"보존할 PO 수정 가치");assert.equal(recovered.source_refs.find(r=>r.id===asset.id).version,2);
    assert.equal(recovered.provenance.actor.asset_version,1);assert.equal(recovered.provenance.actor.historical_source,true);assert.equal(recovered.provenance.actor.extraction_id,run.id);
    assert.ok(recovered.questions.some(q=>q.critical&&q.status==="unanswered"&&q.id.startsWith("source-review-")));
    await page.getByRole("button",{name:"원본 추출 · 원본 영역 보기",exact:true}).click();await page.locator('canvas[aria-label="이전 원본 v1"]').waitFor();
    await page.screenshot({path:"test-results/stage2-source-recovery.png",fullPage:true});assert.deepEqual(errors,[]);
  } catch(error){console.error("Planning failure notice:",await page.locator("#notice").textContent());await page.screenshot({path:"test-results/planning-failure-"+Date.now()+".png",fullPage:true});throw error;} finally {await ctx.close();}
});

test("browser: scoped research forms preserve PO edits, select a pack and show readiness gaps on mobile",async()=>{
  const {ctx,page,errors}=await login("po");
  try{
    await page.locator(".sidebar details summary").filter({hasText:"프로젝트 추가"}).click();
    await page.fill("#project-title","Synthetic Stage 1 UI");
    await page.locator("#project-create button").click();
    await page.waitForFunction(()=>document.querySelector("#project-name").textContent.includes("Synthetic Stage 1 UI"));
    await nav(page,"research-workspace");
    await page.getByRole("button",{name:"새 조사 브리프",exact:true}).click();
    await page.getByLabel("산출물 제목",{exact:true}).fill("Synthetic research brief");
    for(const label of ["제품·시장·대상 범위","지금 결정할 질문","제외 범위와 이유","결정 책임자","필요 시점·단계"]){
      await page.getByLabel(label+" · 검토 시 필수",{exact:true}).fill("Synthetic UI fixture; not actual research");
    }
    await page.getByLabel("수행할 조사 작업",{exact:true}).selectOption(["RS-00"]);
    await page.getByLabel("변경·검토 이유",{exact:true}).fill("Synthetic contract review");
    let response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items")&&r.request().method()==="POST");
    await page.getByRole("button",{name:"초안 저장",exact:true}).click();
    assert.equal((await response).status(),201);
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items/review"));
    await page.getByRole("button",{name:"내용 저장 후 검토 완료",exact:true}).click();
    assert.equal((await response).status(),201);
    await page.getByLabel("작성할 산출물",{exact:true}).selectOption("hypothesis");
    await page.getByRole("button",{name:"새 산출물",exact:true}).click();
    await page.getByLabel("산출물 제목",{exact:true}).fill("Synthetic hypothesis draft");
    await page.getByLabel("적용 여부",{exact:true}).selectOption("not_applicable");
    await page.getByLabel("해당 없음의 이유",{exact:true}).fill("This UI fixture does not assert customer research.");
    await page.getByLabel("변경·검토 이유",{exact:true}).fill("Synthetic test of review consistency");
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items")&&r.request().method()==="POST");
    await page.getByRole("button",{name:"초안 저장",exact:true}).click();
    assert.equal((await response).status(),201);
    await page.getByLabel("해당 없음의 이유",{exact:true}).fill("");
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items/review"));
    await page.getByRole("button",{name:"내용 저장 후 검토 완료",exact:true}).click();
    assert.equal((await response).status(),409);
    await page.locator("#notice").filter({hasText:"해당 없음의 이유가 필요합니다."}).waitFor();
    await page.getByLabel("해당 없음의 이유",{exact:true}).fill("This UI fixture does not assert customer research.");
    await page.getByLabel("산출물 제목",{exact:true}).fill("PO edit immediately before review");
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items/review"));
    await page.getByRole("button",{name:"내용 저장 후 검토 완료",exact:true}).click();
    const reviewed=await response;assert.equal(reviewed.status(),201);
    assert.equal((await reviewed.json()).title,"PO edit immediately before review");
    await page.getByRole("button",{name:"편집 닫기",exact:true}).click();
    await page.getByLabel("묶음 제목",{exact:true}).fill("Synthetic research pack");
    await page.getByRole("button",{name:"검토 산출물 모두 선택",exact:true}).click();
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/packs"));
    await page.getByRole("button",{name:"선택한 버전으로 묶음 저장",exact:true}).click();
    assert.equal((await response).status(),201);
    await page.locator("#research-workspace details summary").filter({hasText:"Synthetic research pack"}).click();
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/select"));
    await page.getByRole("button",{name:"PRD 작성에 이 버전 사용",exact:true}).click();assert.equal((await response).status(),201);
    await page.locator("#notice").filter({hasText:"이 연구 묶음을 후속 기획의 기준으로 선택했습니다."}).waitFor();
    await page.locator("#research-workspace details summary").filter({hasText:"Synthetic research pack"}).click();
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/gate"));
    await page.getByRole("button",{name:"연구 준비 검사",exact:true}).click();
    const checked=await response;assert.equal(checked.status(),201);assert.equal((await checked.json()).ready,false);
    await page.getByText("보완이 필요합니다",{exact:true}).waitFor();
    await page.screenshot({path:"test-results/stage1-research-desktop.png",fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:"test-results/stage1-research-mobile.png",fullPage:true});
    assert.deepEqual(errors,[]);
  }catch(error){console.error("Stage 1 notice:",await page.locator("#notice").textContent());await page.screenshot({path:"test-results/stage1-failure.png",fullPage:true});throw error;}finally{await ctx.close();}
});

test("browser: source nature, detailed baseline interview and ratio rows survive real HTTP review",async()=>{
  const {ctx,page,errors}=await login("po");
  try{
    await nav(page,"baseline");
    await page.getByRole("combobox",{name:"문서 자료 성격"}).selectOption("SYNTHETIC");
    await page.getByLabel("문서 추가 · 파일당 3MB, 한 번에 최대 10개",{exact:true}).setInputFiles({name:"synthetic-nature.md",mimeType:"text/markdown",buffer:Buffer.from("Synthetic UI document only")});
    let response=page.waitForResponse(r=>r.url().endsWith("/api/planning-assets")&&r.request().method()==="POST");
    await page.getByRole("button",{name:"문서 업로드",exact:true}).click();
    const uploaded=await response;assert.equal(uploaded.status(),201);assert.equal((await uploaded.json()).source_nature,"SYNTHETIC");
    await page.getByRole("checkbox",{name:/synthetic-nature.md/}).waitFor();
    await page.getByLabel("문서 추가 · 파일당 3MB, 한 번에 최대 10개",{exact:true}).setInputFiles([
      {name:"valid.md",mimeType:"text/markdown",buffer:Buffer.from("Synthetic valid batch input")},
      {name:"unsupported.bin",mimeType:"application/octet-stream",buffer:Buffer.from("Unsupported fixture")},
      {name:"another.txt",mimeType:"text/plain",buffer:Buffer.from("Synthetic valid second input")}
    ]);
    await page.getByRole("button",{name:"문서 업로드",exact:true}).click();
    await page.getByRole("checkbox",{name:/valid.md/}).waitFor();
    await page.getByRole("checkbox",{name:/another.txt/}).waitFor();
    await page.locator("#notice").getByText(/unsupported.bin/).waitFor();
    // Seed only prerequisite records through the authenticated API; exercise interview and numeric editing in UI.
    const seeded=await page.evaluate(async()=>{
      const contracts=await api("/api/research-workspace");
      const fill=schema=>Object.fromEntries(Object.entries(schema).map(([key,s])=>[key,s.type==="rows"?[]:s.type==="lines"?["RS-00"]:s.type==="checkbox"?false:s.type==="number"?null:s.type==="select"?s.options[0]:"Synthetic prerequisite; unknown unless specified"]));
      async function reviewed(body){let row=await api("/api/research-workspace/items",body);return api("/api/research-workspace/items/review",{item_id:row.id,expected_version:row.version,reason:"Synthetic UI prerequisite review"});}
      const brief=await reviewed({output_type:"brief",title:"Synthetic interview UI scope",fields:fill(contracts.types.brief)});
      const fields=fill(contracts.types.service_baseline);fields.detail_level="STRUCTURED";
      for(const [key,s] of Object.entries(contracts.types.service_baseline))if(s.type==="rows"&&s.required)fields[key]=[{...fill(s.columns),id:key+"-1"}];
      fields.open_topics=[{id:"Q1",question:"Synthetic future option?",owner:"PO",needed_stage:"DEVELOPMENT",next_action:"Interview the PO"}];
      const baseline=await reviewed({output_type:"service_baseline",task_id:"SV-02",research_id:brief.id,title:"Synthetic detailed baseline",fields});
      const evidence=await reviewed({output_type:"evidence",research_id:brief.id,title:"Synthetic calculation source",fields:{...fill(contracts.types.evidence),evidence_type:"ASSUMPTION"}});
      return {brief,baseline,evidence};
    });
    await nav(page,"research-workspace");
    await page.getByLabel("조사 범위",{exact:true}).selectOption(seeded.brief.id);
    await page.getByRole("button",{name:"Synthetic detailed baseline · v2 · 검토 완료",exact:true}).click();
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/interview/start"));
    await page.getByRole("button",{name:"미결 질문으로 PO 인터뷰 준비",exact:true}).click();
    assert.equal((await response).status(),201);
    await page.getByLabel("인터뷰 실행 · 검토 시 필수",{exact:true}).selectOption("EXECUTED");
    await page.getByLabel("인터뷰 자료 성격 · 검토 시 필수",{exact:true}).selectOption("SYNTHETIC");
    await page.getByLabel("답변 상태",{exact:true}).selectOption("ANSWERED");
    for(const [label,value] of [["답변","Synthetic wish, not existing behavior"],["답변자 역할","Synthetic PO"],["확인 날짜","2026-01-03"],["변경 적용일·미확인 이유","Future date undecided"],["적용 대상·예외","Synthetic scope only"],["추가 근거·없음·추가 확인","No actual interview evidence; UI test"],["검토할 반영 문장","Proposed future feature"]])await page.getByLabel(label,{exact:true}).fill(value);
    await page.getByLabel("문서와의 관계",{exact:true}).selectOption("FUTURE_REQUEST");
    await page.getByLabel("반영할 분석 항목",{exact:true}).selectOption("proposed");
    await page.getByLabel("변경·검토 이유",{exact:true}).fill("Synthetic UI review");
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items/review"));
    await page.getByRole("button",{name:"내용 저장 후 검토 완료",exact:true}).click();
    const interview=await response;assert.equal(interview.status(),201,await interview.text());
    await page.getByLabel("반영할 확인 답변",{exact:true}).selectOption(["Q1"]);
    await page.getByLabel("반영 이유",{exact:true}).fill("Synthetic selective future proposal");
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/interview/apply"));
    await page.getByRole("button",{name:"선택한 답변으로 별도 개정안 작성",exact:true}).click();
    const applied=await response;assert.equal(applied.status(),201,await applied.text());
    const revised=await applied.json();assert.notEqual(revised.id,seeded.baseline.id);assert.equal(revised.state,"draft");assert.ok(revised.fields.proposed.includes("향후 희망사항"));
    await page.getByLabel("작성할 산출물",{exact:true}).selectOption("calculation");
    await page.getByRole("button",{name:"새 산출물",exact:true}).click();
    await page.getByLabel("산출물 제목",{exact:true}).fill("Synthetic aggregate ratio");
    await page.getByLabel("계산 방식 · 검토 시 필수",{exact:true}).selectOption("RATIO_OF_SUMS");
    await page.getByLabel("입력 자료 상태 · 검토 시 필수",{exact:true}).selectOption("SYNTHETIC");
    for(const label of ["분자의 의미·단위","분모의 의미·단위","결과 단위","대상·집계 단위","같은 측정 기간·시간대","합산 가능한 이유·중복 제외","귀속·관측 기간·잠정 여부","검토 책임자"])await page.getByLabel(label+" · 검토 시 필수",{exact:true}).fill("Synthetic test context");
    for(let i=0;i<2;i++){
      await page.getByRole("button",{name:"합산할 분자와 분모 행 추가",exact:true}).click();
      for(const [label,value] of [["입력 ID","N"+i],["분자 값",i?"300":"200"],["분모 값",i?"300":"100"],["선택한 근거 ID",seeded.evidence.id],["원문 위치","Synthetic table row "+i]])await page.getByLabel(label,{exact:true}).nth(i).fill(value);
    }
    await page.getByLabel("참조할 검토 자료·원본",{exact:true}).selectOption([seeded.evidence.id]);
    await page.getByLabel("변경·검토 이유",{exact:true}).fill("Synthetic ratio review");
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items")&&r.request().method()==="POST");
    await page.getByRole("button",{name:"초안 저장",exact:true}).click();assert.equal((await response).status(),201);
    await page.getByText(/합산 분자 500 \/ 합산 분모 400 · 결과 1.25/).waitFor();
    response=page.waitForResponse(r=>r.url().endsWith("/api/research-workspace/items/review"));
    await page.getByRole("button",{name:"내용 저장 후 검토 완료",exact:true}).click();assert.equal((await response).status(),201);
    await page.getByRole("button",{name:"버전 이력 보기",exact:true}).click();
    await page.locator("#research-workspace pre").first().waitFor();
    await page.screenshot({path:"test-results/detailed-research-desktop.png",fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1&&e.getBoundingClientRect().width>0).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,20))));
    await page.screenshot({path:"test-results/detailed-research-mobile.png",fullPage:true});
    assert.deepEqual(errors,[]);
  }catch(error){console.error("Detailed research notice:",await page.locator("#notice").textContent());await page.screenshot({path:"test-results/detailed-research-failure.png",fullPage:true});throw error;}finally{await ctx.close();}
});


test("browser: document context, selective interview, report Edit and PRD input selection",async()=>{
 const {ctx,page,errors}=await login("po");
 try {
  await page.locator(".sidebar details summary").click();await page.fill("#project-title","문서 인터뷰 검증");await page.locator("#project-create button").click();
  await page.waitForFunction(()=>document.querySelector("#project-name").textContent==="문서 인터뷰 검증");await nav(page,"baseline");
  assert.equal(await page.locator("#flow-project").textContent(),"문서 인터뷰 검증");
  await page.getByLabel("문서 추가 · 파일당 3MB, 한 번에 최대 10개").setInputFiles({name:"context-demo.md",mimeType:"text/markdown",buffer:Buffer.from("Synthetic: advertiser compares conditions; operator checks approval.")});
  await page.getByRole("combobox",{name:"문서 자료 성격"}).selectOption("SYNTHETIC");await page.getByRole("button",{name:"문서 업로드",exact:true}).click();
  await page.getByRole("checkbox",{name:/context-demo.md/}).check();await page.getByRole("button",{name:"선택 문서에서 서비스 내용 추출"}).click();
  await page.getByLabel("제품 이름",{exact:true}).waitFor();assert.equal(await page.getByLabel("제품 이름",{exact:true}).inputValue(),"시연 서비스");
  const details=await page.getByLabel("상세 서비스 설명",{exact:true}).inputValue();
  await page.getByLabel("제품 이름",{exact:true}).fill("검토 제품");await page.click("#flow-save");await page.waitForFunction(()=>document.querySelector("#flow-save-state").textContent==="저장 완료");
  await page.getByLabel("확인하거나 추가할 서비스 설명").fill("향후 조건 저장을 원합니다.");await page.getByRole("button",{name:"인터뷰 보내기"}).click();
  await page.getByRole("button",{name:"선택한 변경 적용 · 나머지는 제외"}).click();
  await page.waitForFunction(()=>document.querySelector('[name="proposed"]')?.value.includes("향후 조건 저장"));assert.equal(await page.getByLabel("상세 서비스 설명",{exact:true}).inputValue(),details);
  await page.getByLabel("보고서 제목",{exact:true}).fill("검토한 서비스 보고서");await page.getByRole("button",{name:"보고서 생성 · 검토본 저장"}).click();
  await page.getByRole("checkbox",{name:/검토한 서비스 보고서/}).check();await page.getByRole("button",{name:"선택한 버전을 PRD 입력으로 저장"}).click();
  await page.waitForFunction(()=>document.querySelector("#notice").textContent.includes("PRD 참고 결과와 버전"));
  await page.locator('[data-stage="definition"]').click();await page.getByRole("heading",{name:"PRD에 사용할 리서치 결과"}).waitFor();
  assert.equal(await page.getByRole("checkbox",{name:/검토한 서비스 보고서/}).isChecked(),true);
  await page.getByRole("button",{name:"AI초안작성",exact:true}).click();await page.locator("#draft-prompt").fill("선택한 서비스 결과로 개선 방향을 정리해 주세요.");
  // Use the existing prompt dialog's submit action.
  await page.locator("#draft-prompt-form button[type=submit]").click();
  await page.getByRole("button",{name:/선택한 서비스 결과로 개선 방향/}).waitFor();
  await nav(page,"results");await page.locator("#results-workspace").getByText("내용·근거 확인",{exact:true}).click();await page.locator("#results-workspace").getByRole("button",{name:"Edit · 문맥과 인터뷰로 수정"}).click();
  await page.getByLabel("제품 이름",{exact:true}).waitFor();assert.equal(await page.getByLabel("제품 이름",{exact:true}).inputValue(),"검토 제품");
  assert.match(await page.locator(".flow-transcript").textContent(),/향후 조건 저장/);
  await page.screenshot({path:"test-results/research-flow-desktop.png",fullPage:true});await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth+1);
  await page.screenshot({path:"test-results/research-flow-mobile.png",fullPage:true});assert.deepEqual(errors,[]);
 }catch(e){console.error("Research flow notice:",await page.locator("#notice").textContent());await page.screenshot({path:"test-results/research-flow-failure.png",fullPage:true});throw e;}finally{await ctx.close();}
});
