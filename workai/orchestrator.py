"""Persistent stage execution with a durable boundary before external submission."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .security import AuditLogger, redact, utcnow, write_json
from .store import APPLICATION_ARCHIVE_FIELDS, Store, TERMINAL, stable_id


@contextmanager
def runner_lock(root: Path):
    path = root / "data" / "runner.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another WORKAI run owns the application lock") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _fingerprint_files(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("data/master-cv/profile.yaml", "data/answers-bank/answers.json", "config/platforms.yaml"):
        path = root / relative
        digest.update(relative.encode())
        if path.exists():
            if relative.endswith("answers.json"):
                entries = json.loads(path.read_text()).get("answers", [])
                digest.update(json.dumps([{key: value for key, value in entry.items() if key not in {"last_used", "times_used", "companies_used"}} for entry in entries], sort_keys=True).encode())
            else:
                digest.update(path.read_bytes())
    # Analyzer/answer/QA fixes must invalidate a previously unsafe classification.
    for module in ("jobs.py", "answers.py", "cover.py", "cv.py", "orchestrator.py", "browser.py", "reasoning.py"):
        digest.update((Path(__file__).parent / module).read_bytes())
    from .reasoning import settings
    ai = settings(root)
    digest.update(json.dumps({"model": ai.get("WORKAI_OPENAI_MODEL"), "configured": bool(ai.get("OPENAI_API_KEY"))}, sort_keys=True).encode())
    return digest.hexdigest()


def profile_fingerprint(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def job_fingerprint(job: dict) -> str:
    fields = ("company", "position", "description", "job_url", "application_url", "apply_url", "platform", "country", "city", "remote",
              "remote_from_egypt", "employment_type", "seniority", "required_skills", "preferred_skills", "min_years_experience",
              "salary_range", "application_deadline", "application_questions", "work_authorization_required", "status")
    return hashlib.sha256(json.dumps({key: job.get(key) for key in fields}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Orchestrator:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.store = Store(self.root)
        self.logger = AuditLogger(self.root)
        self._profiles_inspected = set()

    def close(self):
        self.store.close()

    def operation(self, agent: str, action: str, job: dict, function, *args, retries=0, **kwargs):
        for attempt in range(retries + 1):
            started = time.monotonic()
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                self.logger.emit(agent, action, "FAILED", job=job, duration=time.monotonic() - started,
                                 error=error, retry_count=attempt,
                                 resolution="retry" if attempt < retries else "record failure and continue")
                if attempt == retries:
                    raise
                time.sleep(min(2 ** attempt, 4))
            else:
                self.logger.emit(agent, action, "OK", job=job, duration=time.monotonic() - started, retry_count=attempt)
                return result

    def discover(self, sources=None) -> dict:
        from .jobs import discover_jobs
        jobs = self.operation("Job Discovery Agent", "discover", {}, discover_jobs, self.root, sources=sources)
        created = 0
        ids = []
        for job in jobs:
            normalized, is_new = self.store.upsert_job(job)
            created += int(is_new)
            ids.append(normalized["id"])
        return {"fetched": len(jobs), "new": created, "job_ids": ids}

    def optimize_profile(self, platform: str, profile: dict, *, write=True) -> dict:
        from .profile_sync import sync_profile
        result = self.operation("Profile Optimization Agent", "sync_profile", {"platform": platform}, sync_profile, self.root, platform, profile, write=write)
        self.store.save_candidate(profile)
        self.store.db.execute("INSERT INTO platform_profiles VALUES(?,?,?,?,?)", (
            "profile_" + uuid.uuid4().hex, profile.get("candidate_id", "ahmed-el-samman"), platform,
            json.dumps(result, ensure_ascii=False), utcnow()))
        return result

    def run(self, *, discover=True, submit=True, limit: int | None = None, sources=None, job_ids=None) -> dict:
        from .profile import load_profile, validate_profile
        with runner_lock(self.root):
            started_at = utcnow()
            profile = load_profile(self.root)
            quality = validate_profile(profile)
            if not quality["valid"]:
                raise ValueError(f"Master Profile invalid: {quality['errors']}")
            self.store.save_candidate(profile)
            recovered = self.store.recover()
            discovery = self.discover(sources) if discover else None
            outcomes = []
            for job in self.store.jobs():
                if job_ids and job["id"] not in job_ids:
                    continue
                if limit is not None and len(outcomes) >= limit:
                    break
                # Pick up approved edits between jobs without mixing facts within an attempt.
                profile = load_profile(self.root)
                quality = validate_profile(profile)
                if not quality["valid"]:
                    raise ValueError("Master Profile changed to invalid data during run")
                outcomes.append(self.process(job, profile, submit=submit))
            self.store.export_csv()
            result = {"started_at": started_at, "finished_at": utcnow(), "mode": "live" if submit else "prepare", "discovery": discovery,
                      "recovered": len(recovered), "processed": len(outcomes), "outcomes": outcomes,
                      "counts": dict(Counter(x["status"] for x in outcomes))}
            run_id = utcnow().replace(":", "-") + "-" + uuid.uuid4().hex[:8]
            write_json(self.root / "data" / "runs" / f"{run_id}.json", result)
            return result

    def process(self, job: dict, profile: dict, *, submit: bool) -> dict:
        from .jobs import analyze_job, assess_eligibility, ats_analysis, research_company
        from .cv import tailor_cv
        from .browser import apply_job
        from .profile import load_profile

        app, _created = self.store.create_application(job)
        if app is None:
            return {"job_id": job["id"], "status": "DUPLICATE_DETECTED"}
        app_id = app["application_id"]
        state = app["application_status"]
        fingerprint = _fingerprint_files(self.root)
        if state in TERMINAL or state == "RECONCILIATION_REQUIRED":
            return {"job_id": job["id"], "application_id": app_id, "status": state}
        if state != "DISCOVERED" and job_fingerprint(app.get("job_snapshot", {})) != job_fingerprint(job):
            # Preserve prior values in append-only transition events, but evaluate
            # current employer requirements for a not-yet-submitted application.
            app = self.store.transition(app_id, "ANALYZED", {"job_snapshot": job, "analyzed_job": None})
            state = "ANALYZED"
        if state in {"EXCLUDED", "BLOCKED_UNKNOWN_FIELDS", "BLOCKED_BY_PLATFORM"}:
            if app.get("knowledge_fingerprint") == fingerprint:
                return {"job_id": job["id"], "application_id": app_id, "status": state}
            app = self.store.transition(app_id, "ANALYZED", {"knowledge_fingerprint": fingerprint})
        if app["application_status"] in {"ANALYZED", "QUALIFIED", "RESEARCHED", "TAILORED", "CV_GENERATED", "CV_VALIDATED", "READY_TO_APPLY", "FAILED"} and app.get("knowledge_fingerprint") != fingerprint:
            app = self.store.transition(app_id, "ANALYZED", {"knowledge_fingerprint": fingerprint})
        if app["application_status"] == "FAILED":
            if not app.get("retryable") or app.get("retry_count", 0) >= 3:
                return {"job_id": job["id"], "application_id": app_id, "status": "FAILED", "failure_reason": app.get("failure_reason")}
            target = "READY_TO_APPLY" if app.get("cv") else "RESEARCHED" if app.get("research") else "ANALYZED"
            app = self.store.transition(app_id, target, {"retry_count": app.get("retry_count", 0) + 1})
        snapshot = None
        try:
            # Submitted snapshots never change; pending workflows use the latest
            # employer revision, with prior versions retained in the event trail.
            job = app.get("analyzed_job") or app.get("job_snapshot") or job
            if app["application_status"] == "DISCOVERED":
                analysis = self.operation("Job Analysis Agent", "analyze", job, analyze_job, job, profile)
                job = {**job, **analysis}
                app = self.store.transition(app_id, "ANALYZED", {"analyzed_job": job, "knowledge_fingerprint": fingerprint})
            if app["application_status"] == "ANALYZED":
                job = self.operation("Job Analysis Agent", "reanalyze", job, analyze_job, app.get("job_snapshot") or job, profile)
                eligibility = self.operation("Job Analysis Agent", "eligibility", job, assess_eligibility, job, profile)
                ats = self.operation("ATS Analysis Agent", "score", job, ats_analysis, job, profile)
                from .reasoning import configured, review_job
                if eligibility["eligible"] and configured(self.root):
                    review = self.operation("AI Source Review Agent", "source_review", job, review_job, self.root, job, profile)
                    ats["ai_review"] = review
                    concerns = [item["quote"] for item in review["review"]["requirements"]
                                if item["importance"] in {"required", "unclear"} and item["needs_factual_review"]]
                    if concerns:
                        eligibility["unknowns"].extend("Source-cited AI review requires factual resolution: " + quote for quote in concerns)
                        eligibility.update(eligible=False, status="NEEDS_INFORMATION")
                    self.store.event("AI_SOURCE_REVIEW", {"review_id": review["id"], "model": review["model"], "concerns": concerns}, application_id=app_id, job_id=job["id"])
                self.store.save_artifact("ats", stable_id("ats", job["id"], json.dumps(ats, sort_keys=True)), job["id"], ats)
                updates = {"eligibility": eligibility, "ats": ats, "knowledge_fingerprint": fingerprint,
                           "profile_fingerprint": profile_fingerprint(profile), "analyzed_job": job, "cv": None, "research": None}
                if not eligibility["eligible"]:
                    status = "BLOCKED_UNKNOWN_FIELDS" if eligibility.get("unknowns") and not eligibility.get("reasons") else "EXCLUDED"
                    app = self.store.transition(app_id, status, updates)
                    for unknown in eligibility.get("unknowns", []):
                        self.store.event("UNKNOWN_APPLICATION_FIELD", {"field": unknown, "stage": "eligibility"}, application_id=app_id, job_id=job["id"])
                    return {"job_id": job["id"], "application_id": app_id, "status": status, "eligibility": eligibility}
                app = self.store.transition(app_id, "QUALIFIED", updates)
            if app["application_status"] == "QUALIFIED":
                research = self.operation("Company Research Agent", "research", job, research_company, self.root, job, retries=1)
                company_id = self.store.db.execute("SELECT company_id FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
                self.store.save_artifact("research", stable_id("research", company_id, json.dumps(research, sort_keys=True)), company_id, research)
                app = self.store.transition(app_id, "RESEARCHED", {"research": research})
            if app["application_status"] == "RESEARCHED":
                cv = self.operation("CV Tailoring Agent", "tailor_compile_validate", job, tailor_cv, self.root, job, app["ats"])
                if profile_fingerprint(load_profile(self.root)) != profile_fingerprint(profile):
                    raise ValueError("Master Profile changed during CV generation; reanalysis required")
                cv["master_profile_fingerprint"] = profile_fingerprint(profile)
                self.store.save_artifact("cv", cv["version_id"], app_id, cv)
                app = self.store.transition(app_id, "TAILORED", {"cv": cv})
            if app["application_status"] == "TAILORED":
                app = self.store.transition(app_id, "CV_GENERATED")
            if app["application_status"] == "CV_GENERATED":
                self.operation("QA Agent", "validate_artifacts", job, validate_cv_artifacts, app["cv"], profile)
                app = self.store.transition(app_id, "CV_VALIDATED")
            if app["application_status"] == "CV_VALIDATED":
                app = self.store.transition(app_id, "READY_TO_APPLY")
            if app["application_status"] != "READY_TO_APPLY":
                return {"job_id": job["id"], "application_id": app_id, "status": app["application_status"]}
            if not submit:
                return {"job_id": job["id"], "application_id": app_id, "status": "READY_TO_APPLY", "cv": app["cv"]}
            # Recheck live eligibility and artifacts after restart/profile changes before disclosing data.
            eligibility = assess_eligibility(job, profile)
            if not eligibility["eligible"]:
                app = self.store.transition(app_id, "FAILED", {"failure_reason": "Eligibility changed; reanalysis required", "retryable": False, "eligibility": eligibility})
                return {"job_id": job["id"], "application_id": app_id, "status": "FAILED"}
            validate_cv_artifacts(app["cv"], profile)
            if profile_fingerprint(load_profile(self.root)) != profile_fingerprint(profile):
                raise ValueError("Master Profile changed before application; reanalysis required")
            platform = job.get("platform", "")
            if platform not in self._profiles_inspected:
                from .browser import _load_adapter
                adapter = _load_adapter(self.root, platform)
                if adapter and adapter.get("profile"):
                    self.optimize_profile(platform, profile, write=True)
                self._profiles_inspected.add(platform)
            snapshot = self.snapshot(app, job)
            app = self.store.transition(app_id, "APPLICATION_STARTED", {"snapshot_path": str(snapshot), "last_attempt": utcnow(), "knowledge_fingerprint": fingerprint})
            attempt_knowledge = _fingerprint_files(self.root)

            def before_submit(evidence):
                if profile_fingerprint(load_profile(self.root)) != profile_fingerprint(profile):
                    raise ValueError("Master Profile changed during form filling; submit cancelled")
                if _fingerprint_files(self.root) != attempt_knowledge:
                    raise ValueError("Approved knowledge or adapter configuration changed during form filling; submit cancelled")
                if job_fingerprint(self.store.get_job(job["id"])) != job_fingerprint(app["job_snapshot"]):
                    raise ValueError("Job requirements changed during form filling; submit cancelled")
                records = question_records(evidence)
                self.store.record_questions(app_id, records)
                write_json(snapshot / "application-questions.json", records)
                write_json(snapshot / "submission-intent.json", evidence)
                self.store.transition(app_id, "SUBMISSION_INTENT", {"submission_intent": evidence})
                self.store.export_csv()

            cv = {**app["cv"], "tex_path": str(snapshot / "cv.tex"), "pdf_path": str(snapshot / "cv.pdf")}
            outcome = self.operation("Application Agent", "apply", job, apply_job, self.root, job, profile, cv, submit=True, before_submit=before_submit)
            if outcome.get("status") == "SUBMITTED":
                # The external fact must survive any subsequent local archival failure.
                outcome.setdefault("submitted_at", utcnow())
                self.store.transition(app_id, "SUBMITTED", outcome)
            records = question_records(outcome)
            self.store.record_questions(app_id, records)
            for unknown in outcome.get("unknown_events", []):
                self.store.event("UNKNOWN_APPLICATION_FIELD", unknown, application_id=app_id, job_id=job["id"])
            if not (snapshot / "application-questions.json").exists():
                write_json(snapshot / "application-questions.json", records)
            status = outcome.get("status", "FAILED")
            if status in {"SUBMISSION_UNCONFIRMED", "RECONCILIATION_REQUIRED"}:
                status = "RECONCILIATION_REQUIRED"
            if status not in {"SUBMITTED", "READY_TO_APPLY", "FAILED", "BLOCKED_UNKNOWN_FIELDS", "BLOCKED_BY_PLATFORM", "RECONCILIATION_REQUIRED"}:
                status = "FAILED"
                outcome.update(failure_reason="Unrecognized adapter result", retryable=False)
            if status == "SUBMITTED":
                outcome.setdefault("submitted_at", utcnow())
            outcome.update(application_questions=[r.get("question") for r in records], application_answers=records,
                           knowledge_fingerprint=_fingerprint_files(self.root))
            outcome = self.archive_browser_evidence(snapshot, outcome)
            if outcome.get("browser_evidence_archive_errors"):
                self.store.event("BROWSER_EVIDENCE_ARCHIVE_ERROR", {"errors": outcome["browser_evidence_archive_errors"]},
                                 application_id=app_id, job_id=job["id"])
            if status == "SUBMITTED":
                app = self.store.update_application_archive(app_id, {key: value for key, value in outcome.items() if key in APPLICATION_ARCHIVE_FIELDS})
            self.finalize_snapshot(snapshot, outcome)
            if status != "SUBMITTED":
                app = self.store.transition(app_id, status, outcome)
            return {"job_id": job["id"], "application_id": app_id, "status": app["application_status"], "failure_reason": outcome.get("failure_reason"),
                    "snapshot_path": str(snapshot), "browser_evidence_archive_errors": outcome.get("browser_evidence_archive_errors", [])}
        except Exception as error:
            latest = self.store.application(app_id)
            if latest["application_status"] == "SUBMITTED":
                # A receipt is an external fact; copying/sealing failures cannot undo it.
                archive_error = {"operation": "post_submission_archival", "error": redact(str(error)), "retryable": False}
                errors = latest.get("browser_evidence_archive_errors", []) + [archive_error]
                self.store.update_application_archive(app_id, {
                    "retryable": False, "browser_evidence_archive_errors": errors, "snapshot_archival_status": "INCOMPLETE"})
                self.store.event("APPLICATION_ARCHIVE_FAILED", archive_error, application_id=app_id, job_id=job["id"])
                self.logger.emit("Recovery Agent", "archive_after_submission", "SUBMITTED", job=job, error=error,
                                 resolution="Preserve confirmed submission; repair local archive without resubmitting")
                return {"job_id": job["id"], "application_id": app_id, "status": "SUBMITTED", "snapshot_path": str(snapshot),
                        "browser_evidence_archive_errors": errors, "snapshot_archival_status": "INCOMPLETE", "retryable": False}
            ambiguous = latest["application_status"] in {"APPLICATION_STARTED", "SUBMISSION_INTENT"}
            failed = {"failure_reason": redact(str(error)), "retryable": not ambiguous, "last_attempt": utcnow(), "knowledge_fingerprint": fingerprint}
            status = "RECONCILIATION_REQUIRED" if ambiguous else "FAILED"
            self.store.transition(app_id, status, failed)
            self.logger.emit("Recovery Agent", "job_failed", status, job=job, error=error,
                             resolution="reconcile platform submission" if ambiguous else "resume from persisted preparation stage")
            if snapshot and not (snapshot / "application-result.json").exists():
                self.finalize_snapshot(snapshot, {"status": status, **failed})
            return {"job_id": job["id"], "application_id": app_id, "status": status, **failed}
        finally:
            self.store.export_csv()

    def snapshot(self, app: dict, job: dict) -> Path:
        folder = self.root / "applications" / utcnow()[:4] / app["application_id"] / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=False)
        write_json(folder / "application.json", app)
        write_json(folder / "ats-analysis.json", app.get("ats", {}))
        write_json(folder / "job.json", job)
        (folder / "job-description.txt").write_text(job["description"])
        (folder / "company-research.md").write_text("# Sourced company research\n\n```json\n" + json.dumps(app.get("research", {}), indent=2, ensure_ascii=False) + "\n```\n")
        shutil.copyfile(app["cv"]["tex_path"], folder / "cv.tex")
        shutil.copyfile(app["cv"]["pdf_path"], folder / "cv.pdf")
        (folder / "logs").mkdir()
        return folder

    def archive_browser_evidence(self, folder: Path, result: dict) -> dict:
        """Copy verified receipt bytes into a new attempt, retaining original captures.

        Failures are evidence-quality findings, never a reason to resubmit an
        already confirmed application. Only the browser's two declared artifacts
        are copied; no headers, cookies, browser state or arbitrary files.
        """
        if (folder / "sha256-manifest.json").exists():
            raise ValueError("Cannot add evidence to an already sealed application snapshot")
        capture = result.get("browser_evidence")
        if not isinstance(capture, dict):
            return result
        archived = {key: value for key, value in capture.items()
                    if key not in {"directory", "visible_text_path", "screenshot_path", "visible_text_sha256", "screenshot_sha256"}}
        archived.update(original_capture=dict(capture), archive_status="FAILED", artifacts={})
        errors = []
        destination = folder / "browser-evidence"
        try:
            destination.mkdir(mode=0o700, exist_ok=False)
            archived["directory"] = str(destination.resolve())
            base = (self.root / "data" / "browser-evidence").resolve()
            source_directory = Path(capture.get("directory", "")).resolve(strict=True)
            if not source_directory.is_relative_to(base) or source_directory == base:
                raise ValueError("Receipt source directory is outside private browser capture storage")
            for kind, path_key, hash_key, filename in (
                    ("visible_text", "visible_text_path", "visible_text_sha256", "visible-page.txt"),
                    ("screenshot", "screenshot_path", "screenshot_sha256", "page.png")):
                if not capture.get(path_key):
                    errors.append({"artifact": kind, "error": "CAPTURE_ARTIFACT_MISSING"})
                    continue
                expected = capture.get(hash_key)
                if not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
                    errors.append({"artifact": kind, "error": "CAPTURE_HASH_MISSING_OR_INVALID"})
                    continue
                try:
                    source = Path(capture[path_key])
                    if source.is_symlink() or source.resolve(strict=True).parent != source_directory:
                        raise ValueError("Receipt artifact is not a regular direct capture file")
                    # One read binds the verified hash and copied content against source changes.
                    content = source.read_bytes()
                    actual = hashlib.sha256(content).hexdigest()
                    if actual != expected.lower():
                        errors.append({"artifact": kind, "error": "CAPTURE_INTEGRITY_MISMATCH",
                                       "expected_sha256": expected, "actual_sha256": actual})
                        continue
                    target = destination / filename
                    with target.open("xb") as handle:
                        target.chmod(0o600)
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    archived[path_key] = str(target.resolve())
                    archived[hash_key] = actual
                    archived["artifacts"][kind] = {"path": str(target.resolve()), "relative_path": str(target.relative_to(folder)),
                                                  "sha256": actual, "source_path": str(source.resolve()), "bytes": len(content)}
                except Exception as error:
                    errors.append({"artifact": kind, "error": type(error).__name__, "reason": redact(str(error))})
            archived["archive_status"] = "PARTIAL" if errors and archived["artifacts"] else "FAILED" if errors else "ARCHIVED"
            archived["archive_errors"] = errors
            write_json(destination / "evidence.json", archived)
        except Exception as error:
            errors.append({"operation": "archive_browser_evidence", "error": type(error).__name__, "reason": redact(str(error))})
            archived["archive_status"] = "PARTIAL" if archived["artifacts"] else "FAILED"
        return {**result, "browser_evidence": archived, "browser_evidence_archive_errors": errors}

    def finalize_snapshot(self, folder: Path, result: dict):
        if (folder / "sha256-manifest.json").exists():
            raise ValueError("Cannot modify an already sealed application snapshot")
        write_json(folder / "application-result.json", result)
        events = [dict(row) for row in self.store.db.execute(
            "SELECT seq,event_type,data,timestamp FROM application_events WHERE application_id=? ORDER BY seq",
            (folder.parent.name,))]
        write_json(folder / "logs" / "application-events.json", events)
        manifest = {}
        for path in folder.rglob("*"):
            if path.is_file():
                manifest[str(path.relative_to(folder))] = hashlib.sha256(path.read_bytes()).hexdigest()
                path.chmod(0o400)
        write_json(folder / "sha256-manifest.json", manifest)
        (folder / "sha256-manifest.json").chmod(0o400)


def validate_cv_artifacts(cv: dict, profile: dict) -> dict:
    from pypdf import PdfReader
    tex, pdf = Path(cv["tex_path"]), Path(cv["pdf_path"])
    if not tex.is_file() or not pdf.is_file() or pdf.read_bytes()[:5] != b"%PDF-":
        raise ValueError("CV artifacts missing or invalid")
    for path, key in ((tex, "tex_sha256"), (pdf, "pdf_sha256")):
        if cv.get(key) and hashlib.sha256(path.read_bytes()).hexdigest() != cv[key]:
            raise ValueError("CV artifact integrity checksum mismatch")
    if cv.get("master_profile_fingerprint") and cv["master_profile_fingerprint"] != profile_fingerprint(profile):
        raise ValueError("CV was generated from a different Master Profile")
    reader = PdfReader(pdf)
    if reader.is_encrypted or not reader.pages:
        raise ValueError("CV is encrypted or empty")
    text = " ".join(page.extract_text() or "" for page in reader.pages)
    if len(text.strip()) < 100:
        raise ValueError("CV does not contain sufficient selectable text")
    email = profile.get("contact", {}).get("email")
    if email and email not in text:
        raise ValueError("CV contact email does not match Master Profile")
    return {"valid": True, "pages": len(reader.pages), "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}


def question_records(outcome: dict) -> list[dict]:
    questions = outcome.get("questions") or []
    answers = outcome.get("answers") or []
    if isinstance(answers, dict):
        return [{"question": key, **(value if isinstance(value, dict) else {"answer": value})} for key, value in answers.items()]
    records = []
    for index, answer in enumerate(answers):
        if isinstance(answer, dict):
            record = dict(answer)
        else:
            record = {"answer": answer}
        if not record.get("question") and index < len(questions):
            record["question"] = questions[index].get("question", "") if isinstance(questions[index], dict) else questions[index]
        record.setdefault("date", utcnow())
        records.append(record)
    return records
