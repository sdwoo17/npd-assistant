"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  user: null,
  csrf: "",
  boot: null,
  evidence: [],
  conversation: null,
  voc: null,
  prds: [],
  sources: [],
  generation: 0,
  pendingChat: null,
};
const busy = new WeakSet();
function node(tag, content, cls) {
  const e = document.createElement(tag);
  if (content != null) e.textContent = content;
  if (cls) e.className = cls;
  return e;
}
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = false;
}
function guard(action) {
  return async (event) => {
    event?.preventDefault?.();
    const target = event?.currentTarget;
    if (target && busy.has(target)) return;
    const button =
      target?.tagName === "BUTTON"
        ? target
        : target?.querySelector?.("button[type='submit']");
    if (target) busy.add(target);
    if (button) button.disabled = true;
    try {
      return await action(event);
    } catch (e) {
      notice(e.message, true);
    } finally {
      if (target) busy.delete(target);
      if (button) button.disabled = false;
    }
  };
}
function button(label, action, cls = "secondary") {
  const b = node("button", label, cls);
  b.type = "button";
  b.onclick = guard(action);
  return b;
}
async function api(path, body) {
  const generation = state.generation;
  const r = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": state.csrf,
          },
          body: JSON.stringify(body),
        },
  );
  const data = await r.json();
  if (generation !== state.generation)
    throw new Error(
      "프로젝트가 변경되어 이전 화면의 응답을 표시하지 않습니다.",
    );
  if (!r.ok) throw new Error(data.error || "요청에 실패했습니다.");
  return data;
}
function fillSelect(select, options, selected) {
  select.replaceChildren(
    ...options.map(([v, t]) => {
      const o = node("option", t);
      o.value = v;
      return o;
    }),
  );
  if (selected != null && options.some(([v]) => v === selected))
    select.value = selected;
}
function featureOptions() {
  return Object.entries(state.boot.features).map(([id, f]) => [id, f.name]);
}
function field(parent, key, label, value = "", type = "input") {
  const wrap = node("label", label);
  const input = node(type);
  input.dataset.field = key;
  input.value = Array.isArray(value) ? value.join("\n") : (value ?? "");
  wrap.append(input);
  parent.append(wrap);
  return input;
}
function values(form) {
  return Object.fromEntries(
    [...form.querySelectorAll("[data-field]")].map((e) => [
      e.dataset.field,
      e.value,
    ]),
  );
}
function saveButton(form, label) {
  const b = node("button", label, "secondary");
  b.type = "submit";
  form.append(b);
}
function lines(value) {
  return value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}
function resetWorkspace() {
  resetStage2();
  state.conversation = null;
  state.evidence = [];
  state.voc = null;
  state.sources = [];
  state.prds = [];
  state.pendingChat = null;
  for (const id of [
    "messages",
    "source-list",
    "insight-list",
    "job-list",
    "raw-content",
    "evidence-detail",
    "persona-list",
    "chat-personas",
    "voc-list",
    "voc-stats",
    "proposal-list",
    "debrief-list",
    "prd-list",
    "decision-list",
  ])
    $(id).replaceChildren();
  $("raw-panel").hidden = true;
}
async function refresh() {
  const [boot, evidence, prds] = await Promise.all([
    api("/api/bootstrap"),
    api("/api/evidence"),
    api("/api/prds"),
  ]);
  state.boot = boot;
  state.user = boot.user;
  state.csrf = boot.user.csrf;
  state.evidence = evidence;
  state.prds = prds;
  $("identity").textContent = state.user.email;
  $("role-name").textContent =
    state.user.role === "owner" ? "지식 소유자" : "상품 기획자 · PO";
  for (const id of ["research-nav", "voc-owner", "taxonomy-owner"])
    $(id).hidden = state.user.role !== "owner";
  const projects = boot.projects?.length
    ? boot.projects
    : [{ id: state.user.project_id, title: state.user.project_id }];
  fillSelect(
    $("project-switch"),
    projects.map((p) => [p.id, p.title]),
    state.user.project_id,
  );
  $("project-name").textContent =
    projects.find((p) => p.id === state.user.project_id)?.title ||
    state.user.project_id;
  $("model-warning").hidden = boot.model_configured;
  $("model-owner-tools").hidden = state.user.role !== "owner";
  $("model-state").textContent = boot.model_configured
    ? boot.model_provider +
      " · " +
      boot.model_name +
      " · " +
      (boot.model_connection_verified ? "호출 확인" : "설정됨 · 호출 미검증")
    : "AI 모델 미설정";
  $("evidence-count").textContent = evidence.length;
  fillSelect(
    $("conversation-select"),
    boot.conversations.map((c) => [c.id, c.title]),
    state.conversation?.id,
  );
  fillSelect($("insight-feature"), featureOptions());
  fillSelect(
    $("persona-evidence"),
    evidence.map((e) => [
      e.id,
      (e.kind === "voc" ? "VoC" : "인사이트") +
        " · " +
        (e.title || e.text).slice(0, 55),
    ]),
  );
  fillSelect(
    $("chat-prd"),
    prds
      .filter((p) => !p.redacted)
      .map((p) => [p.id, p.title + " · v" + p.version]),
    state.conversation?.prd_id,
  );
  const services = [
    ...new Set(Object.values(boot.features).map((f) => f.service_id)),
  ];
  fillSelect(
    $("voc-service"),
    [["", "전체 서비스"], ...services.map((s) => [s, s])],
    $("voc-service").value,
  );
  renderPersonas();
}
async function page(name) {
  if (name === "research" && state.user.role !== "owner") return;
  document
    .querySelectorAll("section[id^='page-']")
    .forEach((e) => (e.hidden = e.id !== "page-" + name));
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.page === name));
  const labels = {
    stage2: ["2. 서비스 요구사항", "기획 원본을 사용자 스토리로 정리하고, PO가 수정·확정하세요."],
    chat: [
      "리서치 채팅",
      "근거를 탐색하고, 가상 광고주를 인터뷰하며 기획 결정을 남기세요.",
    ],
    research: [
      "지식 관리",
      "원문과 공유할 인사이트의 버전·공개 범위를 관리하세요.",
    ],
    voc: [
      "고객 VoC",
      "서비스 기능·기간·광고주 유형별로 고객 요구를 확인하세요.",
    ],
    personas: [
      "페르소나",
      "관찰 근거와 가정을 구분해 가상 광고주를 정의하세요.",
    ],
    prd: [
      "PRD 변경과 디브리프",
      "대화의 결과를 검토하고 실제 PRD 버전에 반영하세요.",
    ],
  };
  $("page-title").textContent = labels[name][0];
  $("page-caption").textContent = labels[name][1];
  if (name === "stage2") await renderStage2();
  if (name === "research") await renderResearch();
  if (name === "voc") await loadVoc();
  if (name === "prd") await renderPlanning();
}
async function showEvidence(id) {
  state.evidence = await api("/api/evidence");
  const e = state.evidence.find((x) => x.id === id);
  $("evidence-detail").replaceChildren();
  if (!e) {
    $("evidence-detail").textContent =
      "근거가 철회됐거나 현재 접근할 수 없습니다.";
    return;
  }
  $("evidence-detail").append(
    node("strong", e.title || "고객 VoC"),
    node("p", e.text),
    node(
      "small",
      e.id +
        " · v" +
        e.version +
        " · " +
        (e.evidence_type === "synthetic"
          ? "합성 자료"
          : e.evidence_type === "real"
            ? "수집 VoC"
            : "리서치 인사이트"),
    ),
  );
  for (const key of [
    "source_name",
    "external_id",
    "occurred_at",
    "applicability",
    "limitations",
  ])
    if (e[key]) $("evidence-detail").append(node("p", key + ": " + e[key]));
}
function references(parent, ids) {
  const bar = node("div", null, "citation-bar");
  (ids || []).forEach((id, i) =>
    bar.append(
      button(
        "근거 " + (i + 1) + " · " + id.slice(0, 6),
        () => showEvidence(id),
        "",
      ),
    ),
  );
  parent.append(bar);
}
function renderMessages() {
  const list = $("messages");
  list.replaceChildren();
  if (!state.conversation?.messages?.length) {
    const e = node("div", null, "empty");
    e.append(
      node("h2", "어떤 서비스를 기획하고 있나요?"),
      node("p", "자료에 질문하고 @태그로 가상 페르소나를 인터뷰하세요."),
    );
    list.append(e);
  } else
    for (const m of state.conversation.messages) {
      const card = node(
        "article",
        null,
        "message" +
          (m.speaker === "PO" ? " user" : m.is_synthetic ? " persona" : ""),
      );
      card.append(
        node(
          "div",
          m.speaker + (m.is_synthetic ? " · 가상 인터뷰" : ""),
          "speaker",
        ),
        node("div", m.text, "bubble"),
      );
      if (m.interpretation_status)
        card.append(node("small", "기획 해석·가설 · PO 검토 필요"));
      references(card, m.evidence_ids);
      if (m.assumptions?.length)
        card.append(
          node("p", "추론·가정: " + m.assumptions.join(" / "), "assumptions"),
        );
      list.append(card);
    }
  list.scrollTop = list.scrollHeight;
  if (!state.conversation) return;
  $("chat-scope").textContent =
    state.conversation.mode === "interview"
      ? "가상 인터뷰 · 실제 고객 검증과 구분"
      : "공유 인사이트와 가명화된 VoC";
  $("chat-mode").value = state.conversation.mode;
  $("round-type").value = state.conversation.round_type || "explore";
  $("chat-prd").value = state.conversation.prd_id;
  $("decision-list").replaceChildren(
    ...(state.conversation.decisions || [])
      .filter((d) => d.active)
      .map((d) => {
        const row = node("div", null, "record");
        row.append(
          node("p", d.text),
          button("결정 철회", async () => {
            state.conversation = await api("/api/conversations/decisions", {
              conversation_id: state.conversation.id,
              decision_id: d.id,
              expected_version: state.conversation.version,
            });
            renderMessages();
          }),
        );
        return row;
      }),
  );
}
async function openConversation(id) {
  state.conversation = await api("/api/conversations/" + id);
  $("conversation-select").value = id;
  renderMessages();
}
async function createConversation(title, mode = "research", ids = []) {
  state.conversation = await api("/api/conversations", {
    title,
    mode,
    persona_ids: ids,
  });
  await refresh();
  await openConversation(state.conversation.id);
}
function insertTag(alias) {
  const input = $("message-input");
  input.value =
    input.value.replace(/@[\p{L}\p{N}_-]*$/u, " ").trimEnd() +
    " @" +
    alias +
    " ";
  $("mentions").hidden = true;
  input.focus();
}
function renderPersonas() {
  $("chat-personas").replaceChildren(
    ...state.boot.personas.map((p) =>
      button(
        "@" + p.alias + " · " + p.segment,
        () => insertTag(p.alias),
        "persona-chip",
      ),
    ),
  );
  $("persona-list").replaceChildren(
    ...state.boot.personas.map((p) => {
      const card = node("article", null, "panel persona-card");
      card.append(
        node("h2", "@" + p.alias + " · v" + p.version),
        node("small", p.segment),
        node("strong", "목표"),
        node("p", p.goals),
        node("strong", "제약"),
        node("p", p.constraints),
        node(
          "p",
          "가정: " +
            (Array.isArray(p.assumptions)
              ? p.assumptions.join(" / ")
              : p.assumptions),
        ),
      );
      const counts = p.grounding_counts || {};
      card.append(
        node(
          "small",
          "리서치 " +
            (counts.research || 0) +
            " · 합성 리서치 " +
            (counts.synthetic_research || 0) +
            " · 실제 VoC " +
            (counts.real_voc || 0) +
            " · 합성 VoC " +
            (counts.synthetic_voc || 0),
        ),
        node(
          "p",
          p.grounding_status === "research_and_real_voc"
            ? "리서치·실제 VoC 연결 · 가상 응답은 별도 검증 필요"
            : "합성 자료 또는 일부 근거에 기반한 가설형 페르소나",
          "micro",
        ),
      );
      for (const observation of p.observations || [])
        card.append(node("blockquote", observation.quote));
      references(card, p.evidence_ids);
      card.append(
        button("이 페르소나 인터뷰하기", async () => {
          await createConversation(p.name + " 인터뷰", "interview", [p.id]);
          await page("chat");
          insertTag(p.alias);
        }),
      );
      const edit = node("details");
      edit.append(node("summary", "페르소나 편집"));
      const form = node("form");
      for (const [key, label, type] of [
        ["name", "이름", "input"],
        ["segment", "광고주 유형", "input"],
        ["goals", "목표", "textarea"],
        ["constraints", "제약", "textarea"],
        ["assumptions", "가정 · 한 줄에 하나", "textarea"],
      ])
        field(form, key, label, p[key], type);
      const select = field(form, "evidence_ids", "근거 선택", "", "select");
      select.multiple = true;
      fillSelect(
        select,
        state.evidence.map((e) => [e.id, (e.title || e.text).slice(0, 60)]),
      );
      [...select.options].forEach(
        (o) => (o.selected = p.evidence_ids.includes(o.value)),
      );
      saveButton(form, "새 버전 저장");
      form.onsubmit = guard(async () => {
        const v = values(form);
        await api("/api/personas/update", {
          ...v,
          persona_id: p.id,
          expected_version: p.version,
          assumptions: lines(v.assumptions),
          evidence_ids: [...select.selectedOptions].map((o) => o.value),
          observations: [],
        });
        await refresh();
        notice("새 버전을 저장했습니다. 기존 인터뷰는 당시 버전을 유지합니다.");
      });
      edit.append(form);
      card.append(
        edit,
        button("버전 이력", async () => {
          const rows = await api("/api/personas/versions/" + p.id);
          const history = node("div");
          rows.forEach((r) =>
            history.append(
              node(
                "p",
                r.redacted
                  ? r.text
                  : "v" + r.version + " · " + r.name + " · " + r.goals,
              ),
            ),
          );
          card.append(history);
        }),
      );
      return card;
    }),
  );
}
async function renderResearch() {
  const templates = await api("/api/assets/persona-templates");
  $("persona-template-list").replaceChildren(...templates.map((t) => {
    const row = node("div", null, "record");
    const labels = {
    stage2: ["2. 서비스 요구사항", "기획 원본을 사용자 스토리로 정리하고, PO가 수정·확정하세요."],ready: "활성화 가능", blocked: "근거 공개 필요", active: "활성화됨", requires_review: "페르소나 근거 재검토 필요"};
    row.append(node("strong", t.definition.name + " · " + labels[t.status]),
      node("p", t.definition.segment + " / " + t.definition.goals),
      node("p", "제약: " + t.definition.constraints),
      node("small", "합성 프로필 · 실제 고객 검증 아님"));
    if (t.status === "ready") row.append(button("검토한 프로필 활성화", async () => {
      await api("/api/assets/activate-personas", {template_ids: [t.id], versions: {[t.id]: t.version}});
      await refresh();
      await renderResearch();
      notice("가상 프로필을 활성화했습니다. 채팅에서 @태그로 선택하세요.");
    }));
    if (t.status === "blocked") row.append(node("small", "현재 사용할 수 없는 근거 " + t.missing_evidence_ids.length + "개"));
    return row;
  }));
  if (!templates.length) $("persona-template-list").append(node("p", "등록된 자산 팩 프로필이 없습니다. 페르소나 화면에서 직접 생성할 수 있습니다."));
  const data = await api("/api/research");
  state.sources = data.sources;
  fillSelect(
    $("insight-source"),
    data.sources.map((s) => [s.id, s.title]),
  );
  fillSelect($("research-revision"), [
    ["", "새 문서"],
    ...data.sources.map((s) => [s.id, s.title + " · v" + s.content_version]),
  ]);
  $("source-list").replaceChildren(
    ...data.sources.map((s) => {
      const row = node("div", null, "record");
      row.append(
        node("strong", s.title),
        node("small", s.filename + " · 원문 v" + (s.content_version || 1)),
        button("원문 확인", async () => {
          const raw = await api("/api/research/raw/" + s.id);
          $("raw-content").textContent = raw.text;
          $("raw-panel").hidden = false;
        }),
        button("AI 인사이트 초안 추출", async () => {
          notice("문서를 청크별로 분석하고 있습니다.");
          try {
            await api("/api/research/extract", { source_id: s.id });
            notice("비공개 초안을 만들었습니다. 편집·검토 후 공개하세요.");
          } finally {
            await renderResearch();
          }
        }),
        button("원문 버전 이력", async () => {
          const versions = await api("/api/research/versions/" + s.id);
          const view = node("div");
          versions.forEach((v) =>
            view.append(
              node(
                "p",
                "v" +
                  (v.content_version || 1) +
                  " · " +
                  v.filename +
                  " · " +
                  v.created_at,
              ),
            ),
          );
          row.append(view);
        }),
      );
      return row;
    }),
  );
  $("insight-list").replaceChildren(
    ...data.insights.map((i) => {
      const row = node("div", null, "record");
      const label = node("label");
      const selected = node("input");
      selected.type = "checkbox";
      selected.dataset.insightId = i.id;
      selected.dataset.version = i.version;
      label.append(selected, node("strong", i.title + " · v" + i.version));
      row.append(
        label,
        node(
          "span",
          i.published ? "PO 활용 허용" : "비공개 초안/철회",
          "state",
        ),
        node("p", i.text),
      );
      const edit = node("details");
      edit.append(node("summary", "인사이트 편집"));
      const form = node("form");
      for (const [key, title, type] of [
        ["title", "제목", "input"],
        ["text", "공유할 주장", "textarea"],
        ["applicability", "적용 조건", "textarea"],
        ["limitations", "한계·예외", "textarea"],
        ["competitor", "경쟁사·서비스", "input"],
        ["observed_at", "관찰일", "input"],
        ["public_url", "공유 가능한 공개 URL", "input"],
      ])
        field(form, key, title, i[key], type);
      const feature = field(form, "feature", "기능", i.feature, "select");
      fillSelect(feature, featureOptions(), i.feature);
      saveButton(form, "수정 후 비공개 초안 저장");
      form.onsubmit = guard(async () => {
        await api("/api/insights/update", {
          ...values(form),
          insight_id: i.id,
          source_id: i.source_id,
          expected_version: i.version,
          evidence_type: i.evidence_type,
        });
        await refresh();
        await renderResearch();
        notice("새 비공개 버전입니다. 검토 후 다시 공개하세요.");
      });
      edit.append(form);
      row.append(
        edit,
        button(i.published ? "공개 철회" : "이 인사이트 공개", async () => {
          await api("/api/insights/release", {
            insight_id: i.id,
            published: !i.published,
            expected_version: i.version,
          });
          await refresh();
          await renderResearch();
          notice(
            i.published
              ? "철회된 근거의 파생 산출물은 재확인이 필요합니다."
              : "공개 인사이트를 PO 채팅에서 활용할 수 있습니다.",
          );
        }),
        button("인사이트 버전 이력", async () => {
          const versions = await api("/api/insights/versions/" + i.id);
          const history = node("div");
          versions.forEach((v) =>
            history.append(
              node(
                "p",
                "v" +
                  v.version +
                  " · " +
                  (v.published ? "공개" : "비공개") +
                  " · " +
                  v.text,
              ),
            ),
          );
          row.append(history);
        }),
      );
      return row;
    }),
  );
  $("job-list").replaceChildren(
    ...data.jobs.map((j) => {
      const row = node("div", null, "record");
      row.append(
        node("span", j.action + " · " + j.status + " · 시도 " + j.attempts),
        node("small", j.progress + " / " + (j.total || "—")),
      );
      if (j.error) row.append(node("p", j.error));
      if (j.status === "failed")
        row.append(
          button("재시도", async () => {
            try {
              await api("/api/jobs/retry", { job_id: j.id });
            } finally {
              await renderResearch();
            }
          }),
        );
      return row;
    }),
  );
}
function vocScope() {
  return {
    date_from: $("voc-date-from").value,
    date_to: $("voc-date-to").value,
    segment: $("voc-segment").value,
    evidence_type: $("voc-evidence-type").value,
    service_id: $("voc-service").value,
    feature: $("voc-filter").value === "all" ? "" : $("voc-filter").value,
  };
}
async function loadVoc() {
  state.voc = await api("/api/voc/analysis", { filters: vocScope() });
  const v = state.voc;
  $("voc-scope").textContent =
    v.scope +
    " 적용 기간: " +
    (v.filters.date_from || "전체") +
    " ~ " +
    (v.filters.date_to || "전체");
  $("voc-stats").replaceChildren(
    ...[
      ["분모 · 전체 VoC", v.total],
      ["실제 수집", v.total - v.synthetic_count],
      ["가상 샘플", v.synthetic_count],
      ["미분류", v.counts.unclassified || 0],
    ].map(([label, count]) => {
      const card = node("div", null, "stat");
      card.append(node("span", label), node("strong", String(count)));
      return card;
    }),
  );
  fillSelect(
    $("voc-filter"),
    [["all", "모든 기능"], ...featureOptions()],
    $("voc-filter").value || "all",
  );
  renderVoc();
}
function renderVoc() {
  const filter = $("voc-filter").value;
  $("voc-list").replaceChildren(
    ...state.voc.records
      .filter(
        (r) =>
          filter === "all" || (r.feature_ids || [r.feature]).includes(filter),
      )
      .map((r) => {
        const card = node("article", null, "voc-card");
        card.append(
          node(
            "small",
            r.segment +
              " · " +
              (r.evidence_type === "synthetic" ? "합성 VoC" : "수집 VoC"),
          ),
          node("p", r.text),
          node(
            "small",
            (r.occurred_at || "일자 미지정") +
              " · " +
              (r.source_name || r.source_type) +
              " · " +
              (r.external_id || "기존 자료: 외부 ID 미기록"),
          ),
        );
        const select = node("select");
        select.setAttribute("aria-label", "VoC 기능 분류");
        fillSelect(select, featureOptions(), r.feature);
        select.onchange = guard(async () => {
          await api("/api/voc/feature", {
            voc_id: r.id,
            feature: select.value,
            expected_version: r.version,
          });
          await refresh();
          await loadVoc();
          notice("서비스 기능 연결을 수정했습니다.");
        });
        card.append(
          select,
          node(
            "small",
            "연결 기능: " +
              (r.feature_ids || [r.feature])
                .map((f) => state.boot.features[f]?.name || f)
                .join(", "),
          ),
        );
        if (r.classification_confidence != null)
          card.append(
            node(
              "small",
              "AI 분류 신뢰도 " +
                Math.round(r.classification_confidence * 100) +
                "% · PO 검토 필요",
            ),
          );
        const edit = node("details");
        edit.append(node("summary", "문제·요구와 기능 보정"));
        const form = node("form");
        field(form, "problem", "문제", r.problem, "textarea");
        field(form, "need", "요구", r.need, "textarea");
        const labels = field(form, "feature_ids", "연결 기능", "", "select");
        labels.multiple = true;
        fillSelect(labels, featureOptions());
        [...labels.options].forEach(
          (o) =>
            (o.selected = (r.feature_ids || [r.feature]).includes(o.value)),
        );
        saveButton(form, "PO 검토 결과 저장");
        form.onsubmit = guard(async () => {
          await api("/api/voc/feature", {
            ...values(form),
            voc_id: r.id,
            expected_version: r.version,
            feature_ids: [...labels.selectedOptions].map((o) => o.value),
          });
          await refresh();
          await loadVoc();
        });
        edit.append(form);
        card.append(edit);
        return card;
      }),
  );
}
async function renderPlanning() {
  await refresh();
  await renderProposals();
  await renderDebriefs();
  renderPrds();
}
async function renderProposals() {
  const proposals = await api("/api/proposals");
  $("proposal-list").replaceChildren(
    ...proposals.map((p) => {
      const card = node("article", null, "panel");
      card.append(
        node("h2", "PRD 변경 제안"),
        node(
          "span",
          { draft: "검토 대기", accepted: "채택", held: "보류" }[p.state],
          "state",
        ),
        node("p", p.text),
        node(
          "small",
          "기준 PRD v" +
            p.target_prd_version +
            (p.applied_prd_version ? " → 반영 v" + p.applied_prd_version : ""),
        ),
        node("p", "미검증 가정: " + p.assumptions.join(" / ")),
      );
      references(card, p.evidence_ids);
      const form = node("form");
      const editors = [];
      for (const change of p.changes || []) {
        const group = node("fieldset");
        group.append(
          node("legend", change.section_title),
          node("p", "변경 전: " + (change.before || "빈 문단")),
        );
        const after = field(
            group,
            "after",
            "변경 후",
            change.after,
            "textarea",
          ),
          rationale = field(
            group,
            "rationale",
            "변경 이유",
            change.rationale,
            "textarea",
          );
        after.disabled = rationale.disabled = p.state === "accepted";
        editors.push({ section_id: change.section_id, after, rationale });
        references(group, change.evidence_ids);
        form.append(group);
      }
      if (p.state !== "accepted") {
        saveButton(form, "제안 문구 수정 저장");
        form.onsubmit = guard(async () => {
          await api("/api/proposals/update", {
            proposal_id: p.id,
            expected_version: p.version,
            changes: editors.map((e) => ({
              section_id: e.section_id,
              after: e.after.value,
              rationale: e.rationale.value,
            })),
          });
          await renderProposals();
        });
      }
      card.append(
        form,
        node("small", "원본 메시지 " + p.source_message_ids.length + "개"),
      );
      const trace = node("details");
      trace.append(node("summary", "PO 결정·메시지·근거 버전"));
      (p.decisions || []).forEach((d) => trace.append(node("p", d.text)));
      p.source_message_ids.forEach((id) =>
        trace.append(
          button("메시지 " + id.slice(0, 8), async () => {
            await openConversation(p.conversation_id);
            await page("chat");
          }),
        ),
      );
      (p.evidence_snapshots || []).forEach((e) =>
        trace.append(node("small", e.id + " v" + e.version)),
      );
      card.append(trace);
      if (p.state !== "accepted")
        for (const [value, label] of [
          ["accepted", "PRD에 반영할 제안으로 채택"],
          ["held", "보류"],
        ])
          card.append(
            button(label, async () => {
              const result = await api("/api/proposals/decision", {
                proposal_id: p.id,
                expected_version: p.version,
                state: value,
              });
              await renderPlanning();
              notice(
                result.applied_prd_version
                  ? "PRD v" +
                      result.applied_prd_version +
                      "에 실제 반영했습니다."
                  : "보류 상태를 저장했습니다.",
              );
            }),
          );
      return card;
    }),
  );
}
async function renderDebriefs() {
  const rows = await api("/api/debriefs");
  const names = {
    common_needs: "공통 요구",
    disagreements: "의견 차이",
    hypotheses: "기획 가설",
    unsupported_claims: "근거 부족",
    followup_questions: "실제 고객 확인 질문",
  };
  $("debrief-list").replaceChildren(
    ...rows.map((d) => {
      const card = node("article", null, "panel");
      card.append(
        node("h3", "가상 FGI 디브리프 · v" + d.version),
        node("p", d.text),
      );
      const form = node("form"),
        edits = {};
      for (const [key, label] of Object.entries(names)) {
        form.append(node("h4", label));
        edits[key] = d[key].map((item) => {
          const group = node("div");
          const input = field(group, key, label, item.text, "textarea");
          references(group, item.evidence_ids);
          form.append(group);
          return { ...item, input };
        });
      }
      saveButton(form, "디브리프 검토·수정 저장");
      form.onsubmit = guard(async () => {
        const groups = Object.fromEntries(
          Object.entries(edits).map(([k, items]) => [
            k,
            items.map(({ input, ...item }) => ({ ...item, text: input.value })),
          ]),
        );
        await api("/api/debriefs/update", {
          ...groups,
          debrief_id: d.id,
          expected_version: d.version,
        });
        await renderDebriefs();
        notice("PO 검토 결과를 저장했습니다.");
      });
      card.append(form);
      return card;
    }),
  );
}
function renderPrds() {
  $("prd-list").replaceChildren(
    ...state.prds.map((p) => {
      const card = node("details", null, "panel");
      card.append(
        node(
          "summary",
          p.redacted ? "재확인 필요 PRD" : p.title + " · v" + p.version,
        ),
      );
      if (p.redacted) {
        card.append(node("p", p.text));
        return card;
      }
      const form = node("form");
      const fields = p.sections.map((s) => ({
        s,
        input: field(form, s.id, s.title, s.text, "textarea"),
      }));
      saveButton(form, "PRD 새 버전 저장");
      form.onsubmit = guard(async () => {
        await api("/api/prds/update", {
          prd_id: p.id,
          expected_version: p.version,
          title: p.title,
          sections: fields.map(({ s, input }) => ({ ...s, text: input.value })),
        });
        await renderPlanning();
        notice("PRD 새 버전을 저장했습니다.");
      });
      card.append(
        form,
        button("PRD 버전 이력", async () => {
          const rows = await api("/api/prds/versions/" + p.id);
          const history = node("div");
          rows.forEach((r) => {
            history.append(node("h4", "v" + r.version));
            if (r.redacted) history.append(node("p", r.text));
            else
              r.sections.forEach((s) =>
                history.append(node("p", s.title + ": " + s.text)),
              );
          });
          card.append(history);
        }),
      );
      return card;
    }),
  );
}
async function fileBody(input) {
  const file = input.files[0];
  if (!file) throw new Error("파일을 선택하세요.");
  if (file.size > 3 * 1024 * 1024)
    throw new Error("파일은 3MB 이하이어야 합니다.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192)
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return { filename: file.name, content_base64: btoa(binary) };
}
async function updateConversation(changes) {
  if (!state.conversation) throw new Error("먼저 대화를 선택하세요.");
  await api("/api/conversations/state", {
    conversation_id: state.conversation.id,
    expected_version: state.conversation.version,
    ...changes,
  });
  await openConversation(state.conversation.id);
}
$("login").onsubmit = guard(async () => {
  try {
    await api("/api/login", {
      email: $("email").value,
      password: $("password").value,
    });
    $("password").value = "";
    await boot();
  } catch (e) {
    $("login-error").textContent = e.message;
  }
});
$("logout").onclick = guard(async () => {
  await api("/api/logout", {});
  resetWorkspace();
  window.location.reload();
});
$("test-bedrock").onclick = guard(async () => {
  notice("Bedrock에 연결 중입니다. 첫 구조화 출력 처리에는 수 분이 걸릴 수 있습니다.");
  try {
    const result = await api("/api/model/test", {});
    await refresh();
    notice("Bedrock 실제 응답 확인 · " + result.region + " · " + result.last_call.seconds + "초. 업무별 답변 품질 검증은 별도입니다.");
  } catch (error) {
    await refresh();
    throw error;
  }
});
$("project-switch").onchange = guard(async () => {
  const result = await api("/api/projects/switch", {
    project_id: $("project-switch").value,
  });
  state.generation++;
  state.csrf = result.csrf;
  resetWorkspace();
  await boot();
});
$("project-create").onsubmit = guard(async () => {
  const project = await api("/api/projects", {
    title: $("project-title").value,
  });
  const result = await api("/api/projects/switch", { project_id: project.id });
  state.generation++;
  state.csrf = result.csrf;
  resetWorkspace();
  await boot();
});
$("member-form").onsubmit = guard(async () => {
  await api("/api/projects/members", {
    email: $("member-email").value,
    role: $("member-role").value,
  });
  notice("등록된 계정에 이 프로젝트 접근 권한을 추가했습니다.");
});
document
  .querySelectorAll("nav button")
  .forEach((b) => (b.onclick = guard(() => page(b.dataset.page))));
$("new-conversation").onclick = () => {
  $("new-conversation-form").hidden = !$("new-conversation-form").hidden;
};
$("create-conversation").onclick = guard(async () => {
  await createConversation($("conversation-title").value);
  $("new-conversation-form").hidden = true;
});
$("conversation-select").onchange = guard(() =>
  openConversation($("conversation-select").value),
);
$("chat-form").onsubmit = guard(async () => {
  const input = $("message-input"),
    b = $("send-message");
  b.disabled = true;
  b.textContent = "근거를 바탕으로 답변 중…";
  try {
    if (!state.conversation) await createConversation(input.value.slice(0, 50));
    const signature =
      state.conversation.id + "|" + $("chat-action").value + "|" + input.value;
    if (!state.pendingChat || state.pendingChat.signature !== signature)
      state.pendingChat = { signature, id: crypto.randomUUID() };
    state.conversation = await api("/api/chat", {
      conversation_id: state.conversation.id,
      message: input.value,
      action: $("chat-action").value,
      request_id: state.pendingChat.id,
    });
    state.pendingChat = null;
    input.value = "";
    $("mentions").hidden = true;
    renderMessages();
    await refresh();
  } finally {
    b.disabled = false;
    b.textContent = "질문 보내기 ↑";
  }
});
$("message-input").oninput = () => {
  const match = $("message-input").value.match(/@([\p{L}\p{N}_-]*)$/u);
  if (!match) {
    $("mentions").hidden = true;
    return;
  }
  const options = state.boot.personas.filter((p) =>
    p.alias.toLowerCase().includes(match[1].toLowerCase()),
  );
  $("mentions").replaceChildren(
    ...options.map((p) =>
      button("@" + p.alias + " · " + p.segment, () => insertTag(p.alias)),
    ),
  );
  $("mentions").hidden = !options.length;
};
$("message-input").onkeydown = (e) => {
  if (e.key === "Escape") $("mentions").hidden = true;
  if (e.key === "ArrowDown" && !$("mentions").hidden) {
    e.preventDefault();
    $("mentions").querySelector("button")?.focus();
  }
};
document.querySelectorAll("[data-question]").forEach(
  (b) =>
    (b.onclick = () => {
      $("message-input").value = b.dataset.question;
      $("message-input").focus();
    }),
);
$("go-personas").onclick = guard(() => page("personas"));
$("chat-mode").onchange = guard(() =>
  updateConversation({ mode: $("chat-mode").value }),
);
$("round-type").onchange = guard(() =>
  updateConversation({ round_type: $("round-type").value }),
);
$("chat-prd").onchange = guard(() =>
  updateConversation({ prd_id: $("chat-prd").value }),
);
$("decision-form").onsubmit = guard(async () => {
  if (!state.conversation) throw new Error("먼저 대화를 선택하세요.");
  state.conversation = await api("/api/conversations/decisions", {
    conversation_id: state.conversation.id,
    text: $("decision-text").value,
    expected_version: state.conversation.version,
  });
  $("decision-text").value = "";
  renderMessages();
});
$("make-proposal").onclick = guard(async () => {
  if (!state.conversation) throw new Error("대화를 먼저 선택하세요.");
  notice("대화·PO 결정·기준 PRD로 변경안을 작성 중입니다.");
  await api("/api/proposals", { conversation_id: state.conversation.id });
  await page("prd");
  notice("변경 전후 문구를 검토하고 채택하세요.");
});
$("make-debrief").onclick = guard(async () => {
  if (!state.conversation) throw new Error("대화를 먼저 선택하세요.");
  notice("전체 대화를 디브리프로 정리 중입니다.");
  await api("/api/debriefs", { conversation_id: state.conversation.id });
  await page("prd");
});
$("export-chat").onclick = guard(async () => {
  if (!state.conversation) throw new Error("내보낼 대화를 선택하세요.");
  const md = $("export-format").value === "markdown";
  const data = await api(
    "/api/export/" + state.conversation.id + (md ? "?format=markdown" : ""),
  );
  const url = URL.createObjectURL(
    new Blob([md ? data.text : JSON.stringify(data, null, 2)], {
      type: md ? "text/markdown;charset=utf-8" : "application/json",
    }),
  );
  const a = node("a");
  a.href = url;
  a.download =
    "research-package-" +
    state.conversation.id.slice(0, 8) +
    (md ? ".md" : ".json");
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
});
$("research-upload").onsubmit = guard(async () => {
  const id = $("research-revision").value;
  try {
    await api("/api/research/upload", {
      ...(await fileBody($("research-file"))),
      title: $("source-title").value,
      ...(id
        ? {
            source_id: id,
            expected_version: state.sources.find((s) => s.id === id).version,
          }
        : {}),
    });
    notice("원문을 소유자 전용으로 저장했습니다.");
  } finally {
    await renderResearch();
  }
});
$("insight-form").onsubmit = guard(async () => {
  await api("/api/insights", {
    source_id: $("insight-source").value,
    title: $("insight-title").value,
    text: $("insight-text").value,
    feature: $("insight-feature").value,
    applicability: $("insight-applicability").value,
    limitations: $("insight-limitations").value,
    competitor: $("insight-competitor").value,
    observed_at: $("insight-observed").value,
    public_url: $("insight-public-url").value,
  });
  $("insight-title").value = "";
  $("insight-text").value = "";
  await renderResearch();
  notice("비공개 초안을 저장했습니다.");
});
$("release-selected").onclick = guard(async () => {
  const selected = [
    ...$("insight-list").querySelectorAll("input[type='checkbox']:checked"),
  ];
  if (!selected.length) throw new Error("공개할 초안을 선택하세요.");
  await api("/api/insights/release", {
    insight_ids: selected.map((e) => e.dataset.insightId),
    versions: Object.fromEntries(
      selected.map((e) => [e.dataset.insightId, Number(e.dataset.version)]),
    ),
    published: true,
  });
  await refresh();
  await renderResearch();
});
$("close-raw").onclick = () => {
  $("raw-content").textContent = "";
  $("raw-panel").hidden = true;
};
$("voc-upload").onsubmit = guard(async () => {
  const r = await api("/api/voc/upload", {
    ...(await fileBody($("voc-file"))),
    source_name: $("voc-source").value,
  });
  await refresh();
  await loadVoc();
  notice(
    "VoC " +
      r.imported +
      "건 추가 · 중복 " +
      r.duplicates +
      "건 · 오류 " +
      r.errors.length +
      "건. " +
      r.errors.map((e) => e.row + "행: " + e.error).join(" / ") +
      " " +
      r.redaction_note,
  );
});
$("review-collect").onsubmit = guard(async () => {
  const r = await api("/api/voc/reviews", {
    app_id: $("apple-app-id").value,
    provider: $("review-provider").value,
    country: $("review-country").value,
    max_pages: Number($("review-pages").value),
    filters: vocScope(),
  });
  await refresh();
  await loadVoc();
  notice(
    r.imported +
      "건 추가 · " +
      r.pages +
      "페이지. " +
      r.scope +
      (r.has_more ? " 한도 이후 자료는 추가 확인이 필요합니다." : ""),
  );
});
$("voc-filter").onchange = guard(loadVoc);
$("voc-scope-form").onsubmit = guard(loadVoc);
$("apply-chat-scope").onclick = guard(async () => {
  await updateConversation({ filters: vocScope() });
  notice("현재 대화에 분석 범위를 적용했습니다.");
});
$("classify-voc").onclick = guard(async () => {
  const r = await api("/api/voc/classify", { filters: vocScope() });
  await refresh();
  await loadVoc();
  notice(r.updated + "건을 분석했습니다. PO가 이미 보정한 분류는 유지합니다.");
});
$("taxonomy-import").onsubmit = guard(async () => {
  const bytes = await $("taxonomy-file").files[0].arrayBuffer();
  const data = JSON.parse(new TextDecoder().decode(bytes));
  await api("/api/features/import", {
    features: Array.isArray(data) ? data : data.features,
  });
  await refresh();
  await loadVoc();
  notice("서비스 기능 트리를 저장했습니다.");
});
$("generate-persona").onsubmit = guard(async () => {
  await api("/api/personas/generate", { segment: $("target-segment").value });
  await refresh();
  notice("관찰 근거와 가정을 검토하세요.");
});
$("persona-form").onsubmit = guard(async () => {
  await api("/api/personas", {
    name: $("persona-name").value,
    segment: $("persona-segment").value,
    goals: $("persona-goals").value,
    constraints: $("persona-constraints").value,
    assumptions: lines($("persona-assumptions").value),
    evidence_ids: [...$("persona-evidence").selectedOptions].map(
      (o) => o.value,
    ),
  });
  await refresh();
  notice("가상 페르소나를 저장했습니다.");
});
$("prd-import").onsubmit = guard(async () => {
  const prd = await api("/api/prds/import", {
    ...(await fileBody($("prd-file"))),
    title: $("prd-title").value,
  });
  if (state.conversation) await updateConversation({ prd_id: prd.id });
  await renderPlanning();
  notice("기준 PRD를 가져왔습니다.");
});
async function boot() {
  await refresh();
  $("login-screen").hidden = true;
  $("workspace").hidden = false;
  if (state.boot.conversations.length)
    await openConversation(
      state.conversation?.id || state.boot.conversations[0].id,
    );
  else renderMessages();
  await page("chat");
}
boot().catch(() => {
  $("login-screen").hidden = false;
  $("workspace").hidden = true;
});

// Stage 2: PO-authored planning intent, distinct from protected research sources.
const STORY_LABELS = {
  title: '스토리 제목', actor: '누가 · 대상 사용자', problem: '어떤 문제를 겪는가',
  goal: '무엇을 하고 싶은가', benefit: '어떤 가치를 얻는가', scenario: '기본 시나리오',
  exceptions: '예외·실패 상황', assumptions: '아직 검증하지 않은 가정',
};
const CONTEXT_LABELS = {
  customer: '대상 고객', problem: '해결할 문제', benefit: '제공할 가치', evidence_summary: '문제의 근거와 해석',
  experience: '기대하는 사용자 경험', existing_product: '기존 상품·현재 기능과의 차이',
  constraints: '정책·권한·데이터·비기능 제약', in_scope: '이번 범위', out_scope: '범위 제외',
  hypothesis: '검증할 가설', metric: '성공 지표·측정 방법·판단 기준',
};
const STORY_ORIGINS = {from_source: '원본에서 추출', ai_proposed: 'AI 제안 · 확인 필요', po_edited: 'PO 작성·수정'};
const RELEASES = [['mvp', 'MVP'], ['later', '후속'], ['excluded', '제외']];
let stage = {menu: 'stories', tab: 'import', selected: '', data: null, asset: '', selectedIds: new Set()};
function resetStage2() {
  stage = {menu: 'stories', tab: 'import', selected: '', data: null, asset: '', selectedIds: new Set()};
  $('page-stage2')?.replaceChildren();
}
function stageSelect(parent, key, label, options, selected) {
  const el = field(parent, key, label, '', 'select');
  fillSelect(el, options, selected);
  return el;
}
function stageCheck(parent, label, checked = false) {
  const wrap = node('label', null, 'story-check');
  const input = node('input'); input.type = 'checkbox'; input.checked = checked;
  wrap.append(input, node('span', label)); parent.append(wrap);
  return input;
}
function stageEvidence(parent, selected = []) {
  const details = node('details'); details.append(node('summary', '공개 인사이트·VoC 연결 (' + selected.length + ')'));
  const boxes = state.evidence.map(e => {
    const box = stageCheck(details, (e.title || e.text).slice(0, 100) + ' · ' + e.evidence_type, selected.includes(e.id));
    box.value = e.id; return box;
  });
  if (!boxes.length) details.append(node('p', '현재 공유된 근거가 없습니다. 가정과 검증 계획을 기록하세요.', 'muted'));
  parent.append(details);
  return () => boxes.filter(b => b.checked).map(b => b.value);
}
async function renderStage2(reload = true) {
  if (reload || !stage.data) {
    const [data, evidence, prds] = await Promise.all([api('/api/stage2'), api('/api/evidence'), api('/api/prds')]);
    stage.data = data; state.evidence = evidence; state.prds = prds;
  }
  const root = $('page-stage2'); root.replaceChildren();
  const nav = node('div', null, 'stage-nav'); nav.setAttribute('aria-label', '2단계 메뉴');
  const menus = [['context', '2.1 상품 정의'], ['stories', '2.2 사용자 스토리'], ['requirements', '2.3 기능 요구사항'],
    ['constraints', '2.4 제약·비기능'], ['scope', '2.5 범위·성공 지표'], ['export', '2.6 PRD 검토·전달']];
  for (const [key, label] of menus) {
    const b = button(label, async () => {stage.menu = key; await renderStage2();}, stage.menu === key ? 'primary' : 'secondary');
    b.dataset.stageMenu = key; nav.append(b);
  }
  root.append(nav);
  const content = node('div', null, 'stage-content'); content.id = 'stage-content'; root.append(content);
  if (['context', 'constraints', 'scope'].includes(stage.menu)) renderStageContext(content);
  else if (stage.menu === 'requirements') renderStoryRequirements(content);
  else if (stage.menu === 'export') renderStoryExport(content);
  else {
    const tabs = node('div', null, 'stage-tabs'); tabs.setAttribute('aria-label', '사용자 스토리 보기');
    for (const [key, label] of [['import', '초안 가져오기'], ['map', '스토리 맵'], ['edit', '원본 대조·편집'], ['review', '검토·확정']]) {
      const b = button(label, async () => {stage.tab = key; await renderStage2();}, stage.tab === key ? 'primary' : 'secondary');
      b.dataset.storyTab = key; tabs.append(b);
    }
    content.append(tabs);
    if (stage.tab === 'import') renderStoryImport(content);
    else if (stage.tab === 'map') renderStoryMap(content);
    else if (stage.tab === 'review') renderStoryReview(content);
    else await renderStoryEditor(content);
  }
}
function renderStageContext(parent) {
  const c = stage.data.context || {};
  if (c.redacted) {parent.append(node('p', '연결한 근거가 변경됐습니다. 공개 근거를 다시 선택해 상품 기준을 작성하세요.', 'warning'));}
  const form = node('form', null, 'card story-form'); form.id = 'stage-context-form';
  const keys = c.redacted ? Object.keys(CONTEXT_LABELS) : stage.menu === 'context' ? ['customer', 'problem', 'benefit', 'evidence_summary', 'experience', 'existing_product']
    : stage.menu === 'constraints' ? ['constraints'] : ['in_scope', 'out_scope', 'hypothesis', 'metric'];
  form.append(node('h2', stage.menu === 'context' ? '어떤 고객의 어떤 문제를 해결하나요?' : stage.menu === 'constraints' ? '서비스가 지켜야 할 조건' : '이번 범위와 성공 판단 기준'));
  for (const key of keys) {const input = field(form, key, CONTEXT_LABELS[key], c[key], 'textarea'); input.rows = 3;}
  let evidence;
  if (stage.menu === 'context' || c.redacted) {
    stageSelect(form, 'prd_id', '참고할 기존 PRD', [['', '선택 안 함'], ...state.prds.filter(r => !r.redacted).map(r => [r.id, r.title + ' · v' + r.version])], c.prd_id);
    evidence = stageEvidence(form, c.evidence_ids);
  }
  form.append(node('p', '저장하면 상품 기준의 새 버전이 생깁니다. 기존 확정 스토리는 변경된 기준으로 다시 검토해야 합니다.', 'muted'));
  saveButton(form, '상품 기준 저장');
  form.onsubmit = guard(async () => {
    const body = values(form); if (c.version) body.expected_version = c.version;
    if (evidence) body.evidence_ids = evidence();
    await api('/api/stage2/context', body); await renderStage2(); notice('상품 기준을 저장했습니다.');
  }); parent.append(form);
}
function renderStoryImport(parent) {
  parent.append(node('h2', '메모 한 장에서 검토할 스토리까지'), node('p', '손글씨·화이트보드 사진이나 텍스트를 올리세요. AI 해석은 초안이며, PO가 원본 의미와 수용 기준을 확인합니다.', 'muted'));
  const form = node('form', null, 'card story-form'); form.id = 'story-upload-form';
  field(form, 'title', '기획 원본 제목').required = true;
  const type = stageSelect(form, 'input_type', '입력 방식', [['image', '이미지 · PNG/JPEG'], ['text', '텍스트 메모']], 'image');
  const imageWrap = node('div'), textWrap = node('div');
  const file = field(imageWrap, 'file', '손글씨·화이트보드 이미지'); file.type = 'file'; file.accept = 'image/png,image/jpeg';
  imageWrap.append(node('small', '장당 3MiB·8,000px·2,000만 화소 이하. 촬영 방향은 자동 보정됩니다.'));
  const text = field(textWrap, 'text', '기획 메모', '', 'textarea'); text.rows = 6; textWrap.hidden = true;
  type.onchange = () => {imageWrap.hidden = type.value !== 'image'; textWrap.hidden = type.value !== 'text';};
  form.append(imageWrap, textWrap, node('p', '이 프로젝트의 기획 자료로 저장됩니다. 실제 고객 관찰 근거와는 구분됩니다.', 'muted'));
  saveButton(form, '기획 원본 저장');
  form.onsubmit = guard(async () => {
    const v = values(form); const body = {title: v.title, input_type: type.value};
    if (type.value === 'image') {
      if (!file.files[0]) throw new Error('이미지를 선택하세요.');
      if (file.files[0].size > 3 * 1024 * 1024) throw new Error('장당 3MiB 이하 이미지를 선택하세요.');
      Object.assign(body, await fileBody(file));
    } else body.text = text.value;
    const asset = await api('/api/planning-assets', body); stage.asset = asset.id;
    await renderStage2(); notice(asset.duplicate ? '같은 원본을 찾았습니다. 기존 자료에서 해석을 이어갑니다.' : '원본을 저장했습니다. AI 해석을 실행하거나 직접 작성할 수 있습니다.');
  }); parent.append(form);
  const assets = stage.data.assets.filter(a => !a.withdrawn);
  if (assets.length) {
    const analyze = node('div', null, 'card story-form');
    const select = stageSelect(analyze, 'asset_id', '해석할 기획 원본', assets.map(a => [a.id, a.title]), stage.asset || assets.at(-1).id);
    select.onchange = () => {stage.asset = select.value;};
    const notes = field(analyze, 'notes', '추가 설명 · 선택', '', 'textarea');
    analyze.append(button('AI로 스토리 초안 해석', async () => {
      notice('원본을 해석하고 있습니다. 완료 후 후보를 검토하세요.');
      await api('/api/stories/extract', {asset_id: select.value, notes: notes.value, request_id: crypto.randomUUID()});
      await renderStage2(); notice('스토리 후보를 만들었습니다. 원본 대조 후 채택하세요.');
    }, 'primary'), button('선택 원본 철회', async () => {
      const a = assets.find(a => a.id === select.value);
      await api('/api/planning-assets/withdraw', {asset_id: a.id, expected_version: a.version});
      await renderStage2(); notice('원본을 철회했습니다. 연결된 산출물은 재검토가 필요합니다.');
    })); parent.append(analyze);
  }
  parent.append(button('스토리 직접 작성', async () => {stage.selected = ''; stage.tab = 'edit'; await renderStage2();}));
  for (const run of [...stage.data.extractions].reverse()) {
    const card = node('div', null, 'card story-extraction');
    const asset = stage.data.assets.find(a => a.id === run.asset_id);
    card.append(node('h3', (asset?.title || '기획 해석') + ' · ' + ({completed: '검토 대기', running: '해석 중', failed: '실패', requires_review: '재검토 필요'}[run.status] || run.status)));
    if (run.redacted || run.status !== 'completed') {card.append(node('p', run.error || run.text || '진행 중입니다. 새로고침으로 상태를 확인하세요.')); parent.append(card); continue;}
    card.append(node('p', run.warnings.join(' / '), 'muted'));
    const source = node('details'); source.append(node('summary', '원본·판독 결과 보기'));
    const sourcePane = node('div'); source.append(sourcePane);
    source.ontoggle = guard(async () => {if (source.open) await renderStorySource(sourcePane, run); else sourcePane.replaceChildren();});
    card.append(source);
    run.candidates.forEach((candidate, index) => {
      const box = node('div', null, 'story-candidate'); box.append(node('h3', candidate.title));
      const details = node('details'); details.append(node('summary', '후보 필드·불확실한 질문 확인'));
      for (const [key, label] of Object.entries(STORY_LABELS)) details.append(node('p', label + ': ' + candidate[key] + ' · ' + STORY_ORIGINS[candidate.origins[key].origin]));
      for (const q of candidate.questions) details.append(node('p', '확인 필요: ' + q.text, 'warning'));
      box.append(details, button('새 스토리로 채택', async () => {
        const story = await api('/api/stories/apply-candidate', {extraction_id: run.id, candidate_index: index});
        stage.selected = story.id; stage.tab = 'edit'; await renderStage2();
      }, 'primary'));
      const compare = node('details'); compare.append(node('summary', '기존 스토리와 비교하여 선택 반영'));
      const options = stage.data.stories.filter(s => !s.redacted && !s.superseded_by);
      const target = stageSelect(compare, 'target_story', '비교할 스토리', [['', '선택하세요'], ...options.map(s => [s.id, s.title + ' · v' + s.version])]);
      const diff = node('div'); let boxes = [];
      target.onchange = () => {
        diff.replaceChildren(); boxes = [];
        const old = options.find(s => s.id === target.value); if (!old) return;
        for (const key of [...Object.keys(STORY_LABELS), 'acceptance_criteria']) {
          const text = key === 'acceptance_criteria' ? x => x.map(r => r.given + ' / ' + r.when + ' / ' + r.then).join('\n') : x => x;
          const b = stageCheck(diff, STORY_LABELS[key] || '수용 기준'); b.value = key; boxes.push(b);
          diff.append(node('p', '현재: ' + text(old[key]), 'muted'), node('p', '후보: ' + text(candidate[key])));
        }
      };
      compare.append(diff, button('선택한 필드만 반영', async () => {
        const old = options.find(s => s.id === target.value); if (!old) throw new Error('비교할 스토리를 선택하세요.');
        const changed = await api('/api/stories/apply-candidate', {extraction_id: run.id, candidate_index: index, story_id: old.id,
          expected_version: old.version, fields: boxes.filter(b => b.checked).map(b => b.value)});
        stage.selected = changed.id; stage.tab = 'edit'; await renderStage2(); notice('선택한 필드를 반영했습니다. 확정 전 다시 검토하세요.');
      })); box.append(compare); card.append(box);
    }); parent.append(card);
  }
}
async function renderStorySource(parent, run, highlighted = []) {
  parent.replaceChildren(node('p', '원본을 불러오는 중입니다.', 'muted'));
  const generation = state.generation, assetId = run.asset_id;
  const data = await api('/api/planning-assets/content/' + encodeURIComponent(assetId));
  if (generation !== state.generation) return;
  parent.replaceChildren();
  if (data.image_base64) {
    const viewport = node('div', null, 'story-image-viewport');
    const picture = node('div', null, 'story-image');
    const img = node('img'); img.src = 'data:' + data.mime + ';base64,' + data.image_base64;
    img.alt = '기획 원본 · 판독 영역을 대조하세요'; picture.append(img);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 1000 1000'); svg.setAttribute('preserveAspectRatio', 'none'); svg.setAttribute('aria-hidden', 'true');
    for (const r of run.regions || []) {
      const rect = document.createElementNS(svg.namespaceURI, 'rect');
      for (const k of ['x', 'y', 'width', 'height']) rect.setAttribute(k, r[k] * 1000);
      rect.setAttribute('class', highlighted.includes(r.id) ? 'selected' : r.uncertain ? 'uncertain' : '');
      const title = document.createElementNS(svg.namespaceURI, 'title'); title.textContent = r.id + ': ' + r.text; rect.append(title); svg.append(rect);
    }
    picture.append(svg); viewport.append(picture); parent.append(viewport);
    const zoom = field(parent, 'zoom', '원본 확대', 100); zoom.type = 'range'; zoom.min = 100; zoom.max = 250;
    zoom.oninput = () => {picture.style.width = zoom.value + '%';};
  } else parent.append(node('pre', data.text, 'story-transcript'));
  parent.append(node('h3', 'AI 판독문 · 원본과 대조 필요'), node('p', run.transcript, 'story-transcript'));
  for (const r of run.regions || []) parent.append(node('p', r.id + ' · ' + (r.uncertain ? '판독 확인 필요 · ' : '') + r.text, highlighted.includes(r.id) ? 'story-highlight' : 'muted'));
}
async function openStoryEditor(id) {stage.selected = id; stage.tab = 'edit'; await renderStage2();}
function renderStoryMap(parent) {
  parent.append(node('h2', '사용자 활동 × 출시 범위'), node('p', '카드에서 사용자 활동·MVP/후속/제외를 편집합니다. 저장한 변경은 재확정이 필요합니다.', 'muted'));
  const rows = stage.data.stories.filter(s => !s.redacted && !s.superseded_by);
  const journeys = [...new Set(rows.map(s => s.journey || '활동 미지정'))];
  if (!rows.length) parent.append(node('p', '초안을 채택하거나 스토리를 직접 작성하세요.'));
  const grid = node('div', null, 'story-map');
  for (const journey of journeys) {
    const column = node('div', null, 'story-map-column'); column.append(node('h3', journey));
    for (const [release, label] of RELEASES) {
      const lane = node('div', null, 'story-lane'); lane.append(node('h4', label));
      for (const s of rows.filter(s => (s.journey || '활동 미지정') === journey && s.release === release)) {
        const card = node('article', null, 'story-map-card'); card.append(node('small', s.epic || '에픽 미지정'), node('h3', s.title), node('p', s.actor), node('small', s.definition_status === 'confirmed' ? 'PO 정의 확정' : '초안'));
        const checkbox = stageCheck(card, '분할·병합 대상으로 선택', stage.selectedIds.has(s.id));
        checkbox.onchange = () => {if (checkbox.checked) stage.selectedIds.add(s.id); else stage.selectedIds.delete(s.id);};
        card.append(button('열고 편집', () => openStoryEditor(s.id))); lane.append(card);
      } column.append(lane);
    } grid.append(column);
  } parent.append(grid);
  const box = node('div', null, 'card story-form'); box.append(node('h3', '스토리 분할·병합'), node('p', '분할은 한 카드와 여러 새 제목, 병합은 여러 카드와 새 제목 한 개를 지정하세요. 원본 이력과 질문은 보존되며 새 스토리는 초안으로 검토합니다.', 'muted'));
  const titles = field(box, 'titles', '새 스토리 제목 · 한 줄에 하나', '', 'textarea');
  box.append(button('선택한 스토리 분할·병합', async () => {
    const selected = rows.filter(r => stage.selectedIds.has(r.id));
    await api('/api/stories/reorganize', {story_ids: selected.map(r => r.id), versions: Object.fromEntries(selected.map(r => [r.id, r.version])), stories: lines(titles.value).map(title => ({title}))});
    stage.selectedIds.clear(); await renderStage2(); notice('새 초안을 만들었습니다. 각 스토리의 내용과 수용 기준을 작성하세요.');
  })); parent.append(box);
}
async function renderStoryEditor(parent) {
  const choices = stage.data.stories.filter(s => !s.superseded_by);
  const selector = stageSelect(parent, 'story_id', '편집할 스토리', [['', '새 스토리 직접 작성'], ...choices.map(s => [s.id, s.redacted ? '재검토 필요 · ' + s.id : s.title + ' · v' + s.version])], stage.selected);
  selector.onchange = guard(() => openStoryEditor(selector.value));
  const story = choices.find(s => s.id === stage.selected) || {};
  if (story.redacted) {
    parent.append(node('p', '상품 기준·원본·근거 버전이 변경됐습니다. 상품 기준만 변경된 경우 새 초안으로 다시 검토할 수 있습니다. 철회된 원본·근거로 만든 내용은 계속 보호됩니다.', 'warning'));
    parent.append(button('현재 기준으로 검토용 초안 열기', async () => {
      await api('/api/stories', {story_id: story.id, expected_version: story.version}); await renderStage2();
    })); return;
  }
  const layout = node('div', null, 'story-editor-layout'); parent.append(layout);
  const original = node('aside', null, 'card story-source'); original.append(node('h2', '원본 대조'));
  const sourcePane = node('div'); original.append(sourcePane); layout.append(original);
  const runs = (story.source_refs || []).map(r => stage.data.extractions.find(e => e.id === r.extraction_id && !e.redacted)).filter(Boolean);
  let sourceNonce = 0;
  const showSource = async (run, ids = []) => {
    const nonce = ++sourceNonce; const pane = node('div'); sourcePane.replaceChildren(pane);
    await renderStorySource(pane, run, ids);
    if (nonce !== sourceNonce) pane.remove();
  };
  if (runs.length) {
    const sourceSelect = stageSelect(original, 'source', '기획 원본·해석', runs.map(r => [r.id, (stage.data.assets.find(a => a.id === r.asset_id)?.title || '원본') + ' · ' + r.created_at]), runs[0].id);
    sourceSelect.onchange = guard(() => showSource(runs.find(r => r.id === sourceSelect.value)));
    await showSource(runs[0]);
  } else sourcePane.append(node('p', '직접 작성한 스토리입니다. 이미지·텍스트 원본에서 시작하려면 “초안 가져오기”를 이용하세요.', 'muted'));
  const form = node('form', null, 'card story-form'); form.id = 'story-editor-form'; layout.append(form);
  form.append(node('h2', story.id ? story.title : '새 사용자 스토리'), node('p', story.definition_status === 'confirmed' ? 'PO 정의 확정 · 수정 후 다시 확정해야 합니다.' : '초안 · PO 검토 필요', 'pill'));
  let dirty = !story.id;
  form.oninput = e => {if (!e.target.dataset.review) dirty = true;};
  form.onchange = e => {if (!e.target.dataset.review) dirty = true;};
  for (const [key, label] of Object.entries(STORY_LABELS)) {
    const input = field(form, key, label, story[key], key === 'title' ? 'input' : 'textarea'); input.rows = 2;
    if (key === 'title') input.required = true;
    const origin = story.origins?.[key];
    if (origin) {
      const meta = node('div', null, 'story-origin'); meta.append(node('small', STORY_ORIGINS[origin.origin]));
      if (origin.region_ids?.length) meta.append(button('원본 위치 보기', async () => {
        const run = stage.data.extractions.find(r => r.id === origin.extraction_id && !r.redacted);
        if (run) await showSource(run, origin.region_ids);
      })); form.append(meta);
    }
  }
  field(form, 'epic', '상위 에픽', story.epic);
  field(form, 'journey', '사용자 활동 · 스토리 맵 열', story.journey);
  stageSelect(form, 'release', '출시 범위', RELEASES, story.release || 'mvp');
  stageSelect(form, 'feature', '연결 기능', [['', '선택 안 함'], ...featureOptions()], story.feature);
  stageSelect(form, 'persona_id', '참고할 가상 페르소나', [['', '선택 안 함'], ...(state.boot.personas || []).filter(p => !p.redacted).map(p => [p.id, p.name])], story.persona_id);
  const ac = node('fieldset'); ac.append(node('legend', '수용 기준 · Given / When / Then'));
  const acRows = [];
  const addCriterion = row => {
    const group = node('div', null, 'story-criterion');
    const inputs = ['given', 'when', 'then'].map((k, i) => field(group, k, ['Given · 전제', 'When · 행동', 'Then · 기대 결과'][i], row[k] || '', 'textarea'));
    inputs.forEach(i => i.removeAttribute('data-field'));
    const item = {inputs, removed: false}; acRows.push(item);
    group.append(button('기준 삭제', () => {item.removed = true; group.remove(); dirty = true;})); ac.append(group);
  };
  for (const row of story.acceptance_criteria || [{}]) addCriterion(row);
  form.append(ac, node('small', STORY_ORIGINS[story.origins?.acceptance_criteria?.origin] || 'PO 작성 수용 기준'), button('수용 기준 추가', () => {addCriterion({}); dirty = true;}));
  const questions = node('fieldset'); questions.append(node('legend', '불확실한 내용·확인 질문'));
  const questionRows = [];
  const addQuestion = q => {
    const group = node('div', null, 'story-question');
    const question = field(group, 'question', q.critical === false ? '추가 확인 질문' : '확정 전 필요한 질문', q.text, 'textarea');
    if (q.id) question.readOnly = true;
    const status = stageSelect(group, 'question_state', '검토 결과', [['open', '확인 필요'], ['answered', '답변 완료'], ['excluded', '범위 제외 · 사유 필요']], q.state || 'open');
    const response = field(group, 'response', '답변 또는 제외 사유', q.response, 'textarea');
    for (const el of [question, status, response]) el.removeAttribute('data-field');
    questionRows.push({q, question, status, response}); questions.append(group);
  };
  for (const q of story.questions || []) addQuestion(q);
  form.append(questions, button('확인 질문 추가', () => {addQuestion({critical: true}); dirty = true;}));
  const evidence = stageEvidence(form, story.evidence_ids);
  field(form, 'validation_plan', '실제 고객 검증 계획 · 대상·방법·판단 기준', story.validation_plan, 'textarea');
  form.append(node('p', '정의 확정은 PO의 설계 결정입니다. 고객 검증은 별도로 진행하며 AI가 완료로 표시하지 않습니다.', 'muted'));
  saveButton(form, '스토리 초안 저장');
  form.onsubmit = guard(async () => {
    const body = values(form); if (story.id) Object.assign(body, {story_id: story.id, expected_version: story.version});
    body.acceptance_criteria = acRows.filter(r => !r.removed).map(r => Object.fromEntries(r.inputs.map((e, i) => [['given', 'when', 'then'][i], e.value])));
    body.questions = questionRows.map(r => ({...(r.q.id ? {id: r.q.id} : {}), text: r.question.value, critical: r.q.critical !== false, state: r.status.value, response: r.response.value}));
    body.evidence_ids = evidence();
    const saved = await api('/api/stories', body); stage.selected = saved.id; await renderStage2(); notice('초안을 저장했습니다. 원본과 수용 기준을 검토한 뒤 확정하세요.');
  });
  if (story.id) {
    const review = node('div', null, 'story-confirm');
    review.append(node('h3', 'PO 검토·확정'));
    const missing = (story.confirmation_missing || []).map(k => ({open_questions: '미해결 질문', acceptance_criteria: '완전한 수용 기준', evidence_or_validation_plan: '근거 또는 가정과 검증 계획'}[k] || STORY_LABELS[k] || k));
    review.append(node('p', missing.length ? '보완할 항목: ' + missing.join(', ') : '필수 항목이 작성됐습니다. PO가 의미와 수용 기준을 최종 검토하세요.', missing.length ? 'warning' : 'muted'));
    const sourceChecked = stageCheck(review, '원본 의미·부정 표현·범위와 내 수정 내용을 확인했습니다.'); sourceChecked.dataset.review = 'true';
    const criteriaChecked = stageCheck(review, '수용 기준·확인 질문·검증 계획을 검토했습니다.'); criteriaChecked.dataset.review = 'true';
    const approve = button('이 버전의 정의 확정', async () => {
      if (dirty) throw new Error('편집한 내용을 먼저 저장하고, 저장된 버전을 검토하세요.');
      await api('/api/stories/confirm', {story_id: story.id, expected_version: story.version, reviewed_source: sourceChecked.checked, reviewed_criteria: criteriaChecked.checked});
      await renderStage2(); notice('PO 정의를 확정했습니다. 실제 고객 검증은 별도로 진행하세요.');
    }, 'primary'); approve.disabled = missing.length > 0; review.append(approve);
    review.append(button('버전 이력 보기', async () => {
      const history = await api('/api/stories/versions/' + encodeURIComponent(story.id));
      const container = node('div', null, 'story-history');
      for (const r of history) {
        const details = node('details'); details.append(node('summary', 'v' + r.version + ' · ' + (r.redacted ? '재검토 필요' : r.title + ' · ' + r.definition_status)));
        if (r.redacted) details.append(node('p', r.text));
        else {
          for (const [k, label] of Object.entries(STORY_LABELS)) details.append(node('p', label + ': ' + r[k]));
          details.append(node('p', 'PO 확정: ' + (r.approval ? r.approval.by + ' · ' + r.approval.at : '없음')));
        } container.append(details);
      } review.querySelector('.story-history')?.remove(); review.append(container);
    })); form.append(review);
  }
}
function renderStoryReview(parent) {
  parent.append(node('h2', '확정할 내용과 확인할 질문'), node('p', '각 스토리에서 원본·수용 기준을 검토한 뒤 버전별로 확정하세요. 확정된 정의만 기능 요구사항과 전달 패키지에 연결됩니다.', 'muted'));
  for (const story of stage.data.stories) {
    const card = node('article', null, 'card');
    card.append(node('h3', story.redacted ? '근거 변경 · 재검토 필요' : story.title), node('p', 'v' + story.version + ' · ' + (story.definition_status === 'confirmed' ? 'PO 정의 확정' : story.superseded_by ? '분할·병합됨' : '검토 필요')));
    if (!story.redacted) {
      card.append(node('p', '고객 검증: ' + (story.validation_status === 'planned' ? '계획 수립 · 미실시' : '미검증')));
      if (story.approval) card.append(node('small', '확정자: ' + story.approval.by + ' · ' + story.approval.at));
      for (const q of story.questions.filter(q => q.state === 'open')) card.append(node('p', q.text, 'warning'));
    }
    if (!story.superseded_by) card.append(button('열고 검토', () => openStoryEditor(story.id))); parent.append(card);
  }
}
function renderStoryRequirements(parent) {
  parent.append(node('h2', '스토리에서 검증 가능한 시스템 동작으로'), node('p', '확정된 스토리의 특정 버전에 연결합니다. 스토리나 근거가 바뀌면 요구사항도 재검토해야 합니다.', 'muted'));
  const stories = stage.data.stories.filter(s => !s.redacted && s.definition_status === 'confirmed' && !s.confirmation_missing.length);
  const renderForm = (container, req = {}) => {
    const form = node('form', null, 'card story-form'); if (!req.id) form.id = 'story-requirement-form';
    const story = stageSelect(form, 'story_id', '확정 스토리', stories.map(s => [s.id, s.title + ' · v' + s.version]), req.story_id);
    for (const [key, label] of [['title', '요구사항 제목'], ['condition', 'WHEN · 조건'], ['behavior', 'THE SYSTEM SHALL · 시스템 동작']]) field(form, key, label, req[key], key === 'title' ? 'input' : 'textarea').required = true;
    stageSelect(form, 'priority', '우선순위', [['must', 'Must'], ['should', 'Should'], ['could', 'Could'], ['wont', 'Won’t · 제외']], req.priority || 'should');
    saveButton(form, req.id ? '요구사항 수정 저장' : '기능 요구사항 저장');
    form.onsubmit = guard(async () => {
      const selected = stories.find(s => s.id === story.value); if (!selected) throw new Error('먼저 사용자 스토리를 확정하세요.');
      await api('/api/story-requirements', {...values(form), story_version: selected.version,
        ...(req.id ? {requirement_id: req.id, expected_version: req.version} : {})});
      await renderStage2(); notice('확정 스토리에 기능 요구사항을 연결했습니다.');
    }); container.append(form);
  };
  if (stories.length) renderForm(parent); else parent.append(node('p', '먼저 2.2에서 사용자 스토리를 검토·확정하세요.', 'warning'));
  for (const req of stage.data.requirements) {
    const detail = node('details', null, 'card'); detail.append(node('summary', req.redacted ? '연결 스토리 변경 · 재검토 필요' : req.title + ' · v' + req.version));
    if (req.redacted) detail.append(node('p', req.text)); else renderForm(detail, req); parent.append(detail);
  }
}
function renderStoryExport(parent) {
  parent.append(node('h2', '확정된 정의를 다음 기획 단계로'), node('p', '유효한 확정 스토리·수용 기준·근거 ID·가정·검증 계획을 함께 전달합니다. AXIOM의 실제 가져오기는 수신 규격 검증이 필요합니다.', 'muted'));
  const preview = node('div', null, 'card'); parent.append(preview);
  let reviewed = null;
  const actions = node('div', null, 'stage-tabs');
  actions.append(button('전달 내용 미리보기', async () => {
    reviewed = await api('/api/stage2/export'); const md = await api('/api/stage2/export?format=markdown');
    preview.replaceChildren(node('pre', md.text, 'story-transcript'));
  }, 'primary'));
  for (const format of ['json', 'markdown']) actions.append(button(format === 'json' ? 'JSON 내려받기' : 'Markdown 내려받기', async () => {
    const result = await api('/api/stage2/export' + (format === 'markdown' ? '?format=markdown' : ''));
    const blob = new Blob([format === 'json' ? JSON.stringify(result, null, 2) : result.text], {type: format === 'json' ? 'application/json' : 'text/markdown'});
    const url = URL.createObjectURL(blob); const a = node('a'); a.href = url; a.download = 'npd-story-package.' + (format === 'json' ? 'json' : 'md'); a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  })); parent.prepend(actions);
  const prd = node('div', null, 'card story-form');
  const title = field(prd, 'title', '새 PRD 제목', '사용자 스토리 기반 PRD');
  prd.append(node('p', '미리보기한 확정안을 새 PRD 초안으로 만듭니다. 기존 PRD는 보존되고, 이후 PRD 메뉴에서 검토·수정할 수 있습니다.', 'muted'));
  prd.append(button('검토한 확정안으로 PRD 초안 만들기', async () => {
    if (!reviewed) throw new Error('전달 내용 미리보기로 확정안을 먼저 확인하세요.');
    await api('/api/stage2/prd', {title: title.value, story_versions: Object.fromEntries(reviewed.stories.map(s => [s.id, s.version])),
      requirement_versions: Object.fromEntries(reviewed.requirements.map(r => [r.id, r.version])), context_version: reviewed.product_context?.version ?? null});
    await refresh(); await page('prd'); notice('확정안을 새 PRD 초안에 반영했습니다.');
  })); parent.append(prd);
}
