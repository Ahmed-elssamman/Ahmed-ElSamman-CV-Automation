"""Reports are derived from persisted observations, never invented counts."""
from collections import Counter, defaultdict
from pathlib import Path

from .security import utcnow, write_json
from .store import Store


def report(root: Path) -> dict:
    with Store(root) as store:
        apps = store.applications()
        submitted = [app for app in apps if app.get("submitted_at")]
        states = Counter(app["application_status"] for app in apps)
        missing = defaultdict(list)
        for app in apps:
            for keyword in app.get("ats", {}).get("missing_keywords", []):
                missing[str(keyword)].append(app["job_id"])
        scores = [app["ats"]["ats_score"] for app in apps if app.get("ats", {}).get("ats_score") is not None]
        questions = Counter()
        for row in store.db.execute("SELECT q.normalized_question,COUNT(*) FROM answers a JOIN questions q ON q.id=a.question_id GROUP BY q.id"):
            questions[row[0]] = row[1]
        result = {
            "generated_at": utcnow(), "total_jobs_discovered": len(store.jobs()),
            "qualified_jobs": sum(bool(app.get("eligibility", {}).get("eligible")) for app in apps),
            "applications_submitted": len(submitted), "states": dict(states),
            "applications_by_country": dict(Counter(app["job_snapshot"].get("country") or "UNKNOWN" for app in submitted)),
            "applications_by_platform": dict(Counter(app["job_snapshot"].get("platform") or "UNKNOWN" for app in submitted)),
            "applications_by_company": dict(Counter(app["job_snapshot"].get("company") or "UNKNOWN" for app in submitted)),
            "applications_by_position": dict(Counter(app["job_snapshot"].get("position") or "UNKNOWN" for app in submitted)),
            "average_ats_match_score": round(sum(scores) / len(scores), 2) if scores else None,
            "ats_explanation": "Estimated description alignment, not interview or hiring probability.",
            "interview_count": states["INTERVIEW"], "rejection_count": states["REJECTED"],
            "pending_applications": sum(count for state, count in states.items() if state not in {"SUBMITTED", "INTERVIEW", "REJECTED", "WITHDRAWN", "EXCLUDED"}),
            "failed_applications": states["FAILED"],
            "duplicate_applications_prevented": store.db.execute("SELECT COUNT(*) FROM application_events WHERE event_type='DUPLICATE_DETECTED'").fetchone()[0],
            "most_common_missing_skills": sorted(({"skill": skill, "frequency": len(set(ids)), "job_ids": sorted(set(ids))} for skill, ids in missing.items()), key=lambda x: -x["frequency"]),
            "most_common_application_questions": questions.most_common(20),
        }
    write_json(root / "data" / "reports" / "dashboard.json", result)
    # Missing keywords are possible gaps; never promote them automatically into the profile.
    gaps = [{**item, "candidate_evidence": "not matched by current analyzer; review source profile", "learning_recommendation": "review relevance before learning", "action": "do not add without evidence"} for item in result["most_common_missing_skills"]]
    write_json(root / "data" / "reports" / "skill-gap-report.json", gaps)
    return result
