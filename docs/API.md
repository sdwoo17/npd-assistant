# API contract v0.2

This is a new application contract, not an AXIOM API specification. All POST bodies are JSON. Login returns an HttpOnly cookie and CSRF token; authenticated POST requests require `X-CSRF-Token`. Host and Origin must match the server origin. Role and active project come from the server session and membership table, never request-body overrides.

## Authentication and projects

| Route | Scope | Contract |
| --- | --- | --- |
| POST /api/login | Public | email,password → session cookie,csrf |
| POST /api/logout | Session | Revoke session |
| GET /api/bootstrap | Session | user,projects,features,personas,conversation summaries,model configuration and last successful schema response |
| GET/POST /api/projects | Session | List memberships / create a new empty project with title |
| POST /api/projects/switch | Session | project_id → new CSRF token; membership verified |
| POST /api/projects/members | Owner | email,role → add an already registered user to current project |
| GET /api/assets/persona-templates | Owner | Decrypted profile definitions for review, missing evidence IDs, current active/blocked/ready/requires_review status; never a PO evidence endpoint |
| POST /api/assets/activate-personas | Owner | template_ids (1–8 unique), versions `{template_id: expected_version}` → current personas, created count. Atomic creation against current published evidence. Repeated activation returns current profiles without replacing PO edits. |

## Research

| Route | Scope | Contract |
| --- | --- | --- |
| GET /api/research | Owner | Source metadata, all insight drafts, safe job metadata |
| POST /api/research/upload | Owner | title,filename,content_base64; optional source_id + expected_version to revise |
| GET /api/research/raw/{id} | Owner | Decrypted extracted text and filename |
| GET /api/research/versions/{id} | Owner | Source metadata version history; no decrypted content |
| POST /api/research/extract | Owner | source_id,evidence_type research/synthetic → chunked private drafts |
| POST /api/insights | Owner | source_id,title,text,feature; applicability,limitations,competitor,observed_at,public_url,evidence_type optional |
| POST /api/insights/update | Owner | Same fields + insight_id,expected_version; new version becomes private |
| POST /api/insights/release | Owner | insight_id,published,expected_version OR insight_ids,published,versions map; atomic batch |
| GET /api/insights/versions/{id} | Owner | Insight version history |
| GET /api/jobs | Owner | State/progress/attempts; encrypted payload omitted |
| POST /api/jobs/retry | Owner | job_id → retry failed document/extraction/collection job |
| GET /api/evidence | Session | Currently released insights and pseudonymized VoC only |
| POST /api/search | Session | query,filters → evidence + method/eligible/retrieved/limit |

## VoC and taxonomy

| Route | Scope | Contract |
| --- | --- | --- |
| GET /api/features | Session | Default + project feature tree |
| POST /api/features/import | Owner | features:[{key,name,service_id,parent_feature_id,terms}] |
| POST /api/voc/upload | Owner | source_name,filename,content_base64 → per-row imported/duplicate/error results |
| POST /api/voc/reviews | Owner | app_id,provider apple_public_rss/app_store_connect,country,max_pages,filters |
| GET /api/voc | Session | Query-string filters → records,counts,total,denominator,evidence type and segment counts |
| POST /api/voc/analysis | Session | filters → same analysis contract |
| POST /api/voc/feature | Session | voc_id,expected_version,feature_ids,problem,need → PO-reviewed version |
| POST /api/voc/classify | Session | voc_ids or filters → batched model classification; skips PO-reviewed rows |
| POST /api/voc/withdraw | Owner | voc_id → withdraw and invalidate downstream dependencies |

Filters: `date_from,date_to,feature,service_id,segment,source_name,evidence_type`. Dates accept ISO dates/timestamps with timezone; analysis compares source-local date portions, not UTC re-bucketing. Explicit filters override month inference from the question. Missing dates are excluded from date-bounded analysis. Multi-label counts can overlap. CSV source ID is dedupe scope together with external_id; plaintext source content is not exposed to PO. Supported pattern pseudonymization is intentionally limited and is not a general PII detector.

## Personas, interviews and PRD

| Route | Scope | Contract |
| --- | --- | --- |
| POST /api/personas | Session | name,segment,goals,constraints,assumptions,evidence_ids,observations [{evidence_id,quote}] |
| POST /api/personas/update | Session | Same fields + persona_id,expected_version |
| POST /api/personas/generate | Session | segment,name optional,filters optional → actual stored persona with evidence snapshots |
| GET /api/personas/versions/{id} | Session | Version history; unavailable source versions redacted |
| POST /api/conversations | Session | title,mode,persona_ids,prd_id,filters,objective; creates a blank PRD when omitted |
| GET /api/conversations/{id} | Session | Version, pinned participants, filtered decisions, messages with invalid dependencies redacted |
| POST /api/conversations/state | Session | conversation_id,expected_version + mode,round_type explore/challenge,filters,prd_id,objective |
| POST /api/conversations/decisions | Session | conversation_id,expected_version,text; decision_id instead of text deactivates decision |
| POST /api/chat | Session | conversation_id,message,request_id,action ask/create_persona; optional persona_name |
| POST /api/debriefs | Session | conversation_id → structured evidence/message-linked debrief |
| POST /api/debriefs/update | Session | debrief_id,expected_version + group arrays |
| GET /api/debriefs | Session | Currently available debriefs |
| POST /api/prds | Session | title,sections:[{id,title,text}] |
| POST /api/prds/import | Session | title,filename,content_base64; Markdown headings map to sections |
| POST /api/prds/update | Session | prd_id,expected_version,title,sections |
| GET /api/prds | Session | Current PRDs or redaction placeholders |
| GET /api/prds/versions/{id} | Session | Version history respecting dependency revocation |
| POST /api/proposals | Session | conversation_id → target PRD/version and before/after changes |
| POST /api/proposals/update | Session | proposal_id,expected_version,changes:[{section_id,after,rationale}] |
| GET /api/proposals | Session | Currently available proposals |
| POST /api/proposals/decision | Session | proposal_id,expected_version,state accepted/held; stale versions return 409; acceptance atomically creates PRD version |
| GET /api/export/{conversation_id} | Session | `npd.research-package.v2` JSON |
| GET /api/export/{conversation_id}?format=markdown | Session | {format:"markdown",text:"..."} with inline IDs and evidence appendix |

Proposal decisions require the version displayed to the reviewer, including holds. Missing or invalid `expected_version` returns 400. An unchanged accepted/held result can be read again using its current version, or retried by the same actor with the version that produced that decision. A later edit or another actor's decision does not authorize a stale retry. Replays never create another PRD version.

Generated debriefs validate all five summary groups before saving. Each item's numerical quantities, inline citation IDs and real-customer assertions are checked against its linked evidence and the conversation's server-computed statistics. Qualitative hypotheses/questions may have no evidence IDs. A failing item rejects the entire generated debrief. These structural guards do not establish semantic truth or replace PO review.

Interview limits: 8 personas, 1,000 prior messages, 160,000 characters of text; overflow produces an explicit error rather than silent history truncation. Provider context/output limits may be lower. Multi-persona turns validate all responses before saving any messages. Idempotency keys are scoped to project+conversation; repeating a committed request does not append messages. Across independent service instances simultaneous requests may receive a retryable 409; supported deployment is one server process.

All generated artifacts retain the complete input dependency closure, including indirect history and filtered aggregate sources. Final evidence IDs are not the sole authorization check. Knowledge epoch and record version checks reject changed inputs at commit. Legacy generated records without complete provenance are redacted; raw source/VoC/PRD records remain available subject to their permissions.

## Errors and provider contracts

400 malformed input; 401 expired/missing session; 403 role/CSRF/Origin/Host; 404 missing or foreign object; 409 stale version, revoked evidence, idempotency conflict or insufficient persona/planning evidence; 413 request limit; 415 content type; 429 login throttling; 502 invalid/unavailable provider response; 503 provider not configured.

Research chat with no matching evidence returns a persisted **system** `no_evidence` response with `model:null`; it is not a simulated AI answer. Model outputs must satisfy strict JSON schemas and server-side ID, exact-quote and numerical-unit validation. These are integrity checks, not semantic truth guarantees.

Official provider references (retrieved 2026-09-12):
- https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html
- https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developer.apple.com/documentation/appstoreconnectapi/get-v1-apps-_id_-customerreviews

## FGI studies and citation verification (2026-09-14)

All routes below require an authenticated session and derive project scope on the server. Every mutation of an existing study requires `expected_version`. Stale requests fail atomically with 409. A failed start creates neither a conversation nor a blank PRD.

| Route | Request / result |
| --- | --- |
| GET /api/studies | studies, guide_sections, participant_limit=6, persona_pool_limit=20; revoked studies are redacted |
| GET /api/studies/{id} | Current available study; unavailable dependencies return 409 |
| POST /api/studies | title; optional objective,research_questions,recruitment_criteria,persona_ids,target_prd_id; starts in design |
| POST /api/studies/update | study_id,expected_version + design fields,stage design/recruitment/guide; forward steps cannot be skipped |
| POST /api/studies/guide | study_id,expected_version; omit sections to call the configured model; supply sections:[{title,text,evidence_ids}] and optional assumptions for PO review |
| POST /api/studies/start | study_id,expected_version; requires a PO-reviewed guide; atomically pins recruited versions and creates an interview/PRD |
| POST /api/studies/complete | study_id,expected_version,debrief_id,debrief_version; selected reviewed debrief must cover every current valid message |
| POST /api/personas/archive | persona_id,expected_version,archived:boolean; archived profiles leave the active pool but remain in historical sessions |
| POST /api/debriefs/select | conversation_id,expected_version,debrief_id,debrief_version; select a reviewed planning basis |
| POST /api/citations/verify | text (up to 200,000 characters), optional expected_versions:{id:positive_integer} |

Design → recruitment requires a purpose and at least one research question. Recruitment → guide requires criteria and 1–6 participants. Changing design or participant versions invalidates the guide. A generated guide remains `model_draft` until the PO saves its reviewed sections. Eight sections cover purpose, participants, session structure/timing/warm-up, core research questions, probes, moderator rules, closing and debrief checklist. Planned timing is a moderator assumption; it is not evidence of measured runtime.

Started studies freeze their objective, mode, PRD and recruited persona versions. A question can address a subset using @tags; subsequent group questions still use the complete recruited roster. Retiring a profile blocks new recruitment without invalidating old pinned sessions. Completed studies freeze further messages and debrief edits/selection, while allowing grounded PRD proposals and exports. The active pool cap is enforced in the storage transaction, including simultaneous generation, template activation and restoration. Existing pools over 20 may be edited/reduced but cannot grow; existing general interviews retain their eight-participant contract.

Reviewing a debrief replaces its top-level summary as well as its groups and selects it as the current planning basis. Optional `summary` provides explicit PO text; omitted summary is derived from reviewed common needs/disagreements/hypotheses. `planning_revision` changes on review/selection. Proposals record that revision and the selected debrief version; `planning_stale` marks earlier proposals and accepting them returns 409. Unreviewed drafts do not replace the selected basis. Completed studies cannot select a different basis.

JSON packages now include the `study` design, questions, criteria, pinned participants, guide and completion reference. Markdown includes the same design and guide alongside transcript, reviewed debriefs, proposal history and evidence. Reusing an existing PRD in another conversation also includes its evidence and previously applied proposals. Manually saved/imported PRDs track recognized current evidence dependencies and list `unresolved_citation_ids`; preserving an unknown external ID does not verify it. Existing dependencies are retained conservatively when editing, so removal of an inline label cannot bypass revocation.

Citation inspection recognizes bracketed UUID IDs and reserved entity prefixes (`INS`, `VOC`, `PROB`, `OPP`, `PDEF`, `REQ`, `NFR`, `SCOPE`, `HYP`, `MET`, `BEN`, `AST`, `TASK`, `UTASK`, `IMP`, `PER`, `FGI`, `DEB`). Explicit versions use `[id] v2` or `expected_versions`; formats such as `[id@v2]` are not supported. It returns occurrence counts, unique IDs, unique verified IDs, version-unchecked counts and per-reference status: `verified`, `version_mismatch`, `retracted`, `requires_review`, `unknown_or_unavailable`. Unknown, private and foreign IDs cannot disclose protected record existence. Retraction status is available only for previously published project insights or withdrawn project VoC. The submitted text is neither persisted nor sent to a model. Counts concern only the submitted text and current project, not actual AXIOM usage, semantic correctness or external transmission.

Moving a feature to another service returns 409 when any project insight or VoC still uses that feature. The entire taxonomy import rolls back. An unused feature can move. Reclassify references explicitly before attempting a move.
