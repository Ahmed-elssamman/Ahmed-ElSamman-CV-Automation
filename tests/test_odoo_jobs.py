"""Public Odoo source fixtures: pagination, vacancy identity and unknown facts."""
import json
import urllib.error

import pytest

from workai import jobs


SOURCE = {"type": "odoo_careers", "url": "https://employer.example/jobs", "company": "Fixture Employer"}


def card(identifier, title="Frontend Developer"):
    return f'<div class="card"><a href="/jobs/role-{identifier}"><h3>{title}</h3><div class="o_job_infos">Software Engineering</div></a></div>'


def listing(cards, total=2, page=1, next_link=None):
    following = f'<li><a href="{next_link}"><span aria-label="Next"></span></a></li>' if next_link else '<li class="disabled"><a href=""><span aria-label="Next"></span></a></li>'
    return f'''<div id="wrap" class="o_website_hr_recruitment_jobs_list">
<a href="/jobs?all_departments=1">All Departments {total}</a><div id="jobs_grid">{cards}</div>
<ul class="pagination"><li class="active"><a href="/jobs">{page}</a></li>{following}</ul></div>'''


def detail(identifier, title="Frontend Developer", address=""):
    return f'''<div id="wrap" class="js_hr_recruitment"><section><h1>{title}</h1>
<address><div itemprop="address" itemtype="http://schema.org/PostalAddress">{address}</div></address>
<a href="/jobs/apply/role-{identifier}">Apply Now!</a></section>
<div><section><h2>Requirements</h2><p>Angular and HTML required.</p></section></div>
<div class="oe_structure"><a href="/jobs/apply/role-{identifier}">Apply Now!</a></div></div>'''


def test_all_pages_full_descriptions_and_identity_without_department_false_matches(monkeypatch):
    calls = []
    pages = {SOURCE["url"]: listing(card(1) + card(3, "Accountant"), 3, next_link="/jobs/page/2"),
             "https://employer.example/jobs/page/2": listing(card(2), 3, 2),
             "https://employer.example/jobs/role-1": detail(1),
             "https://employer.example/jobs/role-2": detail(2, address='<span itemprop="addressCountry" content="EG"></span><span itemprop="addressLocality">Cairo</span>')}
    def fetch(url, **kwargs):
        calls.append((url, kwargs)); return pages[url]
    monkeypatch.setattr(jobs, "_fetch", fetch)
    records, url = jobs._source_jobs(SOURCE)
    assert len(calls) == 4 and len(records) == 2
    assert all(options == {"expect_json": False} for _, options in calls)
    assert records[0]["external_id"] == "employer.example:1"
    assert records[0]["company"] == "Fixture Employer"
    assert records[0]["country"] is None and records[0]["job_posted_date"] is None
    assert records[0]["employment_type"] is None
    assert records[1]["country"] == "Egypt" and records[1]["city"] == "Cairo"
    assert "Angular and HTML required." in records[0]["description"]
    assert "Software Engineering" not in records[0]["position"]
    assert "Apply Now!" not in records[0]["description"]
    assert len(records[0]["provenance"]["listing_pages"]) == 2
    assert records[0]["provenance"]["advertised_listings"] == 3


@pytest.mark.parametrize("first,second,match", [
    (listing(card(1), 2, next_link="/jobs/page/2"), listing(card(1), 2, 2), "repeated"),
    (listing(card(1), 2, next_link="/jobs/page/2"), listing(card(2), 3, 2), "changed"),
    (listing(card(1), 2, next_link="/jobs/page/2"), listing("", 2, 2), "empty"),
    (listing(card(1), 2), None, "before"),
    (listing(card(1), 2, next_link="/jobs/page/2"), listing(card(2), 2, 1), "active page"),
    (listing(card(1), 1, next_link="/jobs/page/2"), listing(card(2), 1, 2), "exceed"),
    ('<h1>Login required</h1>', None, "layout"),
])
def test_incomplete_or_changing_board_is_not_reported_complete(monkeypatch, first, second, match):
    pages = iter([first, second])
    monkeypatch.setattr(jobs, "_fetch", lambda *args, **kwargs: next(pages))
    with pytest.raises(ValueError, match=match): jobs._source_jobs(SOURCE)


@pytest.mark.parametrize("next_link", ["https://other.example/jobs/page/2", "/jobs/page/2?department_id=9", "/jobs/apply/role-1", "/jobs/page/5"])
def test_next_link_cannot_change_origin_scope_or_skip_pages(monkeypatch, next_link):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url); return listing(card(1), 2, next_link=next_link)
    monkeypatch.setattr(jobs, "_fetch", fetch)
    with pytest.raises(ValueError, match="origin/path"): jobs._source_jobs(SOURCE)
    assert calls == [SOURCE["url"]]


@pytest.mark.parametrize("page,match", [(detail(9), "route"), (detail(1, title="Different role"), "title"),
                                        (detail(1).replace('<div><section>', '<div class="oe_structure"><section>'), "description"),
                                        ("<main>No longer available</main>", "layout")])
def test_detail_must_match_title_and_application_identity_and_contain_full_description(monkeypatch, page, match):
    monkeypatch.setattr(jobs, "_fetch", lambda url, **kwargs: listing(card(1), 1) if url == SOURCE["url"] else page)
    with pytest.raises(ValueError, match=match): jobs._source_jobs(SOURCE)


def test_empty_board_requires_explicit_zero_and_valid_structure(monkeypatch):
    monkeypatch.setattr(jobs, "_fetch", lambda *args, **kwargs: listing("", 0))
    assert jobs._source_jobs(SOURCE)[0] == []


def test_empty_description_is_retained_as_unknown_without_discarding_other_jobs(monkeypatch):
    empty = detail(2).replace('<h2>Requirements</h2><p>Angular and HTML required.</p>', '')
    def fetch(url, **kwargs):
        if url == SOURCE["url"]: return listing(card(1) + card(2), 2)
        return empty if url.endswith('role-2') else detail(1)
    monkeypatch.setattr(jobs, "_fetch", fetch)
    records, _ = jobs._source_jobs(SOURCE)
    assert len(records) == 2 and records[0]["description"]
    assert records[1]["description"] == ""
    assert records[1]["provenance"]["description_status"] == "MISSING"
    result = jobs.assess_eligibility(records[1], {"skills": ["Angular", "HTML"]})
    assert result["status"] == "NEEDS_INFORMATION"
    assert "Complete company, position and job description." in result["unknowns"]


def test_bounds_are_enforced_before_fetching_excess_pages_or_details(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url); return listing(card(1), 2, next_link="/jobs/page/2")
    monkeypatch.setattr(jobs, "_fetch", fetch)
    with pytest.raises(ValueError, match="page limit"): jobs._source_jobs({**SOURCE, "max_pages": 1})
    with pytest.raises(ValueError, match="limit"): jobs._source_jobs({**SOURCE, "max_listings": 1})
    assert calls == [SOURCE["url"], SOURCE["url"]]


def test_blocked_odoo_source_does_not_block_unrelated_discovery(tmp_path, monkeypatch):
    def fetch(url, **kwargs):
        if url == SOURCE["url"]: raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return []
    monkeypatch.setattr(jobs, "_fetch", fetch)
    assert jobs.discover_jobs(tmp_path, [SOURCE, {"type": "lever", "board": "fixture"}]) == []
    report = json.loads(next((tmp_path / "data/discovery").glob("*/report.json")).read_text())
    assert [item["status"] for item in report["sources"]] == ["BLOCKED_BY_PLATFORM", "OK"]
