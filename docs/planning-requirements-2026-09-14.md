# September 14 planning requirements: implementation and verification

This change builds on PR #4. It does **not** certify 100% completion of the entire NPD product or a production deployment.

## Implemented in this branch

- Four named product stages and four research entry points; six PRD writing stages use the latest approved labels.
- PO-owned planning documents/images are separate from owner-only proprietary research. Original bytes are encrypted, hashed and versioned. PNG/JPEG verification, EXIF orientation, normalized source coordinates and provider image blocks are supported. Reanalysis creates a new extraction record; failures remain failures and interrupted processing is recoverable.
- Manual stories work without an AI provider. Stable IDs, version history, normal/exception scenarios, Given/When/Then, assumptions, source/provenance metadata and critical review questions are persisted. PO edits reopen drafts; explicit confirmation preserves previous confirmed versions. Splits/merges create new IDs.
- Image-derived story proposals preserve transcription, regions, uncertain relationships and quality questions. PO compares candidate fields and chooses which fields to apply. Server-side version checks prevent overwriting concurrent PO edits.
- Every Stage 2 AI draft asks for intent. Variants are independently stored. Confirming a document selects that exact version as the subsequent planning basis. Source changes or withdrawal invalidate dependent outputs. Design confirmation and actual customer validation are distinct; changing validated story behavior requires validation again.
- Existing-service analysis creates Markdown from uploaded PRDs/manuals. Reviewed VoC analyses, separately submitted real FGI reports, selected virtual FGI debriefs and benchmark analyses feed Stage 2. Real FGI status requires an explicit PO attestation; virtual interviews cannot be promoted through that path.
- 32 explicitly fictional, human-authored design profiles are searchable and may be registered with current evidence. Active pool cap is 100; an FGI study still selects at most six participants. These profiles are assumptions, not collected customers or AI-generated observations.
- Public web search uses the configured Brave endpoint and validates selected server-stored candidates before model analysis. Outputs explicitly disclose that snippets, not full pages, were read. Amazon internal information requests receive a policy refusal; restricted source publication and policy downgrade are blocked.
- Explicit `npd.story-package.v1` and `npd.definition-package.v1` exports carry selected IDs/versions, conditions and review state while excluding original file bytes, whole transcripts and provenance quotes. These are proposed interchange formats, not an AXIOM compatibility claim.
- Desktop source/editor/review panes, mobile tabs, source highlighting, zoom/rotation, field comparison, history export and a journey/release story map are wired to the API. Navigation waits for data before revealing editable panels so late navigation responses cannot erase input.

## Server configuration

Existing model configuration remains unchanged. Image interpretation requires a Bedrock model supporting Converse images; another configured text adapter returns a clear capability error. Missing credentials never produce canned results.

- `NPD_PLANNING_S3_BUCKET`: optional server-selected S3 bucket. Planning originals and internal VoC CSV archives are encrypted before upload and use SSE-S3. Without this setting, encrypted SQLite storage is used. Supply IAM permissions through the existing AWS credential chain; never enter keys in chat or commit them.
- `NPD_PUBLIC_SEARCH_KEY`: Brave search subscription key. Requests use the fixed HTTPS API endpoint, reject redirects and cap response size. Absence returns a configuration error.

Provider contracts: [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html), [ImageBlock](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ImageBlock.html), [Brave web search](https://api-dashboard.search.brave.com/api-reference/web/search/get).

## Validation recorded before CI

- New backend regression suite: 21 tests passed with synthetic fixtures and mocked external services.
- DOM + local HTTP regression: 5 tests passed.
- Headless installed Edge + local HTTP: 11 tests passed, including manual story confirmation/export, independent intent-driven drafts, mobile layout and the existing FGI flow.
- Python/JavaScript syntax and whitespace checks passed.
- Full Windows run at the earlier 198-test revision: 185 passed, 12 errors and 1 failure in existing Windows directory replacement, symlink and POSIX permission expectations. Supported Linux/Python 3.12 CI remains the complete regression gate; its outcome will be recorded in the PR.

## Remaining acceptance work and limitations

- Live Bedrock/Korean handwriting quality, AWS/S3 permissions and deployment, Brave results and AXIOM receiving-system/schema compatibility are unverified. Synthetic tests do not establish model accuracy or production readiness.
- The top-level prototype and UAT labels are present, with explicit implementation status. This repository still lacks a full prototype generator/deployer and UAT execution/feedback application; the attachments do not provide executable integration contracts for those stages.
- Image region bounds are displayed and linked, but a PO-drawn crop/region editor, image ordering controls, multi-page/scanned-PDF OCR and collaborative approvals are not complete.
- Source revisions currently invalidate dependent designs conservatively. History is retained, but a guided recovery/diff workflow after a source revision still needs acceptance work; withdrawn evidence remains inaccessible.
- Story map supports journey/release grouping, with edits in the story form. Drag/drop ordering and bulk map edits are not implemented.
- External web research currently compares search snippets. Whole-page acquisition and a comprehensive search/evaluation workflow are not implemented.
- Full requirement-by-requirement UAT by the Coupang Ads PO and production rollout have not been performed. Do not describe this branch as 100% complete.
