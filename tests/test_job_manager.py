"""Public search fixture: pagination, identity and closure are employer evidence."""
import json
import urllib.error

import pytest

from workai import jobs


SOURCE = {"type": "wp_job_manager", "url": "https://employer.example/all-jobs/", "company": "Fixture Employer", "queries": ["front", "react"]}


def card(identifier, title="Frontend Developer", url=None):
    return f'<li class="post-{identifier} job_listing"><a href="{url or f"/job/{identifier}/"}"><h3>{title}</h3></a></li>'


def detail(identifier, *, closed=False, title="Frontend Developer"):
    record = {"@type": "JobPosting", "identifier": {"value": f"https://employer.example/?post_type=job_listing&amp;#038;p={identifier}"},
              "title": title, "description": "<p>Requirements</p><p>Angular required. Build interfaces.</p>",
              "hiringOrganization": {"name": "Fixture Employer"}, "datePosted": "2023-01-01",
              "jobLocation": {"address": "Cairo, Egypt"}}
    return '<script type="application/ld+json">' + json.dumps(record) + '</script>' + ('<div class="job_position_filled">Filled</div>' if closed else '')


def response(markup, pages=1, found=True):
    return {"found_jobs": found, "max_num_pages": pages, "html": markup}


def test_public_search_fetches_all_pages_deduplicates_and_preserves_details(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if "form_body" in kwargs:
            body = kwargs["form_body"]
            if body["search_keywords"] == "react":
                return response(card(1))
            return response(card(1) + card(9, "Accountant") if body["page"] == 1 else card(2), pages=2)
        return detail(1 if "/1/" in url else 2, closed="/2/" in url)
    monkeypatch.setattr(jobs, "_fetch", fetch)
    found, source_url = jobs._source_jobs(SOURCE)
    assert len(calls) == 5  # Three search pages, exactly one GET per matching vacancy.
    assert len(found) == 2
    assert found[0]["external_id"] == "employer.example:1"
    assert found[0]["job_posted_date"] == "2023-01-01"  # An old date alone is not closure.
    assert found[0].get("status") is None
    assert found[0]["country"] == "Egypt"
    assert "Angular required" in found[0]["description"]
    assert len(found[0]["provenance"]["searches"]) == 2
    assert found[1]["status"] == "closed"
    assert found[1]["provenance"]["closure_evidence"]
    assert any("explicitly closed" in reason for reason in jobs.assess_eligibility(found[1], {"skills": ["Angular"]})["reasons"])


@pytest.mark.parametrize("first,second,error", [
    (response(card(1), 2), response(card(1), 2), "repeated"),
    (response(card(1), 2), response("", 2), "empty"),
    (response(card(1), 2), response(card(2), 3), "changed"),
    (response(card(1), 100), None, "limit"),
    (response(card(1), -1), None, "invalid"),
    (response(card(1), 1, False), None, "contradicts"),
    ({"html": card(1), "max_num_pages": 1}, None, "invalid"),
])
def test_incomplete_or_inconsistent_search_never_claims_complete(monkeypatch, first, second, error):
    pages = iter([first, second])
    monkeypatch.setattr(jobs, "_fetch", lambda *args, **kwargs: next(pages))
    with pytest.raises(ValueError, match=error):
        jobs._source_jobs({**SOURCE, "queries": ["front"]})


@pytest.mark.parametrize("url", ["https://other.example/job/1/", "http://127.0.0.1/job/1/", "https://employer.example/apply/1/"])
def test_search_cannot_choose_arbitrary_detail_targets(monkeypatch, url):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(args[0])
        return response(card(1, url=url))
    monkeypatch.setattr(jobs, "_fetch", fetch)
    with pytest.raises(ValueError, match="origin/path"):
        jobs._source_jobs(SOURCE)
    assert len(calls) == 1


@pytest.mark.parametrize("page", [detail(9), detail(1, title="Different title"), detail(1) + detail(2), "<p>No structured vacancy</p>"])
def test_wrong_or_missing_detail_identity_is_rejected(monkeypatch, page):
    monkeypatch.setattr(jobs, "_fetch", lambda url, **kwargs: response(card(1)) if "form_body" in kwargs else page)
    with pytest.raises(ValueError, match="identify|ID differs"):
        jobs._source_jobs(SOURCE)


def test_public_platform_block_is_recorded_once_and_other_sources_continue(tmp_path, monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if "jm-ajax" in url:
            raise urllib.error.HTTPError(url, 429, "Too many requests", {}, None)
        return []
    monkeypatch.setattr(jobs, "_fetch", fetch)
    assert jobs.discover_jobs(tmp_path, [SOURCE, {"type": "lever", "board": "fixture"}]) == []
    report = json.loads(next((tmp_path / "data/discovery").glob("*/report.json")).read_text())
    assert [item["status"] for item in report["sources"]] == ["BLOCKED_BY_PLATFORM", "OK"]
    assert len(calls) == 2


def test_http_form_encoding_and_request_safety(monkeypatch):
    import urllib.parse
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, limit): return b'{"ok":true}'
    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            return Response()
    monkeypatch.setattr(jobs, "_public_url", lambda url: None)
    monkeypatch.setattr(jobs.urllib.request, "build_opener", lambda *args: Opener())
    result = jobs._fetch("https://employer.example/jm-ajax/get_listings/", form_body={"search_keywords": "full stack & UI", "page": 2})
    assert result == {"ok": True}
    request = captured[0]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(request.data.decode()) == {"search_keywords": ["full stack & UI"], "page": ["2"]}
    with pytest.raises(ValueError, match="both"):
        jobs._fetch("https://employer.example/", json_body={}, form_body={})


@pytest.mark.parametrize("title", ["Angular Instructor", "Full Stack Development Instructor", "React Technical Trainer"])
def test_target_technology_in_a_teaching_title_does_not_authorize_application(title):
    job = jobs.parse_job({"title": title, "company": "Fixture", "country": "Egypt", "description": "Teach Angular and React.",
                          "job_url": "https://employer.example/job/1/"})
    result = jobs.assess_eligibility(job, {"skills": ["Angular", "React"], "experience": [{"title": "Teaching Assistant"}]})
    assert result["status"] == "EXCLUDED"
    assert any("Teaching-focused" in reason for reason in result["reasons"])


@pytest.mark.parametrize("line", ["2.Tech skills (NodeJS and Angular).", "2-Tech skills (MVC, .net API).", "Technical skills: NodeJS and Angular"])
def test_numbered_technical_skill_requirements_without_a_section_heading(line):
    job = jobs.parse_job({"title": "Full Stack Developer", "company": "Fixture", "country": "Egypt",
                          "description": line, "job_url": "https://employer.example/job/2/"})
    result = jobs.assess_eligibility(job, {"skills": ["Angular"]})
    assert result["status"] == "EXCLUDED"
    assert result["missing_required_skills"] in [["Node.js"], [".NET"]]
    optional = jobs.analyze_job({**job, "description": line + " Preferred."})
    assert not optional["required_skills"]


@pytest.mark.parametrize("country,allowed", [("India", False), ("Egypt", True)])
def test_explicit_offshore_work_location_refreshes_old_normalized_records(country, allowed):
    job = jobs.parse_job({"title": "Angular Developer", "company": "Fixture", "country": "Saudi Arabia",
                          "description": f"Angular required.\n8.Work will be offshore from {country}.",
                          "job_url": "https://employer.example/job/2/"})
    assert job["remote"] is True
    assert job["remote_from_egypt"] is allowed
    stale = {**job, "remote": None, "remote_from_egypt": None}
    result = jobs.assess_eligibility(stale, {"skills": ["Angular"]})
    assert result["status"] == ("QUALIFIED" if allowed else "EXCLUDED")
    if not allowed:
        assert "Remote location restrictions exclude residence in Egypt." in result["reasons"]


@pytest.mark.parametrize("line", ["Our team works offshore from India.", "Work will be offshore from India or Egypt.",
                                  "Work will be offshore from India or another agreed location.", "Nationality: Indian."])
def test_offshore_location_is_not_inferred_from_colleagues_or_ambiguous_choices(line):
    assert jobs._offshore_country(line) is None


def test_quoted_employer_description_heading_preserves_responsibility_keywords():
    result = jobs.analyze_job({"description": '\u201d The Job Description\u201d\nBuild mobile applications using Flutter.'})
    assert result["responsibility_keywords"] == ["Flutter"]
    assert result["required_skills"] == ["Flutter"]


@pytest.mark.parametrize("line", ["Collaborate with engineers building applications using Flutter.",
                                  "Build interfaces in collaboration with the Flutter team.",
                                  "Maintain expertise in Angular; Flutter is a plus."])
def test_incidental_or_optional_responsibility_technology_does_not_become_mandatory(line):
    result = jobs.analyze_job({"description": 'Responsibilities\n' + line})
    assert "Flutter" not in result["required_skills"]
