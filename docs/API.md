# API contract v0.1

This is a new application contract, not an existing AXIOM contract.

All mutations use JSON. After login, send the session cookie and X-CSRF-Token from bootstrap. The server uses the authenticated user's project; client-supplied role and project fields do not override it.

| Method / route | Scope | Purpose |
| --- | --- | --- |
| POST /api/login | Public | email/password → HttpOnly cookie and CSRF token |
| POST /api/logout | Signed in | Revoke the current session |
| GET /api/bootstrap | Signed in | Identity, features, persona cards, conversation list, model configuration status |
| GET /api/research | Owner | Source metadata and insight drafts; no decrypted source in list |
| POST /api/research/upload | Owner | title, filename, content_base64 → encrypted extracted source |
| GET /api/research/raw/{id} | Owner | Decrypted source for review |
| POST /api/research/extract | Owner | source_id → model-generated private insight drafts |
| POST /api/insights | Owner | source_id,title,text,feature → private draft |
| POST /api/insights/release | Owner | insight_id,published → release/revoke |
| GET /api/evidence | Signed in | Published insights and redacted VoC only |
| GET /api/voc | Signed in | Records and deterministic feature counts |
| POST /api/voc/upload | Owner | source_name,filename,content_base64 → import/duplicate/error counts |
| POST /api/voc/reviews | Owner | numeric app_id → managed App Store Connect reviews, at most 100 |
| POST /api/voc/feature | Signed in | voc_id,feature → reviewed classification |
| POST /api/personas/generate | Signed in | segment → grounded AI persona |
| POST /api/personas | Signed in | name,segment,goals,constraints,assumptions,evidence_ids → manual persona |
| POST /api/conversations | Signed in | title,mode,persona_ids → conversation |
| GET /api/conversations/{id} | Signed in | Messages with revoked evidence redacted |
| POST /api/chat | Signed in | conversation_id,message → evidence-checked research/persona messages |
| POST /api/proposals | Signed in | conversation_id → PRD proposal from evidence-backed messages |
| GET /api/proposals | Signed in | Proposals with currently accessible evidence |
| POST /api/proposals/decision | Signed in | proposal_id,state accepted/held |
| GET /api/export/{conversation_id} | Signed in | Research package excluding owner-only source contents |

Errors return a JSON object with an error message. 401: login; 403: role/CSRF/origin; 404: missing or inaccessible object; 409: missing/revoked evidence; 429: login limit; 502: provider/response failure; 503: model/collector not configured.

Model JSON citations must be a nonempty subset of the server-supplied evidence IDs. This verifies reference integrity, not semantic truth; model answer quality still requires evaluation.

Official references:
- OpenAI text generation: https://developers.openai.com/api/docs/guides/text
- Apple customer reviews: https://developer.apple.com/documentation/appstoreconnectapi/get-v1-apps-_id_-customerreviews
