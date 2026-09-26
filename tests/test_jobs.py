"""Truthfulness, location, source isolation and transparent score regression tests."""
import json
from datetime import date

import pytest

from workai import jobs


@pytest.fixture
def profile():
    return {"name": "Test Candidate", "skills": ["Angular", "TypeScript", "React.js", "Git", "HTML", "CSS"],
            "experience": [{"company": "Fixture", "title": "Frontend Developer", "start_date": "2023-01", "end_date": "2025-01"}],
            "education": [{"degree": "Bachelor of Computer Engineering"}], "languages": [{"name": "English", "proficiency": None}],
            "preferences": {"relocation": None}}


def make_job(**changes):
    raw = {"title": "Frontend Developer", "company": "Fixture Co", "country": "EG", "location": "Cairo",
           "description": "Requirements\nExperience with Angular and TypeScript.\nPreferred\nDocker is a plus.",
           "url": "https://example.org/jobs/frontend", "id": "test-job"}
    raw.update(changes)
    return jobs.parse_job(raw)


def test_parse_lever_html_dates_and_country():
    parsed = jobs.parse_job({"text": "React Developer", "id": "abc", "_company": "Example",
                            "descriptionPlain": "Example engineering team", "categories": {"location": "Cairo", "commitment": "Full-time"},
                            "country": "EG", "workplaceType": "hybrid", "createdAt": 1704067200000,
                            "hostedUrl": "https://jobs.lever.co/example/abc", "lists": [{"text": "Requirements", "content": "<ul><li>TypeScript required</li></ul>"}]}, "lever")
    assert parsed["country"] == "Egypt"
    assert parsed["remote"] is False
    assert parsed["job_posted_date"] == "2024-01-01T00:00:00+00:00"
    assert "TypeScript required" in parsed["description"]
    assert "<li>" not in parsed["description"]
    assert jobs.parse_job(parsed)["id"] == parsed["id"]


def test_greenhouse_updates_are_not_posting_dates():
    parsed = jobs.parse_job({"title": "Angular Developer", "company_name": "Employer", "content": "&lt;h2&gt;Requirements&lt;/h2&gt;&lt;p&gt;Angular required&lt;/p&gt;",
                            "location": {"name": "Dubai, United Arab Emirates"}, "updated_at": "2026-09-26", "absolute_url": "https://example.org/job"}, "greenhouse")
    assert parsed["country"] == "United Arab Emirates"
    assert parsed["job_posted_date"] is None
    assert jobs.analyze_job(parsed)["required_skills"] == ["Angular"]


def test_jsonld_text_address_is_a_valid_location():
    parsed = jobs.parse_job({"title": "Frontend Developer", "hiringOrganization": {"name": "Employer"},
                            "jobLocation": {"@type": "Place", "address": "Cairo, Egypt."},
                            "description": "React developer", "url": "https://example.org/jobs/1"}, "jsonld")
    assert parsed["country"] == "Egypt"
    assert parsed["city"] == "Cairo"


@pytest.mark.parametrize("text,expected", [
    ("EGP 30,000 - 45,000 gross per month", (30000, 45000, "EGP", "monthly", "gross")),
    ("120k–180k USD annual base", (120000, 180000, "USD", "annual", "base")),
    ("50,000 EGP", (50000, 50000, "EGP", None, None)),
    ("$100k - $120k per year", (100000, 120000, None, "annual", None)),
])
def test_salary_preserves_ambiguity(text, expected):
    parsed = jobs.parse_salary(text)
    assert tuple(parsed[k] for k in ["min", "max", "currency", "period", "basis"]) == expected


def test_salary_structured_units():
    salary = jobs.parse_salary({"currency": "SAR", "value": {"minValue": 10000, "maxValue": 15000, "unitText": "MONTH"}})
    assert salary["period"] == "monthly"
    assert salary["currency"] == "SAR"
    assert salary["basis"] is None
    assert jobs.parse_salary({"min": 60000, "max": 90000, "currency": "USD", "interval": "per-year-salary"})["period"] == "annual"


def test_analysis_separates_preferred_and_required(profile):
    job = jobs.analyze_job(make_job(), profile)
    assert job["required_skills"] == ["Angular", "TypeScript"]
    assert job["preferred_skills"] == ["Docker"]
    assert jobs.assess_eligibility(job, profile)["eligible"]
    report = jobs.ats_analysis(job, profile)
    assert report["missing_keywords"] == ["Docker"]
    assert report["ats_score_breakdown"]["education_match"] is None
    assert "Docker" not in profile["skills"]
    assert "probability" in " ".join(report["limitations"])


def test_required_missing_skill_excludes(profile):
    job = make_job(description="Requirements\nStrong experience with Angular and Docker.")
    result = jobs.assess_eligibility(job, profile)
    assert not result["eligible"]
    assert result["missing_required_skills"] == ["Docker"]


def test_labelled_employer_experience_does_not_hide_minimum(profile):
    job = make_job(description="Requirements\nAngular required.\nPreferred\nDocker is a plus.\nExperience needed: 5 years.")
    parsed = jobs.analyze_job(job)
    assert parsed["min_years_experience"] == 5
    assert not jobs.assess_eligibility(parsed, profile)["eligible"]
    # Structured metadata cannot weaken the full description's explicit minimum.
    job = make_job(min_years_experience=1, description="Requirements\n5+ years of software development experience.\nAngular required.")
    assert jobs.analyze_job(job)["min_years_experience"] == 5


def test_preferred_years_remain_optional(profile):
    job = make_job(description="Requirements\nAngular required.\nPreferred\n5 years of software development experience.")
    assert jobs.analyze_job(job)["min_years_experience"] is None


def test_academic_alternatives_and_database_replication(profile):
    job = make_job(description="Requirements\nAngular required.\nBachelor's or Master's degree in Computer Science or related Engineering.\nResponsibilities\nMaintain master-slave database replication.")
    analysis = jobs.analyze_job(job)
    assert len(analysis["education_requirements"]) == 1
    assert jobs.assess_eligibility(analysis, profile)["eligible"]
    postgraduate = make_job(description="Requirements\nAngular required.\nMaster's degree in Computer Science.")
    assert not jobs.assess_eligibility(postgraduate, profile)["eligible"]


def test_framework_examples_do_not_become_mandatory_brands(profile):
    job = make_job(description="Requirements\nFamiliarity with responsive design principles and frameworks (e.g., Bootstrap).\nFamiliar with ReactJS, HTML, CSS and JavaScript.")
    analysis = jobs.analyze_job(job)
    assert "Bootstrap" not in analysis["required_skills"]
    assert "Bootstrap" in analysis["technology_keywords"]
    assert "React" in analysis["required_skills"]
    strict = jobs.analyze_job(make_job(description="Requirements\nBootstrap required (e.g., its responsive grid)."))
    assert "Bootstrap" in strict["required_skills"]


def test_employer_history_is_not_candidate_experience(profile):
    job = make_job(description="Requirements\n2 years of frontend development experience.\nAngular required.\nAbout the employer\nExample is a leading IT company with over 25 years of experience delivering mission-critical systems.")
    analysis = jobs.analyze_job(job)
    assert analysis["min_years_experience"] == 2
    assert jobs.assess_eligibility(analysis, profile)["eligible"]


@pytest.mark.parametrize("heading", ["What we're looking for", "What we’re looking for", "Required", "Required:"])
def test_contracted_and_standalone_required_headings(heading):
    job = make_job(title="Senior Software Developer", description=f"About the role\nProduction engineering.\n{heading}\nDeep Node.js and modern TypeScript.\nStrong React.\n5+ years building and maintaining production software.")
    analysis = jobs.analyze_job(job)
    assert set(analysis["required_skills"]) == {"Node.js", "TypeScript", "React"}
    assert analysis["min_years_experience"] == 5
    assert "Strong React." in analysis["qualifications"]


def test_trufla_requirements_establish_role_fit_and_real_minimum(profile):
    # Public Trufla vacancy trufla-851 / URL 51143, retrieved 2026-09-26.
    description = """About Trufla
Trufla builds digital infrastructure for insurance brokerages.
What You'll Do
Design and ship features end to end across our Node.js services and React front ends.
What We're Looking For
Required
5+ years building and maintaining production software, with real ownership of what you shipped.
Deep Node.js and modern JavaScript/TypeScript. You understand the runtime, not just the framework.
Strong React, including state management, performance, and component design.
Nice to Have (any one of these is a plus, none are dealbreakers)
Cloud and container experience (AWS or GCP, Kubernetes, Terraform).
About the company
A company with over 25 years building production software."""
    analysis = jobs.analyze_job(make_job(title="Senior Software Developer", description=description))
    assert analysis["min_years_experience"] == 5
    assert {"Node.js", "JavaScript", "TypeScript", "React"} <= set(analysis["required_skills"])
    assert "AWS" in analysis["preferred_skills"] and "AWS" not in analysis["required_skills"]
    result = jobs.assess_eligibility(analysis, profile)
    assert result["status"] == "EXCLUDED"
    assert any("at least 5 years" in reason for reason in result["reasons"])
    assert not any("Generic software role" in reason for reason in result["reasons"])


@pytest.mark.parametrize("phrase,minimum", [
    ("5+ years building and maintaining production software.", 5),
    ("Minimum 4 years maintaining production software.", 4),
    ("2–4 years building web applications.", 2),
])
def test_building_and_maintaining_experience_phrases(phrase, minimum):
    analysis = jobs.analyze_job(make_job(description="Required\nAngular required.\n" + phrase))
    assert analysis["min_years_experience"] == minimum
    optional = jobs.analyze_job(make_job(description="Required\nAngular required.\nPreferred\n" + phrase))
    assert optional["min_years_experience"] is None


def test_trailing_skill_duration_requires_specific_evidence(profile):
    job = make_job(description="Requirements\n1-2 years of experience in front-end development.\nExperience using Angular at least 1 year.")
    analysis = jobs.analyze_job(job)
    assert analysis["experience_requirements"][-1]["skills"] == ["Angular"]
    assert jobs.assess_eligibility(analysis, profile)["status"] == "NEEDS_INFORMATION"
    assert jobs.assess_eligibility(analysis, {**profile, "experience_years_by_skill": {"Angular": 1}})["eligible"]


def test_disjunctions_and_parenthetical_preferences(profile):
    job = make_job(description="What we are looking for in you\nExperience with TypeScript, React or Flutter.\nExperience with Linux (Debian or Ubuntu preferred).")
    analysis = jobs.analyze_job(job)
    assert ["Flutter", "React"] in analysis["required_skill_alternatives"]
    result = jobs.assess_eligibility(analysis, profile)
    assert result["missing_required_skills"] == ["Linux"]
    assert "Linux" in analysis["required_skills"]


def test_smartrecruiters_remote_and_external_id():
    job = jobs.parse_job({"id": "123", "uuid": "internal-uuid", "name": "React Engineer", "company": {"name": "Employer"},
                          "location": {"city": "Cairo", "country": "eg", "remote": True}, "postingUrl": "https://example.org/job",
                          "jobAd": {"sections": {"qualifications": {"title": "Qualifications", "text": "<p>React required</p>"}}}}, "smartrecruiters")
    assert job["remote"] is True
    assert job["remote_from_egypt"] is True
    assert job["external_id"] == "123"
    assert job["job_url"] == "https://example.org/job"


def test_salary_jurisdiction_scope():
    parsed = jobs.parse_salary("For US based candidates, compensation is 48,000 USD to 70,000 USD")
    assert parsed["applicable_countries"] == ["United States"]
    assert parsed["period"] is None


def test_remote_dubai_never_implies_egypt_permission(profile):
    job = make_job(country="AE", location="Dubai", workplaceType="remote")
    assert job["remote"] is True
    assert job["remote_from_egypt"] is None
    result = jobs.assess_eligibility(job, profile)
    assert result["status"] == "NEEDS_INFORMATION"
    assert any("permits residence" in q for q in result["unknowns"])


def test_remote_worldwide_is_allowed_and_country_restriction_is_not(profile):
    job = make_job(country=None, location="Worldwide", workplaceType="remote")
    assert job["remote_from_egypt"] is True
    assert jobs.assess_eligibility(job, profile)["eligible"]
    restricted = make_job(country="US", location="Remote - USA only", workplaceType="remote")
    assert restricted["remote_from_egypt"] is False
    assert not jobs.assess_eligibility(restricted, profile)["eligible"]


def test_specific_residence_restriction_overrides_worldwide_label(profile):
    job = make_job(country=None, location="Worldwide", workplaceType="remote", description="Requirements\nAngular required.\nApplicants must be based in the United States.")
    assert job["remote_from_egypt"] is False
    assert not jobs.assess_eligibility(job, profile)["eligible"]


def test_public_canonical_app_stores_regression(profile):
    # Exact relevant paragraphs from public Greenhouse listing 3159992 retrieved 2026-09-26.
    description = """What you’ll do
Write clean web service APIs to support both CLI and web frontend clients, using Python (and optionally Golang).
Work remotely with global travel for 2 to 4 weeks for internal and external events.
Who you are
You have demonstrated professional proficiency in developing public-facing APIs and web applications using Python.
You have a broad technology base but favour backend code and infrastructure.
You are comfortable with Ubuntu as a development and deployment platform.
You have demonstrated strong academic performance in Computer Science, STEM or a similar degree.
About Canonical
Canonical is an equal opportunity employer."""
    profile["skills"].append("Python")
    job = make_job(title="Software Engineer - App Stores", country=None, location="Home based - Worldwide", description=description)
    analyzed = jobs.analyze_job(job)
    assert "Linux" in analyzed["required_skills"]
    assert "Python" in analyzed["required_skills"]
    assert all("About Canonical" not in text for text in analyzed["responsibilities"])
    result = jobs.assess_eligibility(analyzed, profile)
    assert result["status"] == "EXCLUDED"
    assert any("Generic software role" in text for text in result["reasons"])
    assert any("travel" in text for text in result["unknowns"])
    assert any("academic" in text for text in result["unknowns"])


def test_generic_software_requires_core_javascript_responsibilities(profile):
    profile["skills"].extend(["Python", "Go"])
    outside = make_job(title="Software Engineer - Go", description="Requirements\nProfessional experience with Golang.\nPreferred\nReact is a plus.")
    assert not jobs.assess_eligibility(outside, profile)["eligible"]
    javascript = make_job(title="Software Engineer", description="Requirements\nExperience with TypeScript and React.")
    assert jobs.assess_eligibility(javascript, profile)["eligible"]


def test_reanalysis_removes_obsolete_inferences_but_preserves_declared_fields():
    first = jobs.analyze_job(make_job(description="Requirements\nAngular and Docker required.", required_skills=["TypeScript"]))
    updated = {**first, "description": "Requirements\nAngular required.\nPreferred\nDocker is a plus."}
    final = jobs.analyze_job(updated)
    assert final["required_skills"] == ["Angular", "TypeScript"]
    assert final["preferred_skills"] == ["Docker"]
    assert final["provenance"]["analysis"]["version"] == jobs.ANALYZER_VERSION


def test_gulf_onsite_needs_relocation_and_authorization(profile):
    result = jobs.assess_eligibility(make_job(country="SA", location="Riyadh", workplaceType="on-site"), profile)
    assert not result["eligible"]
    assert len(result["unknowns"]) == 2
    profile["preferences"]["relocation"] = False
    assert jobs.assess_eligibility(make_job(country="SA", workplaceType="on-site"), profile)["status"] == "EXCLUDED"


def test_conditional_relocation_needs_employer_evidence(profile):
    profile["legal"] = {"work_authorization": {"Egypt": True, "Saudi Arabia": False}}
    profile["preferences"]["relocation"] = {"willing": True, "countries": ["Saudi Arabia"], "unconditional": False,
                                               "requires_employer_visa": True, "requires_work_authorization": True, "requires_relocation_support": True}
    role = make_job(country="SA", location="Riyadh", workplaceType="on-site")
    result = jobs.assess_eligibility(role, profile)
    assert result["status"] == "NEEDS_INFORMATION"
    assert any("relocation-support" in text for text in result["unknowns"])
    supported = {**role, "visa_sponsorship": True, "work_permit_support": True, "relocation_support_adequate": True}
    assert jobs.assess_eligibility(supported, profile)["eligible"]
    assert not jobs.assess_eligibility({**supported, "visa_sponsorship": False}, profile)["eligible"]


def test_known_egypt_authorization_resolves_legal_requirement(profile):
    profile["legal"] = {"work_authorization": {"Egypt": True}}
    job = make_job(work_authorization_required=True, description="Requirements\nAngular required.\nMust be authorized to work in Egypt without sponsorship.")
    assert jobs.assess_eligibility(job, profile)["eligible"]


def test_job_id_stays_stable_when_title_is_updated():
    assert make_job(title="Angular Developer")["id"] == make_job(title="Angular Frontend Developer")["id"]


def test_overlapping_experience_not_double_counted(profile):
    profile["experience"].append({"start_date": "2024-01", "end_date": "2025-01", "title": "Part-time"})
    assert jobs.candidate_years(profile, today=date(2026, 9, 26)) == 2.0
    job = make_job(description="Requirements\nAt least 5 years of professional software development experience.\nAngular required.")
    result = jobs.assess_eligibility(job, profile)
    assert not result["eligible"]
    assert any("5 years" in reason for reason in result["reasons"])


def test_skill_years_not_inferred_from_total(profile):
    job = make_job(description="Requirements\nAt least 2 years experience with Angular.")
    result = jobs.assess_eligibility(job, profile)
    assert not result["eligible"]
    assert any("Angular experience duration" in reason for reason in result["unknowns"])


def test_law_and_language_never_guessed(profile):
    job = make_job(description="Requirements\nAngular required.\nMust be an Egyptian citizen.\nFluent English required.")
    result = jobs.assess_eligibility(job, profile)
    assert not result["eligible"]
    assert any("nationality" in q for q in result["unknowns"])
    assert any("English proficiency" in q for q in result["unknowns"])


@pytest.mark.parametrize("requirement", [
    "Fluent English required.", "Native English required.", "English fluency is required.",
    "English at CEFR B2 level is required.", "English (C1).", "Professional working proficiency in English.",
    "Professional English required.",
    "Proficient in English (verbal and written).", "Strong written English is required.",
    "Very good command of English.",
])
def test_good_english_does_not_establish_unverified_higher_or_different_level(profile, requirement):
    profile["languages"] = [{"name": "English", "proficiency": "Good"}]
    job = make_job(description="Requirements\nAngular required.\n" + requirement)
    result = jobs.assess_eligibility(job, profile)
    assert result["status"] == "NEEDS_INFORMATION"
    assert any("English proficiency" in question for question in result["unknowns"])
    assert profile["languages"][0]["proficiency"] == "Good"


@pytest.mark.parametrize("actual,requirement", [
    ("Good", "Good English is required."),
    ("Very Good", "Good English is required."),
    ("Excellent", "Very good command of English."),
    ("Advanced", "Intermediate English required."),
    ("B2", "English B1 required."),
    ("C1", "English B2 required."),
    ("Fluent", "English fluency required."),
    ("Native", "Native English speaker required."),
    ("Professional working proficiency", "Professional working proficiency in English required."),
])
def test_explicit_language_matches_and_comparable_levels(profile, actual, requirement):
    profile["languages"] = [{"name": "English", "proficiency": actual}]
    job = make_job(description="Required\nAngular required.\n" + requirement)
    assert jobs.assess_eligibility(job, profile)["eligible"]


@pytest.mark.parametrize("actual,requirement", [
    ("Fluent", "English B2 required."),
    ("C2", "Native English required."),
    ("Excellent", "Fluent English required."),
    ("B1", "English B2 required."),
    ("not fluent", "Fluent English required."),
    ("Good spoken; basic written", "Good English is required."),
])
def test_language_scales_and_ambiguous_profile_text_are_not_assumed_equivalent(profile, actual, requirement):
    profile["languages"] = [{"name": "English", "proficiency": actual}]
    result = jobs.assess_eligibility(make_job(description="Required\nAngular required.\n" + requirement), profile)
    assert result["status"] == "NEEDS_INFORMATION"


def test_missing_language_and_mixed_language_levels_stay_unknown(profile):
    profile["languages"] = [{"name": "English", "proficiency": "Good"}]
    missing = jobs.assess_eligibility(make_job(description="Required\nAngular required.\nFluent German required."), profile)
    assert any("German proficiency" in question for question in missing["unknowns"])
    profile["languages"].append({"name": "Arabic", "proficiency": "Fluent"})
    mixed = make_job(description="Required\nAngular required.\nGood English and fluent Arabic required.")
    assert jobs.assess_eligibility(mixed, profile)["status"] == "NEEDS_INFORMATION"
    separated = make_job(description="Required\nAngular required.\nGood English; fluent Arabic required.")
    assert jobs.assess_eligibility(separated, profile)["eligible"]


def test_language_under_required_heading_is_not_silently_ignored(profile):
    job = make_job(description="Required\nAngular required.\nFrench.")
    result = jobs.assess_eligibility(job, profile)
    assert result["status"] == "NEEDS_INFORMATION"
    assert any("French proficiency" in question for question in result["unknowns"])


@pytest.mark.parametrize("requirement", ["English required.", "English is required.", "Required language: English.", "Language required: English.", "English."])
def test_bare_language_requirement_accepts_known_proficiency(profile, requirement):
    profile["languages"] = [{"name": "English", "proficiency": "Good"}]
    job = make_job(description="Required\nAngular required.\n" + requirement)
    assert jobs.assess_eligibility(job, profile)["eligible"]


@pytest.mark.parametrize("proficiency", [None, "", "UNKNOWN", "unavailable", "not fluent"])
def test_bare_language_requirement_still_needs_known_proficiency(profile, proficiency):
    profile["languages"] = [{"name": "English", "proficiency": proficiency}]
    result = jobs.assess_eligibility(make_job(description="Required\nAngular required.\nEnglish required."), profile)
    assert result["status"] == "NEEDS_INFORMATION"


def test_preferred_education_and_languages_do_not_become_requirements(profile):
    job = make_job(description="Requirements\nAngular required.\nPreferred qualifications\nMaster's degree.\nFluent Arabic.")
    assert jobs.assess_eligibility(job, profile)["eligible"]


def test_ats_empty_is_unknown_not_perfect(profile):
    job = make_job(description="We are hiring.", title="Web Developer")
    report = jobs.ats_analysis(job, profile)
    assert report["ats_score"] is None
    assert all(value is None for value in report["ats_score_breakdown"].values())


def test_roles_and_deadlines_filter(profile):
    for changes in [{"title": "Principal Angular Architect"}, {"title": "Software Engineer - Machine Learning"}, {"application_deadline": "2020-01-01"}]:
        assert not jobs.assess_eligibility(make_job(**changes), profile)["eligible"]


def test_jsonld_and_applicant_country_restrictions():
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "JobPosting", "title": "Frontend Developer", "identifier": "a",
                    "jobLocationType": "TELECOMMUTE", "applicantLocationRequirements": [{"@type": "Country", "name": "United States"}],
                    "hiringOrganization": {"name": "Example", "sameAs": "https://example.org"},
                    "jobLocation": {"address": {"addressCountry": "US", "addressLocality": "Boston"}}, "description": "Angular"}]}) + "</script>"
    payload = jobs._jsonld_jobs(html)[0]
    job = jobs.parse_job(payload, "jsonld")
    assert job["company"] == "Example"
    assert job["city"] == "Boston"
    assert job["remote_from_egypt"] is False
    assert job["external_id"] == "a"


def test_discovery_is_idempotent_and_failure_does_not_block_other_sources(tmp_path, monkeypatch):
    def fetch(url, **kwargs):
        if "blocked" in url: raise PermissionError("human verification")
        return [{"text": "Angular Developer", "id": "a", "descriptionPlain": "Angular", "country": "EG", "hostedUrl": "https://example.org/a"}]
    monkeypatch.setattr(jobs, "_fetch", fetch)
    sources = [{"type": "lever", "board": "blocked"}, {"type": "lever", "board": "ok", "company": "Example"}, {"type": "lever", "board": "ok", "company": "Example"}]
    first = jobs.discover_jobs(tmp_path, sources)
    second = jobs.discover_jobs(tmp_path, sources)
    assert len(first) == 1 and first[0]["id"] == second[0]["id"]
    reports = list((tmp_path / "data/discovery").glob("*/report.json"))
    assert len(reports) == 2
    report = json.loads(reports[0].read_text())
    assert report["sources"][0]["status"] == "BLOCKED_BY_PLATFORM"
    assert report["sources"][1]["status"] == "OK"


def test_company_records_preserve_sources_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_fetch", lambda *a, **kw: '<title>Company official site</title><meta name="description" content="We are industry leaders">')
    job = make_job(company_url="https://example.org")
    one = jobs.research_company(tmp_path, job)
    two = jobs.research_company(tmp_path, job)
    assert one["id"] == two["id"]
    assert len(list((tmp_path / "data/companies" / one["id"]).glob("*.json"))) == 2
    assert all(x["source"] and x["date"] for x in one["verified_facts"])
    assert one["unverified_claims"][0]["unverified_claim"] == "We are industry leaders"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:secret@example.org", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/"])
def test_discovery_rejects_non_public_targets(url):
    with pytest.raises(ValueError): jobs._public_url(url)
