"""Plan and apply only missing platform profile facts using audited form mappings."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .answers import resolve_question
from .browser import _allowed_url, _blocked, _fill, _load_adapter, _locator, _positive_confirmation


def plan_profile_updates(root: Path, profile: dict, snapshot: list[dict], *, platform: str = "") -> dict:
    """Return sourced missing-field updates and conflicts without altering a profile.

    Snapshot entries contain question, current and optionally context. Populated
    values are never rewritten, including values that differ from the Master CV.
    """
    updates, conflicts, unknowns, unchanged = [], [], [], []
    for item in snapshot:
        resolution = resolve_question(root, item["question"], profile, {"platform": platform}, item.get("context"))
        if resolution["status"] != "KNOWN":
            unknowns.append({"question": item["question"], **resolution})
            continue
        record = {**item, **resolution}
        current = item.get("current")
        if current is None or (isinstance(current, str) and not current.strip()):
            updates.append(record)
        elif str(current).strip() != str(resolution["answer"]).strip():
            conflicts.append(record)
        else:
            unchanged.append(record)
    return {"status": "PLANNED", "platform": platform, "updates": updates,
            "conflicts": conflicts, "unknowns": unknowns, "unchanged": unchanged}


def _persist(root: Path, result: dict) -> dict:
    directory = Path(root) / "data" / "platform-profiles"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex + ".json")
    result = {**result, "inspected_at": datetime.now(timezone.utc).isoformat(), "snapshot_path": str(path.resolve())}
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    path.chmod(0o600)
    return result


def sync_profile(root: Path, platform: str, profile: dict, *, write: bool = False, submit: bool | None = None) -> dict:
    """Inspect, then optionally save missing known profile values.

    The adapter's ``profile`` config supplies URL, fields, save and confirmation.
    Unmapped/populated controls are left alone. ``submit`` is an alias for write
    for callers sharing application-operation interfaces. No adapter is enabled by
    default. Save ambiguity is nonretryable until the profile is re-inspected.
    """
    if submit is not None:
        write = submit
    adapter = _load_adapter(root, platform)
    config = (adapter or {}).get("profile", {})
    if not config or not config.get("url") or not config.get("fields"):
        return {"status": "BLOCKED_BY_PLATFORM", "platform": platform, "failure_reason": "No audited profile editor is configured", "retryable": False}
    if not _allowed_url(config["url"], adapter):
        return {"status": "BLOCKED_BY_PLATFORM", "platform": platform, "failure_reason": "Profile URL is outside configured allowed hosts", "retryable": False}
    saved = False
    result = {"platform": platform}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=adapter.get("headless", True))
            args = {}
            if adapter.get("storage_state"):
                state = Path(root) / adapter["storage_state"]
                if not state.is_file():
                    return {**result, "status": "BLOCKED_BY_PLATFORM", "failure_reason": "Configured login session is unavailable", "retryable": False}
                args["storage_state"] = str(state)
            page = browser.new_page(**args)
            def guard(route):
                if route.request.is_navigation_request() and not _allowed_url(route.request.url, adapter):
                    route.abort()
                else:
                    route.continue_()
            page.context.route("**/*", guard)
            page.set_default_timeout(adapter.get("timeout_ms", 15000))
            page.goto(config["url"], wait_until="domcontentloaded")
            if reason := _blocked(page, adapter):
                return {**result, "status": "BLOCKED_BY_PLATFORM", "failure_reason": reason, "retryable": False}
            snapshot = []
            for index, spec in enumerate(config["fields"]):
                if spec.get("type", "text") not in {"text", "email", "tel", "number", "textarea", "date", "select"}:
                    raise ValueError("Profile editor supports audited text and select fields only")
                control = _locator(page, spec)
                if control.count() != 1:
                    raise ValueError("Profile control is missing or ambiguous")
                snapshot.append({"question": spec.get("question") or spec.get("label"), "current": control.input_value(),
                                 "context": spec.get("context", {}), "field_index": index})
            result = plan_profile_updates(root, profile, snapshot, platform=platform)
            if not write or not result["updates"]:
                return _persist(root, result)
            confirmation_adapter = {**adapter, "confirmation": config.get("confirmation", {})}
            if not config.get("save") or not config.get("confirmation", {}).get("locator"):
                raise ValueError("Profile writes require a save control and positive confirmation")
            if _positive_confirmation(page, confirmation_adapter)[0]:
                raise ValueError("Profile confirmation marker existed before save")
            for update in result["updates"]:
                spec = config["fields"][update["field_index"]]
                control = _locator(page, spec)
                if control.input_value().strip():
                    raise ValueError("Profile changed since inspection; preserve new populated value")
                _fill(control, spec.get("type", "text"), update["answer"], spec)
            if reason := _blocked(page, adapter):
                return _persist(root, {**result, "status": "BLOCKED_BY_PLATFORM", "failure_reason": reason, "retryable": False})
            saved = True
            _locator(page, config["save"]).click()
            _locator(page, config["confirmation"]["locator"]).wait_for(state="visible")
            if not _positive_confirmation(page, confirmation_adapter)[0]:
                return _persist(root, {**result, "status": "SAVE_UNCONFIRMED", "failure_reason": "Positive save confirmation was not detected", "retryable": False})
            return _persist(root, {**result, "status": "UPDATED", "confirmation_url": page.url, "retryable": False})
    except Exception as exc:
        return _persist(root, {**result, "status": "SAVE_UNCONFIRMED" if saved else "FAILED",
                               "failure_reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__, "retryable": False})
