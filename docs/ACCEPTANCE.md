# Acceptance evidence — 2026-09-26

**The first real application pipeline is verified:** Total-TECH's Junior FrontEnd Developer form confirmed submission on 2026-09-26; exact artifacts and repeat-run duplicate prevention were checked. Its source listing is dated August 2023, so recent recruiting activity remains unverified. Broader platform/profile coverage and remaining candidate answers are still pending. Local fixture submissions never appear in the production tracker.

| Area | Observed evidence | Status |
| --- | --- | --- |
| Original CV preservation | Two byte-identical originals retained; immutable source copies and SHA-256 manifest; tampering regression | Passed for supplied PDFs |
| Candidate normalization | Five roles, four projects, two education records, two certifications, 37 evidenced skills; conservative alternate layouts and two-source conflict reconciliation preserve canonical values | Passed supported layouts and conflict tests |
| OCR | Actual raster PDF → Poppler/Tesseract recognition; unreviewed output rejected; reviewed transcription hashes checked | Passed local fixture |
| Master LaTeX/PDF | Actual Tectonic compilation, two-page selectable PDF, error/reference/glyph/overflow checks, visual inspection | Passed |
| Truthful tailoring | Sourced content ranking, unsupported-claim rejection, immutable versions, hash validation | Passed deterministic baseline |
| Discovery | Sixteen enabled public configurations plus verified direct imports; 132 real target-title listings stored; 38 full vacancies retrieved by recurring WP Job Manager search, including 32 new jobs | Passed for documented sources |
| Eligibility | Latest assessment: 129 excluded, two blocked on unknown fields, one qualified and submitted | Reasons and unknowns retained |
| Company research / ATS | Real listing provenance and public website research; explicit employer claims; transparent component scores | Passed baseline |
| Answers | User-approved notice, legal/country, conditional relocation and net salary facts; sourced education/current-role summaries; explicit numeric experience only | Passed |
| Optional model analysis | Strict schema, exact quotes, existing fact IDs and unsupported-output rejection | Mock API contract passed; live credentials absent |
| Salary / FX | Monthly/annual normalization; source/period/basis/jurisdiction checks; dated current EGP→SAR public-provider response stored locally; gross remains unknown without payroll evidence | Passed scoped rules |
| Browser engine | Chromium fixtures cover controls, uploads, unknowns and restrictions; exact vacancy URL/job ID guards and real Total-TECH positive receipt | Passed fixtures and one audited live form |
| Profile completion | Actual local profile-editor fixture: missing known fields only, conflicts retained, save confirmation | Passed local fixture |
| Connected pipeline | Supplied CV → canonical profile → real job discovery/analysis/research → tailored LaTeX/PDF → known answers → employer form → receipt → exact snapshot → SQLite/CSV | Passed for Total-TECH form |
| Duplicate/recovery safety | Repeated and concurrent claims, cross-platform missing-location matches, rejected history, crash reconciliation, stale candidate/job revisions and CV tampering | Covered by regressions |
| Live operation | Production batch: 132 discovered, 129 excluded, two pending unknowns, one confirmed submission; repeats retained exactly one submit event | First real application verified |
| GitHub | Requested private repository created under authenticated account; source/data exclusions verified | Passed: private main pushed and verified |

## Evidence locations (private local data)

- `data/source-cv/manifest.json` and immutable copies/extractions.
- `data/approved/user-clarification-2026-09-26.json`; prior profile revision under `data/master-cv/revisions/`.
- `data/master-cv/master-cv.tex` and `master-cv.pdf`, pointing to immutable version `2add1b1bee63bd100dbb` with the later approved English level. Earlier version `d7cab1a32c897821e899` remains preserved.
- `data/discovery/` contains complete source listings, source failures, original and corrected assessments. Earlier classifications are retained in history; the latest production database is authoritative for current states.
- `data/live-run-verified.json`, `data/report-latest.json`, `data/workai.sqlite3`, `data/applications/applications.csv`.
- `data/fx/EGP_SAR.json` contains the actual dated rate evidence, not a hardcoded future answer.
- `logs/` and immutable application-attempt snapshots preserve errors and attempted stages. The one earlier preparation that reached an unsupported adapter was subsequently excluded after parser review; it was never submitted.

## Remaining operational acceptance

1. Broaden current-vacancy and permitted platform coverage without weakening truthfulness. The first receipt is from a 2023 listing still accepting applications; it does not prove current recruiter activity.
2. Audit additional employers' routes, required questions and confirmations; configure required login sessions securely. The current production mapping is restricted to the one audited Total-TECH vacancy.
3. Resolve only genuinely missing mandatory candidate facts. Current examples include language proficiency, academic dates/grades, citizenship/military status where applicable, explicit base/total compensation, and any gross calculation requiring reliable payroll rules. Do not infer these from the CV or job description.
4. Resolve the already-requested mandatory Advansys answers and establish salary field units before applying. Continue unrelated compatible jobs while those answers are pending.
5. Extend real acceptance to additional forms and platform profile completion. General-layout extraction and broader model reasoning remain engineering work. Reviewed OCR and bounded optional model analysis do not establish universal document understanding; no live model call has been made.

## First confirmed live application

- Employer/position: Total-TECH Co., Junior FrontEnd Developer, Cairo, Egypt.
- Source: `https://totaltech.me/job/total-tech-co-cairo-egypt-full-time-junior-frontend-developer/`, posting date **2023-08-29**. Normal application form had no expired/filled marker or visible authentication/challenge.
- Confirmation: **“Your job application has been submitted successfully”**, observed **2026-09-26 16:42:00 UTC**. No separate confirmation ID was exposed.
- Application: `app_6329a2f7f4c947939eb1164c661df842`; source vacancy ID `21084`. Internal job `job_6bd3762ec29bd7f43c5f3768`.
- Uploaded filename: `Total-TECH_Co_Junior_FrontEnd_Developer_CV.pdf`; SHA256 `342f57ca481503668f3f481635e2fa2387db8a2f52b6d823d2f0fa62620beff5`. Exact LaTeX/PDF, job/research/ATS, sourced answers, durable intent and result are sealed in the application attempt.
- Receipt screenshot was visually inspected; text/screenshot hashes and the entire snapshot manifest were verified. CSV records SUBMITTED. The application used known name/email and unchanged verified project descriptions; no candidate fact was invented.
- Repeat run and full 94-job run retained **one APPLICATION_STARTED, one SUBMISSION_INTENT and one SUBMITTED event**, with duplicate-prevention events instead of another browser attempt. A run summary's SUBMITTED state is an existing application, not another application count.
- Private evidence index: `data/verification-first-live.json`. Supporting runs: `data/totaltech-live-result.json`, `data/totaltech-repeat-result.json`, `data/live-run-first-confirmed.json`, `data/report-first-confirmed.json`.

This establishes submission to the website and stored evidence. It does not prove recruiter review, recent hiring activity, an interview or hiring probability. The original sealed snapshot remains unchanged; its receipt paths point to retained private `data/browser-evidence/` artifacts.

The later 100-job batch added six sourced descriptions and recorded 98 exclusions, one required-information block and the same single confirmed application. Evidence: `data/live-run-100-jobs.json`, `data/report-100-jobs.json`, and the direct-employer source audit. Subsequent approved name components, English Good, gender and current salary units were archived privately and incorporated without altering the submitted CV. New snapshot-archival regressions verify that local receipt damage or persistence failures cannot erase a confirmed submission or cause a repeat submit.

## Platform observations

Normal unauthenticated Wuzzuf and Bayt search requests returned HTTP 403; Naukrigulf timed out. These sources were recorded and not bypassed. A normal browser inspection of a Bosta Lever QA application revealed custom required questions and hCaptcha integration; the role was unsuitable, no fields were filled and no CV was uploaded. Public feed availability does not prove form support. See `docs/JOB_SOURCES.md` and `docs/PLATFORMS.md`.

## Verification after the first live receipt

The latest recurring-search/custom-control implementation passed **327 tests** locally in 96.19 seconds, including actual OCR, LaTeX/PDF and Chromium. Live WP Job Manager discovery retrieved 38 full descriptions and added 32 unique jobs. The final production run has **132 jobs: 129 excluded, two blocked on unknowns and the same single confirmed submission**. An independent final-code assessment matched all tracker states. Snapshot/receipt hashes, original source hashes, canonical evidence, master CV and CSV confirmation were rechecked; the live application still has exactly one APPLICATION_STARTED, one SUBMISSION_INTENT and one SUBMITTED event. No second application was sent.

Private evidence: `data/verification-job-manager-custom-controls.json`, `data/live-run-job-manager-final.json`, `data/report-job-manager-final.json`. The two pending workflows are Advansys's junior Angular vacancy and a Total-TECH senior full-stack vacancy lacking explicit experience requirements. Discovery of additional old vacancies does not establish recent recruiter activity. Custom-control tests establish the implemented widget contracts; Advansys's salary semantics, gender proxy value and pending candidate declarations still prevent enabling its live adapter.

The complete local suite passed **266 tests** in 129.52 seconds, including actual OCR, LaTeX/PDF compilation and Chromium fixtures. The source snapshot is commit `35ed5a76896f72317d858809f50d22029681c612`; the tracked-secret audit passed for all 50 tracked files. Local and remote `main` matched and repository visibility was verified PRIVATE.

A separate final-code assessment of all 100 jobs found no classification differences from the tracker: 98 exclusions, one required-information block and the one qualified/submitted job. Current canonical source evidence, master CV quality gates, the original submitted snapshot manifest, browser receipt hashes and exactly one submission event were rechecked. Private evidence: `data/verification-266-tests-first-live.json`.

GitHub reported **success** for the same implementation commit in [CI run 36257389157](https://github.com/Ahmed-elssamman/Ahmed-ElSamman-CV-Automation/actions/runs/36257389157), exercising fresh Ubuntu/Python 3.12, OCR, Chromium, LaTeX and the tracked-secret audit. The receipt is independent of the local test result.

## Historical verification record — before the first live receipt

GitHub CI passed on implementation commit `3dd96fa588f0a41a57b6ba6d17888c9627ecfaec`: **111 tests passed**, including actual PDF compilation and Chromium integration, plus the tracked-secret audit. [Verified workflow run](https://github.com/Ahmed-elssamman/Ahmed-ElSamman-CV-Automation/actions/runs/36253883443). `main` was pushed; local and remote heads matched; Git status was clean; repository visibility was PRIVATE. No fixture is counted as a real application.

Local verification completed: full suite **110 passed**; after the final snapshot-log and historical-identity regression changes, the affected suites **38 passed** (111 distinct collected tests overall). The tests include actual Tectonic PDF compilation and real Chromium fixtures. Final production state is 90 discovered, 90 excluded, zero submitted. Private GitHub visibility and ignored `.env`, credentials, tokens, cookies, keys, browser config and candidate data paths were verified. Source-only commits are audited before pushing.

## Expanded verification checkpoint

Local suite passed **161 tests**. The subsequent degree-alternative regression and affected analyzer suite passed **38 tests**, bringing the collected total to **162**. This includes actual LaTeX/PDF generation, real Chromium forms, two-source reconciliation, source/review integrity and a real Tesseract recognition fixture. CI now installs Tesseract and English data so that OCR fixture can run in a fresh environment.

Production expansion discovered 18 additional vacancies, all with full employer descriptions. The latest live batch processed all **90 stored jobs** and recorded **90 exclusions, zero submissions**. Source audits include 48 further Workable leads, plus Forasna/Akhtaboot public searches; these results describe the inspected sources only. Shared FlairsTech careers URLs retain distinct external vacancy identities. No candidate data was entered into real forms.

Repeat ingestion preserved the exact canonical profile bytes and both source PDF checksums. Current master CV version remains `d7cab1a32c897821e899`, with source evidence and PDF quality gates rechecked. Local evidence: `data/verification-expanded.json`, `data/discovery-expanded-result.json`, `data/live-run-expanded-verified.json`, `data/report-expanded.json`.

GitHub verified the expanded implementation commit `1c56250882c57f80d7ba99ea4ac9b1618dba89c6`: **162 tests passed**, including fresh Ubuntu OCR and browser/LaTeX fixtures, plus the tracked-secret audit. [Expanded CI run](https://github.com/Ahmed-elssamman/Ahmed-ElSamman-CV-Automation/actions/runs/36255409767). Private visibility, matching local/remote main and clean status were checked after the push. A subsequent identity regression now prevents another person’s CV from being relabelled as the configured candidate; no runtime profile facts were changed.

The follow-up identity guard passed **36 local profile/CV/pipeline tests**, including OCR and connected browser acceptance; **163 tests** are now collected.

Final implementation verification: commit `3130a6979b44f46e4c99f199dece421699cf852d` passed **all 163 tests** and the tracked-secret audit in [GitHub CI](https://github.com/Ahmed-elssamman/Ahmed-ElSamman-CV-Automation/actions/runs/36255546984). The private remote and local `main` matched, with clean status. Real application acceptance remains pending; production counts remain 90 evaluated/excluded and zero submitted.
