"""Local command-line operations. Live run has standing authorization to submit."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

from .security import redact, utcnow


def _json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def parser():
    parser = argparse.ArgumentParser(description="WORKAI — truthful CV and application operations")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Workspace containing workAI.md and data/")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create private runtime directories and database")
    ingest = sub.add_parser("ingest", help="Archive/extract source PDFs and normalize candidate facts")
    ingest.add_argument("pdfs", nargs="+", type=Path)
    ingest.add_argument("--ocr", action="store_true", help="Use local OCR for image-only pages; results require evidence review")
    ingest.add_argument("--ocr-language", default="eng", help="Installed Tesseract language code(s)")
    review = sub.add_parser("review-ocr", help="Archive transcription compared against every original page; does not ingest")
    review.add_argument("source_id")
    review.add_argument("--pages-json", type=Path, required=True, help="JSON list of complete reviewed {page, text} records")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--extraction-sha256", required=True)
    review.add_argument("--note", required=True, help="Actual review performed and corrections made")
    sub.add_parser("build-cv", help="Compile and validate master LaTeX/PDF")
    tailor = sub.add_parser("tailor-cv", help="Build a truthful CV and analysis for a stored job without applying")
    tailor.add_argument("job_id")
    sync = sub.add_parser("sync-profile", help="Fill known missing fields in an audited platform profile")
    sync.add_argument("platform")
    sync.add_argument("--inspect-only", action="store_true")
    sub.add_parser("validate-profile")
    ai = sub.add_parser("ai-analyze", help="Source-cited AI review for a stored job; never submits")
    ai.add_argument("job_id")
    sub.add_parser("unknowns", help="Show missing profile and application questions")
    sub.add_parser("discover", help="Fetch configured live public job feeds")
    load = sub.add_parser("import-jobs", help="Import actual job JSON records without submitting")
    load.add_argument("path", type=Path)
    run = sub.add_parser("run", help="Discover, prepare, and automatically submit eligible known applications")
    run.add_argument("--prepare-only", action="store_true", help="Generate CVs without opening/submitting application forms")
    run.add_argument("--no-discover", action="store_true")
    run.add_argument("--limit", type=int)
    run.add_argument("--job-id", action="append", dest="job_ids")
    sub.add_parser("applications")
    sub.add_parser("jobs")
    sub.add_parser("export")
    sub.add_parser("report")
    sub.add_parser("recover", help="Mark interrupted application attempts as requiring reconciliation")
    reconcile = sub.add_parser("reconcile", help="Record verified platform outcome; never blindly retry")
    reconcile.add_argument("application_id")
    reconcile.add_argument("--status", choices=["submitted", "not-submitted"], required=True)
    reconcile.add_argument("--evidence", required=True, help="What was checked and its source")
    reconcile.add_argument("--confirmation-url")
    reconcile.add_argument("--confirmation-id")
    reconcile.add_argument("--submitted-at")
    answer = sub.add_parser("answer", help="Record an explicitly supplied approved answer")
    answer.add_argument("--question", required=True)
    answer.add_argument("--answer-json", required=True, help='JSON value, e.g. false or "30 days"')
    answer.add_argument("--source", default="explicit-user-input:cli")
    answer.add_argument("--scope-json", default="{}")
    resolve = sub.add_parser("resolve", help="Resolve a question without a form submission")
    resolve.add_argument("question")
    resolve.add_argument("--job-id")
    resolve.add_argument("--context-json", default="{}")
    sub.add_parser("agents")
    sub.add_parser("doctor")
    sub.add_parser("security-audit")
    return parser


def main(argv=None) -> int:
    # Private runtime data should not inherit a world-readable creation mask.
    os.umask(0o077)
    arguments = parser().parse_args(argv)
    root = arguments.root.resolve()
    command = arguments.command
    try:
        from .store import Store
        if command == "init":
            for relative in ("data/source-cv", "data/master-cv", "data/answers-bank", "data/cv-versions", "data/companies", "data/applications", "applications", "logs"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            with Store(root) as store:
                output = {"database": str(store.path), "csv": str(store.export_csv())}
        elif command == "ingest":
            from .profile import ingest_pdfs
            profile = ingest_pdfs(root, arguments.pdfs, ocr=arguments.ocr, ocr_language=arguments.ocr_language)
            with Store(root) as store:
                store.save_candidate(profile)
            output = {"profile_path": str(root / "data/master-cv/profile.yaml"), "source_ids": profile.get("source_ids"), "unknown_fields": profile.get("unknowns")}
        elif command == "build-cv":
            from .cv import build_master
            output = build_master(root)
        elif command == "review-ocr":
            from .profile import review_ocr
            output = review_ocr(root, arguments.source_id,
                                reviewed_pages=json.loads(arguments.pages_json.read_text()),
                                reviewer=arguments.reviewer, expected_extraction_sha256=arguments.extraction_sha256,
                                note=arguments.note)
        elif command == "validate-profile":
            from .profile import load_profile, validate_profile
            output = validate_profile(load_profile(root))
        elif command == "ai-analyze":
            from .profile import load_profile
            from .reasoning import review_job
            with Store(root) as store:
                output = review_job(root, store.get_job(arguments.job_id), load_profile(root))
        elif command == "tailor-cv":
            from .profile import load_profile
            from .cv import tailor_cv
            from .jobs import analyze_job, ats_analysis, assess_eligibility
            from .orchestrator import runner_lock
            with runner_lock(root), Store(root) as store:
                profile = load_profile(root)
                job = analyze_job(store.get_job(arguments.job_id), profile)
                analysis = ats_analysis(job, profile)
                output = {"eligibility": assess_eligibility(job, profile), "ats": analysis, "cv": tailor_cv(root, job, analysis), "submission": "not attempted"}
        elif command == "sync-profile":
            from .profile import load_profile
            from .orchestrator import Orchestrator, runner_lock
            runner = Orchestrator(root)
            try:
                with runner_lock(root):
                    output = runner.optimize_profile(arguments.platform, load_profile(root), write=not arguments.inspect_only)
            finally:
                runner.close()
        elif command == "unknowns":
            from .profile import load_profile
            with Store(root) as store:
                events = [json.loads(row[0]) for row in store.db.execute("SELECT data FROM application_events WHERE event_type='UNKNOWN_APPLICATION_FIELD'")]
            output = {"candidate": load_profile(root).get("unknowns", []), "application_fields": events}
        elif command in {"discover", "run"}:
            from .orchestrator import Orchestrator
            runner = Orchestrator(root)
            try:
                if command == "discover":
                    output = runner.discover()
                else:
                    if arguments.limit is not None and arguments.limit < 1:
                        raise ValueError("limit must be positive")
                    output = runner.run(discover=not arguments.no_discover, submit=not arguments.prepare_only, limit=arguments.limit, job_ids=arguments.job_ids)
            finally:
                runner.close()
        elif command == "import-jobs":
            from .jobs import parse_job
            payload = json.loads(arguments.path.read_text())
            records = payload if isinstance(payload, list) else payload.get("jobs", [payload])
            with Store(root) as store:
                imported = [store.upsert_job(parse_job(record, platform=record.get("platform", "manual"))) for record in records]
            output = {"imported": len(imported), "new": sum(new for _, new in imported), "job_ids": [job["id"] for job, _ in imported]}
        elif command in {"applications", "jobs", "export", "recover", "reconcile"}:
            with Store(root) as store:
                if command == "applications":
                    output = store.applications()
                elif command == "jobs":
                    output = store.jobs()
                elif command == "export":
                    output = {"csv": str(store.export_csv())}
                elif command == "recover":
                    from .orchestrator import runner_lock
                    with runner_lock(root):
                        output = store.recover()
                        store.export_csv()
                else:
                    from .orchestrator import runner_lock
                    with runner_lock(root):
                        status = "SUBMITTED" if arguments.status == "submitted" else "READY_TO_APPLY"
                        updates = {"retryable": arguments.status != "submitted"}
                        if status == "SUBMITTED":
                            updates.update(confirmation_url=arguments.confirmation_url, confirmation_id=arguments.confirmation_id, submitted_at=arguments.submitted_at or utcnow())
                        output = store.transition(arguments.application_id, status, updates, evidence=arguments.evidence)
                        store.export_csv()
        elif command == "answer":
            from .answers import add_answer
            output = add_answer(root, arguments.question, json.loads(arguments.answer_json), source=arguments.source, scope=json.loads(arguments.scope_json))
        elif command == "resolve":
            from .answers import resolve_question
            from .profile import load_profile
            with Store(root) as store:
                job = store.get_job(arguments.job_id) if arguments.job_id else {}
            output = resolve_question(root, arguments.question, load_profile(root), job, json.loads(arguments.context_json))
        elif command == "report":
            from .reporting import report
            output = report(root)
        elif command == "agents":
            from .agents import describe_agents
            output = describe_agents()
        elif command == "security-audit":
            from .security import audit_tracked_files
            output = audit_tracked_files(root)
        elif command == "doctor":
            from .cv import find_compiler
            from .reasoning import configured
            try:
                compiler = find_compiler(root)
            except Exception as error:
                compiler = {"error": str(error)}
            output = {"python": sys.version, "poppler": shutil.which("pdftotext"), "latex": compiler,
                      "playwright": bool(importlib.util.find_spec("playwright")),
                      "master_profile": (root / "data/master-cv/profile.yaml").exists(),
                      "platform_config": (root / "config/platforms.yaml").exists(),
                      "runtime_ai_configured": configured(root),
                      "operating_manual": (root / "workAI.md").exists()}
        else:
            raise ValueError(f"Unknown command {command}")
        _json(output)
        return 1 if isinstance(output, dict) and (output.get("valid") is False or output.get("passed") is False) else 0
    except Exception as error:
        print(json.dumps({"status": "ERROR", "error": redact(str(error)), "type": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
