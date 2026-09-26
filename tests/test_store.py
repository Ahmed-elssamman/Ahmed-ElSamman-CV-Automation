import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from workai.orchestrator import runner_lock
from workai.store import Store, StateError, canonical_url


def job(**updates):
    return {"company": "Example Inc", "position": "Frontend Engineer", "country": "Egypt", "city": "Cairo", "platform": "test", "job_url": "https://jobs.example.test/123", "description": "React TypeScript", **updates}


def ready(store, application_id):
    for state in ("ANALYZED", "QUALIFIED", "RESEARCHED", "TAILORED", "CV_GENERATED", "CV_VALIDATED", "READY_TO_APPLY"):
        store.transition(application_id, state)


def test_url_canonicalization_preserves_job_identifier():
    assert canonical_url("https://EXAMPLE.test/jobs/?id=123&utm_source=search#top") == "https://example.test/jobs?id=123"
    assert canonical_url("https://example.test/jobs?id=123") != canonical_url("https://example.test/jobs?id=456")


def test_idempotent_job_ingestion_preserves_revision(tmp_path):
    with Store(tmp_path) as store:
        first, created = store.upsert_job(job())
        second, again = store.upsert_job(job(job_url="https://jobs.example.test/123?utm_source=other", description="React Angular"))
        assert created and not again
        assert first["id"] == second["id"]
        assert len(store.jobs()) == 1
        revision = store.db.execute("SELECT data FROM application_events WHERE event_type='JOB_REVISION'").fetchone()
        assert json.loads(revision[0])["previous"]["description"] == "React TypeScript"


def test_duplicate_same_job_and_cross_platform(tmp_path):
    with Store(tmp_path) as store:
        a, _ = store.upsert_job(job())
        b, _ = store.upsert_job(job(platform="other", job_url="https://other.example.test/job/1"))
        app, created = store.create_application(a)
        second, created_twice = store.create_application(a)
        duplicate, cross_created = store.create_application(b)
        assert created and not created_twice and not cross_created
        assert app["application_id"] == second["application_id"]
        assert duplicate is None
        assert len(store.applications()) == 1


def test_concurrent_application_claim(tmp_path):
    with Store(tmp_path) as store:
        role, _ = store.upsert_job(job())
    def claim(_):
        with Store(tmp_path) as store:
            return store.create_application(role)
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(claim, range(4)))
    assert sum(created for _, created in claims) == 1
    assert len({app["application_id"] for app, _ in claims}) == 1


def test_transition_recovery_and_confirmation_gate(tmp_path):
    with Store(tmp_path) as store:
        role, _ = store.upsert_job(job())
        app, _ = store.create_application(role)
        aid = app["application_id"]
        with pytest.raises(StateError):
            store.transition(aid, "SUBMITTED")
        ready(store, aid)
        store.transition(aid, "APPLICATION_STARTED")
        store.transition(aid, "SUBMISSION_INTENT", {"intent": "recorded"})
    with Store(tmp_path) as store:
        assert store.recover()[0]["application_status"] == "RECONCILIATION_REQUIRED"
        assert store.recover() == []
        with pytest.raises(StateError, match="evidence"):
            store.transition(aid, "READY_TO_APPLY")
        with pytest.raises(StateError, match="confirmation"):
            store.transition(aid, "SUBMITTED", evidence="looked at platform")
        result = store.transition(aid, "SUBMITTED", {"confirmation_id": "actual-confirmation", "submitted_at": "2026-09-26T00:00:00Z"}, evidence="Verified employer receipt")
        assert result["application_status"] == "SUBMITTED"
        assert store.recover() == []


def test_csv_preserves_history_and_prevents_formula_injection(tmp_path):
    with Store(tmp_path) as store:
        role, _ = store.upsert_job(job(company="=HYPERLINK(unsafe)"))
        app, _ = store.create_application(role)
        store.transition(app["application_id"], "ANALYZED", {"notes": 'commas, quotes " and\nnewlines'})
        path = store.export_csv()
        with path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 1
        assert rows[0]["company"].startswith("'=")
        assert rows[0]["notes"] == 'commas, quotes " and\nnewlines'
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.db.execute("DELETE FROM application_events")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store.db.execute("DELETE FROM applications")


def test_runner_lock_prevents_concurrent_submitter(tmp_path):
    with runner_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Another WORKAI"):
            with runner_lock(tmp_path):
                pass


def test_question_records_are_idempotent(tmp_path):
    with Store(tmp_path) as store:
        role, _ = store.upsert_job(job())
        app, _ = store.create_application(role)
        question = {"question": "Email address", "answer": "fixture@example.test", "source": "test fixture", "status": "KNOWN"}
        store.record_questions(app["application_id"], [question, question])
        assert store.db.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 1


def test_historical_role_identity_survives_discovery_revision(tmp_path):
    with Store(tmp_path) as store:
        original, _ = store.upsert_job(job(external_id="original"))
        app, _ = store.create_application(original)
        ready(store, app["application_id"])
        store.transition(app["application_id"], "APPLICATION_STARTED")
        store.transition(app["application_id"], "SUBMITTED", {"confirmation_id": "fixture-123", "submitted_at": "2026-09-26T00:00:00Z"})
        store.upsert_job({**original, "position": "Different role after employer edit"})
        repost, _ = store.upsert_job(job(platform="another", job_url="https://other.example.test/posting"))
        duplicate, created = store.create_application(repost)
        assert duplicate is None and not created


@pytest.mark.parametrize('location', [{'country': None}, {'city': None}, {'country': None, 'city': None}])
def test_cross_platform_missing_location_cannot_bypass_duplicate_guard(tmp_path, location):
    with Store(tmp_path) as store:
        original, _ = store.upsert_job(job())
        store.create_application(original)
        rediscovered, _ = store.upsert_job(job(platform='other', job_url='https://other.example.test/123', **location))
        duplicate, created = store.create_application(rediscovered)
        assert not created
        assert duplicate is None


def test_cross_platform_rejected_application_needs_explicit_reapplication_reason(tmp_path):
    with Store(tmp_path) as store:
        original, _ = store.upsert_job(job())
        app, _ = store.create_application(original)
        ready(store, app['application_id'])
        store.transition(app['application_id'], 'APPLICATION_STARTED')
        store.transition(app['application_id'], 'SUBMITTED', {'confirmation_id': 'FIXTURE', 'submitted_at': '2026-09-26T00:00:00Z'})
        store.transition(app['application_id'], 'REJECTED')
        rediscovered, _ = store.upsert_job(job(platform='other', job_url='https://other.example.test/123'))
        duplicate, created = store.create_application(rediscovered)
        assert not created
        assert duplicate is None


def test_job_revision_updates_indexed_identity_columns_with_revision_event(tmp_path):
    with Store(tmp_path) as store:
        original, _ = store.upsert_job(job(external_id='requisition-123'))
        revised, created = store.upsert_job(job(external_id='requisition-123', job_url='https://jobs.example.test/new/123', city='Alexandria'))
        assert not created
        assert revised['id'] == original['id']
        row = store.db.execute('SELECT canonical_url,fingerprint FROM jobs WHERE id=?', (original['id'],)).fetchone()
        assert row['canonical_url'] == canonical_url(revised['job_url'])
        from workai.store import stable_id, normalized_name
        expected = stable_id('role', normalized_name(revised['company']), normalized_name(revised['position']), revised['country'], revised['city'])
        assert row['fingerprint'] == expected
        assert store.db.execute("SELECT COUNT(*) FROM application_events WHERE event_type='JOB_REVISION'").fetchone()[0] == 1


def test_distinct_known_locations_do_not_collapse_to_one_application(tmp_path):
    with Store(tmp_path) as store:
        original, _ = store.upsert_job(job())
        store.create_application(original)
        different, _ = store.upsert_job(job(platform='other', job_url='https://other.example.test/456', country='Saudi Arabia', city='Riyadh'))
        app, created = store.create_application(different)
        assert created
        assert app is not None


def test_recorded_answer_retry_ignores_capture_timestamp(tmp_path):
    with Store(tmp_path) as store:
        role, _ = store.upsert_job(job())
        app, _ = store.create_application(role)
        base = {'question': 'Email address', 'answer': 'fixture@example.test', 'source': 'fixture', 'status': 'KNOWN'}
        store.record_questions(app['application_id'], [{**base, 'date': '2026-09-26T00:00:00Z'}])
        store.record_questions(app['application_id'], [{**base, 'date': '2026-09-26T00:00:10Z'}])
        assert store.db.execute('SELECT COUNT(*) FROM answers').fetchone()[0] == 1
