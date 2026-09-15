"use strict";
// The user's task, editable source context and reviewed results share one navigation.
(() => {
  let context = null, labels = {}, save = null, currentPage = "", serial = 0;
  const titles = { existing_service: "기존 서비스", voc: "실제 VoC", benchmark: "선진사례", fgi_actual: "실제 FGI", fgi: "가상 FGI" };
  const ref = r => ({ kind: r.kind, id: r.id, version: r.version });
  const named = r => r.title || "가상 FGI 디브리프";
  const category = r => r.category || (r.kind === "debrief" ? "fgi" : "리서치");
  const synthetic = r => r.is_synthetic || r.contains_synthetic || r.evidence_type === "synthetic";
  function input(root, label, value = "", type = "textarea") {
    const id = "flow-" + (++serial), e = node(type === "textarea" ? "textarea" : "input");
    e.id = id;
    if (type !== "textarea") e.type = type;
    e.value = value;
    const l = node("label", label); l.htmlFor = id; root.append(l, e);
    return e;
  }
  function checks(root, rows, selected = [], render = null) {
    const chosen = new Set(selected), boxes = [];
    for (const r of rows) {
      const box = node("div", null, "flow-choice"), label = node("label"), check = node("input");
      check.type = "checkbox"; check.checked = chosen.has(r.id); check.value = r.id;
      label.append(check, node("span", named(r) + (r.version ? " · v" + r.version : "")));
      box.append(label); render?.(box, r); root.append(box); boxes.push([r, check]);
    }
    return () => boxes.filter(([, e]) => e.checked).map(([r]) => r);
  }
  function setSave(fn, hint) {
    save = fn;
    $("flow-save").disabled = !fn;
    $("flow-save-state").textContent = hint || (fn ? "변경 내용을 저장하세요" : "현재 작업을 선택하세요");
  }
  $("flow-save").onclick = guard(async () => {
    if (["studies", "voc", "chat", "research-workspace"].includes(currentPage)) await window.researchFlowPage(currentPage);
    const action = save;
    if (!action) return;
    $("flow-save-state").textContent = "저장 중…";
    try { const verified = await action(); if (verified !== false) $("flow-save-state").textContent = "저장 완료"; }
    catch (e) { $("flow-save-state").textContent = "저장하지 못했습니다"; throw e; }
  });
  function upload(root, purpose, done) {
    const form = node("form", null, "flow-upload"), file = input(form, "문서 추가 · 파일당 3MB, 한 번에 최대 10개", "", "file");
    file.accept = ".docx,.pdf,.md,.txt,.pptx"; file.multiple = true; file.required = true;
    const nature = node("select"); nature.setAttribute("aria-label", "문서 자료 성격");
    fillSelect(nature, [["UNVERIFIED", "미확인 자료"], ["OBSERVED", "실제 관측 자료"], ["SYNTHETIC", "합성 시연 자료"], ["MIXED", "실제·합성 혼합"]]);
    form.append(nature); saveButton(form, "문서 업로드"); root.append(form);
    form.onsubmit = guard(async () => {
      if (file.files.length > 10) throw new Error("문서는 한 번에 10개 이하로 선택하세요.");
      const failures = [];
      for (const f of file.files) {
        try {
          if (f.size > 3 * 1024 * 1024) throw new Error("파일은 3MB 이하이어야 합니다.");
          const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.onerror = reject; r.readAsDataURL(f); });
          await api("/api/planning-assets", { filename: f.name, title: f.name, content_base64: data, purpose, source_nature: nature.value });
        } catch (e) { failures.push(f.name + ": " + e.message); }
      }
      await done(); notice(failures.length ? failures.join("\n") : "문서를 저장했습니다. 분석할 문서를 선택하세요.", !!failures.length);
    });
  }
  async function history(root, r) {
    const versions = await api("/api/research-results/history/" + r.id);
    root.replaceChildren();
    for (const v of versions) {
      const details = node("details"); details.append(node("summary", "v" + v.version + " · " + (v.review_reason || "초안")), node("pre", v.text)); root.append(details);
    }
  }
  function resultDetails(root, row, edit = false) {
    root.append(node("small", (titles[category(row)] || category(row)) + " · " + (synthetic(row) ? "합성 가설 · 고객 검증 아님" : "검토본 · 출처와 한계 확인")));
    const details = node("details"); details.append(node("summary", "내용·근거 확인"), node("pre", row.text)); root.append(details);
    if (row.kind === "research_result") {
      const versions = node("div"); details.append(button("보고서 버전 이력", () => history(versions, row)), versions);
      if (edit && row.category === "existing_service") details.append(button("Edit · 문맥과 인터뷰로 수정", async () => {
        context = await api("/api/service-contexts/edit", { result_id: row.id, expected_version: row.version }); await page("baseline");
      }));
    }
  }
  async function selectionPanel(root, onlyCategory = null) {
    const generation = state.generation, data = await api("/api/research-inputs");
    if (generation !== state.generation || !root.isConnected) return;
    root.replaceChildren(node("h3", onlyCategory ? "완료 전에 PRD 참고 보고서 선택" : "PRD에 사용할 리서치 결과"),
      node("p", "본문과 자료 성격을 확인한 뒤 사용할 결과의 버전을 선택하세요. 선택하지 않은 결과는 새 PRD 입력에 포함하지 않습니다."));
    const selected = data.selection?.refs || [], rows = data.results.filter(r => !onlyCategory || category(r) === onlyCategory);
    if (!rows.length) root.append(node("p", "아직 검토해 저장한 결과가 없습니다."));
    const choose = checks(root, rows, selected.map(r => r.id), (box, r) => {
      resultDetails(box, r, true);
      const old = selected.find(x => x.id === r.id);
      if (old && old.version !== r.version) box.append(node("p", "선택 이후 내용이 변경됐습니다. 새 버전을 확인하고 다시 저장하세요.", "warning"));
    });
    root.append(button("선택한 버전을 PRD 입력으로 저장", async () => {
      const keep = onlyCategory ? selected.filter(r => !data.results.some(x => x.id === r.id && category(x) === onlyCategory)) : [];
      await api("/api/research-inputs", { refs: [...keep, ...choose().map(ref)], ...(data.selection ? { expected_version: data.selection.version } : {}) });
      await selectionPanel(root, onlyCategory); notice("PRD 참고 결과와 버전을 저장했습니다.");
    }, "primary"));
  }
  window.researchInputPanel = root => selectionPanel(root);
  async function serviceWorkspace() {
    const root = $("baseline-workspace"), data = await api("/api/service-contexts"); if(currentPage!=="baseline")return; labels = data.fields;
    if (context) context = data.contexts.find(r => r.id === context.id) || null;
    root.replaceChildren(node("div", "문서 읽기 → 서비스 설명 확인 → 인터뷰로 보완 → 보고서 저장 → PRD 참고 선택", "flow-steps"));
    const existing = data.contexts.filter(r => r.state === "editing");
    const intake = node("details", null, "panel"); intake.open = !context;
    intake.append(node("summary", "기존 서비스 문서와 진행 중인 분석")); root.append(intake);
    upload(intake, "existing_service", serviceWorkspace);
    const assets = (await api("/api/planning-assets")).filter(a => a.purpose === "existing_service" && a.media_type === "document");
    if(currentPage!=="baseline")return;
    const selected = checks(intake, assets);
    const intent = input(intake, "이번 분석에서 확인할 내용", "기존 서비스의 현재 기능·사용자·업무 흐름·제약을 충실히 요약하고 확인할 질문을 정리하세요.");
    intake.append(button("선택 문서에서 서비스 내용 추출", async () => {
      notice("문서에서 서비스 설명을 추출 중입니다.");
      context = await api("/api/service-contexts/extract", { asset_refs: selected().map(r => ({ id: r.id, version: r.version })), prompt: intent.value });
      await serviceWorkspace(); notice("추출한 내용을 서비스 필드에 채웠습니다. 원문과 비교하고 인터뷰로 보완하세요.");
    }, "primary"));
    for (const r of existing) intake.append(button("편집 계속: " + (r.fields.product_name || "서비스 분석") + " · v" + r.version, async () => { context = r; await serviceWorkspace(); }));
    if (context?.state === "editing") {
      const grid = node("div", null, "flow-service-grid"), left = node("form", null, "panel"), right = node("section", null, "panel flow-interview"); grid.append(left, right); root.append(grid);
      left.append(node("h2", "기존 서비스 설명"));
      const fields = {};
      for (const [key, label] of Object.entries(labels)) {
        fields[key] = input(left, label, context.fields[key] || "", ["product_name", "one_line", "document_status"].includes(key) ? "text" : "textarea");
        fields[key].name = key;
        const origin = context.origins.find(o => o.field === key);
        if (origin) left.append(node("small", origin.origin === "document" ? "문서에서 추출 · " + origin.reason : "PO가 보충한 설명 · 문서의 관측 사실과 구분"));
      }
      const saveContext = async () => {
        context = await api("/api/service-contexts/save", { context_id: context.id, expected_version: context.version,
          fields: Object.fromEntries(Object.entries(fields).map(([k, e]) => [k, e.value])) });
      };
      saveButton(left, "서비스 설명 저장"); left.onsubmit = guard(async () => { await saveContext(); notice("서비스 설명을 저장했습니다."); });
      setSave(saveContext, "서비스 설명 편집 중 · 상단 저장으로 보존");
      right.append(node("h2", "문서 맥락을 확인하는 인터뷰"), node("p", context.fields.summary));
      if (context.questions.length) { const list = node("ul"); context.questions.forEach(q => list.append(node("li", q))); right.append(node("h3", "확인할 질문"), list); }
      const transcript = node("div", null, "flow-transcript");
      for (const turn of context.turns) transcript.append(node("strong", "PO"), node("p", turn.question), node("strong", "인터뷰어"), node("p", turn.answer));
      right.append(transcript);
      if (context.pending_updates.length) {
        const proposed = node("div", null, "flow-proposals"); proposed.append(node("h3", "필드 변경안 · 반영할 항목 선택")); right.append(proposed);
        const rows = context.pending_updates.map(u => ({ ...u, id: u.field, title: labels[u.field] }));
        const choose = checks(proposed, rows, rows.map(r => r.id), (box, r) => box.append(node("pre", "현재: " + context.fields[r.field] + "\n변경안: " + r.value), node("p", r.reason)));
        proposed.append(button("선택한 변경 적용 · 나머지는 제외", async () => {
          await saveContext(); context = await api("/api/service-contexts/apply", { context_id: context.id, expected_version: context.version, fields: choose().map(r => r.field), reason: "PO가 인터뷰 변경안을 검토함" });
          await serviceWorkspace(); notice("선택한 필드에 인터뷰 내용을 반영했습니다.");
        }));
      } else {
        const form = node("form"), message = input(form, "확인하거나 추가할 서비스 설명"); message.required = true; saveButton(form, "인터뷰 보내기"); right.append(form);
        form.onsubmit = guard(async () => {
          await saveContext(); context = await api("/api/service-contexts/interview", { context_id: context.id, expected_version: context.version, message: message.value });
          await serviceWorkspace(); notice("답변과 필드 변경안을 확인하세요.");
        });
      }
      const reportTitle = input(right, "보고서 제목", context.fields.product_name + " · 기존 서비스 분석", "text");
      right.append(button("보고서 생성 · 검토본 저장", async () => {
        await saveContext(); const report = await api("/api/service-contexts/complete", { context_id: context.id, expected_version: context.version, title: reportTitle.value, reason: "문서와 인터뷰 내용을 PO가 확인함" });
        context = null; await serviceWorkspace(); notice("보고서 “" + report.title + "” v" + report.version + "을 리서치 결과함에 저장했습니다. 아래에서 PRD 참고 자료를 선택하세요.");
      }, "primary"));
    } else setSave(null, "문서를 분석하거나 저장된 보고서의 Edit를 선택하세요");
    const reports = node("section", null, "panel"); root.append(reports); await selectionPanel(reports, "existing_service");
  }
  async function personaWorkspace() {
    $("generate-persona")?.closest(".panel")?.setAttribute("hidden", "");
    let root = $("persona-draft-workspace");
    if (!root) { root = node("section", null, "panel"); root.id = "persona-draft-workspace"; $("page-personas").prepend(root); }
    root.replaceChildren(node("h2", "고객 자료로 페르소나 초안 만들기"), node("p", "고객 조사 보고서·서베이 자료·심층 인터뷰 자료를 선택하고 원하는 프로필을 설명하세요. 서로 다른 집단은 여러 초안으로 제시하며 선택한 초안만 풀에 등록합니다."));
    upload(root, "customer_research", personaWorkspace);
    const assets = (await api("/api/planning-assets")).filter(a => a.purpose === "customer_research" && a.media_type === "document");
    const docs = checks(root, assets), profile = input(root, "원하는 persona profile · 역할·행태·목표·제약");
    const sources = node("details"); sources.append(node("summary", "추가 고객 근거 선택")); root.append(sources);
    const chooseEvidence = checks(sources, state.evidence.filter(e => e.kind === "voc" && e.evidence_type === "real"));
    root.append(button("페르소나 초안 제안받기", async () => {
      await api("/api/persona-candidates/generate", { profile: profile.value, asset_refs: docs().map(r => ({ id: r.id, version: r.version })), evidence_ids: chooseEvidence().map(r => r.id) });
      await personaWorkspace(); notice("초안의 차이와 근거를 비교한 후 등록할 프로필을 선택하세요.");
    }, "primary"));
    const batches = await api("/api/persona-candidates");
    for (const batch of batches.filter(b => b.state === "draft")) {
      const panel = node("section", null, "flow-proposals"); panel.append(node("h3", batch.profile), node("p", batch.rationale)); root.append(panel);
      const choose = checks(panel, batch.candidates.map((c, index) => ({ ...c, id: String(index), title: c.name, index })), [], (box, c) => {
        box.append(node("p", c.segment + "\n목표: " + c.goals + "\n제약: " + c.constraints), node("p", "가정: " + c.assumptions.join(" · ")));
        for (const o of c.observations) box.append(node("blockquote", o.quote));
      });
      panel.append(button("선택한 초안만 페르소나 풀에 등록", async () => {
        await api("/api/persona-candidates/adopt", { batch_id: batch.id, expected_version: batch.version, indices: choose().map(r => r.index) });
        await refresh(); await personaWorkspace(); notice("선택한 프로필을 등록했습니다. FGI 참여자로 선택할 수 있습니다.");
      }));
    }
    if(currentPage==="personas")setSave(null, "초안을 선택한 뒤 등록 버튼을 사용하세요");
  }
  window.researchFlowPage = async name => {
    currentPage = name;
    $("flow-project").textContent = $("project-name").textContent;
    $("flow-stage").textContent = (["definition", "prd"].includes(name) ? "2 PRD 작성" : name === "prototype" ? "3 프로토타입" : name === "uat" ? "4 UAT" : "1 리서치") + " / " + $("page-title").textContent;
    setSave(null);
    if (name === "studies" && !$("targeting-demo")) {
      const demo = node("details", null, "panel"); demo.id = "targeting-demo";
      demo.append(node("summary", "타겟팅 기능 개선 FGI 시연"), node("p", "미리 작성한 합성 시나리오입니다. 실제 쿠팡애즈 기능·고객 조사 결과가 아닙니다. 설계, 참여자, 발언, 디브리프를 저장된 예시로 살펴봅니다."), button("합성 FGI 시연 불러오기", async () => {
        const study = await api("/api/studies/demo", {}); state.studyId = study.id; await refresh(); await renderStudies(); await window.researchFlowPage("studies");
      })); $("page-studies").prepend(demo);
    }
    if (name === "baseline") return serviceWorkspace();
    if (name === "personas") return personaWorkspace();
    if (name === "results") return selectionPanel($("results-workspace"));
    if (["prototype", "uat"].includes(name)) {
      let panel = $("page-" + name).querySelector(".flow-inputs");
      if (!panel) { panel = node("div", null, "panel flow-inputs"); $("page-" + name).append(panel); }
      return selectionPanel(panel);
    }
    if (["studies", "voc", "chat", "research-workspace"].includes(name)) {
      const root = $("page-" + name), forms = [...root.querySelectorAll("form")];
      const form = name === "studies" ? ([...$("study-detail").querySelectorAll("form")].at(-1) || forms.find(f => f.querySelector('[name="result-title"]'))) : name === "research-workspace" ? root.querySelector("form") : forms.find(f => f.querySelector('[name="result-title"]'));
      if (form) setSave(async () => { if (form.reportValidity()) form.requestSubmit(); $("flow-save-state").textContent = "저장 결과는 화면 알림에서 확인하세요"; return false; }, "현재 작업의 편집 내용을 저장");
    }
  };
  const updateCollector = () => {
    const provider = $("review-provider").value;
    for (const [id, visible] of [["apple-app-id",provider === "apple_public_rss"],["review-country",provider === "apple_public_rss"],["review-pages",provider !== "s3_import"],["android-package",provider === "android_publisher"],["review-s3-key",provider === "s3_import"]]) {
      const field = $(id); field.hidden = !visible; field.disabled = !visible; field.required = visible;
      document.querySelector(`label[for="${id}"]`).hidden = !visible;
    }
  };
  $("review-provider").onchange = updateCollector; updateCollector();
  window.researchFlowRefresh = () => { if(currentPage==="studies"&&!$("page-studies").hidden)window.researchFlowPage("studies"); };
  window.researchFlowReset = () => { context = null; labels = {}; currentPage = ""; setSave(null); $("persona-draft-workspace")?.replaceChildren(); };
})();
