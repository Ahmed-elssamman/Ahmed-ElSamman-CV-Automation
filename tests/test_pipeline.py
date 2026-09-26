"""End-to-end local fixture: real PDF ingestion/LaTeX/Chromium/SQLite, no employer."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from workai.answers import add_answer
from workai.cv import CVError, build_master, find_compiler
from workai.orchestrator import Orchestrator
from workai.profile import ingest_pdfs
from workai.reporting import report
from workai.store import Store
from test_browser import site, configured
from test_profile import SAMPLE_TEXT


def source_pdf(path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=850, height=1000)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Courier')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    commands = []
    for index, line in enumerate(SAMPLE_TEXT.replace('•', '-').splitlines()):
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        commands.append(f'BT /F1 10 Tf 30 {950-index*18} Td ({escaped}) Tj ET')
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(commands).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as handle:
        writer.write(handle)


@pytest.fixture
def pipeline(configured, monkeypatch):
    root, job, _, _, adapter, state = configured
    try:
        compiler = find_compiler(Path(__file__).resolve().parents[1])
    except CVError:
        pytest.skip('Actual LaTeX compiler is required for the complete fixture pipeline')
    monkeypatch.setenv('WORKAI_TECTONIC', compiler)
    source = root / 'source-fixture.pdf'
    source_pdf(source)
    profile = ingest_pdfs(root, [source])
    build_master(root)
    job.update(position='Angular Frontend Engineer', city='Cairo',
               description='Requirements\nAngular and TypeScript\nResponsibilities\nBuild Angular frontend interfaces.',
               required_skills=['Angular', 'TypeScript'], company_url=None, seniority='mid')
    # Test-only explicit consent and sponsorship facts, isolated from production.
    add_answer(root, 'Do you require sponsorship', False, source='fixture-only', scope={'country': 'Egypt'})
    add_answer(root, 'I agree to processing', True, source='fixture-only', scope={'company': 'Fixture'})
    return root, job, profile, adapter, state


def test_complete_fixture_pipeline_and_duplicate_prevention(pipeline):
    root, job, profile, adapter, state = pipeline
    runner = Orchestrator(root)
    try:
        stored, _ = runner.store.upsert_job(job)
        prepared = runner.run(discover=False, submit=False)
        assert prepared['counts'] == {'READY_TO_APPLY': 1}, prepared
        assert state['submissions'] == 0
        result = runner.run(discover=False, submit=True)
        assert result['counts'] == {'SUBMITTED': 1}, result
        app = runner.store.application_for_job(stored['id'])
        assert app['confirmation_id'] == 'FIXTURE-123'
        snapshot = Path(app['snapshot_path'])
        for filename in ('application.json', 'job-description.txt', 'company-research.md', 'ats-analysis.json', 'cv.tex', 'cv.pdf', 'application-questions.json', 'submission-intent.json', 'application-result.json', 'sha256-manifest.json', 'logs/application-events.json'):
            assert (snapshot / filename).is_file(), filename
        assert hashlib.sha256((snapshot / 'cv.pdf').read_bytes()).hexdigest() == app['upload_evidence']['sha256']
        receipt = app['browser_evidence']
        assert receipt['archive_status'] == 'ARCHIVED'
        assert not app['browser_evidence_archive_errors']
        assert (snapshot / 'browser-evidence/evidence.json').is_file()
        for path_key, hash_key in [('visible_text_path', 'visible_text_sha256'), ('screenshot_path', 'screenshot_sha256')]:
            archived = Path(receipt[path_key])
            original = Path(receipt['original_capture'][path_key])
            assert archived.is_relative_to(snapshot / 'browser-evidence')
            assert original.is_relative_to(root / 'data/browser-evidence')
            assert archived.read_bytes() == original.read_bytes()
            assert hashlib.sha256(archived.read_bytes()).hexdigest() == receipt[hash_key]
        manifest = json.loads((snapshot / 'sha256-manifest.json').read_text())
        assert all(hashlib.sha256((snapshot / path).read_bytes()).hexdigest() == digest for path, digest in manifest.items())
        events = [row[0] for row in runner.store.db.execute('SELECT event_type FROM application_events WHERE application_id=? ORDER BY seq', (app['application_id'],))]
        assert events.index('SUBMISSION_INTENT') < events.index('SUBMITTED')
        assert events.count('SUBMITTED') == 1
        assert events.index('SUBMITTED') < events.index('APPLICATION_ARCHIVE_UPDATED')
        from workai.store import StateError
        for protected in ['confirmation_id', 'confirmation_url', 'submitted_at', 'application_status', 'status']:
            with pytest.raises(StateError, match='protected'):
                runner.store.update_application_archive(app['application_id'], {protected: 'altered'})
        assert runner.store.application(app['application_id'])['confirmation_id'] == 'FIXTURE-123'
        runner.run(discover=False, submit=True)
        assert state['submissions'] == 1
        dashboard = report(root)
        assert dashboard['applications_submitted'] == 1
        assert dashboard['duplicate_applications_prevented'] == 1
        assert 'FIXTURE-123' in (root / 'data/applications/applications.csv').read_text()
    finally:
        runner.close()


def test_unknown_application_does_not_block_unrelated_job(pipeline):
    root, job, profile, adapter, state = pipeline
    unknown_adapter = json.loads(json.dumps(adapter))
    unknown_adapter['steps'][0]['fields'].append({'label': 'Military service status', 'question': 'Military service status'})
    (root / 'config/platforms.yaml').write_text(yaml.safe_dump({'platforms': {'fixture': adapter, 'fixture-unknown': unknown_adapter}}))
    blocked = {**job, 'company': 'Unknown Fixture', 'platform': 'fixture-unknown', 'job_url': job['job_url'] + '?id=unknown'}
    runner = Orchestrator(root)
    try:
        runner.store.upsert_job(blocked)
        runner.store.upsert_job(job)
        results = runner.run(discover=False, submit=True)
        assert results['counts'] == {'BLOCKED_UNKNOWN_FIELDS': 1, 'SUBMITTED': 1}, results
        assert state['submissions'] == 1
        events = runner.store.db.execute("SELECT COUNT(*) FROM application_events WHERE event_type='UNKNOWN_APPLICATION_FIELD'").fetchone()[0]
        assert events >= 1
    finally:
        runner.close()


@pytest.mark.parametrize('artifact', ['tex_path', 'pdf_path'])
def test_preflight_rejects_changed_immutable_cv_artifacts(pipeline, artifact):
    from workai.orchestrator import validate_cv_artifacts
    root, job, profile, adapter, state = pipeline
    cv = build_master(root)
    path = Path(cv[artifact])
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b'\n% fixture artifact changed after validation\n')
    with pytest.raises(ValueError, match='(?i)integrity|hash|checksum|changed|mismatch'):
        validate_cv_artifacts(cv, profile)


def test_retry_after_profile_change_rebuilds_cv_before_submission(pipeline):
    from workai.profile import apply_user_clarification
    from pypdf import PdfReader
    root, job, profile, adapter, state = pipeline
    runner = Orchestrator(root)
    try:
        stored, _ = runner.store.upsert_job(job)
        prepared = runner.run(discover=False, submit=False)
        assert prepared['counts'] == {'READY_TO_APPLY': 1}, prepared
        app = runner.store.application_for_job(stored['id'])
        runner.store.transition(app['application_id'], 'FAILED', {'retryable': True, 'failure_reason': 'Fixture preparation interruption'})
        apply_user_clarification(root, {'contact.phone': '+20 100 222 3344'}, source_id='user:fixture-new-phone', source_text='My phone is +20 100 222 3344.')
        result = runner.run(discover=False, submit=True)
        assert result['counts'] == {'SUBMITTED': 1}, result
        submitted = runner.store.application_for_job(stored['id'])
        snapshot_pdf = Path(submitted['snapshot_path']) / 'cv.pdf'
        text = ''.join((page.extract_text() or '') for page in PdfReader(snapshot_pdf).pages)
        assert '+201002223344' in ''.join(text.split())
        assert state['submissions'] == 1
    finally:
        runner.close()


def test_revised_expired_job_is_not_submitted_from_stale_preparation(pipeline):
    root, job, profile, adapter, state = pipeline
    runner = Orchestrator(root)
    try:
        stored, _ = runner.store.upsert_job(job)
        prepared = runner.run(discover=False, submit=False)
        assert prepared['counts'] == {'READY_TO_APPLY': 1}, prepared
        runner.store.upsert_job({**stored, 'application_deadline': '2000-01-01'})
        result = runner.run(discover=False, submit=True)
        assert state['submissions'] == 0, result
        current = runner.store.application_for_job(stored['id'])
        assert current['application_status'] in {'EXCLUDED', 'FAILED'}
        assert any('deadline' in reason.casefold() for reason in current.get('eligibility', {}).get('reasons', [])), current
    finally:
        runner.close()


def test_corrupt_receipt_is_rejected_without_losing_confirmed_submission(pipeline, monkeypatch):
    import workai.browser as browser
    root, job, profile, adapter, state = pipeline
    real_apply = browser.apply_job
    def corrupt_after_confirmation(*args, **kwargs):
        outcome = real_apply(*args, **kwargs)
        assert outcome['status'] == 'SUBMITTED'
        # Simulate capture damage after the browser recorded its original hash.
        Path(outcome['browser_evidence']['screenshot_path']).write_bytes(b'fixture damaged receipt')
        return outcome
    monkeypatch.setattr(browser, 'apply_job', corrupt_after_confirmation)
    runner = Orchestrator(root)
    try:
        stored, _ = runner.store.upsert_job(job)
        result = runner.run(discover=False, submit=True)
        assert result['counts'] == {'SUBMITTED': 1}, result
        app = runner.store.application_for_job(stored['id'])
        assert app['application_status'] == 'SUBMITTED'
        assert app['confirmation_id'] == 'FIXTURE-123'
        assert app['browser_evidence']['archive_status'] == 'PARTIAL'
        assert any(error['error'] == 'CAPTURE_INTEGRITY_MISMATCH' for error in app['browser_evidence_archive_errors'])
        snapshot = Path(app['snapshot_path'])
        assert (snapshot / 'browser-evidence/visible-page.txt').is_file()
        assert not (snapshot / 'browser-evidence/page.png').exists()
        assert 'screenshot_path' not in app['browser_evidence']
        assert Path(app['browser_evidence']['original_capture']['screenshot_path']).exists()
        result_file = json.loads((snapshot / 'application-result.json').read_text())
        assert result_file['status'] == 'SUBMITTED'
        assert result_file['browser_evidence_archive_errors']
        events = [row[0] for row in runner.store.db.execute('SELECT event_type FROM application_events WHERE application_id=? ORDER BY seq', (app['application_id'],))]
        assert events.index('SUBMITTED') < events.index('BROWSER_EVIDENCE_ARCHIVE_ERROR')
        assert events.count('SUBMITTED') == 1
        assert 'APPLICATION_ARCHIVE_UPDATED' in events
        runner.run(discover=False, submit=True)
        assert state['submissions'] == 1
    finally:
        runner.close()


def test_snapshot_sealing_failure_does_not_demote_or_repeat_confirmed_submission(pipeline, monkeypatch):
    root, job, profile, adapter, state = pipeline
    runner = Orchestrator(root)
    def unavailable(*_):
        raise OSError('Fixture archive disk unavailable')
    monkeypatch.setattr(runner, 'finalize_snapshot', unavailable)
    try:
        stored, _ = runner.store.upsert_job(job)
        result = runner.run(discover=False, submit=True)
        assert result['counts'] == {'SUBMITTED': 1}, result
        app = runner.store.application_for_job(stored['id'])
        assert app['application_status'] == 'SUBMITTED'
        assert app['snapshot_archival_status'] == 'INCOMPLETE'
        assert runner.store.db.execute("SELECT COUNT(*) FROM application_events WHERE application_id=? AND event_type='SUBMITTED'", (app['application_id'],)).fetchone()[0] == 1
        assert runner.store.db.execute("SELECT COUNT(*) FROM application_events WHERE application_id=? AND event_type='APPLICATION_ARCHIVE_UPDATED'", (app['application_id'],)).fetchone()[0] >= 1
        assert app['retryable'] is False
        assert app['confirmation_id'] == 'FIXTURE-123'
        assert any(error.get('operation') == 'post_submission_archival' for error in app['browser_evidence_archive_errors'])
        assert Path(app['browser_evidence']['screenshot_path']).is_file()
        runner.run(discover=False, submit=True)
        assert state['submissions'] == 1
    finally:
        runner.close()


def test_sealed_snapshot_cannot_be_rewritten_or_extended(tmp_path):
    root = tmp_path
    runner = Orchestrator(root)
    folder = root / 'applications/fixture/attempt'
    folder.mkdir(parents=True)
    manifest = folder / 'sha256-manifest.json'
    manifest.write_text('{}\n')
    before = manifest.read_bytes()
    try:
        with pytest.raises(ValueError, match='sealed'):
            runner.archive_browser_evidence(folder, {'browser_evidence': {}})
        with pytest.raises(ValueError, match='sealed'):
            runner.finalize_snapshot(folder, {'status': 'SUBMITTED'})
        assert manifest.read_bytes() == before
        assert not (folder / 'application-result.json').exists()
        assert not (folder / 'browser-evidence').exists()
    finally:
        runner.close()


def test_uncertain_submission_keeps_receipt_inside_reconciliation_snapshot(pipeline):
    root, job, profile, adapter, state = pipeline
    state['html'] = state['html'].replace('Application received. Reference:', 'Processing; receipt pending. Reference:')
    runner = Orchestrator(root)
    try:
        stored, _ = runner.store.upsert_job(job)
        result = runner.run(discover=False, submit=True)
        assert result['counts'] == {'RECONCILIATION_REQUIRED': 1}, result
        app = runner.store.application_for_job(stored['id'])
        snapshot = Path(app['snapshot_path'])
        receipt = app['browser_evidence']
        assert receipt['archive_status'] == 'ARCHIVED'
        assert Path(receipt['visible_text_path']).is_relative_to(snapshot)
        assert 'Processing; receipt pending' in Path(receipt['visible_text_path']).read_text()
        assert Path(receipt['screenshot_path']).is_relative_to(snapshot)
        assert (snapshot / 'sha256-manifest.json').exists()
        runner.run(discover=False, submit=True)
        assert state['submissions'] == 1
    finally:
        runner.close()


def test_question_persistence_failure_after_confirmation_preserves_submitted_state(pipeline, monkeypatch):
    root, job, profile, adapter, state = pipeline
    runner = Orchestrator(root)
    real_record = runner.store.record_questions
    calls = []
    def fail_after_browser_confirmation(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise OSError('Fixture question persistence unavailable after confirmation')
        return real_record(*args, **kwargs)
    monkeypatch.setattr(runner.store, 'record_questions', fail_after_browser_confirmation)
    try:
        stored, _ = runner.store.upsert_job(job)
        result = runner.run(discover=False, submit=True)
        assert result['counts'] == {'SUBMITTED': 1}, result
        app = runner.store.application_for_job(stored['id'])
        assert app['application_status'] == 'SUBMITTED'
        assert app['confirmation_id'] == 'FIXTURE-123'
        assert app['snapshot_archival_status'] == 'INCOMPLETE'
        assert runner.store.db.execute("SELECT COUNT(*) FROM application_events WHERE application_id=? AND event_type='SUBMITTED'", (app['application_id'],)).fetchone()[0] == 1
        assert runner.store.db.execute("SELECT COUNT(*) FROM application_events WHERE application_id=? AND event_type='APPLICATION_ARCHIVE_UPDATED'", (app['application_id'],)).fetchone()[0] >= 1
        assert app['retryable'] is False
        assert Path(app['browser_evidence']['screenshot_path']).is_file()
        assert app['browser_evidence_archive_errors']
        runner.run(discover=False, submit=True)
        assert state['submissions'] == 1
    finally:
        runner.close()
