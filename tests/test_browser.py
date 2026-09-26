"""Real Chromium fixture tests. No real candidate or application data is submitted."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest
import yaml

from workai.answers import add_answer
from workai.browser import apply_job


HTML = '''<!DOCTYPE html><html><body><form id="one">
<label>Full name<input id="full" required></label><label>Email<input type="email" required></label>
<label>Resume<input type="file" required></label><button type="button" onclick="document.getElementById('one').hidden=true;document.getElementById('two').hidden=false">Continue</button>
</form><form id="two" hidden onsubmit="event.preventDefault(); fetch('/submitted',{method:'POST'}).then(()=>{this.hidden=true;document.getElementById('confirmation').hidden=false;})">
<label for="country">Country of residence</label><select id="country" required><option value="">Choose</option><option>Egypt</option></select>
<p>Do you require sponsorship</p><label>Yes<input name="sponsor" type="radio" required></label><label>No<input name="sponsor" type="radio" required></label>
<label>I agree to processing<input type="checkbox" required></label>
<button>Submit application</button></form><p id="confirmation" data-testid="application-confirmation" hidden>Application received. Reference: FIXTURE-123</p></body></html>'''


@pytest.fixture
def site():
    state = {"submissions": 0, "html": HTML}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(state["html"].encode())
        def do_POST(self):
            state["submissions"] += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/apply", state
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def configured(tmp_path, site):
    url, state = site
    adapter = {"enabled": True, "allowed_hosts": ["127.0.0.1"], "timeout_ms": 2500,
               "confirmation_timeout_ms": 600,
               "steps": [
                   {"ready": {"label": "Full name"}, "fields": [
                       {"label": "Full name"}, {"label": "Email", "type": "email"}, {"label": "Resume", "type": "file"}],
                    "next": {"role": "button", "name": "Continue"}},
                   {"ready": {"label": "Country of residence"}, "fields": [
                       {"label": "Country of residence", "type": "select"},
                       {"question": "Do you require sponsorship", "type": "radio", "options": {"True": {"label": "Yes"}, "False": {"label": "No"}}},
                       {"label": "I agree to processing", "type": "checkbox"}],
                    "submit": {"role": "button", "name": "Submit application"}}],
               "confirmation": {"locator": {"test_id": "application-confirmation"}, "text_pattern": "Application received", "id_pattern": "Reference: ([A-Z0-9-]+)"}}
    path = tmp_path / "config/platforms.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump({"platforms": {"fixture": adapter}}))
    profile = {"name": "Fixture Person", "contact": {"email": "fixture@example.invalid", "country": "Egypt"}}
    job = {"id": "fixture-job", "platform": "fixture", "company": "Fixture", "job_url": url, "country": "Egypt"}
    pdf = tmp_path / "fixture.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% fixture upload bytes\n%%EOF")
    add_answer(tmp_path, "Do you require sponsorship", False, source="fixture-only", scope={"country": "Egypt"})
    add_answer(tmp_path, "I agree to processing", True, source="fixture-only", scope={"company": "Fixture"})
    return tmp_path, job, profile, {"pdf_path": str(pdf)}, adapter, state


def test_multistep_upload_select_radio_checkbox_and_confirmed_submit(configured):
    root, job, profile, cv, adapter, state = configured
    intents = []
    result = apply_job(root, job, profile, cv, submit=True, before_submit=intents.append)
    assert result["status"] == "SUBMITTED", result
    assert state["submissions"] == 1
    assert result["confirmation_id"] == "FIXTURE-123"
    assert result["upload_evidence"]["sha256"] == hashlib.sha256(Path(cv["pdf_path"]).read_bytes()).hexdigest()
    assert len(intents) == 1
    assert intents[0]["job_id"] == job["id"]
    assert any(answer["answer"] is False for answer in result["answers"])


def test_prepare_does_not_submit(configured):
    root, job, profile, cv, adapter, state = configured
    result = apply_job(root, job, profile, cv)
    assert result["status"] == "READY_TO_APPLY", result
    assert state["submissions"] == 0


def test_unknown_required_dynamic_field_blocks(configured):
    root, job, profile, cv, adapter, state = configured
    state["html"] = HTML.replace('<button>Submit application', '<label>National ID<input required></label><button>Submit application')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result["status"] == "BLOCKED_UNKNOWN_FIELDS", result
    assert state["submissions"] == 0
    assert any(event["question"] == "National ID" for event in result["unknown_events"])
    assert all(event["type"] == "UNKNOWN_APPLICATION_FIELD" for event in result["unknown_events"])


def test_unknown_legal_answer_blocks(configured):
    root, job, profile, cv, adapter, state = configured
    job["country"] = "Saudi Arabia"
    result = apply_job(root, job, profile, cv, submit=True)
    assert result["status"] == "BLOCKED_UNKNOWN_FIELDS"
    assert state["submissions"] == 0


def test_ambiguous_confirmation_never_claims_success_or_retries(configured):
    root, job, profile, cv, adapter, state = configured
    state["html"] = HTML.replace("Application received. Reference:", "Still processing. Reference:")
    result = apply_job(root, job, profile, cv, submit=True)
    assert result["status"] == "SUBMISSION_UNCONFIRMED", result
    assert state["submissions"] == 1
    assert result["retryable"] is False
    assert result["confirmation_id"] is None


def test_security_challenge_is_recorded(configured):
    root, job, profile, cv, adapter, state = configured
    state["html"] = '<label>Sign in<input type="password"></label>'
    result = apply_job(root, job, profile, cv, submit=True)
    assert result["status"] == "BLOCKED_BY_PLATFORM"
    assert state["submissions"] == 0


def test_failed_durable_intent_callback_prevents_submit(configured):
    root, job, profile, cv, adapter, state = configured
    def broken(_):
        raise ValueError("Fixture durable store unavailable")
    result = apply_job(root, job, profile, cv, submit=True, before_submit=broken)
    assert result["status"] == "FAILED"
    assert state["submissions"] == 0


def test_unsupported_platform_is_not_faked(tmp_path):
    result = apply_job(tmp_path, {"platform": "linkedin"}, {}, {}, submit=True)
    assert result["status"] == "BLOCKED_BY_PLATFORM"


def test_profile_plans_missing_fields_and_reports_conflicts(tmp_path):
    from workai.profile_sync import plan_profile_updates
    profile = {"name": "Fixture Person", "contact": {"email": "fixture@example.invalid", "phone": "fixture-phone"}}
    result = plan_profile_updates(tmp_path, profile, [
        {"question": "Full name", "current": "Fixture Person"},
        {"question": "Email address", "current": ""},
        {"question": "Phone number", "current": "different-existing-phone"},
        {"question": "Work authorization", "current": ""},
    ])
    assert [item["question"] for item in result["updates"]] == ["Email address"]
    assert [item["question"] for item in result["conflicts"]] == ["Phone number"]
    assert len(result["unknowns"]) == len(result["unchanged"]) == 1


def test_profile_only_missing_known_fields_are_saved(configured):
    from workai.profile_sync import sync_profile
    root, job, profile, cv, adapter, state = configured
    state["html"] = '''<form onsubmit="event.preventDefault();fetch('/submitted',{method:'POST'}).then(()=>document.getElementById('saved').hidden=false)">
<label>Full name<input value="Existing Fixture Name"></label><label>Email<input type="email"></label><button>Save profile</button>
</form><p id="saved" data-testid="profile-saved" hidden>Profile saved</p>'''
    adapter["profile"] = {"url": job["job_url"], "fields": [{"label": "Full name"}, {"label": "Email", "type": "email"}],
                          "save": {"role": "button", "name": "Save profile"},
                          "confirmation": {"locator": {"test_id": "profile-saved"}, "text_pattern": "Profile saved"}}
    (root / "config/platforms.yaml").write_text(yaml.safe_dump({"platforms": {"fixture": adapter}}))
    result = sync_profile(root, "fixture", profile)
    assert result["status"] == "PLANNED"
    assert state["submissions"] == 0
    assert result["conflicts"][0]["current"] == "Existing Fixture Name"
    result = sync_profile(root, "fixture", profile, write=True)
    assert result["status"] == "UPDATED", result
    assert state["submissions"] == 1
    assert [item["question"] for item in result["updates"]] == ["Email"]
    assert Path(result["snapshot_path"]).is_file()


def _write_adapter(root, adapter):
    (root / 'config/platforms.yaml').write_text(yaml.safe_dump({'platforms': {'fixture': adapter}}))


def test_vacancy_url_allowlist_rejects_other_job_on_same_host(configured):
    root, job, profile, cv, adapter, state = configured
    adapter['allowed_job_urls'] = [job['job_url']]
    _write_adapter(root, adapter)
    job['job_url'] += '?job_id=other'
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_BY_PLATFORM'
    assert 'vacancy URLs' in result['failure_reason']
    assert state['submissions'] == 0


def test_apply_url_and_canonical_tracking_url_are_honored(configured):
    root, job, profile, cv, adapter, state = configured
    canonical = job['job_url']
    job['job_url'] = canonical + '/listing'
    job['apply_url'] = canonical + '?utm_source=fixture#application'
    adapter['allowed_job_urls'] = [job['job_url'], canonical]
    adapter['identity_checks'] = [{'locator': {'selector': 'input[name="job_id"]'}, 'attribute': 'value', 'expected': '21084'},
                                  {'locator': {'selector': 'h1'}, 'expected': 'Fixture Frontend Developer'}]
    state['html'] = HTML.replace('<body>', '<body><h1>Fixture Frontend Developer</h1><input type="hidden" name="job_id" value="21084">')
    _write_adapter(root, adapter)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'SUBMITTED', result
    assert result['final_url'].startswith(canonical + '?utm_source=fixture')
    assert state['submissions'] == 1


@pytest.mark.parametrize('change', ['wrong_id', 'wrong_text', 'duplicate_id'])
def test_identity_mismatch_blocks_before_any_candidate_fields_are_filled(configured, change):
    root, job, profile, cv, adapter, state = configured
    adapter['identity_checks'] = [{'locator': {'selector': 'input[name="job_id"]'}, 'attribute': 'value', 'expected': '21084'},
                                  {'locator': {'selector': 'h1'}, 'text': 'Fixture Frontend Developer'}]
    extra = '<h1>Fixture Frontend Developer</h1><input type="hidden" name="job_id" value="21084">'
    if change == 'wrong_id':
        extra = extra.replace('21084', '99999')
    if change == 'wrong_text':
        extra = extra.replace('Frontend', 'Backend')
    if change == 'duplicate_id':
        extra += '<input type="hidden" name="job_id" value="21084">'
    state['html'] = HTML.replace('<body>', '<body>' + extra)
    _write_adapter(root, adapter)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_BY_PLATFORM', result
    assert not result['answers']
    assert state['submissions'] == 0


@pytest.mark.parametrize("input_type", ["hidden", "text"])
def test_identity_is_rechecked_after_form_changes_before_submit(configured, input_type):
    root, job, profile, cv, adapter, state = configured
    adapter['identity_checks'] = [{'locator': {'selector': 'input[name="job_id"]'}, 'attribute': 'value', 'expected': '21084'}]
    state['html'] = HTML.replace('<body>', f'<body><input type="{input_type}" id="job-identity" name="job_id" value="21084">')
    state['html'] = state['html'].replace("document.getElementById('one').hidden=true;", "document.getElementById('job-identity').value='99999';document.getElementById('one').hidden=true;")
    _write_adapter(root, adapter)
    intents = []
    result = apply_job(root, job, profile, cv, submit=True, before_submit=intents.append)
    assert result['status'] == 'BLOCKED_BY_PLATFORM', result
    assert ('live form value' if input_type == 'text' else 'expected attribute') in result['failure_reason']
    assert state['submissions'] == 0
    assert not intents


def test_post_submit_timeout_preserves_private_visible_evidence(configured):
    import stat
    root, job, profile, cv, adapter, state = configured
    state['html'] = HTML.replace("document.getElementById('confirmation').hidden=false;", "document.body.insertAdjacentHTML('beforeend','<p>Processing receipt; keep this page.</p>');")
    state['html'] = state['html'].replace('<body>', '<body><input type="hidden" value="FIXTURE_HIDDEN_TOKEN">')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'SUBMISSION_UNCONFIRMED', result
    assert result['retryable'] is False
    assert state['submissions'] == 1
    evidence = result['browser_evidence']
    text_path, screenshot = Path(evidence['visible_text_path']), Path(evidence['screenshot_path'])
    assert 'Processing receipt' in text_path.read_text()
    assert 'FIXTURE_HIDDEN_TOKEN' not in text_path.read_text()
    assert screenshot.read_bytes().startswith(b'\x89PNG')
    assert stat.S_IMODE(text_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(screenshot.stat().st_mode) == 0o600
    assert stat.S_IMODE(text_path.parent.stat().st_mode) == 0o700
    assert not result['evidence_capture_errors']
    assert result['final_url'] == job['job_url']


def test_evidence_capture_failure_is_explicit_and_does_not_enable_retry(configured):
    root, job, profile, cv, adapter, state = configured
    (root / 'data/browser-evidence').write_text('Fixture path conflict')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'SUBMITTED', result
    assert result['evidence_capture_errors'][0]['operation'] == 'create_private_evidence_directory'
    assert result['retryable'] is False
    assert state['submissions'] == 1


def test_snapshot_upload_keeps_original_tailored_filename(configured):
    root, job, profile, cv, adapter, state = configured
    snapshot = root / 'cv.pdf'
    Path(cv['pdf_path']).rename(snapshot)
    metadata = root / 'metadata.json'
    metadata.write_text(json.dumps({'pdf_path': '/original/Fixture_Frontend_Developer_CV.pdf'}))
    cv.update(pdf_path=str(snapshot), metadata_path=str(metadata))
    state['html'] = state['html'].replace('<input type="file" required>', '<input type="file" required onchange="document.getElementById(\'confirmation\').innerText += \' Uploaded: \' + this.files[0].name">')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'SUBMITTED', result
    assert result['upload_evidence']['filename'] == 'Fixture_Frontend_Developer_CV.pdf'
    assert 'Fixture_Frontend_Developer_CV.pdf' in result['confirmation_evidence']['marker_text']
