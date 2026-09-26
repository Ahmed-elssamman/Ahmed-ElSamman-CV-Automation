# WORKAI

Ahmed El-Samman's local job application operating system: evidence-backed candidate knowledge, LaTeX/PDF CVs, public job discovery, transparent ATS alignment, scoped recurring answers, persistent application state, and audited browser adapters.

Read [workAI.md](workAI.md) before operating or extending the system. It contains all 58 operational requirement sections and the current acceptance status. CV facts come from preserved originals and explicitly sourced edits. Target technologies and employer requirements never become candidate facts automatically.

## Current capabilities

The pipeline extracts supported CV layouts and hyperlinks, reconciles multiple sources without replacing canonical facts, retains evidence, compiles selectable-text LaTeX CVs, ranks relevant sourced content, fetches public employer feeds, analyzes eligibility and alignment, persists company research, and tracks every application stage. Optional local OCR requires transcription review before its results become CV evidence. The browser engine can fill and submit audited form mappings with confirmed receipts. It is tested against disposable local forms; production platform adapters remain disabled until actually audited. Source coverage is in [docs/JOB_SOURCES.md](docs/JOB_SOURCES.md); browser limitations are in [docs/PLATFORMS.md](docs/PLATFORMS.md).

No external model API is required. Optional OpenAI analysis extracts cited requirements and ranks existing fact IDs; local validation rejects unsupported quotes or candidate facts. Configure `OPENAI_API_KEY` and `WORKAI_OPENAI_MODEL` in ignored `.env` to enable it, then use `workai ai-analyze JOB_ID`. See [docs/AI_ANALYSIS.md](docs/AI_ANALYSIS.md). Current tailoring preserves exact source claims; unrestricted model-generated rewriting is not implemented. Major authenticated boards are not universally supported. A successful test fixture is not a real application. See [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) for measured acceptance evidence and remaining work.

## Setup

Use Python 3.11+, Git, Poppler (`pdftotext`), a LaTeX compiler, and Playwright Chromium. Linux is the initial supported runtime because the workflow and Answers Bank use filesystem locks. On Debian/Ubuntu, install Poppler with the OS package manager; the CI workflow shows the required browser and compiler dependencies. No production service or public dashboard needs to be exposed.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python scripts/install_tectonic.py
.venv/bin/python -m playwright install chromium
.venv/bin/workai init
.venv/bin/workai doctor
```

The compiler installer verifies the pinned official release checksum and installs locally under ignored `.tools/`. Initial Tectonic compilation downloads TeX bundle dependencies. A system `tectonic` or `pdflatex` may also be used. `WORKAI_TECTONIC=/absolute/path/to/tectonic` overrides compiler lookup. `.env.example` documents optional settings. The AI module reads only its explicitly allowed settings from `.env`; other settings must be exported through your shell or service configuration. Never paste secrets into tracked configuration.

Editable installation from a checkout is the supported deployment method. Keep candidate files and browser sessions backed up securely; they are intentionally absent from Git, including the private remote.

## Candidate knowledge and CVs

```bash
.venv/bin/workai ingest 'Ahmed_ELsamman_Resume.pdf' 'Ahmed_ELsamman_Resume (1).pdf'
.venv/bin/workai validate-profile
.venv/bin/workai unknowns
.venv/bin/workai build-cv
```

Original bytes are copied to immutable `data/source-cv/` files with SHA-256 manifests. Repeating ingestion is idempotent. Distinct PDFs are reconciled with source provenance. Initial conflicts are withheld; later ingestion preserves existing canonical values and records differences for review. Profile saves retain immutable revisions and detect stale writes. Supported layouts remain finite. `workai ingest --ocr FILE.pdf` enables local Tesseract for scanned pages; unreviewed text cannot enter the CV. See [docs/INGESTION.md](docs/INGESTION.md) for transcription review and reconciliation.

`data/master-cv/profile.yaml` is canonical and editable. Each fact requires provenance; unsupported changes fail CV generation. Section YAML files are derived exports. Retain source quotes and explicitly approved edit evidence. Master `.tex`/`.pdf` convenience links point to immutable version directories; application CVs have company/position filenames and content-based version IDs. Do not edit distribution PDFs.

Unknown personal/legal facts remain UNKNOWN. The user confirmed a 20-day notice period, Egypt authorization without sponsorship, remote availability from Egypt, and conditional Gulf relocation with employer visa/work authorization and reasonable support. Gulf authorization/residency/visas are absent; sponsorship is required for relocation. Salary targets are 40,000 EGP net/month in Egypt and approximately 50,000 EGP-equivalent net/month in the Gulf; annual figures multiply by twelve. Current sourced FX rates are required for local-currency answers. Gross conversion, explicit base/total components, citizenship, academic dates and language proficiency remain unknown when not supported.

```bash
# Example syntax only: record an answer only when the user has actually supplied it.
.venv/bin/workai answer --question 'Notice period' --answer-json '"USER_SUPPLIED_VALUE"'
.venv/bin/workai resolve 'Email address'
```

Legal answers need country scope; salary answers need currency, period, gross/net basis, and base/total component scope. Use `--scope-json` for those dimensions. Sources and revisions are retained in the Answers Bank. Never copy illustrative values into candidate facts.

## Discover, prepare, apply

Edit `config/discovery.yaml` to configure public sources and employer board identifiers. Restricted boards are explicitly disabled. Public feeds are fetched independently, with errors recorded without preventing other sources from running.

```bash
.venv/bin/workai discover
.venv/bin/workai jobs
.venv/bin/workai run --prepare-only --no-discover
.venv/bin/workai run --no-discover
```

The default `run` discovers jobs and **automatically submits** eligible jobs whose required answers and audited browser adapters are available, under the user's standing authorization. `--prepare-only` generates validated application CVs without opening or submitting forms. Use `--job-id ID` to target stored jobs or `--limit N` for a bounded batch. No arbitrary ATS threshold overrides hard eligibility requirements. Unresolved mandatory facts block only the affected job.

To import a real vacancy from a supported external workflow, save its full source-backed description and fields in JSON and use `workai import-jobs /path/to/jobs.json`. Importing a record is not a submission. Test fixtures belong in temporary roots, not the production database.

Configure browser mappings in ignored `config/platforms.yaml` using [config/platforms.example.yaml](config/platforms.example.yaml). Inspect actual forms, allowed hosts, required field semantics, navigation, and positive confirmation markers before enabling. Login state belongs in ignored `data/browser-sessions/`. There is no CAPTCHA or authentication bypass. Profile synchronization similarly requires a verified platform editor mapping; it fills only missing known fields and records existing conflicts.

## Tracking, reporting and recovery

```bash
.venv/bin/workai applications
.venv/bin/workai export
.venv/bin/workai report
.venv/bin/workai recover
```

SQLite under `data/workai.sqlite3` holds normalized state. `data/applications/applications.csv` is its current projection; historical events and sealed attempt directories retain every prior state. Exact CVs, source descriptions, research, answers, upload hashes, and confirmation results are stored under `applications/YEAR/APPLICATION_ID/ATTEMPT_ID/` with manifests. Reports live under `data/reports/` and include country/platform/company counts and skill gaps. ATS Match Score is estimated description alignment, never interview probability.

The runner has an exclusive lock. An interrupted application becomes `RECONCILIATION_REQUIRED` before another run. Inspect the actual platform/receipt and use `workai reconcile APPLICATION_ID --status submitted --confirmation-url URL --evidence 'source and observed receipt'` or `--status not-submitted --evidence 'verified absence and check performed'`. Never use the latter solely because confirmation was lost. Preparation failures have bounded retries. Unknown fields and platform blocks are reconsidered after knowledge/configuration changes. Confirmed applications never resubmit automatically.

Structured redacted logs are in `logs/workai.jsonl`. The command exits with an explicit error on system-level failures; individual job failures are captured and other jobs continue. Review the run summary for blocked, failed, and uncertain outcomes.

## Development and deployment

```bash
.venv/bin/python -m pytest -q
.venv/bin/workai security-audit
```

Tests cover extraction, truthfulness, compilation, job/salary analysis, scoped answers, browser controls/confirmation, deduplication, state transitions, recovery, CSV and secrets. Integration fixtures use temporary directories and never apply to an employer. Optional external-source checks and actual application evidence are separate.

See [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [SECURITY.md](SECURITY.md). Run locally as the owner of the private data. No recurring scheduler is installed automatically. A future scheduler should invoke bounded runs, preserve the same lock/database, and monitor unknowns/reconciliation states. Future Codex sessions can start with: **Read workAI.md and execute the project autonomously.**

Current indicative currency conversion data is provided by [ExchangeRate-API](https://www.exchangerate-api.com). Each used rate retains its source and publication time; it is not a payroll gross/net calculation.
