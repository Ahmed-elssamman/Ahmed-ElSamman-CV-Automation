import copy
import json

import httpx
import pytest

from workai.reasoning import AIUnavailable, fact_catalog, rank_profile, review_job, settings, validate_review


@pytest.fixture
def profile():
    return {"name": "Private Name", "contact": {"email": "private@example.test"},
            "legal": {"citizenship": "private"}, "skills": ["Angular", "React"],
            "evidence": {"/skills/0": [{"quote": "Angular"}], "/skills/1": [{"quote": "React"}]},
            "experience": [{"title": "Engineer", "bullets": ["Built Angular screens.", "Built React screens."]}],
            "projects": [{"name": "A", "description": "Angular app"}, {"name": "B", "description": "React app"}]}


@pytest.fixture
def job():
    return {"id": "fixture", "position": "React Developer", "description": "React experience is required. Angular is preferred.", "job_url": "https://example.test/job"}


@pytest.fixture
def review():
    return {"requirements": [{"quote": "React experience is required.", "importance": "required", "category": "skills", "skills": ["React"], "needs_factual_review": False}],
            "skill_order": ["skill:1"], "project_order": ["project:1"], "bullet_order": ["bullet:0:1"]}


def test_settings_are_scoped_and_never_execute_shell(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WORKAI_OPENAI_MODEL", raising=False)
    (tmp_path / '.env').write_text('OPENAI_API_KEY="$(touch should-not-exist)"\nWORKAI_OPENAI_MODEL=chosen-model\nHOME=/malicious\n')
    config = settings(tmp_path)
    assert config['OPENAI_API_KEY'] == '$(touch should-not-exist)'
    assert 'HOME' not in config
    assert not (tmp_path / 'should-not-exist').exists()
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-key')
    assert settings(tmp_path)['OPENAI_API_KEY'] == 'environment-key'


def test_only_existing_facts_can_be_ranked(profile, job, review):
    original = copy.deepcopy(profile)
    validated = validate_review(review, job, profile)
    ranked = rank_profile(profile, validated)
    assert ranked['skills'] == ['React', 'Angular']
    assert ranked['evidence']['/skills/0'] == profile['evidence']['/skills/1']
    assert ranked['experience'][0]['bullets'] == list(reversed(profile['experience'][0]['bullets']))
    assert len(ranked['projects']) == 2 and ranked['projects'][0]['name'] == 'B'
    assert profile == original
    assert 'private@example.test' not in json.dumps(fact_catalog(profile))
    assert 'citizenship' not in json.dumps(fact_catalog(profile))


@pytest.mark.parametrize('mutation', ['fabricated_fact', 'fabricated_quote', 'unsupported_skill', 'extra_action', 'duplicate', 'wrong_category'])
def test_model_output_cannot_introduce_unsupported_claims(profile, job, review, mutation):
    if mutation == 'fabricated_fact': review['skill_order'].append('skill:999')
    if mutation == 'fabricated_quote': review['requirements'][0]['quote'] = 'Docker is essential.'
    if mutation == 'unsupported_skill': review['requirements'][0]['skills'] = ['Docker']
    if mutation == 'extra_action': review['submit_application'] = True
    if mutation == 'duplicate': review['skill_order'].append('skill:1')
    if mutation == 'wrong_category': review['skill_order'] = ['project:1']
    with pytest.raises(AIUnavailable): validate_review(review, job, profile)


def test_responses_contract_cache_and_no_candidate_contact(tmp_path, monkeypatch, profile, job, review):
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-key')
    monkeypatch.setenv('WORKAI_OPENAI_MODEL', 'explicit-model')
    calls = []
    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload['store'] is False
        assert payload['text']['format']['strict'] is True
        assert payload['model'] == 'explicit-model'
        assert 'private@example.test' not in payload['input']
        return httpx.Response(200, json={'id': 'response-fixture', 'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(review)}]}], 'usage': {'output_tokens': 20}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = review_job(tmp_path, job, profile, client=client)
        second = review_job(tmp_path, job, profile, client=client)
    assert first == second
    assert len(calls) == 1
    assert first['response_id'] == 'response-fixture'


@pytest.mark.parametrize('response', [
    {'status': 'incomplete', 'output': []},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}]},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'not JSON'}]}]},
])
def test_incomplete_refused_or_invalid_results_fail_closed(tmp_path, monkeypatch, profile, job, response):
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-key')
    monkeypatch.setenv('WORKAI_OPENAI_MODEL', 'explicit-model')
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as client:
        with pytest.raises(AIUnavailable): review_job(tmp_path, job, profile, client=client)
    assert not (tmp_path / 'data/ai-reviews').exists()


def test_missing_credentials_is_explicit(tmp_path, monkeypatch, profile, job):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('WORKAI_OPENAI_MODEL', raising=False)
    with pytest.raises(AIUnavailable, match='Set OPENAI_API_KEY'):
        review_job(tmp_path, job, profile)
