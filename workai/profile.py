"""Immutable PDF ingestion and conservative, evidence-backed candidate normalization.

Canonical profile fields are plain JSON/YAML values. Historical records carry an
``evidence`` list and the top-level ``evidence`` map records scalar/skill origins.
Unknown personal information is null; preferences are explicitly user supplied,
never inferred from job descriptions. Ambiguous layouts and OCR require review;
compatible original sources merge without overriding prior canonical facts.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
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
    _atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".workai-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock(root: Path, name: str):
    directory = Path(root) / "data/master-cv"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _ocr_page(path: Path, page: int, language: str) -> str:
    missing = [tool for tool in ("pdftoppm", "tesseract") if not shutil.which(tool)]
    if missing:
        raise ProfileError("OCR_DEPENDENCY_MISSING: install " + ", ".join(missing))
    with tempfile.TemporaryDirectory(prefix="workai-ocr-") as directory:
        image = Path(directory) / "page"
        commands = [
            ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-r", "300", "-png", str(path), str(image)],
            ["tesseract", str(image) + ".png", "stdout", "-l", language],
        ]
        try:
            for command in commands:
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if result.returncode:
                    raise ProfileError("OCR_FAILED: " + result.stderr.strip())
        except subprocess.TimeoutExpired as error:
            raise ProfileError("OCR_TIMEOUT: local OCR did not finish") from error
        return result.stdout


def extract_pdf(path: Path, *, ocr: bool = False, ocr_language: str = "eng") -> dict:
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
            text = (page.extract_text(extraction_mode="layout") or "") if page.get("/Contents") is not None else ""
        links = []
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A")
            if action and action.get("/S") == "/URI":
                url = str(action.get("/URI", ""))
                if url.startswith(("https://", "http://", "mailto:")) and url not in links:
                    links.append(url)
        method = "selectable_text"
        if len(text.strip()) < 40 and not ocr and page.images:
            raise ProfileError(f"OCR_REQUIRED: page {n} contains an image without sufficient selectable text")
        if len(text.strip()) < 40 and ocr:
            text = _ocr_page(path, n, ocr_language)
            method = "ocr"
        pages.append({"page": n, "text": text, "links": links, "method": method})
    if sum(len(p["text"].strip()) for p in pages) < 80:
        raise ProfileError("OCR_REQUIRED: source PDF lacks sufficient selectable text. OCR transcription must retain page evidence and be verified before CV generation.")
    return {"pages": pages, "text": "\n".join(p["text"] for p in pages),
            "verification": "ocr_unverified" if any(p["method"] == "ocr" for p in pages) else "selectable_text"}


def _clean(value: str) -> str:
    # PDF icon glyphs are decorative, never candidate facts.
    return re.sub(r"\s+", " ", re.sub(r"[\ue000-\uf8ff]", "", value)).strip()


def _sections(text: str) -> dict[str, str]:
    aliases = {"PROFESSIONAL SUMMARY": "SUMMARY", "PROFILE": "SUMMARY",
               "WORK EXPERIENCE": "EXPERIENCE", "PROFESSIONAL EXPERIENCE": "EXPERIENCE",
               "EMPLOYMENT HISTORY": "EXPERIENCE", "TECHNICAL SKILLS": "SKILLS",
               "ACADEMIC BACKGROUND": "EDUCATION", "CERTIFICATES": "CERTIFICATIONS",
               "PERSONAL PROJECTS": "PROJECTS"}
    names = "SUMMARY|EXPERIENCE|EDUCATION|PROJECTS|SKILLS|CERTIFICATIONS|LANGUAGES|" + "|".join(aliases)
    matches = list(re.finditer(rf"(?mi)^[ \t]*({names})[ \t]*:?[ \t]*$", text))
    result = {"HEADER": text[:matches[0].start()] if matches else text}
    for i, match in enumerate(matches):
        heading = match.group(1).upper()
        key = aliases.get(heading, heading)
        content = text[match.end(): matches[i + 1].start() if i + 1 < len(matches) else None].strip()
        result[key] = "\n\n".join(filter(None, [result.get(key), content]))
    return result


def _date(value: str) -> str | None:
    if value.lower() == "present":
        return None
    return datetime.strptime(value, "%B %Y").strftime("%Y-%m")


def normalize_profile(extraction: dict, source_id: str) -> dict:
    """Normalize only extractable source facts. Source claims are not externally verified."""
    if extraction.get("verification") == "ocr_unverified" or (
        any(page.get("method") == "ocr" for page in extraction.get("pages", []))
        and extraction.get("verification") != "ocr_reviewed"
    ):
        raise ProfileError("OCR_REVIEW_REQUIRED: inspect and approve page transcription before canonical normalization")
    text = extraction["text"]
    sections = _sections(text)
    if not all(s in sections for s in ("EXPERIENCE", "EDUCATION", "SKILLS")):
        raise ProfileError("UNRECOGNIZED_CV_LAYOUT: expected experience, education and skills headings; preserve extraction and review normalization.")

    def evidence(quote: str) -> list[dict]:
        cleaned = _clean(quote)
        page = next((p["page"] for p in extraction["pages"] if cleaned in _clean(p["text"])), None)
        item = {"source_id": source_id, "page": page, "quote": cleaned, "status": "source_claim"}
        if extraction.get("verification") == "ocr_reviewed":
            item["transcription_review"] = extraction["review"]
        return [item]

    header = _clean(sections["HEADER"])
    links = [u for p in extraction["pages"] for u in p.get("links", [])]
    links += [u.rstrip(".,)") for u in re.findall(r"https?://[^\s]+", sections["HEADER"]) if u not in links]
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", header)
    phone_match = re.search(r"\+\d[\d ]{8,18}\d", header)
    source_name = _clean(sections["HEADER"].strip().splitlines()[0])
    # This workspace belongs to the explicitly named candidate. A different CV
    # identity must never be relabelled with the configured name and merged.
    if re.sub(r"[\W_]+", "", source_name.casefold()) != "ahmedelsamman":
        raise ProfileError("CANDIDATE_IDENTITY_REVIEW_REQUIRED: source name differs from the approved candidate; verify identity before importing any facts")
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
        company = _clean(line[:match.start()])
        preceding = [role_lines[k].strip() for k in range(i - 1, -1, -1) if role_lines[k].strip()]
        if not company:
            if len(preceding) < 2:
                raise ProfileError("Ambiguous standalone employment dates require title and company rows")
            company, previous = _clean(preceding[0]), preceding[1]
        else:
            previous = preceding[0] if preceding else ""
        title_location = re.split(r"\s{2,}|\s+\|\s+", previous)
        title = _clean(title_location[0])
        location_text = _clean(title_location[-1]) if len(title_location) > 1 else None
        if not title or title.startswith(("•", "●", "- ")):
            raise ProfileError("Ambiguous employment title requires review")
        bullets = []
        next_date = next((k for k in range(i + 1, len(role_lines)) if date_re.search(role_lines[k])), None)
        stop = len(role_lines)
        if next_date is not None:
            next_match = date_re.search(role_lines[next_date])
            header_count = 1 if _clean(role_lines[next_date][:next_match.start()]) else 2
            preceding_indices = [k for k in range(i + 1, next_date) if role_lines[k].strip()]
            if len(preceding_indices) < header_count:
                raise ProfileError("Ambiguous next employment header requires review")
            stop = preceding_indices[-header_count]
        j = i + 1
        while j < stop:
            current = role_lines[j].strip()
            if current.startswith(("•", "●", "- ")):
                bullets.append(_clean(current[1:]))
            elif current and bullets:
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
            "work_mode": next((m for m in ("Remote", "Hybrid", "On-site") if m in (location_text or "")), None),
            "employment_type": "Part-time" if "Part-time" in title else None,
            "bullets": bullets,
            "evidence": evidence(previous) + evidence(company) + evidence(line) + [item for b in bullets for item in evidence(b)],
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
    project_links = [u for p in extraction["pages"] for u in p.get("links", []) if u not in contact.values() and not u.startswith("mailto:")]
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
    skills = list(dict.fromkeys(_clean(s).lstrip("•●- ") for s in re.split(r"[,;\n•]", sections["SKILLS"]) if _clean(s).lstrip("•●- ")))
    # Include only technologies named literally in sourced professional records.
    for skill in ("NestJS", "PostgreSQL", "Python", "Odoo", "Nx Monorepo", "SignalR", "Angular Signals", "SSR", "JWT", "Three.js", "Model Viewer", "RBAC"):
        if re.search(r"(?<![\w])" + re.escape(skill) + r"(?![\w])", text, re.I) and skill not in skills:
            skills.append(skill)
    certifications = []
    for line in sections.get("CERTIFICATIONS", "").splitlines():
        if not line.strip():
            continue
        match = re.match(r"(.+?)\s{2,}(\d{4})\s*\\\s*(.+)", line)
        if match:
            name, year, issuer = _clean(match[1]), int(match[2]), _clean(match[3])
        else:
            parts = [_clean(part) for part in line.split("|")]
            if len(parts) != 3 or not re.fullmatch(r"\d{4}", parts[2]):
                raise ProfileError(f"Ambiguous certification: {line.strip()}")
            name, issuer, year = parts[0], parts[1], int(parts[2])
        certifications.append({"name": name, "year": year, "issuer": issuer, "evidence": evidence(line)})
    languages = [{"name": _clean(s), "proficiency": None, "evidence": evidence(s)} for s in re.split(r"[•,;\n]", sections.get("LANGUAGES", "")) if _clean(s)]
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
            annotation_page = next((p["page"] for p in extraction["pages"] if value in p.get("links", [])), None)
            profile["evidence"][f"/contact/{key}"] = ([{"source_id": source_id, "quote": value, "kind": "pdf_annotation", "page": annotation_page, "status": "source_claim"}] if annotation_page else evidence(value))
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
    if profile.get("verification", {}).get("status") == "ocr_unverified":
        errors.append("OCR_REVIEW_REQUIRED: unreviewed transcription cannot supply canonical facts")
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
    all_evidence = [item for items in evidence.values() for item in items]
    all_evidence += [item for field in ("experience", "education", "projects", "certifications", "languages")
                     for record in profile.get(field, []) for item in record.get("evidence", [])]
    if any(item.get("status") == "ocr_unverified" for item in all_evidence):
        errors.append("OCR_REVIEW_REQUIRED: unreviewed evidence is not canonical")
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


def _archive_revision(root: Path, data: bytes, *, reason: str) -> Path:
    digest = hashlib.sha256(data).hexdigest()
    directory = root / "data/master-cv/revisions" / digest
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "profile.yaml"
    if path.exists() and path.read_bytes() != data:
        raise ProfileError("PROFILE_REVISION_CONFLICT: refusing to overwrite history")
    if not path.exists():
        with path.open("xb") as handle:
            handle.write(data)
        path.chmod(0o444)
        _write_json(directory / "metadata.json", {"sha256": digest, "archived_at": _now(), "reason": reason})
        (directory / "metadata.json").chmod(0o444)
    return path


def _persist_profile(root: Path, profile: dict, *, expected_sha256: str | None = None) -> None:
    """Atomic canonical replacement; advisory writer lock and optional optimistic check.

    Empty expected hash means the canonical file must not yet exist. Projections
    are individually atomic and carry the hash of their canonical version.
    """
    root = Path(root)
    with _lock(root, ".profile-write.lock"):
        directory = root / "data/master-cv"
        canonical = directory / "profile.yaml"
        prior = canonical.read_bytes() if canonical.exists() else None
        digest = hashlib.sha256(prior).hexdigest() if prior is not None else ""
        if expected_sha256 is not None and expected_sha256 != digest:
            raise ProfileError("PROFILE_CONCURRENT_EDIT: reload canonical facts before retrying")
        payload = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).encode()
        if prior is not None and prior != payload:
            _archive_revision(root, prior, reason="Canonical profile update")
        new_digest = hashlib.sha256(payload).hexdigest()
        # Source consumers read only profile.yaml. The immutable revision also
        # makes the complete new canonical version recoverable after a crash.
        _archive_revision(root, payload, reason="Canonical profile snapshot")
        _atomic_write(canonical, payload)
        for field in ("experience", "education", "skills", "projects", "certifications", "achievements", "languages", "preferences"):
            data = {"canonical_source": "profile.yaml", "canonical_sha256": new_digest, field: profile[field]}
            _atomic_write(directory / f"{field}.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode())
        _write_json(directory / "missing-information.json", profile["unknowns"])
        _write_json(directory / "conflicts.json", profile.get("conflicts", []))
        _write_json(directory / "validation.json", validate_profile(profile))


def _key(value: Any) -> str:
    return _clean(value).casefold() if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _combined(first: list, second: list) -> list:
    result = copy.deepcopy(first)
    for item in second:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result


def reconcile_profiles(profiles: list[dict], *, canonical: dict | None = None) -> dict:
    """Merge normalized source assertions; prior canonical/user assertions win.

    Equal-priority scalar conflicts become null. Conflicting historical records
    are withheld as a whole because partial dates/titles can imply false claims.
    Both complete assertions and their evidence remain in ``conflicts``.
    """
    if not profiles and canonical is None:
        raise ProfileError("At least one normalized source is required")
    identities = []
    for candidate in ([canonical] if canonical is not None else []) + profiles:
        identity = candidate.get("candidate_id") if isinstance(candidate, dict) else None
        name = candidate.get("name") if isinstance(candidate, dict) else None
        if not isinstance(identity, str) or not identity.strip() or not isinstance(name, str):
            raise ProfileError("CANDIDATE_IDENTITY_REVIEW_REQUIRED: every profile must have a nonempty candidate ID and name")
        normalized_name = re.sub(r"[\W_]+", "", name.casefold())
        if not normalized_name:
            raise ProfileError("CANDIDATE_IDENTITY_REVIEW_REQUIRED: every profile must have a nonempty candidate ID and name")
        source_name = candidate.get("source_name")
        if source_name is not None and (not isinstance(source_name, str)
                or re.sub(r"[\W_]+", "", source_name.casefold()) != normalized_name):
            raise ProfileError("CANDIDATE_IDENTITY_REVIEW_REQUIRED: source name differs from the candidate name")
        identities.append((identity, normalized_name))
    if len(set(identities)) != 1:
        raise ProfileError("CANDIDATE_IDENTITY_REVIEW_REQUIRED: profiles identify different candidates; no facts imported")
    profile = copy.deepcopy(canonical if canonical is not None else profiles[0])
    incoming_profiles = profiles if canonical is not None else profiles[1:]
    profile.setdefault("conflicts", [])
    protected = canonical is not None

    def conflict(field, before, after, first_evidence, second_evidence, retained):
        item = {"field": field, "status": "NEEDS_USER_INPUT", "canonical_status": "retained" if retained else "UNKNOWN",
                "reason": "Higher-priority canonical fact retained" if retained else "Original CV assertions disagree; no value selected",
                "values": [{"value": copy.deepcopy(before), "evidence": copy.deepcopy(first_evidence)},
                           {"value": copy.deepcopy(after), "evidence": copy.deepcopy(second_evidence)}]}
        if item not in profile["conflicts"]:
            profile["conflicts"].append(item)
        profile["unknowns"] = [u for u in profile["unknowns"] if u["field"] != field]
        profile["unknowns"].append({"field": field, "status": "NEEDS_USER_INPUT", "reason": item["reason"]})

    for incoming in incoming_profiles:
        for parent, fields in [(profile, ("summary", "source_name")), (profile["contact"], tuple(incoming["contact"]))]:
            contact = parent is profile["contact"]
            source = incoming["contact"] if contact else incoming
            for field in fields:
                pointer = ("/contact/" if contact else "/") + field
                path = ("contact." if contact else "") + field
                old, new = parent.get(field), source.get(field)
                old_e = profile["evidence"].get(pointer, [])
                new_e = incoming["evidence"].get(pointer, [])
                if new in (None, ""):
                    continue
                explicitly_unknown = protected and any(e.get("status") == "user_provided" for e in old_e)
                if old in (None, "") and not explicitly_unknown:
                    # Do not fill a null created by an earlier unresolved conflict.
                    if any(c["field"] == path for c in profile["conflicts"]):
                        continue
                    parent[field], profile["evidence"][pointer] = copy.deepcopy(new), copy.deepcopy(new_e)
                elif _key(old) == _key(new):
                    profile["evidence"][pointer] = _combined(old_e, new_e)
                else:
                    conflict(path, old, new, old_e, new_e, protected)
                    if not protected:
                        parent[field] = None
                        profile["evidence"][pointer] = _combined(old_e, new_e)
        for index, skill in enumerate(incoming["skills"]):
            existing = next((i for i, value in enumerate(profile["skills"]) if _key(value) == _key(skill)), None)
            if existing is None:
                existing = len(profile["skills"])
                profile["skills"].append(skill)
            pointer = f"/skills/{existing}"
            profile["evidence"][pointer] = _combined(profile["evidence"].get(pointer, []), incoming["evidence"][f"/skills/{index}"])
        identity_fields = {"experience": ("company", "title"), "education": ("institution",),
                           "projects": ("name",), "certifications": ("name",), "languages": ("name",), "achievements": ("name",)}
        for section, identities in identity_fields.items():
            for record in incoming.get(section, []):
                identity = tuple(_key(record.get(field)) for field in identities)
                matches = [r for r in profile[section] if tuple(_key(r.get(field)) for field in identities) == identity]
                if not matches:
                    added = copy.deepcopy(record)
                    if "id" in added:
                        used = {r.get("id") for r in profile[section]}
                        number = len(profile[section]) + 1
                        while f"{section}-{number}" in used:
                            number += 1
                        added["id"] = f"{section}-{number}"
                    profile[section].append(added)
                    continue
                existing = matches[0]
                differences = [field for field in set(existing) | set(record)
                               if field not in {"id", "evidence", "bullets"}
                               and existing.get(field) is not None and record.get(field) is not None
                               and _key(existing[field]) != _key(record[field])]
                # Current versus ended is a real conflict even though end_date
                # uses null for Present in the source schema.
                if section == "experience" and existing.get("current") != record.get("current"):
                    differences.append("current")
                if differences or len(matches) > 1:
                    field = section + "." + str(existing.get("id", "|".join(identity)))
                    conflict(field, existing, record, existing["evidence"], record["evidence"], protected)
                    if not protected:
                        profile[section].remove(existing)
                    continue
                existing["evidence"] = _combined(existing["evidence"], record["evidence"])
                for field, value in record.items():
                    if existing.get(field) is None and field not in {"id", "evidence"}:
                        existing[field] = copy.deepcopy(value)
                if "bullets" in record:
                    existing["bullets"] = _combined(existing.get("bullets", []), record["bullets"])
        profile["source_ids"] = _combined(profile.get("source_ids", []), incoming.get("source_ids", []))
    # Remove initialization unknowns only where a nonconflicting value now exists.
    profile["unknowns"] = [u for u in profile["unknowns"] if not (
        u["field"].startswith("contact.") and u["status"] == "UNKNOWN"
        and profile["contact"].get(u["field"].split(".", 1)[1]))]
    profile["updated_at"] = _now()
    return profile


def load_source_extraction(root: Path, source: dict) -> dict:
    """Re-extract selectable PDFs; validate immutable approved OCR when needed."""
    directory = Path(root) / "data/source-cv"
    archive = directory / source["archive_name"]
    if not archive.exists() or _sha(archive) != source["sha256"]:
        raise ProfileError("SOURCE_INTEGRITY_FAILED: immutable archive changed")
    extracted_path = directory / (source["archive_name"] + ".extracted.json")
    extracted = json.loads(extracted_path.read_text()) if extracted_path.exists() else None
    if extracted and source.get("extraction_sha256") and _sha(extracted_path) != source["extraction_sha256"]:
        raise ProfileError("SOURCE_INTEGRITY_FAILED: extraction artifact changed")
    if extracted and extracted.get("verification") == "ocr_unverified":
        reviewed_path = directory / (source["archive_name"] + ".reviewed.json")
        if not reviewed_path.exists() or not source.get("reviewed_sha256"):
            raise ProfileError("OCR_REVIEW_REQUIRED: saved OCR pages require explicit transcription review")
        if _sha(reviewed_path) != source["reviewed_sha256"]:
            raise ProfileError("SOURCE_INTEGRITY_FAILED: reviewed transcription changed")
        reviewed = json.loads(reviewed_path.read_text())
        if reviewed.get("review", {}).get("extraction_sha256") != _sha(extracted_path):
            raise ProfileError("SOURCE_INTEGRITY_FAILED: reviewed transcription is bound to a different extraction")
        return reviewed
    return extract_pdf(archive)


def review_ocr(root: Path, source_id: str, *, reviewed_pages: list[dict], reviewer: str,
               expected_extraction_sha256: str, note: str) -> dict:
    """Save an explicit page-by-page transcription review; never imply employer verification.

    Call only after a reviewer has compared every supplied page with the original.
    Corrections are supplied as complete page text, preserving the raw OCR artifact.
    """
    root = Path(root).resolve()
    if not reviewer.strip() or not note.strip():
        raise ProfileError("OCR_REVIEW_REQUIRED: reviewer and actual review note are required")
    with _lock(root, ".ingestion.lock"):
        directory = root / "data/source-cv"
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        source = next((s for s in manifest["sources"] if s["source_id"] == source_id), None)
        if source is None:
            raise ProfileError("Unknown archived source")
        archive = directory / source["archive_name"]
        if _sha(archive) != source["sha256"]:
            raise ProfileError("SOURCE_INTEGRITY_FAILED: immutable archive changed")
        extracted_path = directory / (source["archive_name"] + ".extracted.json")
        digest = _sha(extracted_path)
        if digest != expected_extraction_sha256 or digest != source.get("extraction_sha256"):
            raise ProfileError("OCR_REVIEW_CONFLICT: extraction hash changed; review the current artifact")
        extracted = json.loads(extracted_path.read_text())
        if extracted.get("verification") != "ocr_unverified":
            raise ProfileError("Source does not require OCR transcription review")
        expected_pages = [p["page"] for p in extracted["pages"]]
        if (not isinstance(reviewed_pages, list) or any(not isinstance(p, dict) for p in reviewed_pages)
                or [p.get("page") for p in reviewed_pages] != expected_pages
                or any(not isinstance(p.get("text"), str) for p in reviewed_pages)):
            raise ProfileError("OCR_REVIEW_REQUIRED: provide every page exactly once in source order")
        pages = [{**raw, "text": approved["text"]} for raw, approved in zip(extracted["pages"], reviewed_pages)]
        reviewed = {"pages": pages, "text": "\n".join(p["text"] for p in pages), "verification": "ocr_reviewed",
                    "review": {"reviewer": reviewer, "note": note, "extraction_sha256": digest, "source_id": source_id}}
        reviewed_path = directory / (source["archive_name"] + ".reviewed.json")
        if reviewed_path.exists():
            stored = json.loads(reviewed_path.read_text())
            if any(stored.get(key) != value for key, value in reviewed.items()):
                raise ProfileError("OCR_REVIEW_CONFLICT: an existing review cannot be overwritten")
            reviewed = stored
        else:
            reviewed["reviewed_at"] = _now()
            _write_json(reviewed_path, reviewed)
            reviewed_path.chmod(0o444)
        source["reviewed_sha256"] = _sha(reviewed_path)
        _write_json(manifest_path, manifest)
        return reviewed


def ingest_pdfs(root: Path, paths: list[Path], *, ocr: bool = False, ocr_language: str = "eng") -> dict:
    root = Path(root).resolve()
    with _lock(root, ".ingestion.lock"):
        return _ingest_pdfs(root, paths, ocr=ocr, ocr_language=ocr_language)


def _ingest_pdfs(root: Path, paths: list[Path], *, ocr: bool, ocr_language: str) -> dict:
    if not paths or len(paths) > 2:
        raise ProfileError("Provide one or two source PDFs")
    source_dir = root / "data/source-cv"
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = source_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"schema_version": 1, "sources": []}
    for existing in manifest["sources"]:
        target = source_dir / existing["archive_name"]
        if not target.exists() or _sha(target) != existing["sha256"]:
            raise ProfileError(f"SOURCE_INTEGRITY_FAILED: immutable archive {target.name} changed")
    inputs = [(Path(path).resolve(), _sha(Path(path))) for path in paths]
    if len({s["sha256"] for s in manifest["sources"]} | {digest for _, digest in inputs}) > 2:
        raise ProfileError("SOURCE_LIMIT_EXCEEDED: at most two distinct original PDFs")
    sources = []
    for original, digest in inputs:
        source = next((s for s in manifest["sources"] if s["sha256"] == digest), None)
        if source is None:
            archive = source_dir / f"cv-source-{len(manifest['sources']) + 1:02d}.pdf"
            if archive.exists():
                raise ProfileError(f"Refusing to overwrite source CV {archive.name}")
            with archive.open("xb") as handle:
                handle.write(original.read_bytes())
            archive.chmod(0o444)
            source = {"source_id": f"sha256:{digest}", "archive_name": archive.name, "original_name": original.name,
                      "sha256": digest, "ingested_at": _now()}
            manifest["sources"].append(source)
            # Preserve archive identity even when extraction/review later fails.
            _write_json(manifest_path, manifest)
        if source not in sources:
            sources.append(source)
    canonical_path = root / "data/master-cv/profile.yaml"
    canonical_bytes = canonical_path.read_bytes() if canonical_path.exists() else None
    canonical = yaml.safe_load(canonical_bytes) if canonical_bytes is not None else None
    known = canonical.get("source_ids", []) if canonical else []
    incoming = []
    for source in sources:
        if source["source_id"] in known:
            continue
        extraction_path = source_dir / (source["archive_name"] + ".extracted.json")
        if not extraction_path.exists():
            extracted = extract_pdf(source_dir / source["archive_name"], ocr=True, ocr_language=ocr_language) if ocr else extract_pdf(source_dir / source["archive_name"])
            _write_json(extraction_path, extracted)
            extraction_path.chmod(0o444)
            source["extraction_sha256"] = _sha(extraction_path)
            _write_json(manifest_path, manifest)
        extracted = load_source_extraction(root, source)
        incoming.append(normalize_profile(extracted, source["source_id"]))
    if not incoming and canonical is not None:
        return canonical
    profile = reconcile_profiles(incoming, canonical=canonical)
    report = validate_profile(profile)
    if not report["valid"]:
        raise ProfileError("Profile validation failed: " + "; ".join(report["errors"]))
    expected = hashlib.sha256(canonical_bytes).hexdigest() if canonical_bytes is not None else ""
    _persist_profile(root, profile, expected_sha256=expected)
    return profile


def apply_user_clarification(root: Path, updates: dict[str, Any], *, source_id: str,
                             source_text: str, resolved_unknowns: list[str] | None = None,
                             additional_unknowns: list[dict] | None = None,
                             expected_sha256: str | None = None) -> dict:
    """Apply explicit user facts, preserving exact approval and prior canonical bytes.

    ``updates`` uses dotted object paths. Call this only with facts the user
    actually supplied; external research/job text is never an approval source.
    Reusing a source ID with changed text or facts is rejected. The returned
    profile is idempotent for the same approved update.
    """
    root = Path(root).resolve()
    if not source_id.startswith("user:") or not source_text.strip():
        raise ProfileError("Explicit user provenance and verbatim source text are required")
    canonical = root / "data/master-cv/profile.yaml"
    prior_bytes = canonical.read_bytes()
    prior_sha = hashlib.sha256(prior_bytes).hexdigest()
    if expected_sha256 is not None and expected_sha256 != prior_sha:
        raise ProfileError("PROFILE_CONCURRENT_EDIT: reload canonical facts before retrying")
    current = yaml.safe_load(prior_bytes)
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
        if not all(parts) or parts[0] in {"evidence", "source_ids", "schema_version", "candidate_id", "conflicts", "revision", "verification", "approved_sources"}:
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
    for conflict in profile.get("conflicts", []):
        if conflict["field"] in resolved:
            conflict["status"] = "RESOLVED_BY_USER"
            conflict["resolution_source"] = source_id
    for unknown in additional:
        profile["unknowns"] = [u for u in profile["unknowns"] if u["field"] != unknown["field"]]
        profile["unknowns"].append(copy.deepcopy(unknown))
    profile["approved_sources"] = [*old_sources, *([] if source_id in old_sources else [source_id])]
    if any(path.startswith("preferences.") for path in updates):
        prior = profile["preferences"].get("evidence", [])
        profile["preferences"]["evidence"] = [e for e in prior if e.get("source_id") != source_id] + [copy.deepcopy(provenance)]
    profile["updated_at"] = timestamp
    prior_path = _archive_revision(root, prior_bytes, reason="Explicit user clarification")
    profile["revision"] = {"previous_profile_sha256": prior_sha,
                           "previous_profile_path": str(prior_path.relative_to(root)),
                           "approved_source": source_id, "applied_at": timestamp}
    validation = validate_profile(profile)
    if not validation["valid"]:
        raise ProfileError("Approved update produced invalid profile: " + "; ".join(validation["errors"]))
    _persist_profile(root, profile, expected_sha256=prior_sha)
    return profile
