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
    const labels = {ready: "활성화 가능", blocked: "근거 공개 필요", active: "활성화됨", requires_review: "페르소나 근거 재검토 필요"};
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
