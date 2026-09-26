import os

from workai.security import AuditLogger, atomic_write, redact


def test_secrets_redacted_from_nested_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKAI_API_KEY", "fixture-secret-sensitive")
    value = redact({"access_token": "anything", "message": "failure fixture-secret-sensitive", "nested": [{"cookie": "private"}]})
    assert value["access_token"] == "[REDACTED]"
    assert value["message"] == "failure [REDACTED]"
    assert value["nested"][0]["cookie"] == "[REDACTED]"
    logger = AuditLogger(tmp_path)
    logger.emit("test", "test", "FAILED", error=ValueError("fixture-secret-sensitive"))
    assert "fixture-secret-sensitive" not in logger.path.read_text()
    assert os.stat(logger.path).st_mode & 0o077 == 0


def test_atomic_write_replaces_without_partial_output(tmp_path):
    path = tmp_path / "private" / "record.json"
    atomic_write(path, "first")
    atomic_write(path, "second")
    assert path.read_text() == "second"
    assert os.stat(path).st_mode & 0o077 == 0


def test_authorization_headers_redact_actual_bearer_and_basic_credentials():
    for scheme, credential in [('Bearer', 'fixture-sensitive-bearer'), ('Basic', 'Zml4dHVyZS1zZWNyZXQ=')]:
        result = redact(f'Authorization: {scheme} {credential}')
        assert credential not in result


def test_cookie_header_redacts_all_cookie_values():
    result = redact('Cookie: session=fixture-session-value; csrftoken=fixture-csrf-value')
    assert 'fixture-session-value' not in result
    assert 'fixture-csrf-value' not in result
