"""Public employer adapter fixtures; no live network or candidate information."""
import json
import urllib.error

import pytest

from workai import jobs


def test_workable_full_description_locations_and_stable_vacancy(monkeypatch):
    calls = []
    listing = {"name": "Fixture", "jobs": [
        {"title": "Frontend Developer", "shortcode": "ABC", "country": "Saudi Arabia", "city": "Riyadh", "url": "https://apply.workable.com/j/ABC", "application_url": "https://apply.workable.com/j/ABC/apply"},
        {"title": "Frontend Developer", "shortcode": "ABC", "country": "Egypt", "city": "Cairo"},
        {"title": "Accountant", "shortcode": "NO"},
    ]}
    detail = {"shortcode": "ABC", "title": "Frontend Developer", "state": "published", "remote": True,
              "description": "<p>Build interfaces.</p>", "requirements": "<ul><li>Angular required.</li></ul>",
              "benefits": "<p>Training.</p>", "published": "2026-09-01T00:00:00Z"}

    def fetch(url):
        calls.append(url)
        return detail if "/api/v2/" in url else listing

    monkeypatch.setattr(jobs, "_fetch", fetch)
    found, url = jobs._source_jobs({"type": "workable_widget", "board": "fixture"})
    assert len(found) == 1 and len(calls) == 2
    job = found[0]
    assert job["external_id"] == "ABC"
    assert job["country"] == "Egypt" and job["city"] == "Cairo"
    assert job["remote_from_egypt"] is True
    assert job["apply_url"].endswith("/ABC/apply")
    assert job["source_kind"] == "employer_published"
    assert job["source_url"].endswith("/api/v2/accounts/fixture/jobs/ABC")
    assert job["job_posted_date"] == "2026-09-01T00:00:00Z"
    assert "Requirements\nAngular required.\nBenefits\nTraining." in job["description"]
    assert len(job["provenance"]["locations"]) == 2
    assert job["provenance"]["widget_rows"] == 2
    listing["jobs"].reverse()
    assert jobs._source_jobs({"type": "workable_widget", "board": "fixture"})[0][0]["id"] == job["id"]


@pytest.mark.parametrize("extra", [{"state": "closed"}, {"state": "published", "isInternal": True}])
def test_workable_omits_closed_and_internal_jobs(monkeypatch, extra):
    monkeypatch.setattr(jobs, "_fetch", lambda url: extra if "/api/v2/" in url else {"jobs": [{"title": "React Developer", "shortcode": "ABC"}]})
    assert jobs._source_jobs({"type": "workable_widget", "board": "fixture"})[0] == []


def test_flairstech_all_pages_full_descriptions_and_no_internal_metadata(monkeypatch):
    calls = []
    record = {"id": "fixture-id", "title": "Angular Developer", "isAvailable": True, "validForCandidates": True,
              "requirements": "<p>Build Angular interfaces.</p>", "experienceNeeded": "2-3", "positionLocation": "Cairo",
              "createDate": "2026-09-01", "lastModifiedDate": "2026-09-02", "createdBy": {"fullName": "PRIVATE"}}

    def fetch(url, *, json_body):
        assert json_body == {}
        calls.append(url)
        index = len(calls) - 1
        return {"status": True, "result": {"pageIndex": index, "pagesTotalCount": 2, "records": [record] if index == 0 else [record, {**record, "id": "private", "validForCandidates": False}]}}

    monkeypatch.setattr(jobs, "_fetch", fetch)
    found, url = jobs._source_jobs({"type": "flairstech", "job_url_template": "https://engage.flairstech.com/position/{id}"})
    assert len(found) == 1 and len(calls) == 2
    job = found[0]
    assert job["country"] == "Egypt"
    assert "Build Angular interfaces." in job["description"]
    assert "Experience needed: 2-3 years." in job["description"]
    assert job["job_posted_date"] is None
    assert job["provenance"]["source_created_at"] == "2026-09-01"
    assert job["source_kind"] == "employer_published"
    assert job["job_url"].endswith("/fixture-id")
    assert "PRIVATE" not in json.dumps(job)


def test_flairstech_rejects_repeated_page(monkeypatch):
    monkeypatch.setattr(jobs, "_fetch", lambda url, **kwargs: {"status": True, "result": {"pageIndex": 0, "pagesTotalCount": 2, "records": [{"id": "x"}]}})
    with pytest.raises(ValueError, match="unexpected page"):
        jobs._source_jobs({"type": "flairstech"})


def test_flairstech_existing_experience_unit_is_preserved(monkeypatch):
    record = {"id": "fixture", "title": "React Developer", "isAvailable": True, "validForCandidates": True,
              "requirements": "<p>React required.</p>", "experienceNeeded": "3 years"}
    monkeypatch.setattr(jobs, "_fetch", lambda url, **kwargs: {"status": True, "result": {"pageIndex": 0, "pagesTotalCount": 1, "records": [record]}})
    found, _ = jobs._source_jobs({"type": "flairstech"})
    assert "Experience needed: 3 years." in found[0]["description"]
    assert "years years" not in found[0]["description"]


def test_workable_security_block_is_reported_without_retry(tmp_path, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(jobs, "_fetch", fetch)
    assert jobs.discover_jobs(tmp_path, [{"type": "workable_widget", "board": "fixture"}]) == []
    report = json.loads(next((tmp_path / "data/discovery").glob("*/report.json")).read_text())
    assert report["sources"][0]["status"] == "BLOCKED_BY_PLATFORM"
    assert len(calls) == 1
