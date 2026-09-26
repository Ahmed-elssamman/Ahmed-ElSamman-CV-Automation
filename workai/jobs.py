"""Public job discovery, conservative matching and sourced company research.

Job descriptions are untrusted data. No content from a listing is executed or
allowed to change candidate facts. Scores describe text alignment, not hiring
probability. Missing mandatory facts block only the affected application.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

ANALYZER_VERSION = "2026.09.26.2"
TARGET_COUNTRIES = {"Egypt", "Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman"}
COUNTRIES = {
    "Egypt": ("EG", "egypt", "cairo", "giza", "alexandria", "mansoura"),
    "Saudi Arabia": ("SA", "saudi arabia", "saudi", "ksa", "riyadh", "jeddah", "dammam"),
    "United Arab Emirates": ("AE", "united arab emirates", "uae", "dubai", "abu dhabi", "sharjah"),
    "Qatar": ("QA", "qatar", "doha"), "Kuwait": ("KW", "kuwait"),
    "Bahrain": ("BH", "bahrain", "manama"), "Oman": ("OM", "oman", "muscat"),
    "United States": ("US", "USA", "united states"), "United Kingdom": ("GB", "UK", "united kingdom"),
    "Pakistan": ("PK", "pakistan", "karachi", "lahore"), "India": ("IN", "india", "bangalore", "bengaluru"),
    "Germany": ("DE", "germany", "berlin"), "Canada": ("CA", "canada"),
    "Algeria": ("DZ", "algeria", "algiers"), "Tunisia": ("TN", "tunisia", "tunis"),
    "Morocco": ("MA", "morocco", "casablanca"), "France": ("FR", "france", "paris"),
}
SKILLS = {
    "Angular": r"angular(?:js|\s*[2-9]\d*)?", "React": r"react(?:\.js|js)?(?!\s+native)",
    "React Native": r"react\s+native", "TypeScript": r"typescript", "JavaScript": r"javascript|ecmascript",
    "Node.js": r"node(?:\.js|js)?", "NestJS": r"nest(?:\.js|js)", "Next.js": r"next(?:\.js|js)",
    "HTML": r"html5?", "CSS": r"css3?", "Sass": r"s[ac]ss", "Tailwind CSS": r"tailwind(?:\s*css)?",
    "Bootstrap": r"bootstrap", "RxJS": r"rxjs", "NgRx": r"ngrx", "Redux": r"redux", "Vue.js": r"vue(?:\.js|js)?",
    "MongoDB": r"mongodb", "PostgreSQL": r"postgres(?:ql)?", "MySQL": r"mysql", "SQL": r"sql",
    "Python": r"python", "Odoo": r"odoo", "Three.js": r"three(?:\.js|js)", "Express.js": r"express(?:\.js|js)?",
    "Java": r"java", "C#": r"c\#|c\s*sharp", ".NET": r"\.net|asp\.net", "PHP": r"php", "Laravel": r"laravel",
    "Docker": r"docker", "Kubernetes": r"kubernetes|k8s", "AWS": r"aws|amazon web services",
    "Azure": r"azure", "Google Cloud": r"gcp|google cloud", "Git": r"git", "GitHub": r"github",
    "CI/CD": r"ci\s*/\s*cd|continuous integration", "REST": r"rest(?:ful)?(?:\s*apis?)?", "GraphQL": r"graphql",
    "Jest": r"jest", "Jasmine": r"jasmine", "Karma": r"karma", "Cypress": r"cypress", "Playwright": r"playwright",
    "Unit testing": r"unit test(?:ing|s)?", "Agile": r"agile", "Scrum": r"scrum", "Figma": r"figma",
    "Accessibility": r"accessibility|wcag|a11y", "Responsive design": r"responsive(?:\s+(?:web|design))?",
    "Redis": r"redis", "Linux": r"linux|ubuntu|debian", "WebSocket": r"websockets?", "Webpack": r"webpack", "Vite": r"vite",
    "Flutter": r"flutter", "Dart": r"dart", "Go": r"golang|go language", "Rust": r"rust", "C++": r"c\+\+",
}
ROLE_PATTERN = re.compile(r"front[ -]?end|full[ -]?stack|angular|react|node\.?js|nestjs|javascript|typescript|\bweb\s+(?:developer|engineer)|\bui\s+(?:developer|engineer)|software\s+(?:engineer|developer)", re.I)
RESTRICTED = ["LinkedIn", "Wuzzuf", "Indeed", "Bayt", "GulfTalent", "Naukrigulf", "Akhtaboot", "Forasna"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*values: Any) -> str:
    return hashlib.sha256("|".join(str(v or "").strip().lower() for v in values).encode()).hexdigest()[:24]


def _work_authorization(profile: dict, country: str | None) -> bool | None:
    authorizations = profile.get("legal", {}).get("work_authorization", profile.get("work_authorization", {}))
    return authorizations.get(country) if isinstance(authorizations, dict) else None


def _write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag in {"br", "p", "li", "div", "h1", "h2", "h3", "h4", "h5", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "li", "div", "h1", "h2", "h3", "h4", "h5"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(value: Any) -> str:
    parser = _Text()
    parser.feed(html.unescape(str(value or "")))
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines() if line.strip())


def _country(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name", value.get("addressCountry"))
    text = str(value or "").strip()
    for country, aliases in COUNTRIES.items():
        if any(text.lower() == alias.lower() or (len(alias) > 3 and re.search(r"\b" + re.escape(alias) + r"\b", text, re.I)) for alias in aliases):
            return country
    return None


def canonical_skills(value: Any) -> list[str]:
    exact = set()
    if isinstance(value, list):
        for item in value:
            name = item.get("name", item.get("skill", "")) if isinstance(item, dict) else item
            exact.update(skill for skill in SKILLS if str(name).strip().lower() == skill.lower())
        text = "\n".join(str(x.get("name", x.get("skill", ""))) if isinstance(x, dict) else str(x) for x in value)
    elif isinstance(value, dict):
        text = "\n".join(str(v) for v in value.values())
    else:
        text = str(value or "")
    return sorted(exact | {name for name, pattern in SKILLS.items() if re.search(r"(?<![\w])(?:" + pattern + r")(?![\w])", text, re.I)})


def parse_salary(value: Any) -> dict | None:
    """Keep currency/unit/basis UNKNOWN when the employer does not specify them."""
    if not value:
        return None
    if isinstance(value, dict):
        if "min" in value or "max" in value:
            interval = str(value.get("interval", "")).lower()
            return {**value, "period": value.get("period") or {"per-year-salary": "annual", "per-month-salary": "monthly", "per-hour-wage": "hourly"}.get(interval), "basis": value.get("basis")}
        amount = value.get("value", {})
        if not isinstance(amount, dict):
            amount = {"value": amount}
        unit = str(amount.get("unitText", "")).lower()
        return {"min": amount.get("minValue", amount.get("value")), "max": amount.get("maxValue", amount.get("value")),
                "currency": value.get("currency"), "period": {"month": "monthly", "year": "annual", "hour": "hourly", "week": "weekly", "day": "daily"}.get(unit),
                "basis": None, "source_text": json.dumps(value, ensure_ascii=False)}
    text = plain_text(value)
    currency_match = re.search(r"\b(EGP|AED|SAR|QAR|KWD|BHD|OMR|USD|EUR|GBP)\b", text, re.I)
    currency = currency_match.group(1).upper() if currency_match else None
    if not currency:
        for pattern, code in [(r"egyptian pounds?|\bLE\b", "EGP"), (r"saudi riyals?", "SAR"), (r"\bUS\$", "USD"), (r"€", "EUR"), (r"£", "GBP")]:
            if re.search(pattern, text, re.I):
                currency = code
                break
    # Bare '$' is ambiguous (USD, CAD, AUD, ...).
    numbers = re.findall(r"(?<![\w])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)[ \t]*[kK]?(?![\w])", text)
    amounts = [float(re.sub(r"[kK,\s]", "", n)) * (1000 if "k" in n.lower() else 1) for n in numbers[:2]]
    if not amounts:
        return None
    period = None
    for pattern, unit in [(r"monthly|per month|/\s*mo(?:nth)?\b|\bmonth\b", "monthly"), (r"annual(?:ly)?|yearly|per year|/\s*y(?:ea)?r\b|\byear\b", "annual"), (r"hourly|per hour|/\s*h(?:ou)?r\b", "hourly"), (r"weekly|per week", "weekly"), (r"daily|per day", "daily")]:
        if re.search(pattern, text, re.I):
            period = unit
            break
    basis_match = re.search(r"\b(gross|net|base|total compensation)\b", text, re.I)
    countries = []
    for pattern, country in [(r"\bUS[- ]based|\bUSA|United States", "United States"), (r"\bEgypt(?:ian)?[- ]based|\bin Egypt\b", "Egypt"), (r"\bUAE[- ]based|\bin (?:UAE|Dubai)\b", "United Arab Emirates")]:
        if re.search(pattern, text, re.I): countries.append(country)
    return {"min": min(amounts), "max": max(amounts), "currency": currency, "period": period,
            "basis": basis_match.group(1).lower() if basis_match else None, "source_text": text, "applicable_countries": countries or None}


def _posted(value: Any) -> str | None:
    if isinstance(value, (float, int)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).isoformat()
        except (ValueError, OverflowError):
            return None
    # Preserve publisher timezone/precision; never label an update date a posting date.
    return str(value) if value else None


def _remote_egypt(location: str, description: str, remote: bool | None, country: str | None) -> bool | None:
    if remote is False:
        return False
    text = location.lower()
    # Specific residence restrictions override a generic worldwide/remote label.
    residency = re.search(r"(?:must|need to|are required to)\s+(?:currently\s+)?(?:be\s+(?:located|based|resident)|reside|live)\s+in\s+([^\n.;]+)", description, re.I)
    if residency:
        residence = residency.group(1).strip()
        if not re.search(r"\begypt\b|worldwide|anywhere", residence, re.I):
            return False
    if re.search(r"(?:US|USA|UK|Canada|Europe|EU|UAE|Saudi Arabia)[ -]only|only (?:in|within) (?:the )?(?:US|USA|UK|Canada|Europe|EU|UAE|Saudi Arabia)\b", location + "\n" + description, re.I):
        return False
    if re.search(r"egypt", text) or country == "Egypt":
        return True if remote else None
    if re.search(r"worldwide|anywhere(?:\s+in the world)?|globally|global remote", text):
        return True
    if re.search(r"remote (?:work )?(?:from|in) egypt|work (?:remotely )?from (?:anywhere|egypt)|(?:open|available) to (?:candidates|applicants) (?:in|from) egypt", description, re.I):
        return True
    if re.search(r"(?:remote|resid(?:ent|e)|based).{0,25}(?:only|must)|(?:only|must).{0,30}(?:resid|based)|\bUS only\b|\bUSA only\b", text + "\n" + description, re.I):
        return False
    if remote and text and country and country != "Egypt":
        return None  # A foreign city does not establish remote residence eligibility.
    return None


def parse_job(payload: dict, platform: str = "manual") -> dict:
    """Normalize a Lever, Greenhouse, Remotive, SmartRecruiters or JSON-LD listing."""
    p = dict(payload)
    if p.get("platform"):
        platform = p["platform"]
    company = p.get("company") or p.get("company_name") or p.get("hiringOrganization") or p.get("_company")
    if isinstance(company, dict):
        company = company.get("name")
    company_url = p.get("company_url") or p.get("_company_url")
    if isinstance(p.get("hiringOrganization"), dict):
        company_url = company_url or p["hiringOrganization"].get("sameAs") or p["hiringOrganization"].get("url")
    position = p.get("position") or p.get("title") or p.get("text") or p.get("name")
    location = p.get("location") or p.get("candidate_required_location") or p.get("categories", {}).get("location") or p.get("jobLocation") or ""
    if isinstance(location, list):
        location = location[0] if location else ""
    address = location.get("address", location) if isinstance(location, dict) else {}
    city = p.get("city") or address.get("city") or address.get("addressLocality")
    location_text = str(location.get("name") or ", ".join(str(address.get(k, "")) for k in ["city", "addressLocality", "region", "country", "addressCountry"]) if isinstance(location, dict) else location)
    country = _country(p.get("country")) or _country(address.get("country") or address.get("addressCountry")) or _country(location_text)
    # Keep unrecognized explicit country names for exclusion instead of treating them as Egypt.
    country = country or p.get("country") or address.get("addressCountry")
    city = city or (location_text.split(",")[0].strip() if location_text and not re.search(r"remote|worldwide|anywhere", location_text, re.I) else None)
    desc = p.get("descriptionPlain") or p.get("description") or p.get("content") or ""
    for item in p.get("lists", []):
        desc += "\n" + str(item.get("text", "")) + "\n" + plain_text(item.get("content", ""))
    if p.get("additionalPlain"):
        desc += "\n" + p["additionalPlain"]
    if isinstance(p.get("jobAd"), dict):
        desc += "\n" + "\n".join(str(v.get("title", "")) + "\n" + str(v.get("text", "")) for v in p["jobAd"].get("sections", {}).values() if isinstance(v, dict))
    description = plain_text(desc)
    work_mode = str(p.get("workplaceType") or p.get("jobLocationType") or "").lower()
    remote = p.get("remote") if isinstance(p.get("remote"), bool) else None
    if remote is None and isinstance(location, dict) and isinstance(location.get("remote"), bool):
        remote = location["remote"]
    if remote is None:
        if platform == "remotive" or work_mode in {"remote", "telecommute"} or re.search(r"\bremote\b|home[ -]based", location_text, re.I):
            remote = True
        elif work_mode in {"on-site", "onsite", "hybrid", "on_site"}:
            remote = False
        elif re.search(r"\b(?:fully remote|100% remote|remote position|work remotely)\b", description, re.I):
            remote = True
        elif re.search(r"\b(?:on-site|onsite|hybrid)\b", description, re.I):
            remote = False
    applicant_locations = p.get("applicantLocationRequirements")
    if applicant_locations:
        items = applicant_locations if isinstance(applicant_locations, list) else [applicant_locations]
        location_text = "; ".join(str(x.get("name", "")) if isinstance(x, dict) else str(x) for x in items)
    remote_egypt = p.get("remote_from_egypt") if isinstance(p.get("remote_from_egypt"), bool) else _remote_egypt(location_text, description, remote, country)
    if applicant_locations and remote and "Egypt" not in location_text and not re.search("worldwide|anywhere", location_text, re.I):
        remote_egypt = False
    commitment = p.get("employment_type") or p.get("employmentType") or p.get("job_type") or p.get("categories", {}).get("commitment") or p.get("typeOfEmployment")
    if isinstance(commitment, dict):
        commitment = commitment.get("label", commitment.get("id"))
    job_url = p.get("job_url") or p.get("absolute_url") or p.get("hostedUrl") or p.get("postingUrl") or p.get("url") or p.get("applyUrl") or p.get("_url")
    identifier = p.get("identifier", {})
    ext_id = str(p.get("external_id") or p.get("id") or p.get("uuid") or (identifier.get("value") if isinstance(identifier, dict) else identifier) or "")
    required = canonical_skills(p.get("required_skills", []))
    preferred = canonical_skills(p.get("preferred_skills", []))
    seniority = p.get("seniority")
    if not seniority:
        for label, pattern in [("executive", r"\b(?:cto|vp|director|head of)\b"), ("staff", r"\b(?:staff|principal|architect)\b"), ("lead", r"\b(?:lead|manager)\b"), ("senior", r"\bsenior\b|\bsr\.?\b"), ("junior", r"\bjunior\b|\bjr\.?\b|\bassociate\b|\bentry.level\b"), ("intern", r"\bintern(?:ship)?\b"), ("mid", r"\bmid(?:.level)?\b")]:
            if re.search(pattern, position or "", re.I):
                seniority = label
                break
    result = {**p, "id": p.get("id") if p.get("platform") and p.get("date_discovered") else _id(platform, p.get("board") or company, ext_id or job_url or position),
              "external_id": ext_id or None, "company": company, "position": position, "description": description,
              "job_url": job_url, "apply_url": p.get("apply_url") or p.get("applyUrl") or job_url, "platform": platform,
              "country": country, "city": city, "location_text": location_text, "remote": remote, "work_mode": work_mode or None,
              "remote_from_egypt": remote_egypt, "employment_type": commitment, "seniority": seniority,
              "required_skills": required, "preferred_skills": preferred, "min_years_experience": p.get("min_years_experience"),
              "salary_range": parse_salary(p.get("salary_range") or p.get("salaryRange") or p.get("salary") or p.get("baseSalary")),
              "date_discovered": p.get("date_discovered") or _now(), "job_posted_date": _posted(p.get("job_posted_date") or p.get("first_published") or p.get("datePosted") or p.get("publication_date") or p.get("createdAt") or p.get("releasedDate")),
              "application_deadline": p.get("application_deadline") or p.get("validThrough"), "company_url": company_url,
              "source_url": p.get("_source_url") or p.get("source_url") or job_url,
              "source_kind": p.get("source_kind") or ("employer_published" if platform in {"lever", "greenhouse", "smartrecruiters", "jsonld"} else "job_board"),
              "application_questions": p.get("application_questions", []), "provenance": p.get("provenance", {})}
    # Do not retain arbitrary hidden API fields, recruiter metadata or tracking code.
    fields = {"id", "external_id", "company", "position", "description", "job_url", "apply_url", "platform", "country", "city", "location_text", "remote", "work_mode", "remote_from_egypt", "employment_type", "seniority", "required_skills", "preferred_skills", "min_years_experience", "salary_range", "date_discovered", "job_posted_date", "application_deadline", "company_url", "source_url", "source_kind", "application_questions", "provenance", "board", "adapter", "work_authorization_required", "allowed_countries", "required_languages", "visa_sponsorship", "work_permit_support", "relocation_support", "relocation_support_adequate"}
    return {k: v for k, v in result.items() if k in fields}


def analyze_job(job: dict, profile: dict | None = None) -> dict:
    result = dict(job)
    previously_analyzed = bool(job.get("provenance", {}).get("analysis"))
    declared_required = job.get("declared_required_skills", job.get("required_skills", []) if not previously_analyzed else [])
    declared_preferred = job.get("declared_preferred_skills", job.get("preferred_skills", []) if not previously_analyzed else [])
    declared_minimum = job.get("declared_min_years_experience", job.get("min_years_experience") if not previously_analyzed else None)
    text = plain_text(job.get("description", ""))
    required, preferred, responsibilities, education, experience, languages, benefits, qualifications = [], [], [], [], [], [], [], []
    education_required = []
    mode = "general"
    exp_requirements = []
    alternatives = []
    for line in text.splitlines():
        low = line.lower()
        # A preference inside parentheses qualifies that variant, not the whole requirement.
        clean_line = re.sub(r"\([^)]*(?:preferred|nice.to.have|a plus)[^)]*\)", "", line, flags=re.I)
        clean_low = clean_line.lower()
        if len(line) < 100:
            if not canonical_skills(clean_line) and re.search(r"nice.to.have|preferred|bonus|desirable", clean_low): mode = "preferred"
            elif re.search(r"^(?:(?:minimum |basic |essential )?(?:requirements|qualifications)|what you.?ll need|what you need|who are you|who you are|must[ -]have|what you bring|what we are looking for)", low): mode = "required"
            elif re.search(r"^(?:responsibilities|what you.?ll do|what will you do|your role|what you.?ll lead|the role entails|job description)", low): mode = "responsibilities"
            elif re.search(r"benefits|what we offer", low): mode = "benefits"
            elif re.search(r"^about\b|why join|^what .+ offers", low): mode = "general"
        skills = canonical_skills(clean_line)
        optional = mode == "preferred" or bool(re.search(r"preferred|nice.to.have|bonus|is a plus|would be a plus|desirable", clean_low))
        mandatory = mode == "required" or bool(re.search(r"\b(?:must|required|at least|proficien\w*|experience (?:with|in)|knowledge of|familiarity with|strong (?:understanding|knowledge))\b", clean_low))
        if optional: preferred.extend(skills)
        elif mandatory:
            required.extend(skills)
            for left in skills:
                for right in skills:
                    if left != right and re.search(r"(?:" + SKILLS[left] + r")\s+or\s+(?:" + SKILLS[right] + r")", clean_line, re.I):
                        group = sorted({left, right})
                        if group not in alternatives: alternatives.append(group)
        if mode == "responsibilities": responsibilities.append(line)
        if mode == "benefits": benefits.append(line)
        if mode == "required": qualifications.append(line)
        if re.search(r"bachelor|master.?s|degree|computer science|engineering degree|equivalent (?:education|experience)", low):
            education.append(line)
            if not optional and mode != "benefits": education_required.append(line)
        if not optional and re.search(r"\b(?:english|arabic|french|german)\b", low): languages.append(line)
        for match in re.finditer(r"(?:(?:at least|minimum(?: of)?)\s*)?(\d+(?:\.\d+)?)\s*(?:[-–to]+\s*(\d+(?:\.\d+)?))?\+?\s*years?\b.{0,65}(?:experience|working|development)", line, re.I):
            if optional: continue
            experience.append(line)
            exp_requirements.append({"min": float(match.group(1)), "max": float(match.group(2)) if match.group(2) else None, "skills": skills, "text": line})
    technologies = canonical_skills(text + "\n" + str(job.get("position", "")))
    required = sorted(set(required + declared_required))
    preferred = sorted(set(preferred + declared_preferred) - set(required))
    minimum = declared_minimum
    if minimum is None and exp_requirements:
        minimum = max(x["min"] for x in exp_requirements)
    salary = job.get("salary_range")
    if not salary:
        salary_lines = [line for line in text.splitlines() if re.search(r"salary|compensation|remuneration", line, re.I) and re.search(r"\d", line) and len(line) < 300]
        salary = parse_salary(salary_lines[0]) if salary_lines else None
    result.update({"required_skills": required, "preferred_skills": preferred, "min_years_experience": minimum,
                   "declared_required_skills": declared_required, "declared_preferred_skills": declared_preferred, "declared_min_years_experience": declared_minimum,
                   "experience_requirements": exp_requirements, "required_skill_alternatives": alternatives, "salary_range": salary,
                   "required_keywords": required, "preferred_keywords": preferred,
                   "responsibility_keywords": canonical_skills("\n".join(responsibilities)), "technology_keywords": technologies,
                   "domain_keywords": sorted({x for x in ["fintech", "e-commerce", "logistics", "healthcare", "banking", "saas", "education", "payments"] if re.search(r"\b" + re.escape(x) + r"\b", text, re.I)}),
                   "soft_skill_keywords": sorted({x for x in ["communication", "collaboration", "leadership", "problem solving", "teamwork"] if x in text.lower()}),
                   "education_keywords": education, "experience_keywords": experience, "responsibilities": responsibilities,
                   "education_requirements": education_required, "language_requirements": languages, "qualifications": qualifications,
                   "benefits": benefits, "technology_stack": technologies, "analyzed_at": _now()})
    result["provenance"] = {**job.get("provenance", {}), "analysis": {"source": job.get("source_url") or job.get("job_url"), "method": "deterministic section/phrase extraction; ambiguous conditions stay unknown", "version": ANALYZER_VERSION, "date": _now()}}
    return result


def candidate_years(profile: dict, today: date | None = None) -> float | None:
    """Conservative completed months, union of employment ranges (no overlap inflation)."""
    if isinstance(profile.get("years_experience"), (int, float)):
        return float(profile["years_experience"])
    months: set[int] = set()
    today = today or date.today()
    for entry in profile.get("experience", []):
        if entry.get("experience_type") in {"training", "education"}:
            continue
        start = entry.get("start_date")
        end = entry.get("end_date") or (today.isoformat() if entry.get("current") else None)
        try:
            sy, sm = (int(n) for n in str(start).split("-")[:2])
            ey, em = (int(n) for n in str(end).split("-")[:2])
            if 1 <= sm <= 12 and 1 <= em <= 12:
                months.update(range(sy * 12 + sm, min(ey * 12 + em, today.year * 12 + today.month)))
        except (ValueError, TypeError):
            continue
    return round(len(months) / 12, 2) if months else None


def assess_eligibility(job: dict, profile: dict) -> dict:
    job = analyze_job(job, profile) if job.get("provenance", {}).get("analysis", {}).get("version") != ANALYZER_VERSION else job
    reasons, unknowns = [], []
    description = job.get("description", "")
    position = job.get("position") or ""
    if not ROLE_PATTERN.search(position): reasons.append("Position is outside frontend/full-stack/software target role families.")
    specific_title = re.search(r"front[ -]?end|full[ -]?stack|angular|react|node\.?js|nestjs|javascript|typescript|\bweb\s+(?:developer|engineer)|\bui\s+(?:developer|engineer)", position, re.I)
    if not specific_title and ROLE_PATTERN.search(position):
        javascript_ecosystem = {"Angular", "React", "TypeScript", "JavaScript", "Node.js", "NestJS", "Next.js", "Vue.js", "Express.js"}
        required_stack = set(job.get("required_skills", []))
        responsibility_evidence = "\n".join(job.get("responsibilities", []))
        builds_target_stack = re.search(r"(?:build|develop|implement|write|ship).{0,50}(?:using|with|in)\s+(?:Angular|React|TypeScript|JavaScript|Node\.?js|NestJS|Next\.js)", responsibility_evidence, re.I)
        if not required_stack & javascript_ecosystem and not builds_target_stack:
            reasons.append("Generic software role does not establish frontend/full-stack or JavaScript/Node ecosystem responsibilities; incidental frontend clients do not establish role fit.")
    if re.search(r"\b(?:quality|qa|sdet|test|security|data|machine learning|embedded|ios|android|devops)\b", position, re.I) and not re.search(r"front.end|full.stack|angular|react|node", position, re.I):
        reasons.append("Specialist role does not match configured frontend/full-stack targets.")
    if job.get("seniority") in {"executive", "staff", "lead", "intern"}:
        reasons.append(f"Seniority {job['seniority']} is outside the configured junior/mid/mid-senior targets.")
    country = job.get("country")
    if country and country not in TARGET_COUNTRIES and job.get("remote_from_egypt") is not True:
        reasons.append(f"Location {country} is outside target markets and remote work from Egypt is not established.")
    if not country and job.get("remote_from_egypt") is not True:
        unknowns.append("Job country or explicit permission to work remotely from Egypt.")
    if country in TARGET_COUNTRIES - {"Egypt"}:
        if job.get("remote_from_egypt") is not True:
            if job.get("remote"):
                unknowns.append("Employer confirmation that this remote Gulf role permits residence in Egypt.")
            else:
                relocation = profile.get("preferences", {}).get("relocation")
                if relocation is False: reasons.append("Gulf on-site/hybrid work requires relocation, which the candidate has declined.")
                elif isinstance(relocation, dict):
                    if relocation.get("willing") is not True:
                        unknowns.append("Candidate willingness to relocate for this Gulf role.")
                    if relocation.get("countries") and country not in relocation["countries"]:
                        reasons.append("Job country is outside the candidate's approved relocation countries.")
                    no_sponsor = re.search(r"(?:no|without|do not (?:offer|provide))\s+(?:visa\s+)?sponsorship", description, re.I)
                    sponsor = job.get("visa_sponsorship") is True or bool(re.search(r"(?:provide|offer|include|support).{0,30}(?:work )?visa sponsorship|(?:work )?visa sponsorship (?:provided|available|included)", description, re.I))
                    permit = job.get("work_permit_support") is True or bool(re.search(r"(?:provide|offer|include|support).{0,30}(?:work visa|work permit|work authori[sz]ation)|work visa sponsorship (?:provided|available|included)", description, re.I))
                    if no_sponsor or job.get("visa_sponsorship") is False:
                        reasons.append("Employer does not provide the visa sponsorship required by the candidate's relocation conditions.")
                    elif relocation.get("requires_employer_visa") and not sponsor:
                        unknowns.append("Employer evidence of the visa sponsorship required for conditional relocation.")
                    if relocation.get("requires_work_authorization") and not permit and _work_authorization(profile, country) is not True:
                        unknowns.append("Employer evidence of required work permit/authorization support.")
                    if relocation.get("requires_relocation_support") and job.get("relocation_support_adequate") is not True:
                        if job.get("relocation_support") is False:
                            reasons.append("Employer does not provide required relocation support.")
                        else:
                            unknowns.append("Employer relocation-support details sufficient to meet the candidate's conditional relocation preference.")
                elif relocation is not True: unknowns.append("Candidate willingness to relocate for this Gulf role.")
                authorization = _work_authorization(profile, country)
                if not isinstance(relocation, dict) and authorization is not True:
                    unknowns.append(f"Work authorization or employer-supported sponsorship for {country}.")
    if job.get("remote") is True and job.get("remote_from_egypt") is False:
        reasons.append("Remote location restrictions exclude residence in Egypt.")
    requirements = job.get("required_skills", [])
    actual = set(canonical_skills(profile.get("skills", [])))
    missing = sorted(set(requirements) - actual)
    for group in job.get("required_skill_alternatives", []):
        if set(group) & actual: missing = [skill for skill in missing if skill not in group]
    # Only explicitly mandatory technologies exclude; preferred gaps remain visible in ATS.
    if missing:
        reasons.append("Required skills absent from the verified profile: " + ", ".join(missing))
    if not requirements and not (actual & set(job.get("technology_keywords", []))):
        unknowns.append("No demonstrated overlap between the role technology stack and verified candidate skills.")
    years = candidate_years(profile)
    minimum = job.get("min_years_experience")
    if minimum is not None:
        if years is None: unknowns.append("Verified total professional experience duration.")
        elif years + 0.01 < minimum: reasons.append(f"Requires at least {minimum:g} years; verified non-overlapping employment supports {years:g} years.")
    for requirement in job.get("experience_requirements", []):
        if not requirement.get("skills"): continue
        skill_years = profile.get("experience_years_by_skill", {})
        for skill in requirement["skills"]:
            if skill not in actual: continue
            verified = skill_years.get(skill)
            if verified is None: unknowns.append(f"Verified {skill} experience duration for requirement: {requirement['text']}")
            elif verified < requirement["min"]: reasons.append(f"Required {skill} experience exceeds verified duration.")
    if job.get("seniority") == "senior" and minimum is None:
        unknowns.append("Senior title requires demonstrable fit from explicit experience requirements.")
    if job.get("work_authorization_required"):
        auth = _work_authorization(profile, country)
        if auth is not True: unknowns.append(f"Verified work authorization for {country or 'job country'}.")
    if re.search(r"(?:must|require[ds]?|only).{0,45}(?:national|citizen|security clearance)|(?:national|citizen).{0,20}only", description, re.I):
        unknowns.append("Explicit nationality or security-clearance restriction requires verified candidate evidence.")
    if re.search(r"(?:must|require[ds]?).{0,40}(?:work authori[sz]|authori[sz]ed to work)|(?:no|without)\s+(?:visa\s+)?sponsorship", description, re.I) and _work_authorization(profile, country) is not True:
        unknowns.append("Explicit legal authorization or sponsorship restriction requires verified candidate evidence.")
    if re.search(r"ability to travel|must.{0,20}travel|willingness to travel|work remotely with.{0,20}travel|(?:requires?|expected).{0,20}(?:global )?travel|travel (?:for|up to)\s+\d", description, re.I) and profile.get("preferences", {}).get("travel") is not True:
        unknowns.append("Verified ability and willingness to meet the role's travel requirement.")
    if re.search(r"(?:strong|exceptional|outstanding).{0,25}academic (?:performance|track record|results)|academic (?:performance|track record|results).{0,25}(?:strong|exceptional|outstanding)", description, re.I):
        unknowns.append("Evidence meeting the employer's stated academic-performance requirement; a degree alone does not establish this.")
    for requirement in job.get("education_requirements", []):
        if re.search(r"preferred|nice.to.have|bonus|a plus", requirement, re.I): continue
        if not re.search(r"degree|bachelor|master", requirement, re.I): continue
        degrees = " ".join(str(x.get("degree", "")) for x in profile.get("education", [])).lower()
        if not degrees:
            unknowns.append("Verified education matching employer requirement: " + requirement)
        elif re.search(r"\bmaster", requirement, re.I) and not re.search(r"master|m\.?sc", degrees):
            unknowns.append("Required postgraduate degree or accepted equivalent: " + requirement)
        elif re.search(r"computer science", requirement, re.I) and not re.search(r"computer science|computer engineering", degrees) and not re.search(r"related|equivalent|engineering", requirement, re.I):
            unknowns.append("Evidence of the specifically required computer science degree or employer-accepted equivalent.")
    for requirement in job.get("language_requirements", []):
        if not re.search(r"fluent|fluency|native|proficien|required|must|professional|strong written", requirement, re.I): continue
        for language in ["English", "Arabic", "French", "German"]:
            if not re.search(r"\b" + language + r"\b", requirement, re.I): continue
            item = next((x for x in profile.get("languages", []) if isinstance(x, dict) and x.get("name", "").lower() == language.lower()), None)
            if not item or not item.get("proficiency"):
                unknowns.append(f"Verified {language} proficiency for requirement: {requirement}")
    deadline = job.get("application_deadline")
    if deadline:
        try:
            if date.fromisoformat(str(deadline)[:10]) < date.today(): reasons.append("Application deadline has passed.")
        except ValueError: unknowns.append("Application deadline has an unrecognized date format.")
    if not job.get("job_url"): unknowns.append("Valid application URL.")
    if not job.get("company") or not position or not job.get("description"): unknowns.append("Complete company, position and job description.")
    return {"eligible": not reasons and not unknowns, "reasons": list(dict.fromkeys(reasons)), "unknowns": list(dict.fromkeys(unknowns)),
            "status": "EXCLUDED" if reasons else "NEEDS_INFORMATION" if unknowns else "QUALIFIED", "candidate_years": years,
            "missing_required_skills": missing, "evaluated_at": _now()}


def ats_analysis(job: dict, profile: dict) -> dict:
    job = analyze_job(job, profile) if job.get("provenance", {}).get("analysis", {}).get("version") != ANALYZER_VERSION else job
    actual = set(canonical_skills(profile.get("skills", [])))
    required = set(job.get("required_skills", []))
    keywords = set(job.get("technology_keywords", [])) | required | set(job.get("preferred_skills", []))
    responsibilities = set(job.get("responsibility_keywords", []))
    candidate_text = json.dumps({k: profile.get(k) for k in ["experience", "projects", "summary", "education"]}, ensure_ascii=False).lower()
    def coverage(items):
        return round(100 * len(items & actual) / len(items), 1) if items else None
    years, minimum = candidate_years(profile), job.get("min_years_experience")
    experience_score = round(min(100, 100 * years / minimum), 1) if years is not None and minimum else None
    domains = set(job.get("domain_keywords", []))
    breakdown = {"skills_match": coverage(required), "experience_match": experience_score,
                 "responsibilities_match": coverage(responsibilities), "keyword_coverage": coverage(keywords),
                 "education_match": None, "domain_match": round(100 * sum(d in candidate_text for d in domains) / len(domains), 1) if domains else None}
    # Education equivalence and degree wording require evidence-aware judgment, never an automatic 100%.
    weights = {"skills_match": 0.35, "experience_match": 0.2, "responsibilities_match": 0.15, "keyword_coverage": 0.2, "education_match": 0.05, "domain_match": 0.05}
    denominator = sum(weights[k] for k, value in breakdown.items() if value is not None)
    score = round(sum(weights[k] * value for k, value in breakdown.items() if value is not None) / denominator, 1) if denominator else None
    missing = sorted(keywords - actual)
    return {"ats_score": score, "ats_score_breakdown": breakdown, "matched_keywords": sorted(keywords & actual),
            "missing_keywords": missing, "recommended_changes": ["Emphasize existing evidence for: " + ", ".join(sorted(keywords & actual))] if keywords & actual else [],
            "skill_gaps": [{"skill": skill, "evidence_present": False, "action": "Verify existing evidence or learn; do not add to CV without evidence."} for skill in missing],
            "method": "Weighted deterministic coverage; unknown components omitted and weights renormalized. Skills are matched only against canonical candidate skills; domain is exact textual evidence.",
            "weights": weights, "evaluated_components": [k for k, v in breakdown.items() if v is not None],
            "limitations": ["Internal CV-to-description alignment, not interview or hiring probability.", "Keyword overlap does not prove duration, depth, degree equivalence or legal eligibility.", "Eligibility must be assessed independently."],
            "created_at": _now(), "job_id": job["id"]}


def _public_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs without credentials are supported.")
    for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError("Private/local network URLs are not allowed for discovery or research.")


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, *, expect_json: bool = True) -> Any:
    _public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "WorkAI/0.1 (public job discovery; no authentication bypass)", "Accept": "application/json" if expect_json else "text/html"})
    with urllib.request.build_opener(_PublicRedirect()).open(request, timeout=25) as response:
        content = response.read(12_000_001)
        if len(content) > 12_000_000: raise ValueError("Source response exceeds 12 MB limit.")
        text = content.decode("utf-8", errors="replace")
        if any(marker in text.lower() for marker in ["cf-chl-", "verify you are human", "captcha challenge"]):
            raise PermissionError("BLOCKED_BY_PLATFORM: human verification challenge.")
        return json.loads(text) if expect_json else text


class _JSONLD(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.parts = []
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.capture, self.current = True, []

    def handle_endtag(self, tag):
        if tag == "script" and self.capture:
            self.parts.append("".join(self.current))
            self.capture = False

    def handle_data(self, data):
        if self.capture: self.current.append(data)


def _jsonld_jobs(text: str) -> list[dict]:
    parser = _JSONLD()
    parser.feed(text)
    result = []
    def visit(value):
        if isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind: result.append(value)
            for item in value.values():
                if isinstance(item, (dict, list)): visit(item)
    for part in parser.parts:
        try: visit(json.loads(part))
        except json.JSONDecodeError: continue
    return result


def _source_jobs(source: dict) -> tuple[list[dict], str]:
    kind, board = source["type"], source.get("board", "")
    if kind == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(board, safe='')}/jobs?content=true"
        payloads = _fetch(url).get("jobs", [])
    elif kind == "lever":
        url = f"https://api.lever.co/v0/postings/{urllib.parse.quote(board, safe='')}?mode=json"
        payloads = _fetch(url)
    elif kind == "remotive":
        url = "https://remotive.com/api/remote-jobs?category=software-dev&limit=100"
        payloads = _fetch(url).get("jobs", [])
    elif kind == "smartrecruiters":
        params = {"limit": 100}
        if source.get("country"): params["country"] = source["country"]
        url = f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(board, safe='')}/postings?{urllib.parse.urlencode(params)}"
        listing = _fetch(url)
        payloads = []
        for item in listing.get("content", []):
            if ROLE_PATTERN.search(item.get("name", "")):
                detail = _fetch(f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(board, safe='')}/postings/{urllib.parse.quote(item['id'], safe='')}")
                detail["_url"] = detail.get("applyUrl") or f"https://jobs.smartrecruiters.com/{board}/{item['id']}"
                payloads.append(detail)
    elif kind == "jsonld":
        url = source["url"]
        payloads = _jsonld_jobs(_fetch(url, expect_json=False))
    elif kind == "manual_session":
        raise PermissionError("MANUAL_SESSION_REQUIRED: authenticated permitted adapter not configured; discovery is not implemented for this platform.")
    else:
        raise ValueError(f"Unsupported discovery source type: {kind}")
    jobs = []
    for payload in payloads:
        payload = {**payload, "_company": source.get("company") or board, "_company_url": source.get("company_url"), "_source_url": url, "board": board or None}
        if kind == "jsonld": payload["_url"] = payload.get("url") or url
        job = parse_job(payload, kind)
        if ROLE_PATTERN.search(job.get("position") or ""):
            jobs.append(job)
    return jobs, url


def discover_jobs(root: Path, sources: list[dict] | None = None) -> list[dict]:
    root = Path(root)
    if sources is None:
        path = root / "config/discovery.yaml"
        config = yaml.safe_load(path.read_text()) if path.exists() else {}
        sources = config.get("sources", [])
    jobs, results = {}, []
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for source in sources:
        if source.get("enabled", True) is False:
            results.append({"source": source.get("name") or source.get("board") or source.get("type"), "status": "NOT_CONFIGURED", "adapter": source.get("type"),
                            "reason": "Authenticated permitted adapter not implemented." if source.get("type") == "manual_session" else "Disabled in discovery configuration."})
            continue
        started = datetime.now(timezone.utc)
        try:
            found, url = _source_jobs(source)
            for job in found: jobs.setdefault(job["id"], job)
            results.append({"source": source.get("name") or source.get("board") or source["type"], "status": "OK", "jobs_found": len(found), "url": url, "retrieved_at": _now()})
        except Exception as error:
            status = "BLOCKED_BY_PLATFORM" if isinstance(error, PermissionError) or isinstance(error, urllib.error.HTTPError) and error.code in {401, 403, 429} else "FAILED"
            entry = {"timestamp": _now(), "agent": "Job Discovery Agent", "source": source.get("name") or source.get("board") or source.get("type"), "operation": "fetch_public_jobs", "status": status,
                     "error": str(error), "stack": traceback.format_exc(), "retry_count": 0,
                     "resolution": "Continue other sources; inspect source availability or configure a permitted session adapter.", "duration": (datetime.now(timezone.utc) - started).total_seconds()}
            results.append(entry)
            (root / "logs").mkdir(parents=True, exist_ok=True)
            with (root / "logs/discovery.jsonl").open("a", encoding="utf-8") as stream: stream.write(json.dumps(entry) + "\n")
    values = list(jobs.values())
    directory = root / "data/discovery" / run_id
    _write_once(directory / "jobs.json", values)
    _write_once(directory / "report.json", {"run_id": run_id, "created_at": _now(), "sources": results, "total_jobs": len(values),
                 "note": "Discovered listings are not qualified or submitted applications. Publisher fields are preserved; date_discovered is retrieval time."})
    return values


def research_company(root: Path, job: dict) -> dict:
    """Record employer-published claims with source/date, never unsourced inference."""
    root = Path(root)
    name = job.get("company")
    company_id = _id("company", name)
    facts, claims = [], []
    source = job.get("source_url") or job.get("job_url")
    if name:
        facts.append({"verified_fact": "Company name on published vacancy", "value": name, "source": source, "date": _now(), "verification": "Listing attribution; not independent corporate due diligence."})
    for key in ["position", "country", "city", "remote", "employment_type", "salary_range"]:
        if job.get(key) is not None:
            facts.append({"verified_fact": "Published job " + key, "value": job[key], "source": source, "date": _now()})
    website = job.get("company_url")
    research_error = None
    if website:
        try:
            page = _fetch(website, expect_json=False)
            title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            if title:
                facts.append({"verified_fact": "Title of configured company website", "value": plain_text(title.group(1)), "source": website, "date": _now()})
            about = re.search(r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)', page, re.I)
            if about:
                claims.append({"unverified_claim": html.unescape(about.group(1)), "source": website, "date": _now(), "note": "Company marketing description; not independently verified."})
        except Exception as error:
            research_error = {"error": str(error), "source": website, "date": _now(), "status": "RESEARCH_SOURCE_UNAVAILABLE"}
    record = {"id": company_id, "company_id": company_id, "company": name, "website": website,
              "careers_page": source, "linkedin": None, "industry": None, "company_size": None,
              "verified_facts": facts, "unverified_claims": claims, "relevant_job_urls": [job.get("job_url")],
              "application_platform": job.get("platform"), "date_researched": _now(), "error": research_error}
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _write_once(root / "data/companies" / company_id / (version + ".json"), record)
    return record
