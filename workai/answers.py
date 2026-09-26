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
    "current_country": ["current country", "country of residence", "country"],
    "notice_period": ["notice period", "what is your notice period"],
    "relocation": ["willing to relocate", "are you willing to relocate", "relocation"],
    "sponsorship": ["do you require visa sponsorship", "will you require sponsorship", "do you require sponsorship", "visa sponsorship"],
    "work_authorization": ["are you authorized to work", "are you legally authorized to work", "work authorization"],
    "expected_salary": ["expected salary", "desired salary", "salary expectation", "salary expectations", "what is your expected salary", "expected compensation"],
    "current_salary": ["current salary", "what is your current salary", "salary history"],
    "years_experience": ["years of experience", "total years of experience", "how many years of experience do you have"],
    "summary": ["professional summary", "summary"],
}
_ALIAS_LOOKUP = {normalize_question(alias): key for key, aliases in _ALIASES.items() for alias in aliases}
_LEGAL = {"work_authorization", "sponsorship", "relocation"}


def question_key(question: str) -> str:
    normalized = normalize_question(question)
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
    if key in {"first_name", "last_name"} and profile.get(key):
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
        candidates = [a for a in bank["answers"] if a.get("approved") and a.get("semantic_key") == key
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
    return _unknown(question, "No approved, context-compatible fact found in profile, Answers Bank or previous records")
