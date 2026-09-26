from test_profile import sample_profile
from workai.answers import add_answer, resolve_question
from workai.cover import compose_cover_message


def vacancy():
    return {"id": "fixture", "company": "Fixture Employer", "position": "Frontend Developer", "description": "Angular and Docker"}


def test_cover_preserves_source_and_never_adds_missing_skills(tmp_path, sample_profile):
    result = compose_cover_message(tmp_path, sample_profile, vacancy())
    assert result["status"] == "KNOWN"
    assert sample_profile["projects"][0]["description"] in result["answer"]
    assert "Docker" not in result["answer"]
    assert result["scope"] == {"job_id": "fixture"}
    assert result["derivation"]["profile_references"] == ["/name", "/projects/0"]


def test_cover_refuses_unsupported_candidate_claims(tmp_path, sample_profile):
    sample_profile["projects"][0]["description"] = "Built Docker systems for 500 clients."
    assert compose_cover_message(tmp_path, sample_profile, vacancy())["status"] == "UNKNOWN"


def test_saved_specific_message_precedes_template(tmp_path, sample_profile):
    add_answer(tmp_path, "Application cover message", "Applicant's supplied wording", source="user:fixture", scope={"job_id": "fixture"})
    result = resolve_question(tmp_path, "Application cover message", sample_profile, vacancy())
    assert result["answer"] == "Applicant's supplied wording"


def test_personal_authorship_and_motivation_are_not_invented(tmp_path, sample_profile):
    assert resolve_question(tmp_path, "Cover letter", sample_profile, vacancy(), {"requires_personal_authorship": True})["status"] == "UNKNOWN"
    assert resolve_question(tmp_path, "Why this company?", sample_profile, vacancy())["status"] == "UNKNOWN"
