# Requirements audit — 2026-09-14

Baseline: main `e800af900439ea18b69f7666b8ffd96badd6bb90` (PR #1 merged). The supplied requirements, remaining-work plan, and domain review are specifications and historical evidence, not proof of current completion. Private source documents and customer data are not included here.

The repository does **not** implement 100% of the full requirements. Core research, VoC, interview and PRD workflows exist, but the audit found correctness gaps even within that implemented scope. The changes below address those gaps; they do not declare the broader roadmap complete.

| Requirements | Defect addressed | Result |
| --- | --- | --- |
| F1-08, AT-06 | Empty projects still attempted a model query expansion for multiword questions | If there are no eligible records, return no_evidence without a model call. Query expansion remains available when eligible records exist but lexical matching fails. |
| F1-09, F3-09, AT-14 | Reviewed debrief groups could add evidence without updating version dependencies or exported evidence | Review stores the union of references and dependencies atomically; concurrent revocation is rejected. Legacy summaries with untracked group references are hidden pending regeneration. |
| F3-09, PRD-02 | Proposal generation ignored saved and PO-edited debriefs | Valid debriefs and their evidence enter the proposal input; their versions and transitive dependencies are recorded. |
| PRD-04, DATA-03 | New PO decisions could be ignored by accepting an older proposal; conversation/debrief changes during generation were not guarded | Changed decisions or target PRD reject adoption. Conversation, PRD and debrief version checks execute in the same SQLite write transaction. Changed referenced debriefs reject adoption without changing the PRD. |
| F2-02, F2-08 | Manual problem/need edits bypassed existing masking and empty values could not clear a previous entry | Apply the same pattern masking to PO edits; explicit empty strings clear fields, omitted fields preserve them. This does not provide comprehensive PII detection. |
| F2-05 | Editing a parent could invalidate an existing child absent from the import batch | Validate the entire resulting feature tree before any writes. |
| F2-06, F2-09 | Initial rule classification could mix features from different services while recording only one service | Keep matching labels within the highest-ranked service; CSV and review ingestion share this rule. This is a deterministic contract fix, not measured classification accuracy. |
| PRD-05/06 | Markdown lacked stable section IDs and the applied PRD version | Include proposal ID/version, PRD ID, section IDs and the applied PRD version. AXIOM receiving-side verification remains external. |

## Validation

`tests/test_requirements_audit.py` adds 13 synthetic regressions for the above behavior, including revocation and planning-input races. Models remain explicit test doubles. Local Python compilation, JavaScript syntax and diff checks passed. Full local tests could not start because boto3/jsonschema are unavailable and the restricted package environment did not supply the requirements. The existing GitHub Actions workflow runs all Python, DOM/HTTP and Chromium suites three times, template lint and container checks. Refer to the PR's exact-commit CI result; this document does not predeclare its success.

## Remaining acceptance work

| Scope | Current boundary |
| --- | --- |
| F1-01–10 | Core protected research path exists. Real document/model quality, independent retrieval evaluation and deployed permission checks remain. |
| F1-11/12, NX-14/17 | Automatic web research and embedding/vector retrieval are not implemented. Weighted lexical retrieval is not semantic retrieval. |
| F2-01–11/13 | Core CSV, Apple review, classification and analytics paths exist. Full PII processing, real model classification evaluation and source representativeness are not established. |
| F2-12, NX-15/16 | Broader PII, Google Play/CS/NPS/channel connectors, schedules and independent Problem entities remain extensions. |
| F3-01–12 | Core personas, moderation, debrief and bounded context exist. Real eight-persona quality, diversity, latency and long-context consistency remain unverified. |
| PRD-01–06 | Local versioned PRD and file exchange exist. AXIOM ingestion, preserved references and retransmission must be tested with the receiving system. |
| PRD-07, NX-23 | AXIOM API/ontology/authentication and two-way revocation need an actual integration contract. |
| ACL/DATA/MODEL | Server-side access control, encryption and Bedrock adapters exist; actual hosting/IAM/model configuration and adversarial live-model validation remain separate gates. |
| OPS-01–07, NX-01–12 | Code/tooling is partial evidence. AWS model connection, end-to-end live execution, owner release decisions, HTTPS deployment, on-host recovery and PO tasks are not completed by this patch. |
| OPS-08, NX-19–22 | Organization SSO, managed storage/KMS, queues, HA, operational telemetry and existing-host release automation remain extensions. |
| NX-13/18 | Deferred profile activation and PPTX text extraction already exist in main. Arbitrary asset-pack merging, OCR and larger asynchronous ingestion remain. |
| NX-24–28; MP2/MP3/MP4 | Existing CloudFront integration, full requirement/story/gate entities, prototype generation/deployment and real-user behavior/UAT lifecycle are not implemented here. |
| Domain review | Private source approval, uncertain claims, simulated versus actual customer evidence and source duplication require owner/PO review; no private document claims are automatically published. |

The next release gates remain actual Bedrock connection, actual workflow/quality evaluation, restricted HTTPS deployment and recovery, then PO/AXIOM acceptance. No AWS deployment or real-model test was performed by this code audit.
