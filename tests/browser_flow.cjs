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
  browser = await chromium.launch({ headless: true });
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
  await page.locator('nav [data-page="' + name + '"]').click();
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
    await page.getByRole("button", {name:"설계 저장 · 리크루팅으로", exact:true}).click();
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
