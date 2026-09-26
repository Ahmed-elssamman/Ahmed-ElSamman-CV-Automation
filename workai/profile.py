"""Immutable PDF ingestion and conservative, evidence-backed candidate normalization.

Canonical profile fields are plain JSON/YAML values. Historical records carry an
``evidence`` list and the top-level ``evidence`` map records scalar/skill origins.
Unknown personal information is null; preferences are explicitly user supplied,
never inferred from job descriptions. This deterministic parser recognizes the
supplied CV layout; unfamiliar or scanned layouts fail visibly for review.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pypdf import PdfReader


class ProfileError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_pdf(path: Path) -> dict:
    """Extract selectable text plus real annotation URLs, with page provenance."""
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ProfileError(f"Encrypted source CV requires legitimate access: {path.name}")
    pages = []
    for n, page in enumerate(reader.pages, 1):
        if shutil.which("pdftotext"):
            proc = subprocess.run(["pdftotext", "-layout", "-f", str(n), "-l", str(n), str(path), "-"], capture_output=True, text=True, timeout=45)
            if proc.returncode:
                raise ProfileError(f"PDF extraction failed: {proc.stderr.strip()}")
            text = proc.stdout.replace("\f", "")
        else:
            text = page.extract_text(extraction_mode="layout") or ""
        links = []
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A")
            if action and action.get("/S") == "/URI":
                url = str(action.get("/URI", ""))
                if url.startswith(("https://", "http://", "mailto:")) and url not in links:
                    links.append(url)
        pages.append({"page": n, "text": text, "links": links})
    if sum(len(p["text"].strip()) for p in pages) < 80:
        raise ProfileError("OCR_REQUIRED: source PDF lacks sufficient selectable text. OCR transcription must retain page evidence and be verified before CV generation.")
    return {"pages": pages, "text": "\n".join(p["text"] for p in pages)}


def _clean(value: str) -> str:
    # PDF icon glyphs are decorative, never candidate facts.
    return re.sub(r"\s+", " ", re.sub(r"[\ue000-\uf8ff]", "", value)).strip()


def _sections(text: str) -> dict[str, str]:
    names = "SUMMARY|EXPERIENCE|EDUCATION|PROJECTS|SKILLS|CERTIFICATIONS|LANGUAGES"
    matches = list(re.finditer(rf"(?m)^\s*({names})\s*$", text))
    result = {"HEADER": text[:matches[0].start()] if matches else text}
    for i, match in enumerate(matches):
        result[match.group(1)] = text[match.end(): matches[i + 1].start() if i + 1 < len(matches) else None].strip()
    return result


def _date(value: str) -> str | None:
    if value.lower() == "present":
        return None
    return datetime.strptime(value, "%B %Y").strftime("%Y-%m")


def normalize_profile(extraction: dict, source_id: str) -> dict:
    """Normalize only extractable source facts. Source claims are not externally verified."""
    text = extraction["text"]
    sections = _sections(text)
    if not all(s in sections for s in ("EXPERIENCE", "EDUCATION", "SKILLS")):
        raise ProfileError("UNRECOGNIZED_CV_LAYOUT: expected experience, education and skills headings; preserve extraction and review normalization.")

    def evidence(quote: str) -> list[dict]:
        cleaned = _clean(quote)
        page = next((p["page"] for p in extraction["pages"] if cleaned in _clean(p["text"])), None)
        return [{"source_id": source_id, "page": page, "quote": cleaned, "status": "source_claim"}]

    header = _clean(sections["HEADER"])
    links = [u for p in extraction["pages"] for u in p["links"]]
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", header)
    phone_match = re.search(r"\+\d[\d ]{8,18}\d", header)
    source_name = _clean(sections["HEADER"].strip().splitlines()[0])
    location = re.search(r"\b(Cairo),\s*(Egypt)\b", header, re.I)
    contact = {
        "email": email_match.group() if email_match else None,
        "phone": _clean(phone_match.group()) if phone_match else None,
        "github": next((u for u in links if "github.com/" in u), None),
        "linkedin": next((u for u in links if "linkedin.com/in/" in u), None),
        "portfolio": next((u for u in links if "portfolio" in u and u.startswith("https://")), None),
        "city": location.group(1) if location else None,
        "country": location.group(2) if location else None,
    }
    roles = []
    role_lines = sections["EXPERIENCE"].splitlines()
    date_re = re.compile(r"([A-Z][a-z]+ \d{4})\s*[-–]\s*([A-Z][a-z]+ \d{4}|Present)\s*$")
    for i, line in enumerate(role_lines):
        match = date_re.search(line)
        if not match:
            continue
        previous = next((role_lines[k].strip() for k in range(i - 1, -1, -1) if role_lines[k].strip()), "")
        title_location = re.split(r"\s{2,}", previous)
        if len(title_location) < 2:
            raise ProfileError(f"Ambiguous role/location layout near {line.strip()}")
        title, location_text = _clean(title_location[0]), _clean(title_location[-1])
        company = _clean(line[:match.start()])
        bullets = []
        j = i + 1
        while j < len(role_lines):
            current = role_lines[j].strip()
            if current.startswith(("•", "●", "- ")):
                bullets.append(_clean(current[1:]))
            elif current and bullets:
                # The next role has a wide title/location gap and then a date row.
                if re.search(r"\s{2,}", role_lines[j].strip()) and any(date_re.search(x) for x in role_lines[j + 1:j + 3]):
                    break
                bullets[-1] += " " + _clean(current)
            elif current:
                break
            j += 1
        if not bullets:
            raise ProfileError(f"No evidence-backed bullets found for {company}")
        roles.append({
            "id": "experience-" + str(len(roles) + 1), "title": title, "company": company,
            "start_date": _date(match.group(1)), "end_date": _date(match.group(2)),
            "current": match.group(2) == "Present", "location": location_text,
            "work_mode": next((m for m in ("Remote", "Hybrid", "On-site") if m in location_text), None),
            "employment_type": "Part-time" if "Part-time" in title else None,
            "bullets": bullets,
            "evidence": evidence(previous) + evidence(line) + [item for b in bullets for item in evidence(b)],
        })
    if not roles:
        raise ProfileError("No dated employment extracted; manual evidence review required")
    education = []
    for block in re.split(r"\n\s*\n", sections["EDUCATION"]):
        lines = [_clean(line) for line in block.splitlines() if line.strip()]
        if len(lines) != 2:
            raise ProfileError("Ambiguous education block requires review")
        education.append({"id": f"education-{len(education) + 1}", "degree": lines[0], "institution": lines[1], "start_date": None, "end_date": None, "evidence": evidence(block)})
    projects = []
    project_links = [u for p in extraction["pages"] for u in p["links"] if u not in contact.values() and not u.startswith("mailto:")]
    for block in re.split(r"\n\s*\n", sections.get("PROJECTS", "")):
        block = _clean(block)
        if not block:
            continue
        split = re.split(r"\s+(?=Developed\b|Led\b|Built\b)", block, maxsplit=1)
        if len(split) != 2:
            raise ProfileError("Ambiguous project name/description; review extraction")
        index = len(projects)
        url = project_links[index] if index < len(project_links) else None
        projects.append({"id": f"project-{index + 1}", "name": split[0], "description": split[1], "url": url, "evidence": evidence(block) + ([{"source_id": source_id, "page": next(p["page"] for p in extraction["pages"] if url in p["links"]), "quote": url, "kind": "pdf_annotation", "status": "source_claim"}] if url else [])})
    skills = [_clean(s) for s in sections["SKILLS"].split(",") if _clean(s)]
    # Include only technologies named literally in sourced professional records.
    for skill in ("NestJS", "PostgreSQL", "Python", "Odoo", "Nx Monorepo", "SignalR", "Angular Signals", "SSR", "JWT", "Three.js", "Model Viewer", "RBAC"):
        if re.search(r"(?<![\w])" + re.escape(skill) + r"(?![\w])", text, re.I) and skill not in skills:
            skills.append(skill)
    certifications = []
    for line in sections.get("CERTIFICATIONS", "").splitlines():
        if not line.strip():
            continue
        match = re.match(r"(.+?)\s{2,}(\d{4})\s*\\\s*(.+)", line)
        if not match:
            raise ProfileError(f"Ambiguous certification: {line.strip()}")
        certifications.append({"name": _clean(match[1]), "year": int(match[2]), "issuer": _clean(match[3]), "evidence": evidence(line)})
    languages = [{"name": _clean(s), "proficiency": None, "evidence": evidence(s)} for s in re.split(r"[•,]", sections.get("LANGUAGES", "")) if _clean(s)]
    user_source = [{"source_id": "user:workAI-specification", "quote": "Explicit candidate preferences and operating instructions", "status": "user_provided"}]
    profile = {
        "schema_version": 1, "candidate_id": "ahmed-el-samman", "name": "Ahmed El-Samman", "source_name": source_name,
        "contact": contact, "summary": _clean(sections.get("SUMMARY", "")),
        "experience": roles, "education": education, "skills": skills, "projects": projects,
        "certifications": certifications, "achievements": [], "languages": languages,
        "preferences": {
            "target_markets": ["Egypt", "Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman"],
            "target_seniority": ["Junior", "Mid-level", "Mid-Senior", "Associate"],
            "target_roles": ["Frontend", "Full Stack", "Angular", "React", "JavaScript", "TypeScript", "Node.js", "NestJS"],
            "remote_from_egypt": True, "relocation": None,
            "salary": {"Egypt": {"amount": 40000, "currency": "EGP", "period": None, "basis": None}, "Gulf": {"amount": 50000, "currency": "EGP", "period": None, "basis": None, "approximate": True}},
            "evidence": user_source,
        },
        "legal": {"work_authorization": None, "sponsorship": None, "citizenship": None},
        "availability": {"notice_period": None},
        "current_salary": None,
        "evidence": {"/name": [{"source_id": "user:workAI-specification", "quote": "Ahmed El-Samman", "status": "user_provided"}], "/summary": evidence(sections.get("SUMMARY", "")), "/source_name": evidence(source_name)},
        "unknowns": [], "source_ids": [source_id], "updated_at": _now(),
        "verification": {"status": "source_transcription_verified", "note": "Historical assertions transcribed from original CV; not independently verified with employers or issuers."},
    }
    for key, value in contact.items():
        if value:
            profile["evidence"][f"/contact/{key}"] = ([{"source_id": source_id, "quote": value, "kind": "pdf_annotation", "page": next(p["page"] for p in extraction["pages"] if value in p["links"]), "status": "source_claim"}] if value in links else evidence(value))
    for i, skill in enumerate(skills):
        profile["evidence"][f"/skills/{i}"] = evidence(skill)
    missing = {
        "legal.work_authorization": "Country-specific legal authorization cannot be inferred from location or prior employment.",
        "legal.sponsorship": "Sponsorship requirements are not present in the CV.",
        "legal.citizenship": "Nationality cannot be inferred from name or current location.",
        "availability.notice_period": "Notice period and earliest start date are not provided.",
        "preferences.relocation": "Relocation willingness is not provided.",
        "preferences.salary.period": "40,000 EGP Egypt / approximately 50,000 EGP Gulf targets have no explicit monthly/annual period.",
        "preferences.salary.basis": "Gross/net/base/total compensation basis is not specified.",
        "current_salary": "Current or historical salary is not provided.",
        "education.dates": "No graduation or enrollment dates appear in source CV.",
        "languages.proficiency": "Arabic and English are listed without proficiency levels.",
        "experience.total_years": "No approved total-years value; overlapping concurrent roles must not be summed.",
    }
    for field, reason in missing.items():
        status = "NEEDS_USER_INPUT" if field.startswith(("legal", "availability", "preferences")) else "UNKNOWN"
        profile["unknowns"].append({"field": field, "status": status, "reason": reason})
    for key, value in contact.items():
        if value is None:
            profile["unknowns"].append({"field": f"contact.{key}", "status": "UNKNOWN", "reason": "Not extracted from source CV."})
    return profile


def validate_profile(profile: dict) -> dict:
    errors, warnings = [], []
    if not profile.get("name"):
        errors.append("Candidate name is missing")
    evidence = profile.get("evidence", {})
    for key in ("name", "summary"):
        if profile.get(key) and not evidence.get("/" + key):
            errors.append(f"Missing provenance for {key}")
    for i, skill in enumerate(profile.get("skills", [])):
        if not isinstance(skill, str) or not skill.strip() or not evidence.get(f"/skills/{i}"):
            errors.append(f"Missing factual provenance for skill {i}")
    for field in ("experience", "education", "projects", "certifications", "languages"):
        for i, record in enumerate(profile.get(field, [])):
            if not record.get("evidence"):
                errors.append(f"Missing provenance for {field}[{i}]")
    for i, role in enumerate(profile.get("experience", [])):
        for field in ("start_date", "end_date"):
            if role.get(field) and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", role[field]):
                errors.append(f"Invalid employment date {i}.{field}")
        if role.get("end_date") and role.get("start_date", "") > role["end_date"]:
            errors.append(f"Reversed employment dates for role {i}")
        if role.get("current") and role.get("end_date"):
            errors.append(f"Current role {i} must not have an end date")
    for item in profile.get("unknowns", []):
        warnings.append(f"{item['status']}: {item['field']}")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def load_profile(root: Path) -> dict:
    path = Path(root) / "data/master-cv/profile.yaml"
    if not path.exists():
        raise ProfileError("No Master Profile. Ingest source CV PDFs first.")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _persist_profile(root: Path, profile: dict) -> None:
    directory = root / "data/master-cv"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profile.yaml").write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")
    for field in ("experience", "education", "skills", "projects", "certifications", "achievements", "languages", "preferences"):
        (directory / f"{field}.yaml").write_text(yaml.safe_dump({"canonical_source": "profile.yaml", field: profile[field]}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _write_json(directory / "missing-information.json", profile["unknowns"])
    _write_json(directory / "validation.json", validate_profile(profile))


def ingest_pdfs(root: Path, paths: list[Path]) -> dict:
    root = Path(root).resolve()
    if not paths:
        raise ProfileError("Provide at least one source PDF")
    source_dir = root / "data/source-cv"
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = source_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"schema_version": 1, "sources": []}
    for existing in manifest["sources"]:
        target = source_dir / existing["archive_name"]
        if not target.exists() or _sha(target) != existing["sha256"]:
            raise ProfileError(f"SOURCE_INTEGRITY_FAILED: immutable archive {target.name} changed")
    extractions = []
    source_ids = []
    for input_path in paths:
        original = Path(input_path).resolve()
        digest = _sha(original)
        existing = next((s for s in manifest["sources"] if s["sha256"] == digest and (s["original_name"] == original.name or original == source_dir / s["archive_name"])), None)
        if existing:
            source = existing
        else:
            archive = source_dir / f"cv-source-{len(manifest['sources']) + 1:02d}.pdf"
            if archive.exists():
                raise ProfileError(f"Refusing to overwrite source CV {archive.name}")
            with archive.open("xb") as handle:
                handle.write(original.read_bytes())
            archive.chmod(0o444)
            source = {"source_id": f"sha256:{digest}", "archive_name": archive.name, "original_name": original.name, "sha256": digest, "ingested_at": _now(), "duplicate_of": next((s["archive_name"] for s in manifest["sources"] if s["sha256"] == digest), None)}
            manifest["sources"].append(source)
        extraction_path = source_dir / (source["archive_name"] + ".extracted.json")
        extracted = extract_pdf(source_dir / source["archive_name"])
        if not extraction_path.exists():
            _write_json(extraction_path, extracted)
            extraction_path.chmod(0o444)
        extractions.append(extracted)
        if source["source_id"] not in source_ids:
            source_ids.append(source["source_id"])
    _write_json(manifest_path, manifest)
    profile_path = root / "data/master-cv/profile.yaml"
    if profile_path.exists():
        current = load_profile(root)
        if set(source_ids).issubset(set(current.get("source_ids", []))):
            return current
        raise ProfileError("NEW_SOURCE_REVIEW_REQUIRED: sources archived; reconcile new/conflicting evidence against existing canonical profile before replacing facts")
    if len(source_ids) > 1:
        raise ProfileError("MULTIPLE_DISTINCT_SOURCES_REVIEW_REQUIRED: archived all sources; reconcile conflicting candidate assertions before canonical normalization")
    profile = normalize_profile(extractions[0], source_ids[0])
    report = validate_profile(profile)
    if not report["valid"]:
        raise ProfileError("Profile validation failed: " + "; ".join(report["errors"]))
    _persist_profile(root, profile)
    return profile


def apply_user_clarification(root: Path, updates: dict[str, Any], *, source_id: str,
                             source_text: str, resolved_unknowns: list[str] | None = None,
                             additional_unknowns: list[dict] | None = None) -> dict:
    """Apply explicit user facts, preserving exact approval and prior canonical bytes.

    ``updates`` uses dotted object paths. Call this only with facts the user
    actually supplied; external research/job text is never an approval source.
    Reusing a source ID with changed text or facts is rejected. The returned
    profile is idempotent for the same approved update.
    """
    import copy

    root = Path(root).resolve()
    if not source_id.startswith("user:") or not source_text.strip():
        raise ProfileError("Explicit user provenance and verbatim source text are required")
    current = load_profile(root)
    profile = copy.deepcopy(current)
    resolved = resolved_unknowns or []
    additional = additional_unknowns or []
    approval = {
        "source_id": source_id, "verbatim_user_text": source_text,
        "updates": updates, "resolved_unknowns": resolved,
        "additional_unknowns": additional,
    }
    approved_dir = root / "data/approved"
    approved_dir.mkdir(parents=True, exist_ok=True)
    approval_path = approved_dir / (re.sub(r"[^A-Za-z0-9_-]+", "-", source_id).strip("-") + ".json")
    if approval_path.exists():
        stored = json.loads(approval_path.read_text())
        if any(stored.get(k) != v for k, v in approval.items()):
            raise ProfileError("APPROVAL_CONFLICT: preserve the existing source and use a new ID for a new user clarification")
        timestamp = stored["recorded_at"]
    else:
        timestamp = _now()
        approval["recorded_at"] = timestamp
        with approval_path.open("x", encoding="utf-8") as handle:
            json.dump(approval, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        approval_path.chmod(0o444)
    provenance = {"source_id": source_id, "quote": source_text,
                  "status": "user_provided", "recorded_at": timestamp,
                  "source_path": str(approval_path.relative_to(root))}
    unchanged = True
    for path, value in updates.items():
        parts = path.split(".")
        if not all(parts) or parts[0] in {"evidence", "source_ids", "schema_version", "candidate_id"}:
            raise ProfileError(f"Unsupported direct clarification path: {path}")
        target = profile
        for part in parts[:-1]:
            if target.get(part) is None:
                target[part] = {}
            if not isinstance(target.get(part), dict):
                raise ProfileError(f"Cannot traverse non-object profile path: {path}")
            target = target[part]
        unchanged = unchanged and target.get(parts[-1]) == value
        target[parts[-1]] = copy.deepcopy(value)
        pointer = "/" + "/".join(parts)
        profile.setdefault("evidence", {})[pointer] = [copy.deepcopy(provenance)]
    old_sources = current.get("approved_sources", [])
    if unchanged and source_id in old_sources:
        return current
    profile["unknowns"] = [u for u in current.get("unknowns", []) if u["field"] not in resolved]
    for unknown in additional:
        profile["unknowns"] = [u for u in profile["unknowns"] if u["field"] != unknown["field"]]
        profile["unknowns"].append(copy.deepcopy(unknown))
    profile["approved_sources"] = [*old_sources, *([] if source_id in old_sources else [source_id])]
    if any(path.startswith("preferences.") for path in updates):
        prior = profile["preferences"].get("evidence", [])
        profile["preferences"]["evidence"] = [e for e in prior if e.get("source_id") != source_id] + [copy.deepcopy(provenance)]
    profile["updated_at"] = timestamp
    canonical = root / "data/master-cv/profile.yaml"
    prior_bytes = canonical.read_bytes()
    prior_sha = hashlib.sha256(prior_bytes).hexdigest()
    revision_id = f"{timestamp[:10]}-{prior_sha[:16]}"
    revision_dir = root / "data/master-cv/revisions" / revision_id
    revision_dir.mkdir(parents=True, exist_ok=True)
    prior_path = revision_dir / "profile.yaml"
    if prior_path.exists() and prior_path.read_bytes() != prior_bytes:
        raise ProfileError("PROFILE_REVISION_CONFLICT: refusing to overwrite historical profile")
    if not prior_path.exists():
        with prior_path.open("xb") as handle:
            handle.write(prior_bytes)
        prior_path.chmod(0o444)
        _write_json(revision_dir / "metadata.json", {"sha256": prior_sha, "archived_at": timestamp,
                    "superseded_by_source": source_id, "reason": "Explicit user clarification"})
        (revision_dir / "metadata.json").chmod(0o444)
    profile["revision"] = {"previous_profile_sha256": prior_sha,
                           "previous_profile_path": str(prior_path.relative_to(root)),
                           "approved_source": source_id, "applied_at": timestamp}
    validation = validate_profile(profile)
    if not validation["valid"]:
        raise ProfileError("Approved update produced invalid profile: " + "; ".join(validation["errors"]))
    _persist_profile(root, profile)
    return profile
