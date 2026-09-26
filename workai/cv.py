"""Evidence-preserving LaTeX rendering, immutable CV versions and PDF quality gates.

Tailoring ranks existing skills, bullets and projects; it never adds job keywords
or generates unsupported claims. Job/company strings are application metadata,
not candidate claims. Compilation disables shell escape and validates extraction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from .profile import load_profile, validate_profile


class CVError(RuntimeError):
    pass


def latex_escape(text: object) -> str:
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(c, c) for c in str(text))


def sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")[:85] or "Unnamed"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold().strip()


def _assert_evidence(profile: dict, root: Path | None = None) -> None:
    result = validate_profile(profile)
    if not result["valid"]:
        raise CVError("Invalid Master Profile: " + "; ".join(result["errors"]))
    # Evidence must contain the actual claim, not merely exist as an unrelated citation.
    def supported(value: object, evidence: list[dict]) -> bool:
        return any(_normalize_text(str(value)) in _normalize_text(e.get("quote", "")) for e in evidence)
    for key in ("name", "summary"):
        if profile.get(key) and not supported(profile[key], profile["evidence"].get("/" + key, [])):
            raise CVError(f"Unsupported candidate claim: {key}")
    for key, value in profile.get("contact", {}).items():
        if value and not supported(value, profile["evidence"].get("/contact/" + key, [])):
            raise CVError(f"Unsupported candidate contact: {key}")
    for index, skill in enumerate(profile.get("skills", [])):
        if not supported(skill, profile["evidence"].get(f"/skills/{index}", [])):
            raise CVError(f"Unsupported skill: {skill}")
    for section, fields in {"experience": ("title", "company", "location", "bullets"), "education": ("degree", "institution"), "projects": ("name", "description", "url"), "certifications": ("name", "issuer", "year"), "languages": ("name", "proficiency")}.items():
        for record in profile.get(section, []):
            for field in fields:
                values = record.get(field) if isinstance(record.get(field), list) else [record.get(field)]
                for value in values:
                    if value and not supported(value, record.get("evidence", [])):
                        raise CVError(f"Unsupported {section} assertion: {field}")
            if section == "experience":
                for field in ("start_date", "end_date"):
                    value = record.get(field)
                    if value and not supported(datetime.strptime(value, "%Y-%m").strftime("%B %Y"), record["evidence"]):
                        raise CVError(f"Unsupported employment date: {field}")
                if record.get("current") and not supported("Present", record["evidence"]):
                    raise CVError("Unsupported ongoing employment")
    if root is not None:
        evidence_items = [e for items in profile["evidence"].values() for e in items]
        evidence_items += [e for field in ("experience", "education", "projects", "certifications", "languages") for record in profile.get(field, []) for e in record.get("evidence", [])]
        required_sources = {source for source in profile.get("source_ids", []) if str(source).startswith("sha256:")}
        required_sources.update(item["source_id"] for item in evidence_items if item.get("source_id", "").startswith("sha256:"))
        manifest_file = root / "data/source-cv/manifest.json"
        if required_sources and not manifest_file.exists():
            raise CVError("SOURCE_EVIDENCE_UNAVAILABLE: immutable source manifest required")
        if manifest_file.exists():
            manifest = json.loads(manifest_file.read_text())
            if required_sources - {source["source_id"] for source in manifest["sources"]}:
                raise CVError("SOURCE_EVIDENCE_UNAVAILABLE: canonical source is absent from the immutable manifest")
            source_texts = {}
            from .profile import load_source_extraction
            for source in manifest["sources"]:
                archive = root / "data/source-cv" / source["archive_name"]
                if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != source["sha256"]:
                    raise CVError("Immutable source CV checksum mismatch")
                # An archived source can await transcription review without
                # participating in any existing canonical candidate claim.
                if source["source_id"] not in required_sources:
                    continue
                extraction = load_source_extraction(root, source)
                source_texts[source["source_id"]] = _normalize_text(extraction["text"]) + " " + " ".join(u for p in extraction["pages"] for u in p["links"]).casefold()
            for item in evidence_items:
                if item.get("source_id", "").startswith("sha256:"):
                    quote = _normalize_text(item.get("quote", ""))
                    # Decorative source icons are removed by ingestion.
                    source = re.sub(r"[\ue000-\uf8ff]", "", source_texts.get(item["source_id"], ""))
                    source = _normalize_text(source)
                    if not quote or quote not in source:
                        raise CVError("Evidence quote no longer matches the immutable source")


def _date_label(value: str | None) -> str:
    return datetime.strptime(value, "%Y-%m").strftime("%b %Y") if value else "Present"


def render_latex(profile: dict, *, job: dict | None = None) -> str:
    _assert_evidence(profile)
    esc = latex_escape
    lines = [r"{\LARGE\bfseries " + esc(profile["name"]) + r"}\par"]
    contact = profile.get("contact", {})
    primary = [contact.get("email"), contact.get("phone"), ", ".join(v for v in (contact.get("city"), contact.get("country")) if v)]
    lines.append(" | ".join(esc(v) for v in primary if v) + r"\par")
    # Explicit URL text remains selectable without icon-only representations.
    for key in ("github", "linkedin", "portfolio"):
        if contact.get(key):
            lines.append(esc({"github": "GitHub", "linkedin": "LinkedIn", "portfolio": "Portfolio"}[key] + ": " + contact[key]) + r"\par")
    if job:
        lines.append(r"{\small Application: " + esc(job["position"] + " at " + job["company"]) + r"}\par")
    if profile.get("summary"):
        lines.extend([r"\section*{Professional Summary}", esc(profile["summary"])])
    lines.append(r"\section*{Professional Experience}")
    for role in profile.get("experience", []):
        dates = _date_label(role["start_date"]) + " -- " + _date_label(role.get("end_date"))
        lines.extend([r"\Needspace{8\baselineskip}", r"\textbf{" + esc(role["title"]) + "} " + r"\hfill " + esc(dates) + r"\par",
                      esc(role["company"] + (" | " + role["location"] if role.get("location") else "")) + r"\par",
                      r"\begin{itemize}"])
        lines.extend(r"\item " + esc(b) for b in role.get("bullets", []))
        lines.append(r"\end{itemize}")
    if profile.get("skills"):
        lines.extend([r"\section*{Technical and Professional Skills}", esc(", ".join(profile["skills"]))])
    if profile.get("projects"):
        lines.append(r"\section*{Projects}")
        for project in profile["projects"]:
            lines.append(r"\textbf{" + esc(project["name"]) + r"}\par " + esc(project["description"]))
            if project.get("url"):
                lines.append(r"\par {\small " + esc(project["url"]) + r"}\par")
    if profile.get("education"):
        lines.append(r"\section*{Education}")
        for record in profile["education"]:
            lines.append(r"\textbf{" + esc(record["degree"]) + r"}\par " + esc(record["institution"]) + r"\par")
    if profile.get("certifications"):
        lines.append(r"\section*{Certifications}")
        for record in profile["certifications"]:
            lines.append(esc(record["name"] + " | " + record["issuer"] + " | " + str(record["year"])) + r"\par")
    if profile.get("languages"):
        lines.extend([r"\section*{Languages}", esc(", ".join(l["name"] + (" (" + l["proficiency"] + ")" if l.get("proficiency") else "") for l in profile["languages"]))])
    template = Path(__file__).resolve().parent.parent / "templates/cv.tex"
    if not template.exists():
        raise CVError("LaTeX template missing: templates/cv.tex")
    return template.read_text().replace("@@BODY@@", "\n".join(lines))


def find_compiler(root: Path) -> str:
    configured = os.environ.get("WORKAI_TECTONIC")
    candidates = [configured, str(root / ".tools/tectonic"), shutil.which("tectonic"), shutil.which("xelatex")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise CVError("LATEX_COMPILER_UNAVAILABLE: install Tectonic or XeLaTeX, or set WORKAI_TECTONIC")


def compile_pdf(root: Path, tex_path: Path) -> dict:
    compiler = find_compiler(root)
    if "tectonic" in Path(compiler).name.lower():
        command = [compiler, "--untrusted", "--keep-logs", "--keep-intermediates", "--outdir", str(tex_path.parent), str(tex_path)]
    else:
        command = [compiler, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-output-directory", str(tex_path.parent), str(tex_path)]
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=180, cwd=tex_path.parent)
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        stderr = error.stderr or b""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        (tex_path.parent / "compiler-output.log").write_text(output + stderr)
        raise CVError("LATEX_COMPILATION_TIMEOUT") from error
    compiler_log = tex_path.parent / "compiler-output.log"
    compiler_log.write_text(process.stdout + process.stderr)
    combined_log = process.stdout + process.stderr
    if tex_path.with_suffix(".log").exists():
        combined_log += tex_path.with_suffix(".log").read_text(errors="replace")
    if process.returncode or re.search(r"undefined references|LaTeX Error|Undefined control sequence", combined_log, re.I):
        raise CVError(f"LATEX_COMPILATION_FAILED: inspect {compiler_log}")
    pdf_path = tex_path.with_suffix(".pdf")
    if not pdf_path.exists() or pdf_path.stat().st_size < 1000:
        raise CVError("Invalid or missing compiled PDF")
    reader = PdfReader(pdf_path)
    extracted = "\n".join(p.extract_text() or "" for p in reader.pages)
    if len(extracted.strip()) < 100:
        raise CVError("PDF_NOT_MACHINE_READABLE: extracted selectable text insufficient")
    if re.search(r"Missing character:|Overfull \\hbox", combined_log):
        raise CVError(f"PDF_TYPOGRAPHY_FAILED: missing glyphs or overflowing lines; inspect {compiler_log}")
    return {"pdf_path": str(pdf_path), "page_count": len(reader.pages), "selectable_characters": len(extracted), "extracted_text": extracted, "compiler": Path(compiler).name, "compiler_log": str(compiler_log), "quality_gates": {"compiled": True, "valid_pdf": True, "selectable_text": True, "no_missing_references": True, "no_missing_glyphs": True, "no_line_overflow": True}}


def _validate_pdf_content(result: dict, profile: dict, job: dict | None) -> None:
    extracted = re.sub(r"\s+", "", result["extracted_text"]).casefold()
    required = [profile["name"]]
    required += [profile.get("contact", {}).get(k) for k in ("email", "phone")]
    if job:
        required += [job["company"], job["position"]]
    for text in required:
        if text and re.sub(r"\s+", "", text).casefold() not in extracted:
            raise CVError(f"PDF_CONTENT_MISMATCH: required contact/application text absent: {text}")
    result["quality_gates"]["correct_contact_and_application"] = True
    result["quality_gates"]["evidence_checked"] = True


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _create_version(root: Path, profile: dict, job: dict | None, analysis: dict | None, *, master: bool) -> dict:
    _assert_evidence(profile, root)
    template = Path(__file__).resolve().parent.parent / "templates/cv.tex"
    version_id = _fingerprint({"profile": profile, "job": job, "template": template.read_text(), "renderer_version": 2})[:20]
    directory = root / "data/master-cv/versions" / version_id if master else root / "data/cv-versions" / sanitize(job["company"]) / sanitize(job["position"]) / version_id
    filename = "master-cv" if master else sanitize(job["company"]) + "_" + sanitize(job["position"]) + "_CV"
    metadata_path = directory / "metadata.json"
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text())
        for field, hash_field in (("tex_path", "tex_sha256"), ("pdf_path", "pdf_sha256")):
            path = Path(existing[field])
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != existing[hash_field]:
                raise CVError("CV_VERSION_INTEGRITY_FAILED: immutable version changed")
        return existing
    directory.mkdir(parents=True, exist_ok=True)
    tex_path = directory / (filename + ".tex")
    content = render_latex(profile, job=job)
    if tex_path.exists() and tex_path.read_text() != content:
        raise CVError("Refusing to overwrite existing CV source")
    if not tex_path.exists():
        with tex_path.open("x") as handle:
            handle.write(content)
    result = compile_pdf(root, tex_path)
    _validate_pdf_content(result, profile, job)
    result.pop("extracted_text")
    result.update({"version_id": version_id, "tex_path": str(tex_path), "metadata_path": str(metadata_path), "created_at": datetime.now(timezone.utc).isoformat(), "company": job.get("company") if job else None, "position": job.get("position") if job else None, "job_id": job.get("id") if job else None, "profile_sha256": _fingerprint(profile), "tex_sha256": hashlib.sha256(tex_path.read_bytes()).hexdigest(), "pdf_sha256": hashlib.sha256(Path(result["pdf_path"]).read_bytes()).hexdigest(), "tailoring_method": "Rank sourced skills, projects and bullets; no generated candidate claims"})
    for name, value in (("metadata.json", result), ("profile-snapshot.json", profile), ("ats-analysis.json", analysis or {})):
        path = directory / name
        if not path.exists():
            path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    if job:
        (directory / "job-description.txt").write_text(job.get("description", ""))
    for path in directory.iterdir():
        if path.is_file():
            path.chmod(0o444)
    return result


def build_master(root: Path) -> dict:
    root = Path(root).resolve()
    profile = load_profile(root)
    result = _create_version(root, profile, None, None, master=True)
    # Replace only convenience pointers; versioned PDF/LaTeX snapshots are immutable.
    for suffix, field in (("tex", "tex_path"), ("pdf", "pdf_path")):
        destination = root / "data/master-cv" / ("master-cv." + suffix)
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink():
                destination.unlink()
            else:
                raise CVError("Master convenience path is a regular file; preserve it before refreshing")
        destination.symlink_to(Path(result[field]).relative_to(destination.parent))
    return result


def tailor_cv(root: Path, job: dict, analysis: dict) -> dict:
    root = Path(root).resolve()
    profile = load_profile(root)
    _assert_evidence(profile, root)
    if analysis.get("ai_review"):
        from .reasoning import rank_profile, validate_review
        review = validate_review(analysis["ai_review"]["review"], job, profile)
        return _create_version(root, rank_profile(profile, review), job, analysis, master=False)
    tailored = copy.deepcopy(profile)
    keywords = []
    for key in ("required_skills", "preferred_skills", "required_keywords", "preferred_keywords", "technology_keywords"):
        keywords.extend(job.get(key) or analysis.get(key) or [])
    def relevance(value: str) -> int:
        return sum(1 for keyword in keywords if re.search(r"(?<!\w)" + re.escape(str(keyword)) + r"(?!\w)", value, re.I))
    old_skills = profile["skills"]
    tailored["skills"] = sorted(old_skills, key=relevance, reverse=True)
    for i, skill in enumerate(tailored["skills"]):
        tailored["evidence"][f"/skills/{i}"] = copy.deepcopy(profile["evidence"][f"/skills/{old_skills.index(skill)}"])
    for role in tailored["experience"]:
        role["bullets"] = sorted(role["bullets"], key=relevance, reverse=True)
    tailored["projects"] = sorted(tailored["projects"], key=lambda p: relevance(p["description"]), reverse=True)
    return _create_version(root, tailored, job, analysis, master=False)
