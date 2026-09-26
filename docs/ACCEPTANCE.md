# Acceptance evidence — 2026-09-26

The initial implementation milestone is implemented and tested. **The complete operational goal is not yet accepted: no suitable real-job submission has been confirmed.** Local fixture submissions never appear in the production tracker.

| Area | Observed evidence | Status |
| --- | --- | --- |
| Original CV preservation | Two byte-identical originals retained; immutable source copies and SHA-256 manifest; tampering regression | Passed for supplied PDFs |
| Candidate normalization | Five roles, four projects, two education records, two certifications, 37 evidenced skills; conservative alternate layouts and two-source conflict reconciliation preserve canonical values | Passed supported layouts and conflict tests |
| OCR | Actual raster PDF → Poppler/Tesseract recognition; unreviewed output rejected; reviewed transcription hashes checked | Passed local fixture |
| Master LaTeX/PDF | Actual Tectonic compilation, two-page selectable PDF, error/reference/glyph/overflow checks, visual inspection | Passed |
| Truthful tailoring | Sourced content ranking, unsupported-claim rejection, immutable versions, hash validation | Passed deterministic baseline |
| Discovery | Fourteen public configurations; 90 real target-title listings stored, including Workable and FlairsTech | Passed for documented sources |
| Eligibility | Latest assessment excludes all 90 current listings; role, skills, experience, location and mandatory-unknown reasons stored | No suitable current listing |
| Company research / ATS | Real listing provenance and public website research; explicit employer claims; transparent component scores | Passed baseline |
| Answers | User-approved notice, legal/country, conditional relocation and net salary facts; sourced education/current-role summaries; explicit numeric experience only | Passed |
| Optional model analysis | Strict schema, exact quotes, existing fact IDs and unsupported-output rejection | Mock API contract passed; live credentials absent |
| Salary / FX | Monthly/annual normalization; source/period/basis/jurisdiction checks; dated current EGP→SAR public-provider response stored locally; gross remains unknown without payroll evidence | Passed scoped rules |
| Browser engine | Real Chromium disposable fixtures cover controls, PDF upload digest, multistep navigation, unknown fields, CAPTCHA/auth blocks and positive receipts | Passed local fixtures |
| Profile completion | Actual local profile-editor fixture: missing known fields only, conflicts retained, save confirmation | Passed local fixture |
| Connected pipeline | Real fixture PDF extraction → profile → LaTeX → PDF → job analysis/research → tailored PDF → browser form → receipt → exact snapshot → SQLite/CSV | Local acceptance only |
| Duplicate/recovery safety | Repeated and concurrent claims, cross-platform missing-location matches, rejected history, crash reconciliation, stale candidate/job revisions and CV tampering | Covered by regressions |
| Live operation | Production batch: 90 discovered, 90 excluded after latest analysis, zero qualified, zero submitted | Real submission pending |
| GitHub | Requested private repository created under authenticated account; source/data exclusions verified | Passed: private main pushed and verified |

## Evidence locations (private local data)

- `data/source-cv/manifest.json` and immutable copies/extractions.
- `data/approved/user-clarification-2026-09-26.json`; prior profile revision under `data/master-cv/revisions/`.
- `data/master-cv/master-cv.tex` and `master-cv.pdf`, pointing to immutable version `d7cab1a32c897821e899`.
- `data/discovery/` contains complete source listings, source failures, original and corrected assessments. Earlier classifications are retained in history; the latest production database is authoritative for current states.
- `data/live-run-verified.json`, `data/report-latest.json`, `data/workai.sqlite3`, `data/applications/applications.csv`.
- `data/fx/EGP_SAR.json` contains the actual dated rate evidence, not a hardcoded future answer.
- `logs/` and immutable application-attempt snapshots preserve errors and attempted stages. The one earlier preparation that reached an unsupported adapter was subsequently excluded after parser review; it was never submitted.

## Remaining operational acceptance

1. Discover a materially suitable currently open role with all hard requirements established. Broaden approved public employers and permitted platform integrations without weakening truthfulness.
2. Audit that employer's actual application route, required questions and confirmation signals; configure any required login session securely. No production application/profile adapter is enabled by default.
3. Resolve only genuinely missing mandatory candidate facts. Current examples include language proficiency, academic dates/grades, citizenship/military status where applicable, explicit base/total compensation, and any gross calculation requiring reliable payroll rules. Do not infer these from the CV or job description.
4. Run the live orchestrator and capture an actual employer receipt plus exact CV, sourced answers, event trail and CSV record. Re-run to prove no duplicate submission.
5. Only then mark real end-to-end application acceptance passed. Additional platform coverage, general-layout extraction and broader model reasoning remain engineering work. Reviewed OCR and bounded optional model analysis now exist, but neither establishes universal document understanding; no live model call has been made.

## Platform observations

Normal unauthenticated Wuzzuf and Bayt search requests returned HTTP 403; Naukrigulf timed out. These sources were recorded and not bypassed. A normal browser inspection of a Bosta Lever QA application revealed custom required questions and hCaptcha integration; the role was unsuitable, no fields were filled and no CV was uploaded. Public feed availability does not prove form support. See `docs/JOB_SOURCES.md` and `docs/PLATFORMS.md`.

## Verification record

GitHub CI passed on implementation commit `3dd96fa588f0a41a57b6ba6d17888c9627ecfaec`: **111 tests passed**, including actual PDF compilation and Chromium integration, plus the tracked-secret audit. [Verified workflow run](https://github.com/Ahmed-elssamman/Ahmed-ElSamman-CV-Automation/actions/runs/36253883443). `main` was pushed; local and remote heads matched; Git status was clean; repository visibility was PRIVATE. No fixture is counted as a real application.

Local verification completed: full suite **110 passed**; after the final snapshot-log and historical-identity regression changes, the affected suites **38 passed** (111 distinct collected tests overall). The tests include actual Tectonic PDF compilation and real Chromium fixtures. Final production state is 90 discovered, 90 excluded, zero submitted. Private GitHub visibility and ignored `.env`, credentials, tokens, cookies, keys, browser config and candidate data paths were verified. Source-only commits are audited before pushing.

## Expanded verification checkpoint

Local suite passed **161 tests**. The subsequent degree-alternative regression and affected analyzer suite passed **38 tests**, bringing the collected total to **162**. This includes actual LaTeX/PDF generation, real Chromium forms, two-source reconciliation, source/review integrity and a real Tesseract recognition fixture. CI now installs Tesseract and English data so that OCR fixture can run in a fresh environment.

Production expansion discovered 18 additional vacancies, all with full employer descriptions. The latest live batch processed all **90 stored jobs** and recorded **90 exclusions, zero submissions**. Source audits include 48 further Workable leads, plus Forasna/Akhtaboot public searches; these results describe the inspected sources only. Shared FlairsTech careers URLs retain distinct external vacancy identities. No candidate data was entered into real forms.

Repeat ingestion preserved the exact canonical profile bytes and both source PDF checksums. Current master CV version remains `d7cab1a32c897821e899`, with source evidence and PDF quality gates rechecked. Local evidence: `data/verification-expanded.json`, `data/discovery-expanded-result.json`, `data/live-run-expanded-verified.json`, `data/report-expanded.json`.
