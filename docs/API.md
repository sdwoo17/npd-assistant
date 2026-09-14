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

## Stage 2: PO planning assets and user stories

See [workflow and limits](STAGE2_USER_STORIES.md). All routes below require a project member session; neither client-supplied role nor project changes their scope. Owner-only research routes are unchanged.

| Route | Contract |
| --- | --- |
| GET /api/stage2 | context,stories,assets,extractions,requirements; invalid dependencies are redacted; no image payload or ciphertext |
| POST /api/stage2/context | Partial product-context fields; evidence_ids,prd_id; existing record requires expected_version |
| POST /api/planning-assets | title,input_type image/text; image: filename,content_base64; text: text; returns metadata and optional duplicate flag |
| GET /api/planning-assets/content/{id} | Orientation-normalized JPEG base64 or text; project scope and withdrawal checked |
| POST /api/planning-assets/withdraw | asset_id,expected_version; soft withdrawal invalidates dependent artifacts |
| POST /api/stories/extract | asset_id,request_id,notes optional; synchronous model request → extraction candidates only. Repeated completed request is idempotent. Retry failed/interrupted work with a new request_id. |
| POST /api/stories/apply-candidate | extraction_id,candidate_index; optional story_id,expected_version,fields for explicit field-wise replacement. fields may include acceptance_criteria. No automatic overwrite. |
| POST /api/stories | title,actor,problem,goal,benefit,scenario,exceptions,assumptions,epic,journey,release,feature,persona_id,evidence_ids,validation_plan,acceptance_criteria,questions. Existing record: story_id,expected_version. Returns a draft. |
| POST /api/stories/confirm | story_id,expected_version,reviewed_source:true,reviewed_criteria:true,note optional. Server validates required fields, criteria, unresolved critical questions, evidence/validation plan and current dependencies. |
| GET /api/stories/versions/{id} | History; changed or withdrawn dependencies are redacted |
| POST /api/stories/reorganize | story_ids,versions map,stories array. Split one to many or merge many to one; originals become superseded and new drafts retain lineage/questions. |
| POST /api/story-requirements | story_id,story_version,title,condition,behavior,priority; optional requirement_id,expected_version. Requires a currently valid confirmed story. |
| GET /api/stage2/export | npd.story-package.v1; ?format=markdown → {format,text}. Only valid confirmed stories and current linked requirements. |
| POST /api/stage2/prd | title,story_versions map,requirement_versions map,context_version (null if absent), matching the previewed package. Creates a new versioned PRD snapshot; does not overwrite an existing PRD or send to AXIOM. |

`acceptance_criteria` is an array of `{given,when,then}`. Questions have `{id,text,critical,state,response}`; omit id for a new question. State is `open/answered/excluded`; non-open states need a response. Existing question IDs and criticality cannot be removed/downgraded. `release` is `mvp/later/excluded`; priority is `must/should/could/wont`.

Client origin/approval/dependency/validation-status claims are not accepted. The server preserves generated source references, computes dependencies, sets definition status, and records the approving actor and exact revision. `validation_status` is only `unverified/planned`: PO confirmation never claims actual customer validation. Image originals/processing copies are encrypted; model output is schema/domain checked. Semantic accuracy still requires PO review.
