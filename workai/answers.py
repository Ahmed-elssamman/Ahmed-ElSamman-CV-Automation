"""Conservative, evidence-backed application answers with immutable revision history.

``add_answer(root, question, answer, source=..., scope={...})`` records an
approved assertion. The caller must actually possess the authority named in source;
webpage/job text is never a source of candidate facts. JSON is canonical; YAML is a
human-readable export. Explicit scope prevents reuse across countries/companies.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_question(question: str) -> str:
    text = question.casefold().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


# Deliberately finite aliases: similar wording is not evidence of equivalence.
_ALIASES = {
    "full_name": ["full name", "name", "your name", "candidate name", "what is your full name"],
    "first_name": ["first name", "given name"],
    "middle_name": ["middle name", "middle names", "your middle name", "what is your middle name"],
    "last_name": ["last name", "family name", "surname"],
    "email": ["email", "email address", "your email", "your email address"],
    "phone": ["phone", "phone number", "mobile", "mobile number", "telephone number"],
    "github": ["github", "github url", "github profile", "github profile url"],
    "linkedin": ["linkedin", "linkedin url", "linkedin profile", "linkedin profile url"],
    "portfolio": ["portfolio", "portfolio url", "portfolio website", "personal website"],
    "current_city": ["current city", "city of residence"],
    "current_location": ["current location", "where do you currently live", "where are you currently based"],
    "current_company": ["current company", "current employer"],
    "current_job_title": ["current job title", "current title"],
    "currently_employed": ["are you currently employed", "currently employed"],
    "current_employment_summary": ["current employment summary", "describe your current employment", "describe your current role", "describe your current roles", "current roles"],
    "current_employers": ["current employers", "current companies"],
    "education_degree": ["degree", "university degree", "academic degree", "what is your degree"],
    "highest_degree": ["highest degree", "highest academic degree", "highest level of education", "highest education level"],
    "education_institution": ["university", "university name", "college university", "college or university", "which university did you attend"],
    "education_field": ["field of study", "major", "academic major", "degree subject"],
    "education_summary": ["education summary", "educational background", "describe your education"],
    "graduation_year": ["graduation year", "year of graduation", "what year did you graduate"],
    "current_country": ["current country", "country of residence", "country"],
    "notice_period": ["notice period", "what is your notice period"],
    "relocation": ["willing to relocate", "are you willing to relocate", "relocation"],
    "sponsorship": ["do you require visa sponsorship", "will you require sponsorship", "do you require sponsorship", "visa sponsorship"],
    "work_authorization": ["are you authorized to work", "are you legally authorized to work", "work authorization"],
    "expected_salary": ["expected salary", "desired salary", "salary expectation", "salary expectations", "what is your expected salary", "expected compensation"],
    "current_salary": ["current salary", "what is your current salary", "current compensation"],
    "years_experience": ["years of experience", "total years of experience", "how many years of experience do you have"],
    "summary": ["professional summary", "summary"],
    "cover_message": ["cover letter", "application cover message", "application message"],
}
_ALIAS_LOOKUP = {normalize_question(alias): key for key, aliases in _ALIASES.items() for alias in aliases}
_LEGAL = {"work_authorization", "sponsorship", "relocation"}
_LANGUAGE_ALIASES = {
    normalize_question(template.format(language=language)): language
    for language in ("English", "Arabic", "French", "German", "Spanish", "Italian", "Portuguese",
                     "Russian", "Turkish", "Hindi", "Urdu", "Mandarin", "Chinese", "Japanese", "Korean")
    for template in ("{language} level", "{language} proficiency", "{language} language level",
                     "{language} language proficiency", "level of {language}", "proficiency in {language}",
                     "what is your {language} level", "what is your level of {language}",
                     "how would you rate your {language}")
}


def _skill_key(skill: str) -> str:
    # Preserve meaningful punctuation: C, C++ and C# are different skills.
    normalized = re.sub(r"\s+", " ", skill.casefold()).strip()
    return {"react.js": "react", "reactjs": "react", "node.js": "nodejs", "nest.js": "nestjs"}.get(normalized, normalized)


def _question_skill(question: str) -> str | None:
    text = re.sub(r"\s+", " ", question.strip().rstrip("?:")).casefold()
    patterns = [r"(?:how many )?years of experience (?:do you have )?(?:in|with) (.+)",
                r"how many years of (.+) experience do you have", r"years of (.+) experience",
                r"(.+) experience in years"]
    for pattern in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return _skill_key(match.group(1))
    return None


def question_key(question: str) -> str:
    if skill := _question_skill(question):
        return "skill_years_experience:" + skill
    normalized = normalize_question(question)
    if normalized in _LANGUAGE_ALIASES:
        return "language_proficiency:" + _LANGUAGE_ALIASES[normalized].casefold()
    return _ALIAS_LOOKUP.get(normalized, normalized)


def _risk_group(question: str) -> str | None:
    """Detect required scopes without treating differently worded questions as equivalent."""
    text = normalize_question(question)
    key = question_key(question)
    if key in _LEGAL or re.search(r"\b(sponsorship|sponsor|authorized|authorised|authorization|relocate|relocation|citizenship|nationality|military)\b|right to work|work permit|work visa", text):
        return "legal"
    if key in {"expected_salary", "current_salary"} or re.search(r"\b(salary|compensation|remuneration)\b", text):
        return "salary"
    if re.search(r"\b(company|organisation|organization)\b", text) and re.search(r"\b(why|interest|motivation)\b", text):
        return "company"
    if re.search(r"\b(position|role|job)\b", text) and re.search(r"\b(why|interest|motivation)\b", text):
        return "job"
    return None


def _has_required_scope(question: str, scope: dict) -> bool:
    group = _risk_group(question)
    required = {"legal": ("country",), "salary": ("currency", "period", "basis"),
                "company": ("company",), "job": ("job_id",)}.get(group, ())
    return all(scope.get(key) for key in required)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def _bank(root: Path):
    directory = Path(root) / "data" / "answers-bank"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "answers.json"
        bank = json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "answers": []}
        if not isinstance(bank.get("answers"), list):
            raise ValueError("Answers Bank must contain an answers list")
        yield bank, directory
        _atomic_write(path, json.dumps(bank, ensure_ascii=False, indent=2) + "\n")
        _atomic_write(directory / "answers.yaml", yaml.safe_dump(bank, allow_unicode=True, sort_keys=False))


def _history(directory: Path, entry: dict, event: str) -> None:
    with (directory / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": event, "timestamp": now(), "entry": entry}, ensure_ascii=False) + "\n")
    (directory / "history.jsonl").chmod(0o600)


def add_answer(root: Path, question: str, answer: Any, *, source: str,
               category: str | None = None, scope: dict | None = None,
               notes: str = "", confidence: float = 1.0, approved: bool = True) -> dict:
    """Append or revise an explicitly sourced approved answer; retain every revision.

    Legal answers require scope.country. Compensation answers require currency,
    period and basis in scope; unspecified components never match explicit ones. This prevents context-free reuse.
    """
    if answer is None or answer == "UNKNOWN" or not str(source).strip() or not approved:
        raise ValueError("A known answer, provenance, and explicit approval are required")
    key = question_key(question)
    scope = dict(scope or {})
    if _risk_group(question) == "salary":
        scope.setdefault("component", None)
    if _risk_group(question) == "legal" and not scope.get("country"):
        raise ValueError("Legal and relocation answers require country scope")
    if _risk_group(question) == "salary" and not all(scope.get(k) for k in ("currency", "period", "basis")):
        raise ValueError("Salary answers require currency, period and basis scope")
    if not _has_required_scope(question, scope):
        raise ValueError("Company and job motivations require their respective scope")
    identifier = hashlib.sha256(json.dumps([key, scope], sort_keys=True).encode()).hexdigest()[:24]
    with _bank(root) as (bank, directory):
        existing = next((a for a in bank["answers"] if a["id"] == identifier), None)
        if existing and existing["answer"] == answer and existing["source"] == source:
            return dict(existing)
        revision = (existing or {}).get("revision", 0) + 1
        entry = {"id": identifier, "revision": revision, "question": question,
                 "normalized_question": normalize_question(question), "semantic_key": key,
                 "answer": answer, "category": category or key, "source": source,
                 "created_at": now(), "last_used": None, "times_used": 0,
                 "companies_used": [], "notes": notes, "confidence": confidence,
                 "scope": scope, "approved": True}
        _history(directory, entry, "ANSWER_CREATED" if not existing else "ANSWER_REVISED")
        bank["answers"] = [a for a in bank["answers"] if a["id"] != identifier] + [entry]
        return dict(entry)


def _known(answer: Any, source: str, **extra) -> dict:
    return {"status": "KNOWN", "answer": answer, "source": source, "confidence": 1.0, **extra}


def _unknown(question: str, reason: str, **extra) -> dict:
    return {"status": "UNKNOWN", "answer": None, "source": None, "confidence": 0.0,
            "event": {"type": "UNKNOWN_APPLICATION_FIELD", "question": question,
                      "reason": reason, "timestamp": now()}, **extra}


def _scope_matches(scope: dict, context: dict) -> bool:
    return all((value is None and context.get(key) is None) or (value is not None and str(context.get(key, "")).casefold() == str(value).casefold())
               for key, value in scope.items())


def _usable_evidence(items: list | None) -> list:
    return [item for item in (items or []) if isinstance(item, dict)
            and (item.get("source_id") or item.get("source"))
            and item.get("status") in {"source_claim", "user_provided", "verified", "verified_fact"}]


def _numeric_experience(profile: dict, question: str, skill: str | None) -> dict:
    """Retrieve explicit sourced years; dates/skill presence never supply a duration."""
    pointer = "/years_experience"
    value = profile.get("years_experience")
    if skill is not None:
        matches = [(name, value) for name, value in profile.get("experience_years_by_skill", {}).items()
                   if _skill_key(name) == skill]
        if len(matches) != 1:
            return _unknown(question, "No single explicit duration exists for the requested skill")
        name, value = matches[0]
        pointer = "/experience_years_by_skill/" + name.replace("~", "~0").replace("/", "~1")
    evidence = _usable_evidence(profile.get("evidence", {}).get(pointer))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or not evidence:
        return _unknown(question, "Experience years require an explicit nonnegative number with verified provenance; dates are not summed")
    return _known(value, "master_profile:" + pointer, evidence=evidence,
                  scope={"skill": skill} if skill else {},
                  interpretation="Explicit sourced years; no date arithmetic or rounding")


def _academic_rank(degree: str) -> int | None:
    for rank, pattern in [(4, r"^(?:doctor(?:ate)?|ph[.]?d[.]?)\b"),
                          (3, r"^(?:master(?:'s)?|m[.]?sc[.]?)\b"),
                          (2, r"^(?:bachelor(?:'s)?|b[.]?sc[.]?)\b"),
                          (1, r"^associate(?:'s)?\b")]:
        if re.search(pattern, degree, re.I):
            return rank
    return None


def _education_fact(profile: dict, question: str, key: str, context: dict) -> dict:
    entries = [(index, item) for index, item in enumerate(profile.get("education", []))
               if isinstance(item, dict) and _usable_evidence(item.get("evidence"))]
    if context.get("education_id"):
        entries = [(index, item) for index, item in entries if item.get("id") == context["education_id"]]
    elif key not in {"education_summary"}:
        # A university/degree field cannot silently choose a vocational program.
        entries = [(index, item) for index, item in entries if _academic_rank(item.get("degree", "")) is not None]
    if key == "highest_degree":
        entries = [(index, item) for index, item in entries if _academic_rank(item.get("degree", "")) is not None]
    if key == "highest_degree" and entries:
        if any(not _usable_evidence(item.get("evidence")) for item in profile.get("education", [])):
            return _unknown(question, "Highest degree cannot be established while education records lack verified evidence")
        # Unclassified qualifications make claims of highest attainment ambiguous.
        unclassified = [item for item in profile.get("education", [])
                        if _academic_rank(item.get("degree", "")) is None
                        and not re.search(r"\b(program|course|training|certificate)\b", item.get("degree", ""), re.I)]
        if unclassified:
            return _unknown(question, "Highest degree cannot be determined from unclassified education records")
        highest = max(_academic_rank(item["degree"]) for _, item in entries)
        entries = [(index, item) for index, item in entries if _academic_rank(item["degree"]) == highest]
    if not entries:
        return _unknown(question, "No unambiguous sourced education record matches this field")
    values = []
    for index, item in entries:
        if key in {"education_degree", "highest_degree"}:
            value, field = item.get("degree"), "degree"
        elif key == "education_institution":
            value, field = item.get("institution"), "institution"
        elif key == "education_field":
            value, field = item.get("field_of_study"), "field_of_study"
            if not value:
                match = re.search(r"\bin (.+)$", item.get("degree", ""), re.I)
                value, field = (match.group(1).strip(), "degree") if match else (None, "field_of_study")
        elif key == "graduation_year":
            # An employment/education end date is not proof of graduation.
            value, field = item.get("graduation_year"), "graduation_year"
            if not isinstance(value, (int, str)) or not re.fullmatch(r"[12][0-9]{3}", str(value)):
                value = None
        else:
            value = f"{item['degree']} — {item['institution']}" if item.get("degree") and item.get("institution") else None
            field = "degree+institution"
        if value is None or value == "":
            return _unknown(question, "The selected education record does not explicitly state the requested information")
        values.append((value, index, field, _usable_evidence(item.get("evidence"))))
    unique = list(dict.fromkeys(value for value, _, _, _ in values))
    if key != "education_summary" and len(unique) != 1:
        return _unknown(question, "Multiple education records match; an approved education_id or a more specific question is required")
    return _known("; ".join(str(value) for value in unique) if key == "education_summary" else unique[0],
                  "master_profile:" + "+".join(f"/education/{index}/{field}" for _, index, field, _ in values),
                  evidence=[item for _, _, _, evidence in values for item in evidence],
                  scope={"education_id": context["education_id"]} if context.get("education_id") else {})


def _language_proficiency(profile: dict, question: str, language: str) -> dict:
    matches = [(index, item) for index, item in enumerate(profile.get("languages", []))
               if isinstance(item, dict) and str(item.get("name", "")).casefold() == language]
    if len(matches) != 1:
        return _unknown(question, "No single sourced proficiency record exists for this language")
    index, item = matches[0]
    value = item.get("proficiency")
    if not isinstance(value, str) or not value.strip() or value.upper() == "UNKNOWN":
        return _unknown(question, "The language is listed, but its proficiency is not stated")
    pointer = f"/languages/{index}/proficiency"
    level_pattern = rf"\b{re.escape(normalize_question(value))}\b"
    evidence = [record for record in _usable_evidence(profile.get("evidence", {}).get(pointer)) + _usable_evidence(item.get("proficiency_evidence"))
                if re.search(level_pattern, normalize_question(str(record.get("quote", ""))))]
    # A source listing only 'English' does not establish 'Good' or any other level.
    language_words, level_words = re.escape(normalize_question(item["name"])), re.escape(normalize_question(value))
    pattern = rf"\b{language_words}(?: language)?(?: proficiency| level)?(?: is)? {level_words}\b"
    reverse = rf"\b{level_words} (?:level of |proficiency in |command of )?{language_words}\b"
    for record in _usable_evidence(item.get("evidence")):
        quote = normalize_question(str(record.get("quote", "")))
        matches = list(re.finditer(pattern, quote)) + list(re.finditer(reverse, quote))
        if any(not {"not", "never", "no"}.intersection(quote[:match.start()].split()[-2:]) for match in matches):
            evidence.append(record)
    if not evidence:
        return _unknown(question, "Proficiency lacks explicit source evidence; language presence alone is insufficient")
    return _known(value, "master_profile:" + pointer, evidence=evidence, scope={"language": language},
                  interpretation="Exact sourced proficiency; no fluency, native-speaker or CEFR conversion")


def _current_salary(question: str, profile: dict, context: dict) -> dict:
    """Resolve historical compensation independently of desired salary and job pay."""
    salary = profile.get("compensation", {}).get("current_salary")
    if not isinstance(salary, dict) or salary.get("amount") is None:
        return _unknown(question, "No explicit current-salary amount is recorded")
    pointer = "/compensation/current_salary"
    evidence = _usable_evidence(profile.get("evidence", {}).get(pointer))
    evidence += _usable_evidence(profile.get("evidence", {}).get(pointer + "/amount"))
    amount_is_sourced = bool(evidence)
    for field in ("currency", "period", "basis", "component"):
        evidence += _usable_evidence(profile.get("evidence", {}).get(pointer + "/" + field))
    metadata = {"current_salary_amount": salary["amount"], "salary_source": "master_profile:" + pointer,
                "salary_currency": salary.get("currency")}
    if not amount_is_sourced:
        return _unknown(question, "Current-salary amount is recorded but has no verified provenance", **metadata)
    missing_profile = [field for field in ("currency", "period", "basis") if not salary.get(field)]
    missing_form = [field for field in ("currency", "period", "basis") if not context.get(field)]
    if missing_profile or missing_form:
        details = []
        if missing_profile:
            details.append("profile units missing: " + ", ".join(missing_profile))
        if missing_form:
            details.append("audited form units missing: " + ", ".join(missing_form))
        return _unknown(question, "Current-salary amount is known; " + "; ".join(details),
                        missing_profile_units=missing_profile, missing_form_units=missing_form, **metadata)
    if str(salary["currency"]).casefold() != str(context["currency"]).casefold():
        return _unknown(question, "Current-salary currency does not match the audited form; no currency conversion is inferred", **metadata)
    if str(salary["basis"]).casefold() != str(context["basis"]).casefold():
        return _unknown(question, "Current-salary gross/net basis does not match the audited form", **metadata)
    if salary.get("component") != context.get("component"):
        return _unknown(question, "Current-salary base/total component does not match the audited form", **metadata)
    period, requested_period = str(salary["period"]).casefold(), str(context["period"]).casefold()
    if period not in {"monthly", "annual"} or requested_period not in {"monthly", "annual"}:
        return _unknown(question, "Current salary supports only explicit monthly or annual periods", **metadata)
    try:
        if isinstance(salary["amount"], bool):
            raise ValueError("Boolean is not a monetary amount")
        amount = Decimal(str(salary["amount"]))
        if not amount.is_finite() or amount < 0:
            raise ValueError("Salary must be finite and nonnegative")
        if period != requested_period:
            amount = amount * 12 if requested_period == "annual" else amount / 12
        answer = int(amount) if amount == amount.to_integral_value() else float(amount)
    except (ValueError, TypeError, InvalidOperation):
        return _unknown(question, "Current-salary amount is not a valid nonnegative number", **metadata)
    return _known(answer, "master_profile:" + pointer, evidence=evidence,
                  current_salary_amount=answer, original_current_salary=dict(salary),
                  salary_currency=context["currency"], salary_period=requested_period,
                  salary_basis=context["basis"], salary_component=context.get("component"),
                  salary_source="master_profile:" + pointer,
                  salary_reason="Explicit current salary with matching units; monthly/annual normalization only",
                  scope={key: context.get(key) for key in ("currency", "period", "basis", "component")})


def _salary(root: Path, question: str, profile: dict, job: dict, context: dict) -> dict:
    required = ("currency", "period", "basis")
    missing = [key for key in required if not context.get(key)]
    if missing:
        return _unknown(question, "Compensation units are unspecified: " + ", ".join(missing))
    salary = job.get("salary_range")
    if isinstance(salary, dict) and salary.get("max") is not None and (not salary.get("applicable_countries") or context.get("country") in salary["applicable_countries"]) and salary.get("component") == context.get("component") and all(
        str(salary.get(k, "")).casefold() == str(context[k]).casefold() for k in required
    ):
        return _known(salary["max"], "job.salary_range", salary_requested=salary["max"],
                      salary_currency=context["currency"], salary_source="job.salary_range",
                      salary_reason="Highest advertised amount with the same period, basis and component")
    country = context.get("country")
    region = "Egypt" if country == "Egypt" else "Gulf" if country in {
        "Saudi Arabia", "United Arab Emirates", "UAE", "Qatar", "Kuwait", "Bahrain", "Oman"} else None
    prefs = profile.get("preferences", {}).get("salary", {}).get(region, {})
    if not prefs.get("amount"):
        return _unknown(question, "No approved salary preference for this market")
    if not prefs.get("period") or not prefs.get("basis"):
        return _unknown(question, "Approved salary preference does not specify period and gross/net basis",
                        salary_requested=prefs["amount"], salary_currency=prefs.get("currency"),
                        salary_source="profile.preferences.salary", salary_reason="Cannot normalize unspecified compensation units")
    if prefs["basis"] != context["basis"] or prefs.get("component") != context.get("component"):
        return _unknown(question, "Gross/net or base/total compensation conversion is not approved")
    try:
        amount = Decimal(str(prefs["amount"]))
        if prefs["currency"] != context["currency"]:
            from .fx import FXUnavailable, get_exchange_rate, validate_rate_evidence
            try:
                rate = context.get("exchange_rate") or get_exchange_rate(prefs["currency"], context["currency"], root=root)
                rate = validate_rate_evidence(rate, prefs["currency"], context["currency"])
            except FXUnavailable as exc:
                return _unknown(question, str(exc))
            context["exchange_rate"] = rate
            amount *= Decimal(str(rate["rate"]))
        if prefs["period"] != context["period"]:
            if (prefs["period"], context["period"]) == ("monthly", "annual"):
                amount *= 12
            elif (prefs["period"], context["period"]) == ("annual", "monthly"):
                amount /= 12
            else:
                return _unknown(question, "Unsupported compensation period normalization")
    except (InvalidOperation, TypeError, ValueError):
        return _unknown(question, "Invalid numeric salary or exchange rate")
    amount = float(amount.quantize(Decimal("0.01")))
    return _known(amount, "profile.preferences.salary", salary_requested=amount,
                  salary_currency=context["currency"], salary_source="profile.preferences.salary",
                  salary_reason="Approved preference with explicit units; any conversion uses supplied sourced rate",
                  exchange_rate=context.get("exchange_rate"))


def resolve_question(root: Path, question: str, profile: dict, job: dict, context: dict | None = None) -> dict:
    """Resolve factual fields, approved bank, then approved prior/company records.

    No fuzzy matching, experience arithmetic, legal inference or generated personal
    motivation is used. Context must come from trusted adapter configuration, not
    instructions embedded in a job description.
    """
    context = {"country": job.get("country"), "company": job.get("company"), "job_id": job.get("id"), **(context or {})}
    key = question_key(question)
    profile_unknown = None
    if key.startswith("language_proficiency:"):
        language = key.partition(":")[2]
        if context.get("language") and str(context["language"]).casefold() != language:
            return _unknown(question, "Question and audited language context conflict")
        context["language"] = language
        result = _language_proficiency(profile, question, language)
        if result["status"] == "KNOWN":
            return result
        profile_unknown = result
    if key == "current_salary":
        result = _current_salary(question, profile, context)
        if result["status"] == "KNOWN":
            return result
        profile_unknown = result
    explicit_skill = _question_skill(question)
    contextual_skill = context.get("skill") or context.get("technology")
    if explicit_skill and contextual_skill and explicit_skill != _skill_key(contextual_skill):
        return _unknown(question, "Question and configured skill context conflict")
    if key == "years_experience" and contextual_skill:
        key = "skill_years_experience:" + _skill_key(contextual_skill)
    if key == "years_experience" or key.startswith("skill_years_experience:"):
        requested_skill = key.partition(":")[2] if ":" in key else None
        if requested_skill:
            context["skill"] = requested_skill
        result = _numeric_experience(profile, question, requested_skill)
        if result["status"] == "KNOWN":
            return result
        profile_unknown = result
    if key.startswith("education_") or key in {"highest_degree", "graduation_year"}:
        result = _education_fact(profile, question, key, context)
        if result["status"] == "KNOWN":
            return result
        profile_unknown = result
    if key in {"currently_employed", "current_employment_summary", "current_employers"}:
        current = [(index, item) for index, item in enumerate(profile.get("experience", []))
                   if item.get("current") is True and _usable_evidence(item.get("evidence"))]
        if current:
            if key == "currently_employed":
                answer = True
            elif key == "current_employers" and all(item.get("company") for _, item in current):
                answer = "; ".join(dict.fromkeys(item["company"] for _, item in current))
            elif key == "current_employment_summary" and all(item.get("company") and item.get("title") for _, item in current):
                answer = "; ".join(f"{item['title']} at {item['company']}" for _, item in current)
            else:
                answer = None
            if answer is not None:
                return _known(answer, "master_profile:" + "+".join(f"/experience/{index}" for index, _ in current),
                              evidence=[e for _, item in current for e in _usable_evidence(item.get("evidence"))])
    fields = {"full_name": ("name",), "email": ("contact", "email"), "phone": ("contact", "phone"),
              "github": ("contact", "github"), "linkedin": ("contact", "linkedin"),
              "portfolio": ("contact", "portfolio"), "current_city": ("contact", "city"),
              "current_country": ("contact", "country"), "summary": ("summary",),
              "notice_period": ("availability", "notice_period")}
    if key in fields:
        value = profile
        for part in fields[key]:
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None and value != "" and value != "UNKNOWN":
            return _known(value, "master_profile:/" + "/".join(fields[key]))
    if key == "current_location":
        contact = profile.get("contact", {})
        if contact.get("city") and contact.get("country"):
            return _known(f"{contact['city']}, {contact['country']}", "master_profile:/contact/city+/contact/country")
    if key in {"current_company", "current_job_title"}:
        current = [item for item in profile.get("experience", []) if item.get("current") is True]
        field = "company" if key == "current_company" else "title"
        if len(current) == 1 and current[0].get(field):
            return _known(current[0][field], "master_profile:/experience:single_current/" + field)
    # Names are not split: culturally ambiguous first/family-name boundaries require evidence.
    if key in {"first_name", "middle_name", "last_name"} and profile.get(key):
        return _known(profile[key], "master_profile:/" + key)
    # Legal facts can be recorded per country; never reuse an unscoped boolean.
    if key in _LEGAL and context.get("country"):
        value = (profile.get("preferences", {}).get("relocation") if key == "relocation"
                 else profile.get("legal", {}).get(key))
        if key == "relocation" and isinstance(value, dict) and value.get("willing") is True:
            countries = value.get("countries", [])
            if context["country"] in countries and context.get("visa_and_work_authorization_provided") is True and context.get("reasonable_relocation_support") is True:
                return _known(True, "master_profile:/preferences/relocation", scope={"country": context["country"], "visa_and_work_authorization_provided": True, "reasonable_relocation_support": True})
            return _unknown(question, "Relocation willingness is conditional on visa/work authorization and reasonable relocation support", known_conditions=value.get("conditions", []))
        if isinstance(value, dict) and value.get(context["country"]) is not None:
            scope = {"country": context["country"]}
            if key == "sponsorship":
                requirement = profile.get("legal", {}).get("sponsorship_scope", {}).get(context["country"])
                if requirement and requirement != "unconditional":
                    scope["work_arrangement"] = requirement
                    if context.get("work_arrangement") != requirement:
                        return _unknown(question, "Sponsorship answer is approved only for relocation to this country")
            return _known(value[context["country"]], "master_profile:" + key + ":" + context["country"], scope=scope)
    if key == "expected_salary":
        salary_result = _salary(root, question, profile, job, context)
        salary_result["scope"] = {key: context.get(key) for key in ("country", "currency", "period", "basis", "component")}
        if salary_result.get("salary_source") == "job.salary_range":
            salary_result["scope"]["job_id"] = job.get("id")
        if salary_result["status"] == "KNOWN":
            return salary_result
    with _bank(root) as (bank, directory):
        candidates = [a for a in bank["answers"] if a.get("approved") and (a.get("semantic_key") == key or (key.startswith("language_proficiency:") and question_key(a.get("question", "")) == key) or (key.startswith("skill_years_experience:") and a.get("semantic_key") == "years_experience" and _skill_key(str(a.get("scope", {}).get("skill", ""))) == key.partition(":")[2]))
                      and _scope_matches(a.get("scope", {}), context) and _has_required_scope(question, a.get("scope", {}))]
        if key in _LEGAL:
            candidates = [a for a in candidates if a.get("scope", {}).get("country")]
        if _risk_group(question) == "salary":
            candidates = [a for a in candidates if all(a.get("scope", {}).get(k) for k in ("currency", "period", "basis")) and a.get("scope", {}).get("component") == context.get("component")]
        if candidates:
            candidates.sort(key=lambda a: len(a.get("scope", {})), reverse=True)
            best = candidates[0]
            same = [a for a in candidates if len(a.get("scope", {})) == len(best.get("scope", {}))]
            if any(a["answer"] != best["answer"] for a in same):
                return _unknown(question, "Conflicting approved answers with equally specific scope")
            best["last_used"] = now()
            best["times_used"] += 1
            if job.get("company") and job["company"] not in best["companies_used"]:
                best["companies_used"].append(job["company"])
            _history(directory, best, "ANSWER_USED")
            return _known(best["answer"], best["source"], confidence=best.get("confidence", 1.0), answer_id=best["id"], scope=best.get("scope", {}))
    # Only records explicitly identified as approved assertions are reusable.
    paths = list((Path(root) / "applications").glob("**/application-questions.json"))
    paths += list((Path(root) / "data" / "companies").glob("**/approved-answers.json"))
    for path in sorted(paths, reverse=True):
        records = json.loads(path.read_text())
        if isinstance(records, dict):
            records = records.get("answers", [])
        for record in records:
            if _risk_group(question) == "salary":
                # Recompute compensation from current preferences/rates, not historical conversions.
                continue
            if not isinstance(record, dict) or not record.get("approved") or not record.get("source"):
                continue
            if question_key(record.get("question", "")) != key or not _scope_matches(record.get("scope", {}), context):
                continue
            try:
                entry = add_answer(root, question, record.get("answer"), source=record["source"], scope=record.get("scope", {}))
            except ValueError:
                continue
            return _known(entry["answer"], entry["source"], answer_id=entry["id"], scope=entry.get("scope", {}))
    if key == "expected_salary":
        return salary_result
    if key == "cover_message":
        if context.get("requires_personal_authorship"):
            return _unknown(question, "This employer requires applicant-authored prose; a stored applicant-authored answer is needed")
        from .cover import compose_cover_message
        return compose_cover_message(root, profile, job)
    return profile_unknown or _unknown(question, "No approved, context-compatible fact found in profile, Answers Bank or previous records")
