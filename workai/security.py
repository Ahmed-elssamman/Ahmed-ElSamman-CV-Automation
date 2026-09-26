"""Local secret hygiene, atomic files and redacted structured logging."""
from __future__ import annotations

import json
import os
import re
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, content: str | bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode() if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


SECRET_PATTERN = re.compile(
    r"(?i)(authorization|password|access[_-]?token|api[_-]?key|cookie|secret)"
    r"([\s\"':=]+)([^\s,;\"}]+)"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if re.search(r"password|token|cookie|secret|api.?key|authorization", key, re.I)
            else redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        # Header values can contain spaces and multiple cookie pairs. Redact the
        # entire header before generic key-value patterns can truncate it.
        text = re.sub(r"(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:[^\r\n]*", "[REDACTED HEADER]", value)
        text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", "[REDACTED AUTH]", text)
        text = SECRET_PATTERN.sub(r"\1\2[REDACTED]", text)
        text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,})\b", "[REDACTED]", text)
        for key, secret in os.environ.items():
            if len(secret) >= 8 and re.search(r"TOKEN|PASSWORD|SECRET|API_KEY", key):
                text = text.replace(secret, "[REDACTED]")
        return text
    return value


class AuditLogger:
    def __init__(self, root: Path):
        self.path = root / "logs" / "workai.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, agent: str, action: str, status: str, *, job: dict | None = None,
             duration: float | None = None, error: BaseException | None = None,
             retry_count: int = 0, resolution: str | None = None) -> None:
        job = job or {}
        event = redact({
            "timestamp": utcnow(), "agent": agent, "action": action, "operation": action,
            "status": status, "job_id": job.get("id"), "company": job.get("company"),
            "position": job.get("position"), "duration": duration,
            "error": str(error) if error else None,
            "stack": "".join(traceback.format_exception(error)) if error else None,
            "retry_count": retry_count, "resolution": resolution,
        })
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(event, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)


def audit_tracked_files(root: Path) -> dict:
    """Inspect tracked source without returning detected secret values."""
    import subprocess
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    problems = []
    secret_patterns = [
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
        re.compile(rb"\bsk-[A-Za-z0-9_-]{30,}\b"),
    ]
    files = [p.decode() for p in result.stdout.split(b"\0") if p]
    for relative in files:
        path = Path(relative)
        if path.parts[0] in {"data", "applications", "logs", ".secrets", "browser-profiles", ".venv"}:
            problems.append({"path": relative, "reason": "private runtime path tracked"})
        if path.name.startswith(".env") and path.name != ".env.example":
            problems.append({"path": relative, "reason": "environment secrets file tracked"})
        if path.suffix in {".key", ".pem", ".pdf"} or re.search(r"cookies|credentials|storage-state", path.name, re.I):
            problems.append({"path": relative, "reason": "sensitive artifact tracked"})
        # Inspect the index, including staged additions/removals, rather than a different working copy.
        indexed = subprocess.run(["git", "show", f":{relative}"], cwd=root, capture_output=True, check=True).stdout
        if any(pattern.search(indexed) for pattern in secret_patterns):
            problems.append({"path": relative, "reason": "potential credential detected"})
    return {"passed": not problems, "tracked_files": len(files), "problems": problems}
