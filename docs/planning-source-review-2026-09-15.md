# Planning source review continuation — September 15

This extends Draft PR #6, stacked on PR #4. It does not claim completion of the entire NPD product.

## Behavior

1. A PO uploads a PNG/JPEG and explicitly starts analysis with an intent. A dragged or numeric crop and quarter-turn rotation define the provider input. Source bytes remain encrypted and unchanged. The server records the effective pixel-aligned crop and maps provider rectangles back to the original oriented coordinate space.
2. A PO may correct region boundaries, text and relationships. Saving creates a new extraction ID and keeps the previous analysis, original values and unresolved ambiguity questions. An extraction revision must reference the current original version.
3. Story generation uses the explicitly selected extraction IDs in the selected order (maximum three different originals). Previous runs are selectable; the most recent runs are not automatically included. Field provenance pins the extraction and source version.
4. Replacing an original retains its ID and increments its version. Dependent stories remain blocked until a PO compares the versions and accepts a reviewed snapshot. The recovery action creates a new draft version under the existing story ID, preserving PO edits and confirmed history. A mandatory critical question requires renewed review before confirmation; deletion, priority downgrading and replacement by an AI candidate cannot bypass it.
5. Previous extracted coordinates and quotes remain tied to the historical original. They are not relabeled as evidence from the replacement. Historical previews are only available while the original remains authorized in the current project. Original bytes and transcripts remain excluded from design exports.

## Concurrency and confidentiality

- Revision and recovery writes check the project epoch and exact target versions. A second replacement, concurrent PO edit, replay of a recovery snapshot, project mismatch or source withdrawal prevents the stale write.
- Recovery handles planning-original revisions. Every other dependency must remain accessible under the existing strict checks. A revoked insight or a stale linked research/definition document cannot be bypassed by rebasing original versions.
- Customer validation remains distinct from design approval. Recovery changes `actual_results` to `planned`; it never claims that an image interpretation is an actual customer result.
- Missing image-provider configuration is still an error, with no runtime canned fallback.

## Validation

The new synthetic backend suite exercises crop pixels and coordinate transforms, immutable PO region revisions, invalid relationship/version/scope rejection, explicit ordering, edit/history preservation, renewed confirmation, source withdrawal, shared-evidence revocation, concurrent replacement and recovery races. Browser coverage exercises selection, editing, ordering and before/after recovery through HTTP.

Local continuation validation:

- Stage 2 and source backend tests: 37 passed (23 existing + 14 new).
- Browser + DOM: 14 installed-Edge browser tests and 5 DOM tests passed, including real pointer crop selection and the new workflows.
- Full Windows run at the 217-test checkpoint: 204 passed, 12 errors and 1 failure in existing directory replacement, symlink and POSIX permission expectations. This run used UTF-8 mode. The additional mandatory-review regression subsequently passed in the 37-test focused suite.
- Python/JavaScript syntax and whitespace checks passed. Screenshots of the ordered-input and recovered-story screens were inspected.
- UI inputs are locked while planning saves refresh their data, and initial navigation finishes before exposing the logged-in workspace. These prevent fast input from being erased by a late response.

Exact final supported-platform results are recorded in the PR after the continuation commit completes CI. The previous PR head (`376b351`) passed Linux Python 204 / DOM 5 / Chromium 12 tests three times each. Local Windows still has known baseline directory replacement, symlink and POSIX permission failures; Linux CI is the full supported-platform regression gate.

## Remaining requirements

- Prototype generation/deployment and a complete UAT execution/feedback application.
- Multi-page/scanned-PDF OCR, collaborative approvals and drag/drop story-map editing.
- Guided revalidation through changed research/definition documents and complete product-context impact review for existing/manual stories.
- Full-page web acquisition and evaluation beyond the existing search-snippet analysis.
- Live Korean handwriting quality, Bedrock/S3/AWS deployment, Brave results and AXIOM receiving-system validation. These require the actual integration specifications and deployment targets, which have not been supplied.
- Coupang Ads PO acceptance and production rollout. Keep the PR draft until these remaining acceptance decisions are resolved.
