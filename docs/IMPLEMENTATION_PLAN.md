# Implementation plan

## Architecture decisions

Python 3.11+ package `workai`, command line entry `workai`, SQLite durable state, append-only application events, CSV exports, structured JSON logs, YAML candidate knowledge, Jinja-compatible LaTeX templates, Poppler PDF extraction, Tectonic/pdflatex compilation, and Playwright browser adapters. Candidate and application data remain local by default. Source code and operating documentation go to a private GitHub repository.

## Module contracts

Modules accept a project root `pathlib.Path` and exchange JSON-serializable dictionaries. Dates are ISO strings, unknown values are null with explicit missing-information records, and every assertion must carry provenance. Network content is untrusted data, never operating instructions.

* `profile.py`: `ingest_pdfs(root, paths)`, `load_profile(root)`, `validate_profile(profile)`; preserve source bytes and SHA256 manifests, extract text/links, write evidence-backed canonical profile.
* `cv.py`: `build_master(root)`, `tailor_cv(root, job, analysis)` returning a dictionary with absolute `tex_path`, `pdf_path`, `version_id`, `metadata_path`; compile and validate selectable text. Candidate schema coordinated in module documentation.
* `jobs.py`: `discover_jobs(root, sources=None)` returns jobs; `parse_job(payload, platform='manual')`; `analyze_job(job, profile)` returns extracted fields and keywords; `assess_eligibility(job, profile)` returns `eligible`, `reasons`, `unknowns`; `ats_analysis(job, profile)` returns scores and keywords; `research_company(root, job)` persists sourced facts. Job fields: `id`, `company`, `position`, `description`, `job_url`, `platform`, `country`, `city`, `remote`, `remote_from_egypt`, `employment_type`, `seniority`, `required_skills`, `preferred_skills`, `min_years_experience`, `salary_range`, `date_discovered`, `external_id`, `company_url`.
* `answers.py`: `resolve_question(root, question, profile, job, context=None)` returns `status` KNOWN/UNKNOWN, `answer`, `source`, `confidence`, and salary metadata where relevant. Persistent bank with revision history and safe semantic matching; `add_answer(...)` documented by owner.
* `browser.py`: `apply_job(root, job, profile, cv, *, submit=False)` returns `status`, `questions`, `answers`, `confirmation_id`, `confirmation_url`, `failure_reason`, `retryable`; only confirmed submission is SUBMITTED. Default CLI live run supplies submit=True under user's standing authorization. Explicit test/prepare mode never submits. Supported adapters must fail closed for unknown fields, expired sessions, platform restrictions and ambiguous confirmation.
* `store.py`: root-owned SQLite repository with normalized companies, jobs, applications, CV versions, ATS analyses, question/answer records, platform profiles, research records, events; atomic claims and duplicate prevention; CSV export.
* `orchestrator.py`, `cli.py`, `agents.py`: root-owned persistent lifecycle and recovery, specialist agent registry, operations and reporting.

## Milestones

1. Inspect original PDFs; preserve full operating manual; normalize verified master profile and explicit missing fields.
2. Test ingestion and truthful LaTeX/PDF generation, initialize Answers Bank and durable tracker.
3. Test job parsing, transparent ATS analysis, eligibility, multi-source public discovery and research.
4. Test browser automation and unknown-question handling against a local form fixture, then validate supported live adapters where credentials and eligibility allow.
5. Exercise repeatability, duplicate prevention, recovery and reporting. Preserve real-job evidence separately from test fixtures.
6. Audit secrets and Git history, create and verify private remote, commit and push code/docs, record actual capabilities and outstanding acceptance criteria.

## Acceptance rule

A successful local fixture is not a real application. A discovered vacancy is not a submitted application. Completion requires a confirmed suitable real-job submission and every required persisted artifact. Unknown legal/personal fields remain unresolved until supplied; unrelated jobs continue.
