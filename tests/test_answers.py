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


def _fixture_evidence(quote):
    return [{'source_id': 'user:fixture', 'quote': quote, 'status': 'user_provided'}]


def test_education_reuses_explicit_degree_and_university_without_graduation_guess(tmp_path, profile):
    profile['education'] = [
        {'id': 'degree-1', 'degree': 'Bachelor of Science in Computer Science', 'institution': 'Fixture University',
         'start_date': None, 'end_date': None,
         'evidence': _fixture_evidence('Bachelor of Science in Computer Science, Fixture University')},
        {'id': 'course-1', 'degree': 'Web Development Program', 'institution': 'Fixture Institute',
         'evidence': _fixture_evidence('Web Development Program, Fixture Institute')},
    ]
    for question in ['University name', 'Which university did you attend?']:
        result = resolve_question(tmp_path, question, profile, {})
        assert result['answer'] == 'Fixture University'
        assert result['source'] == 'master_profile:/education/0/institution'
        assert result['evidence'][0]['source_id'] == 'user:fixture'
    assert resolve_question(tmp_path, 'Degree', profile, {})['answer'] == 'Bachelor of Science in Computer Science'
    assert resolve_question(tmp_path, 'Highest level of education', profile, {})['answer'] == 'Bachelor of Science in Computer Science'
    assert resolve_question(tmp_path, 'Field of study', profile, {})['answer'] == 'Computer Science'
    assert resolve_question(tmp_path, 'Graduation year', profile, {})['status'] == 'UNKNOWN'
    summary = resolve_question(tmp_path, 'Educational background', profile, {})
    assert 'Fixture University' in summary['answer'] and 'Fixture Institute' in summary['answer']


def test_multiple_education_records_require_specificity_and_never_use_end_date_as_graduation(tmp_path, profile):
    profile['education'] = [
        {'id': 'bachelor', 'degree': 'Bachelor of Science in Mathematics', 'institution': 'First University',
         'end_date': '2020-05', 'evidence': _fixture_evidence('Bachelor of Science in Mathematics at First University')},
        {'id': 'master', 'degree': 'Master of Science in Computer Science', 'institution': 'Second University',
         'graduation_year': 2024, 'evidence': _fixture_evidence('Master of Science in Computer Science at Second University, graduated 2024')},
    ]
    assert resolve_question(tmp_path, 'University', profile, {})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Degree', profile, {})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Highest degree', profile, {})['answer'] == 'Master of Science in Computer Science'
    assert resolve_question(tmp_path, 'University', profile, {}, {'education_id': 'bachelor'})['answer'] == 'First University'
    assert resolve_question(tmp_path, 'Graduation year', profile, {}, {'education_id': 'bachelor'})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Year of graduation', profile, {}, {'education_id': 'master'})['answer'] == 2024


def test_unverified_education_is_not_promoted_to_fact(tmp_path, profile):
    profile['education'] = [{'degree': 'Doctor of Philosophy in Computing', 'institution': 'Unverified University',
                             'evidence': [{'source_id': 'external:speculation', 'status': 'unverified_claim'}]}]
    for question in ['Degree', 'University', 'Educational background', 'Highest degree']:
        assert resolve_question(tmp_path, question, profile, {})['status'] == 'UNKNOWN'


def test_all_current_roles_can_be_summarized_without_primary_employer_guess(tmp_path, profile):
    profile['experience'] = [
        {'title': 'Frontend Engineer', 'company': 'First Corp', 'current': True,
         'evidence': _fixture_evidence('Frontend Engineer at First Corp, Present')},
        {'title': 'Frontend Developer - Part-time', 'company': 'Second Corp', 'current': True,
         'evidence': _fixture_evidence('Frontend Developer - Part-time at Second Corp, Present')},
        {'title': 'Earlier Developer', 'company': 'Past Corp', 'current': False,
         'evidence': _fixture_evidence('Earlier Developer at Past Corp, ended 2020')},
    ]
    result = resolve_question(tmp_path, 'Describe your current roles', profile, {})
    assert result['answer'] == 'Frontend Engineer at First Corp; Frontend Developer - Part-time at Second Corp'
    assert resolve_question(tmp_path, 'Are you currently employed?', profile, {})['answer'] is True
    assert resolve_question(tmp_path, 'Current employers', profile, {})['answer'] == 'First Corp; Second Corp'
    assert resolve_question(tmp_path, 'Current employer', profile, {})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Current job title', profile, {})['status'] == 'UNKNOWN'
    profile['experience'] = []
    assert resolve_question(tmp_path, 'Are you currently employed?', profile, {})['status'] == 'UNKNOWN'


def test_experience_requires_explicit_sourced_duration_and_never_sums_concurrent_roles(tmp_path, profile):
    profile['experience'] = [
        {'start_date': '2020-01', 'current': True, 'company': 'One'},
        {'start_date': '2020-01', 'current': True, 'company': 'Two'},
    ]
    assert resolve_question(tmp_path, 'Total years of experience', profile, {})['status'] == 'UNKNOWN'
    profile['years_experience'] = 3.5
    assert resolve_question(tmp_path, 'Years of experience', profile, {})['status'] == 'UNKNOWN'
    profile['evidence'] = {'/years_experience': _fixture_evidence('My total professional experience is 3.5 years')}
    result = resolve_question(tmp_path, 'How many years of experience do you have?', profile, {})
    assert result['answer'] == 3.5
    assert result['source'] == 'master_profile:/years_experience'
    assert result['evidence'][0]['quote'].endswith('3.5 years')


def test_skill_years_never_reuse_total_years_or_infer_from_skill_presence(tmp_path, profile):
    profile.update(years_experience=5, skills=['Angular', 'React.js'], experience_years_by_skill={'Angular': 2, 'React.js': 0.5})
    profile['evidence'] = {'/years_experience': _fixture_evidence('Five total years'),
                           '/experience_years_by_skill/Angular': _fixture_evidence('Two years using Angular'),
                           '/experience_years_by_skill/React.js': _fixture_evidence('Half a year using React')}
    assert resolve_question(tmp_path, 'How many years of experience do you have with Angular?', profile, {})['answer'] == 2
    assert resolve_question(tmp_path, 'Years of React experience', profile, {})['answer'] == 0.5
    assert resolve_question(tmp_path, 'Years of experience', profile, {}, {'skill': 'Angular'})['answer'] == 2
    assert resolve_question(tmp_path, 'How many years of experience do you have with AngularJS?', profile, {})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Years of React Native experience', profile, {})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Years of Angular experience', profile, {}, {'skill': 'React'})['status'] == 'UNKNOWN'
    del profile['evidence']['/experience_years_by_skill/Angular']
    assert resolve_question(tmp_path, 'Years of Angular experience', profile, {})['status'] == 'UNKNOWN'


def test_skill_question_synonyms_reuse_approved_bank_without_punctuation_collisions(tmp_path, profile):
    add_answer(tmp_path, 'Years of experience with Angular', 2, source='user:fixture')
    assert resolve_question(tmp_path, 'How many years of Angular experience do you have?', profile, {})['answer'] == 2
    assert resolve_question(tmp_path, 'Years of experience with AngularJS', profile, {})['status'] == 'UNKNOWN'
    add_answer(tmp_path, 'Years of experience with C++', 4, source='user:fixture')
    assert resolve_question(tmp_path, 'C++ experience in years', profile, {})['answer'] == 4
    assert resolve_question(tmp_path, 'Years of experience with C', profile, {})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Years of experience with C#', profile, {})['status'] == 'UNKNOWN'


def test_generic_scoped_experience_answer_cannot_leak_into_other_skills(tmp_path, profile):
    add_answer(tmp_path, 'Years of experience', 5, source='user:fixture')
    add_answer(tmp_path, 'Years of experience', 2, source='user:fixture', scope={'skill': 'Angular'})
    assert resolve_question(tmp_path, 'Years of experience', profile, {}, {'skill': 'Angular'})['answer'] == 2
    assert resolve_question(tmp_path, 'Years of experience', profile, {}, {'skill': 'React'})['status'] == 'UNKNOWN'
    assert resolve_question(tmp_path, 'Years of experience', profile, {})['answer'] == 5


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '3+', 'about four'])
def test_invalid_or_nonexact_profile_years_are_not_numeric_answers(tmp_path, profile, value):
    profile['years_experience'] = value
    profile['evidence'] = {'/years_experience': _fixture_evidence(str(value))}
    assert resolve_question(tmp_path, 'Years of experience', profile, {})['status'] == 'UNKNOWN'
