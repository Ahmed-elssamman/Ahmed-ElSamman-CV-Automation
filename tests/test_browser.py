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


# Mirrors the audited Workable structure: hidden native inputs inside accessible
# radio wrappers and a readonly combobox backed by a separate validation proxy.
# Listbox IDs are generated at runtime and its options live outside the container.
CUSTOM_CONTROLS = '''
<style>[data-ui="CA_34103"] [role="radio"] {width:20px;height:20px;border:1px solid;}</style>
<p id="english-label">English Level</p>
<fieldset role="radiogroup" data-ui="CA_34103" aria-required="true" aria-labelledby="english-label">
<label role="presentation"><div role="radio" tabindex="0" aria-required="true" aria-checked="false"
 aria-labelledby="english-label fair-label" onclick="chooseRadio(this)">
<input style="display:none" type="radio" name="CA_34103" value="328693" required aria-hidden="true" tabindex="-1"></div><span id="fair-label">Fair</span></label>
<label role="presentation"><div role="radio" tabindex="-1" aria-required="true" aria-checked="false"
 aria-labelledby="english-label good-label" onclick="chooseRadio(this)">
<input style="display:none" type="radio" name="CA_34103" value="328694" required aria-hidden="true" tabindex="-1"></div><span id="good-label">Good</span></label>
</fieldset>
<p id="gender-label">Gender</p>
<div data-ui="CA_34667" data-input-type="select">
<input role="combobox" aria-labelledby="gender-label" readonly aria-required="true" aria-expanded="false"
 onclick="this.setAttribute('aria-expanded','true');document.getElementById(this.getAttribute('aria-controls')).hidden=false">
<input name="CA_34667" required aria-hidden="true" tabindex="-1" style="display:none">
</div>
<div role="listbox" hidden><div role="option" onclick="chooseGender(this)">Male</div></div>
<script>
function chooseRadio(wrapper) {
 const group = wrapper.closest('[role="radiogroup"]');
 for (const peer of group.querySelectorAll('[role="radio"]')) {
  peer.querySelector('input').checked = peer === wrapper;
  peer.setAttribute('aria-checked', String(peer === wrapper));
 }
}
function chooseGender(option) {
 const group = document.querySelector('[data-ui="CA_34667"]');
 const box = group.querySelector('[role="combobox"]');
 box.value = 'Male';
 group.querySelector('[name="CA_34667"]').value = 'fixture-male';
 box.setAttribute('aria-expanded', 'false');
 option.closest('[role="listbox"]').hidden = true;
}
for (const el of document.querySelectorAll('[data-ui="CA_34103"] input, [data-ui="CA_34103"] [role="radio"], [role="listbox"]')) {
 el.id = 'hydrated-' + crypto.randomUUID();
}
document.querySelector('[role="combobox"]').setAttribute('aria-controls', document.querySelector('[role="listbox"]').id);
</script>
'''


@pytest.fixture
def custom_configured(configured):
    root, job, profile, cv, adapter, state = configured
    state['html'] = HTML.replace('<button>Submit application', CUSTOM_CONTROLS + '<button>Submit application')
    add_answer(root, 'English Level', 'Good', source='fixture-only', scope={'company': 'Fixture'})
    add_answer(root, 'Gender', 'Male', source='fixture-only', scope={'company': 'Fixture'})
    adapter['steps'][-1]['fields'] += [
        {'question': 'English Level', 'type': 'custom_radio',
         'group': {'selector': 'fieldset[role="radiogroup"][data-ui="CA_34103"]'},
         'native_name': 'CA_34103',
         'options': {'Good': {'locator': {'role': 'radio', 'name': 'English Level Good'}, 'value': '328694'}}},
        {'question': 'Gender', 'type': 'custom_select', 'group': {'selector': '[data-ui="CA_34667"]'},
         'control': {'role': 'combobox', 'name': 'Gender'}, 'proxy': {'selector': 'input[name="CA_34667"]'},
         'native_name': 'CA_34667', 'options': {'Male': {'label': 'Male', 'value': 'fixture-male'}}},
    ]
    _write_adapter(root, adapter)
    return configured


def test_audited_custom_radios_and_portal_dropdown_submit_with_native_controls(custom_configured):
    root, job, profile, cv, adapter, state = custom_configured
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'SUBMITTED', result
    assert state['submissions'] == 1
    assert [answer['answer'] for answer in result['answers'][-2:]] == ['Good', 'Male']


@pytest.mark.parametrize('fragment', [
    '<div role="radio" aria-required="true" aria-label="Unknown wrapper" tabindex="0">Unknown</div>',
    '<div data-input-type="select"><input required name="unknown-proxy" aria-hidden="true" tabindex="-1" style="display:none"></div>',
])
def test_unrelated_required_wrappers_and_hidden_proxies_remain_unknown(custom_configured, fragment):
    root, job, profile, cv, adapter, state = custom_configured
    # Give the second hidden-proxy owner a visible box, as in the audited widget.
    fragment = fragment.replace('data-input-type="select"', 'data-input-type="select" style="height:20px"')
    state['html'] = state['html'].replace('<button>Submit application', fragment + '<button>Submit application')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_UNKNOWN_FIELDS', result
    assert state['submissions'] == 0


@pytest.mark.parametrize('mutation', [
    "wrapper.querySelector('input').checked = false;",
    "wrapper.setAttribute('aria-checked', 'false');",
    "wrapper.querySelector('input').value = 'changed';",
    "wrapper.querySelector('input').name = 'different';",
    "wrapper.querySelector('input').setAttribute('form', 'unrelated');",
])
def test_custom_radio_native_and_wrapper_must_agree(custom_configured, mutation):
    root, job, profile, cv, adapter, state = custom_configured
    state['html'] = state['html'].replace("\n}\nfunction chooseGender", '\n' + mutation + '\n}\nfunction chooseGender')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'FAILED', result
    assert 'Custom radio' in result['failure_reason']
    assert state['submissions'] == 0


@pytest.mark.parametrize('mutation', [
    "group.querySelector('[name=\"CA_34667\"]').value = 'changed';",
    "group.querySelector('[name=\"CA_34667\"]').remove();",
    "group.querySelector('[name=\"CA_34667\"]').name = 'different';",
    "group.querySelector('[name=\"CA_34667\"]').setAttribute('form', 'unrelated');",
    "box.value = 'Other';",
    "box.setAttribute('aria-expanded', 'true');",
])
def test_custom_select_rejects_missing_wrong_proxy_and_display(custom_configured, mutation):
    root, job, profile, cv, adapter, state = custom_configured
    state['html'] = state['html'].replace("option.closest('[role=\"listbox\"]').hidden = true;", "option.closest('[role=\"listbox\"]').hidden = true;" + mutation)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'FAILED', result
    assert 'Custom select' in result['failure_reason']
    assert state['submissions'] == 0


@pytest.mark.parametrize('field_type', ['custom_radio', 'custom_select'])
def test_custom_control_without_exact_approved_option_blocks(custom_configured, field_type):
    root, job, profile, cv, adapter, state = custom_configured
    for field in adapter['steps'][-1]['fields']:
        if field.get('type') == field_type:
            field['options'] = {}
    _write_adapter(root, adapter)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_UNKNOWN_FIELDS', result
    assert state['submissions'] == 0


@pytest.mark.parametrize('selector, mutation', [
    ('[name="CA_34103"][value="328694"]', "el.checked=false;"),
    ('[name="CA_34667"]', "el.value='changed';"),
])
def test_custom_values_are_rechecked_after_later_fields(custom_configured, selector, mutation):
    root, job, profile, cv, adapter, state = custom_configured
    state['html'] = state['html'].replace('<button>Submit application', '<label>Fixture trigger<input id="trigger"></label><button>Submit application')
    state['html'] = state['html'].replace('</body>', '<script>document.getElementById("trigger").oninput=()=>{const el=document.querySelector(' + json.dumps(selector) + ');' + mutation + '};</script></body>')
    adapter['steps'][-1]['fields'].append({'question': 'Full name', 'selector': '#trigger'})
    _write_adapter(root, adapter)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'FAILED', result
    assert state['submissions'] == 0


def test_other_form_same_radio_name_is_never_marked(custom_configured):
    root, job, profile, cv, adapter, state = custom_configured
    foreign = '<form id="foreign" hidden><label>Other form required<input type="radio" name="CA_34103" value="328694" required></label></form>'
    state['html'] = state['html'].replace('</body>', foreign + '</body>')
    state['html'] = state['html'].replace("document.getElementById('two').hidden=false", "document.getElementById('two').hidden=false;document.getElementById('foreign').hidden=false")
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_UNKNOWN_FIELDS', result
    assert any('Other form required' in item['question'] for item in result['unknown_events'])
    assert any(item['question'] == 'English Level' for item in result['answers'])
    assert state['submissions'] == 0


def test_native_formless_radio_does_not_mark_same_name_inside_other_form(configured):
    root, job, profile, cv, adapter, state = configured
    state['html'] = '''<label>Standalone no<input type="radio" name="sponsor" required></label>
<form><label>Other form required<input type="radio" name="sponsor" required></label><button>Submit application</button></form>
<p data-testid="application-confirmation" hidden>Application received</p>'''
    adapter['steps'] = [{'fields': [{'question': 'Do you require sponsorship', 'type': 'radio', 'options': {'False': {'label': 'Standalone no'}}}],
                         'submit': {'role': 'button', 'name': 'Submit application'}}]
    _write_adapter(root, adapter)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_UNKNOWN_FIELDS', result
    assert any('Other form required' in item['question'] for item in result['unknown_events'])
    assert state['submissions'] == 0


def test_nested_unrelated_radio_group_with_same_name_remains_unmapped(custom_configured):
    root, job, profile, cv, adapter, state = custom_configured
    nested = '''<fieldset role="radiogroup"><div role="radio" aria-required="true" aria-checked="false" aria-label="Unrelated nested question">
<input type="radio" name="CA_34103" value="unrelated" required aria-hidden="true" tabindex="-1" style="display:none"></div></fieldset>'''
    state['html'] = state['html'].replace('</fieldset>', nested + '</fieldset>')
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'BLOCKED_UNKNOWN_FIELDS', result
    assert any(item['question'] == 'Unrelated nested question' for item in result['unknown_events'])
    assert state['submissions'] == 0


@pytest.mark.parametrize('kind', ['radio', 'select'])
def test_custom_hydrated_replacement_uses_stable_group_and_option_semantics(custom_configured, kind):
    root, job, profile, cv, adapter, state = custom_configured
    replace_group = "const replacement=group.cloneNode(true);for(const el of replacement.querySelectorAll('input[id], [role=\"radio\"][id]'))el.id='new-'+crypto.randomUUID();group.replaceWith(replacement);"
    if kind == 'radio':
        state['html'] = state['html'].replace('\n}\nfunction chooseGender', '\n' + replace_group + '\n}\nfunction chooseGender')
    else:
        state['html'] = state['html'].replace("option.closest('[role=\"listbox\"]').hidden = true;", "option.closest('[role=\"listbox\"]').hidden = true;" + replace_group)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == 'SUBMITTED', result
    assert state['submissions'] == 1


@pytest.mark.parametrize('proxy_change', ['missing', 'wrong_selector', 'invalid', 'duplicate'])
def test_custom_select_proxy_contract_is_enforced(custom_configured, proxy_change):
    root, job, profile, cv, adapter, state = custom_configured
    field = adapter['steps'][-1]['fields'][-1]
    if proxy_change == 'missing':
        field.pop('proxy')
    elif proxy_change == 'wrong_selector':
        field['proxy'] = {'role': 'combobox', 'name': 'Gender'}
    elif proxy_change == 'invalid':
        state['html'] = state['html'].replace('name="CA_34667" required', 'name="CA_34667" pattern="[0-9]+" required')
    else:
        state['html'] = state['html'].replace('<div role="listbox" hidden>', '<input name="CA_34667" form="two" required aria-hidden="true" tabindex="-1"><div role="listbox" hidden>')
        # An unrelated same-name proxy outside the mapped container stays unmapped.
    _write_adapter(root, adapter)
    result = apply_job(root, job, profile, cv, submit=True)
    assert result['status'] == ('BLOCKED_UNKNOWN_FIELDS' if proxy_change == 'duplicate' else 'FAILED'), result
    assert state['submissions'] == 0


def test_custom_values_rechecked_after_durable_intent(custom_configured, monkeypatch):
    import workai.browser as browser_module
    root, job, profile, cv, adapter, state = custom_configured
    original = browser_module._identity_mismatch
    durable = []
    def change_after_intent(page, adapter):
        if durable:
            page.locator('[name="CA_34667"]').evaluate("el => el.value = 'changed'")
        return original(page, adapter)
    monkeypatch.setattr(browser_module, '_identity_mismatch', change_after_intent)
    result = apply_job(root, job, profile, cv, submit=True, before_submit=durable.append)
    assert durable
    assert result['status'] == 'FAILED', result
    assert 'Custom select' in result['failure_reason']
    assert result['retryable'] is False
    assert state['submissions'] == 0
