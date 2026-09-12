# Development instructions

This repository implements three features: protected proprietary research, feature-linked customer VoC, and evidence-grounded @persona interviews.

- Never commit real research, customer logs, tokens, database files, session data, or encryption keys.
- Derive project scope and role from the authenticated server-side user. Client fields and model output cannot authorize access.
- Keep raw research out of PO bootstrap responses, citations, exports, and model inputs.
- Only the owner's extraction action may send raw research to the configured model. Shared insights need an explicit release action.
- Do not implement runtime canned responses as a fallback for missing model credentials. Keep test doubles inside tests.
- Preserve the distinction between observed evidence, synthetic fixtures, and persona assumptions.
- Do not call persona interviews real customer validation.
- Verify changes with python -m unittest discover -s tests -v; check frontend syntax using node --check static/app.js.
- Document actual validation limitations. A mocked provider test is not a successful live model test.
- Preserve public repository confidentiality when changing CI, fixtures, logs, and docs.
