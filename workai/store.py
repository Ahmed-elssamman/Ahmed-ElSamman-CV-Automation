"""SQLite state, immutable events, conservative deduplication, and CSV projection."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .security import atomic_write, utcnow


CSV_FIELDS = """application_id company position country city remote employment_type seniority
platform job_url company_url company_linkedin job_posted_date date_discovered date_applied
cv_version cv_tex_path cv_pdf_path ats_score ats_score_breakdown matched_keywords missing_keywords
salary_range expected_salary currency application_status application_questions application_answers
recruiter recruiter_url interview_date follow_up_date last_updated result rejection_reason notes
submitted_at confirmation_id confirmation_url failure_reason retryable last_attempt snapshot_path""".split()

TERMINAL = {"SUBMITTED", "INTERVIEW", "REJECTED", "WITHDRAWN"}
TRANSITIONS = {
    "DISCOVERED": {"ANALYZED", "EXCLUDED", "FAILED"},
    "ANALYZED": {"QUALIFIED", "EXCLUDED", "BLOCKED_UNKNOWN_FIELDS", "FAILED"},
    "QUALIFIED": {"ANALYZED", "RESEARCHED", "FAILED"},
    "RESEARCHED": {"ANALYZED", "TAILORED", "FAILED"},
    "TAILORED": {"ANALYZED", "CV_GENERATED", "FAILED"},
    "CV_GENERATED": {"ANALYZED", "CV_VALIDATED", "FAILED"},
    "CV_VALIDATED": {"ANALYZED", "READY_TO_APPLY", "FAILED"},
    "READY_TO_APPLY": {"APPLICATION_STARTED", "ANALYZED", "FAILED"},
    "APPLICATION_STARTED": {"SUBMISSION_INTENT", "SUBMITTED", "READY_TO_APPLY", "FAILED", "BLOCKED_UNKNOWN_FIELDS", "BLOCKED_BY_PLATFORM", "RECONCILIATION_REQUIRED"},
    "SUBMISSION_INTENT": {"SUBMITTED", "FAILED", "RECONCILIATION_REQUIRED"},
    "SUBMITTED": {"INTERVIEW", "REJECTED", "WITHDRAWN"},
    "INTERVIEW": {"REJECTED", "WITHDRAWN"},
    "EXCLUDED": {"ANALYZED"},
    "BLOCKED_UNKNOWN_FIELDS": {"ANALYZED", "READY_TO_APPLY"},
    "BLOCKED_BY_PLATFORM": {"ANALYZED", "READY_TO_APPLY"},
    "FAILED": {"ANALYZED", "QUALIFIED", "RESEARCHED", "READY_TO_APPLY", "RECONCILIATION_REQUIRED"},
    "RECONCILIATION_REQUIRED": {"SUBMITTED", "READY_TO_APPLY"},
    "REJECTED": set(), "WITHDRAWN": set(),
}


def stable_id(namespace: str, *parts: object) -> str:
    text = "\x1f".join(str(part or "").strip().casefold() for part in parts)
    return namespace + "_" + hashlib.sha256(text.encode()).hexdigest()[:24]


def normalized_name(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.casefold().startswith("utm_") and k.casefold() not in {"ref", "source", "trk", "trackingid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urlencode(sorted(query)), ""))


def json_value(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class StateError(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / "data" / "workai.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
            canonical_url TEXT, platform TEXT, external_id TEXT, fingerprint TEXT NOT NULL,
            data TEXT NOT NULL, discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS job_url_unique ON jobs(canonical_url) WHERE canonical_url != '';
        CREATE UNIQUE INDEX IF NOT EXISTS job_external_unique ON jobs(platform, external_id) WHERE external_id != '';
        CREATE INDEX IF NOT EXISTS job_fingerprint ON jobs(fingerprint);
        CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
            state TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cv_versions(id TEXT PRIMARY KEY, application_id TEXT REFERENCES applications(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ats_analyses(id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, normalized_question TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, question_id TEXT REFERENCES questions(id), application_id TEXT REFERENCES applications(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS platform_profiles(id TEXT PRIMARY KEY, candidate_id TEXT REFERENCES candidates(id), platform TEXT, data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_records(id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies(id), data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS application_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT REFERENCES applications(id),
            job_id TEXT REFERENCES jobs(id), event_type TEXT NOT NULL, data TEXT NOT NULL, timestamp TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON application_events BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON application_events BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS applications_no_delete BEFORE DELETE ON applications BEGIN SELECT RAISE(ABORT,'application history is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS answers_no_update BEFORE UPDATE ON answers BEGIN SELECT RAISE(ABORT,'answers are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS answers_no_delete BEFORE DELETE ON answers BEGIN SELECT RAISE(ABORT,'answers are immutable'); END;
        PRAGMA user_version = 1;
        """)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def event(self, event_type: str, data: dict | None = None, *, application_id=None, job_id=None):
        self.db.execute("INSERT INTO application_events(application_id,job_id,event_type,data,timestamp) VALUES(?,?,?,?,?)",
                        (application_id, job_id, event_type, json_value(data or {}), utcnow()))

    def save_candidate(self, profile: dict):
        candidate_id = profile.get("candidate_id", "ahmed-el-samman")
        old = self.db.execute("SELECT data FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        with self.transaction():
            if old and old[0] != json_value(profile):
                self.event("CANDIDATE_REVISION", {"previous": json.loads(old[0]), "current": profile})
            self.db.execute("INSERT INTO candidates VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
                            (candidate_id, json_value(profile), utcnow()))

    def upsert_job(self, job: dict) -> tuple[dict, bool]:
        job = dict(job)
        company = job.get("company") or "UNKNOWN"
        company_id = stable_id("company", normalized_name(company))
        url = canonical_url(job.get("job_url"))
        platform = str(job.get("platform") or "manual").casefold()
        external_id = str(job.get("external_id") or "")
        if job.get("job_url_kind") == "listing":
            if not external_id:
                raise StateError("A shared listing URL requires a stable external vacancy ID")
            # Keep the real navigation URL in job data. It does not identify one
            # vacancy and must not collapse every opening on the careers page.
            url = ""
        fingerprint = stable_id("role", normalized_name(company), normalized_name(job.get("position", "")), job.get("country"), job.get("city"))
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO companies VALUES(?,?,?,?)", (company_id, company, json_value({"name": company, "website": job.get("company_url")}), utcnow()))
            found = self.db.execute("SELECT * FROM jobs WHERE (canonical_url=? AND canonical_url!='') OR (platform=? AND external_id=? AND external_id!='')", (url, platform, external_id)).fetchone()
            if not found and not url and not external_id:
                found = self.db.execute("SELECT * FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
            if found:
                old = json.loads(found["data"])
                job["id"] = found["id"]
                job["date_discovered"] = old.get("date_discovered", found["discovered_at"])
                if old != job:
                    self.event("JOB_REVISION", {"previous": old, "current": job}, job_id=job["id"])
                    self.db.execute("UPDATE jobs SET company_id=?,canonical_url=?,platform=?,external_id=?,fingerprint=?,data=?,updated_at=? WHERE id=?", (company_id, url, platform, external_id, fingerprint, json_value(job), utcnow(), job["id"]))
                return job, False
            job["id"] = stable_id("job", url or f"{platform}/{external_id}" if url or external_id else fingerprint)
            job.setdefault("date_discovered", utcnow())
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)", (job["id"], company_id, url, platform, external_id, fingerprint, json_value(job), job["date_discovered"], utcnow()))
            self.event("DISCOVERED", {"source": platform, "url": url}, job_id=job["id"])
            return job, True

    def jobs(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM jobs ORDER BY discovered_at,id")]

    def get_job(self, job_id: str) -> dict:
        row = self.db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return json.loads(row[0])

    @staticmethod
    def _application(row) -> dict | None:
        if not row:
            return None
        return {**json.loads(row["data"]), "application_id": row["id"], "job_id": row["job_id"], "application_status": row["state"], "created_at": row["created_at"], "last_updated": row["updated_at"]}

    def application_for_job(self, job_id: str) -> dict | None:
        return self._application(self.db.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone())

    def application(self, application_id: str) -> dict:
        result = self._application(self.db.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone())
        if result is None:
            raise KeyError(application_id)
        return result

    def applications(self) -> list[dict]:
        return [self._application(row) for row in self.db.execute("SELECT * FROM applications ORDER BY created_at")]

    def create_application(self, job: dict) -> tuple[dict | None, bool]:
        with self.transaction():
            existing = self.application_for_job(job["id"])
            if existing:
                if existing["application_status"] in TERMINAL or existing["application_status"] in {"APPLICATION_STARTED", "SUBMISSION_INTENT", "RECONCILIATION_REQUIRED"}:
                    self.event("DUPLICATE_DETECTED", {"reason": "existing submitted or uncertain application"}, application_id=existing["application_id"], job_id=job["id"])
                return existing, False
            row = self.db.execute("SELECT fingerprint FROM jobs WHERE id=?", (job["id"],)).fetchone()
            # Cross-platform roles are treated as possible duplicates, even if URLs differ.
            duplicate = self.db.execute("SELECT a.id,a.state FROM applications a JOIN jobs j ON j.id=a.job_id WHERE j.fingerprint=? AND a.state!='EXCLUDED' LIMIT 1", (row[0],)).fetchone()
            if not duplicate:
                # A missing city/country is not evidence that another board's
                # listing is a different role. Preserve prior rejection history too.
                for possible in self.db.execute("SELECT a.id,a.state,a.data,j.data FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.state!='EXCLUDED'"):
                    history = json.loads(possible[2])
                    other = history.get("job_snapshot") or json.loads(possible[3])
                    same_role = normalized_name(other.get("company", "")) == normalized_name(job.get("company", "")) and normalized_name(other.get("position", "")) == normalized_name(job.get("position", ""))
                    compatible_location = all(not other.get(key) or not job.get(key) or normalized_name(str(other[key])) == normalized_name(str(job[key])) for key in ("country", "city"))
                    if same_role and compatible_location:
                        duplicate = possible
                        break
            if duplicate:
                self.event("DUPLICATE_DETECTED", {"reason": "same company, role and location", "existing_application_id": duplicate[0]}, job_id=job["id"])
                return None, False
            application_id = "app_" + uuid.uuid4().hex
            created = utcnow()
            self.db.execute("INSERT INTO applications VALUES(?,?,?,?,?,?)", (application_id, job["id"], "DISCOVERED", json_value({"job_snapshot": job}), created, created))
            self.event("APPLICATION_CREATED", {"job": job}, application_id=application_id, job_id=job["id"])
            return self.application(application_id), True

    def transition(self, application_id: str, state: str, updates: dict | None = None, *, evidence: str | None = None) -> dict:
        with self.transaction():
            application = self.application(application_id)
            before = application["application_status"]
            if state != before and state not in TRANSITIONS.get(before, set()):
                raise StateError(f"Invalid transition {before} -> {state}")
            if before == "RECONCILIATION_REQUIRED" and state != before and not evidence:
                raise StateError("Reconciliation requires recorded evidence before changing state")
            updates = updates or {}
            if state == "SUBMITTED" and not (updates.get("confirmation_id") or updates.get("confirmation_url")):
                raise StateError("A submission requires captured confirmation evidence")
            if state == "SUBMITTED" and not updates.get("submitted_at"):
                raise StateError("A submission requires a confirmation timestamp")
            data = {k: v for k, v in application.items() if k not in {"application_id", "job_id", "application_status", "created_at", "last_updated"}}
            data.update(updates)
            self.db.execute("UPDATE applications SET state=?,data=?,updated_at=? WHERE id=?", (state, json_value(data), utcnow(), application_id))
            self.event(state, {"previous_state": before, "updates": updates, "evidence": evidence}, application_id=application_id, job_id=application["job_id"])
            return self.application(application_id)

    def save_artifact(self, kind: str, identifier: str, owner_id: str, value: dict):
        tables = {"cv": ("cv_versions", "application_id"), "ats": ("ats_analyses", "job_id"), "research": ("research_records", "company_id")}
        table, owner = tables[kind]
        self.db.execute(f"INSERT OR IGNORE INTO {table}(id,{owner},data,created_at) VALUES(?,?,?,?)", (identifier, owner_id, json_value(value), utcnow()))

    def record_questions(self, application_id: str, records: list[dict]):
        for record in records:
            question = str(record.get("question", ""))
            question_id = stable_id("question", normalized_name(question))
            stable_record = {key: value for key, value in record.items() if key not in {"date", "last_used", "times_used"}}
            answer_id = stable_id("answer", application_id, question_id, json_value(stable_record))
            self.db.execute("INSERT OR IGNORE INTO questions VALUES(?,?,?)", (question_id, normalized_name(question), json_value({"question": question})))
            self.db.execute("INSERT OR IGNORE INTO answers VALUES(?,?,?,?,?)", (answer_id, question_id, application_id, json_value(record), utcnow()))
            if record.get("status") == "UNKNOWN":
                self.event("UNKNOWN_APPLICATION_FIELD", {"question": question, "reason": record.get("reason")}, application_id=application_id, job_id=self.application(application_id)["job_id"])

    def recover(self) -> list[dict]:
        """Call only while holding the exclusive runner lock."""
        recovered = []
        for app in self.applications():
            if app["application_status"] in {"APPLICATION_STARTED", "SUBMISSION_INTENT"}:
                recovered.append(self.transition(app["application_id"], "RECONCILIATION_REQUIRED", {"retryable": False, "failure_reason": "Process stopped during application; verify platform status before retrying."}))
        return recovered

    def export_csv(self) -> Path:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for app in self.applications():
            job = app.get("job_snapshot", self.get_job(app["job_id"]))
            cv = app.get("cv") or {}
            ats = app.get("ats") or {}
            row = {**job, **ats, **app, "cv_version": cv.get("version_id"), "cv_tex_path": cv.get("tex_path"), "cv_pdf_path": cv.get("pdf_path"), "date_applied": app.get("submitted_at"), "job_posted_date": job.get("job_posted_date", job.get("posting_date"))}
            out = {}
            for key in CSV_FIELDS:
                value = row.get(key, "")
                if isinstance(value, (dict, list)):
                    value = json_value(value)
                # Avoid spreadsheet formula injection from untrusted job/answer text.
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                out[key] = value
            writer.writerow(out)
        path = self.root / "data" / "applications" / "applications.csv"
        atomic_write(path, stream.getvalue())
        return path
