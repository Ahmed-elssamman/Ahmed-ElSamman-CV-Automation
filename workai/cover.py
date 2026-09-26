"""Application messages assembled from validated, unchanged professional facts."""
from pathlib import Path


def compose_cover_message(root: Path, profile: dict, job: dict) -> dict:
    from .answers import _known, _unknown
    from .cv import CVError, _assert_evidence
    from .jobs import canonical_skills
    if not all(job.get(key) for key in ("id", "position", "company")):
        return _unknown("Application cover message", "A specific employer and vacancy are required")
    try:
        _assert_evidence(profile, Path(root))
    except (CVError, ValueError) as exc:
        return _unknown("Application cover message", "Candidate evidence validation failed: " + str(exc))
    keywords = set(canonical_skills(job.get("description", "")))
    projects = sorted(enumerate(profile.get("projects", [])),
                      key=lambda pair: -len(keywords & set(canonical_skills(pair[1].get("description", "")))))[:2]
    references = ["/name"]
    company_sentence = str(job['company']).rstrip('.')
    paragraphs = ["Dear hiring team,", f"I am applying for the {job['position']} position at {company_sentence}."]
    if projects:
        paragraphs.append("My project experience includes:")
        for index, project in projects:
            paragraphs.append(project["name"] + ": " + project["description"])
            references.append(f"/projects/{index}")
    elif profile.get("summary"):
        paragraphs.append(profile["summary"])
        references.append("/summary")
    else:
        return _unknown("Application cover message", "No validated professional content is available")
    paragraphs.extend(["Please find my CV attached.", profile["name"]])
    return _known("\n\n".join(paragraphs), "derived:validated-master-profile",
                  scope={"job_id": job["id"]},
                  derivation={"method": "Fixed application-intent template and exact validated source facts; no generated achievements or personal motivation",
                              "profile_references": references})
