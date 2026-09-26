from pathlib import Path
import json
import pytest

from workai.answers import add_answer, normalize_question, resolve_question


@pytest.fixture
def profile():
    return {"name": "Test Candidate", "contact": {"email": "fixture@example.invalid", "country": "Egypt"},
            "legal": {"work_authorization": None, "sponsorship": None},
            "preferences": {"salary": {"Egypt": {"amount": 40000, "currency": "EGP", "period": None, "basis": None},
                                       "Gulf": {"amount": 50000, "currency": "EGP", "period": None, "basis": None}}}}


def test_profile_precedes_approved_bank(tmp_path, profile):
    add_answer(tmp_path, "Email", "old@example.invalid", source="explicit-user")
    result = resolve_question(tmp_path, "Email address?", profile, {})
    assert result["answer"] == "fixture@example.invalid"
    assert result["source"] == "master_profile:/contact/email"


def test_history_preserved_and_alias_reused(tmp_path, profile):
    add_answer(tmp_path, "What is your notice period?", "30 days", source="explicit-user")
    add_answer(tmp_path, "Notice period", "60 days", source="explicit-user")
    result = resolve_question(tmp_path, "Notice period", profile, {"company": "Fixture Corp"})
    assert result["answer"] == "60 days"
    history = [json.loads(line) for line in (tmp_path / "data/answers-bank/history.jsonl").read_text().splitlines()]
    assert [item["event"] for item in history] == ["ANSWER_CREATED", "ANSWER_REVISED", "ANSWER_USED"]
    assert history[0]["entry"]["answer"] == "30 days"
    bank = json.loads((tmp_path / "data/answers-bank/answers.json").read_text())
    assert bank["answers"][0]["times_used"] == 1
    assert bank["answers"][0]["companies_used"] == ["Fixture Corp"]


def test_legal_answers_are_country_scoped(tmp_path, profile):
    with pytest.raises(ValueError):
        add_answer(tmp_path, "Do you require sponsorship", False, source="user")
    add_answer(tmp_path, "Do you require sponsorship", False, source="user", scope={"country": "Egypt"})
    assert resolve_question(tmp_path, "Do you require sponsorship", profile, {"country": "Egypt"})["answer"] is False
    assert resolve_question(tmp_path, "Do you require sponsorship", profile, {"country": "Saudi Arabia"})["status"] == "UNKNOWN"
    assert resolve_question(tmp_path, "Do you NOT require sponsorship", profile, {"country": "Egypt"})["status"] == "UNKNOWN"


def test_no_name_split_experience_or_current_salary_inference(tmp_path, profile):
    for question in ["First name", "Last name", "Years of experience", "Current salary", "Are you legally authorized to work in Saudi Arabia?"]:
        result = resolve_question(tmp_path, question, profile, {"country": "Saudi Arabia"})
        assert result["status"] == "UNKNOWN"
        assert result["event"]["type"] == "UNKNOWN_APPLICATION_FIELD"


def test_unspecified_salary_units_never_assumed(tmp_path, profile):
    result = resolve_question(tmp_path, "Expected salary", profile, {"country": "Egypt"},
                              {"currency": "EGP", "period": "monthly", "basis": "gross", "component": "base"})
    assert result["status"] == "UNKNOWN"
    assert result["salary_requested"] == 40000
    assert "period" in result["event"]["reason"]


def test_salary_range_max_requires_matching_units(tmp_path, profile):
    units = {"currency": "EGP", "period": "monthly", "basis": "gross", "component": "base"}
    job = {"country": "Egypt", "salary_range": {"min": 30000, "max": 45000, **units}}
    result = resolve_question(tmp_path, "Expected salary", profile, job, units)
    assert result["answer"] == 45000
    assert result["salary_source"] == "job.salary_range"
    assert resolve_question(tmp_path, "Expected salary", profile, job, {**units, "basis": "net"})["status"] == "UNKNOWN"


def test_salary_conversion_requires_sourced_rate(tmp_path, profile, monkeypatch):
    from datetime import datetime, timezone
    from workai.fx import FXUnavailable
    def unavailable(*args, **kwargs):
        raise FXUnavailable("Fixture rate unavailable")
    monkeypatch.setattr("workai.fx.get_exchange_rate", unavailable)
    profile["preferences"]["salary"]["Gulf"].update(period="monthly", basis="gross", component="base")
    units = {"currency": "AED", "period": "annual", "basis": "gross", "component": "base"}
    job = {"country": "United Arab Emirates"}
    assert resolve_question(tmp_path, "Expected salary", profile, job, units)["status"] == "UNKNOWN"
    result = resolve_question(tmp_path, "Expected salary", profile, job, {**units, "exchange_rate": {
        "from": "EGP", "to": "AED", "rate": "0.075", "date": datetime.now(timezone.utc).isoformat(), "source": "fixture-rate"}})
    assert result["answer"] == 45000
    assert result["salary_currency"] == "AED"


def test_company_answers_do_not_leak(tmp_path, profile):
    add_answer(tmp_path, "Why this company?", "Approved motivation for fixture", source="user", scope={"company": "Fixture"})
    assert resolve_question(tmp_path, "Why this company", profile, {"company": "Other"})["status"] == "UNKNOWN"
    assert resolve_question(tmp_path, "Why this company", profile, {"company": "Fixture"})["status"] == "KNOWN"


def test_approved_previous_answers_can_be_learned(tmp_path, profile):
    path = tmp_path / "applications/2026/fixture/application-questions.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"question": "Notice period", "answer": "Two weeks", "source": "user", "approved": True}]))
    result = resolve_question(tmp_path, "What is your notice period?", profile, {})
    assert result["answer"] == "Two weeks"
    assert (tmp_path / "data/answers-bank/answers.json").exists()


def test_unknown_prior_record_not_trusted(tmp_path, profile):
    path = tmp_path / "applications/2026/fixture/application-questions.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"question": "Current salary", "answer": 999999, "source": "job-description"}]))
    assert resolve_question(tmp_path, "Current salary", profile, {})["status"] == "UNKNOWN"


def test_sensitive_unlisted_phrasing_still_requires_scope(tmp_path):
    for question in ["Will you now or in the future need sponsorship?", "What compensation do you want?", "Why are you interested in our company?", "Why this position?"]:
        with pytest.raises(ValueError):
            add_answer(tmp_path, question, "Approved fixture answer", source="fixture")


def test_foreign_jurisdiction_salary_range_cannot_be_reused(tmp_path, profile):
    units = {"currency": "USD", "period": "annual", "basis": "gross", "component": "base"}
    job = {"country": "Egypt", "salary_range": {"min": 48000, "max": 70000, "applicable_countries": ["United States"], **units}}
    result = resolve_question(tmp_path, "Expected salary", profile, job, units)
    assert result["status"] == "UNKNOWN"


def test_approved_net_monthly_salary_normalizes_generic_annual(tmp_path, profile):
    profile['preferences']['salary']['Egypt'].update(period='monthly', basis='net', component=None)
    result = resolve_question(tmp_path, 'Expected salary', profile, {'country': 'Egypt'}, {'currency': 'EGP', 'period': 'annual', 'basis': 'net'})
    assert result['answer'] == 480000
    assert result['scope']['component'] is None
    assert resolve_question(tmp_path, 'Expected salary', profile, {'country': 'Egypt'}, {'currency': 'EGP', 'period': 'monthly', 'basis': 'gross'})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Expected salary', profile, {'country': 'Egypt'}, {'currency': 'EGP', 'period': 'monthly', 'basis': 'net', 'component': 'base'})['status'] == 'UNKNOWN'


def test_relocation_conditions_and_sponsorship_scope_are_preserved(tmp_path, profile):
    profile['preferences']['relocation'] = {'willing': True, 'countries': ['Saudi Arabia'], 'conditions': ['visa', 'support'], 'unconditional': False}
    profile['legal']['sponsorship'] = {'Saudi Arabia': True}
    profile['legal']['sponsorship_scope'] = {'Saudi Arabia': 'relocation'}
    job = {'country': 'Saudi Arabia'}
    assert resolve_question(tmp_path, 'Are you willing to relocate', profile, job)['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Are you willing to relocate', profile, job, {'visa_and_work_authorization_provided': True, 'reasonable_relocation_support': True})['answer'] is True
    assert resolve_question(tmp_path, 'Do you require sponsorship', profile, job)['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Do you require sponsorship', profile, job, {'work_arrangement': 'relocation'})['answer'] is True


def test_generic_salary_bank_scope_cannot_match_explicit_component(tmp_path, profile):
    scope = {'country': 'Egypt', 'currency': 'EGP', 'period': 'monthly', 'basis': 'net'}
    add_answer(tmp_path, 'What compensation do you want?', 40000, source='fixture-user', scope=scope)
    assert resolve_question(tmp_path, 'What compensation do you want?', profile, {'country': 'Egypt'}, scope)['answer'] == 40000
    assert resolve_question(tmp_path, 'What compensation do you want?', profile, {'country': 'Egypt'}, {**scope, 'component': 'base'})['status'] == 'UNKNOWN'
