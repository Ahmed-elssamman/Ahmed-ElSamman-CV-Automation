"""Playwright automation for explicitly configured and audited application forms.

No universal job-board adapter is claimed. A local YAML configuration defines
allowed hosts, stable labels/selectors, every step, and positive confirmation.
An uncertain submission is never automatically retryable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import re
from urllib.parse import urlparse

import yaml

from .answers import resolve_question


def _result(status: str, *, reason: str | None = None, retryable: bool = False, **kwargs) -> dict:
    return {"status": status, "questions": [], "answers": [], "confirmation_id": None,
            "confirmation_url": None, "failure_reason": reason, "retryable": retryable, **kwargs}


def _load_adapter(root: Path, platform: str) -> dict | None:
    path = Path(root) / "config" / "platforms.yaml"
    if not path.exists():
        return None
    config = yaml.safe_load(path.read_text()) or {}
    adapter = config.get("platforms", {}).get(platform)
    return adapter if adapter and adapter.get("enabled") else None


def _locator(page, spec: dict):
    if spec.get("test_id"):
        return page.get_by_test_id(spec["test_id"])
    if spec.get("label"):
        return page.get_by_label(spec["label"], exact=spec.get("exact", True))
    if spec.get("role"):
        return page.get_by_role(spec["role"], name=spec.get("name"), exact=spec.get("exact", True))
    if spec.get("selector"):
        return page.locator(spec["selector"])
    raise ValueError("Adapter controls require a stable label, role, test_id or selector")


def _allowed_url(url: str, adapter: dict) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in adapter.get("allowed_hosts", [])


def _blocked(page, adapter: dict) -> str | None:
    if not _allowed_url(page.url, adapter):
        return "Navigation left the configured allowed hosts"
    if re.search(r"/(?:login|signin|sign-in|auth)(?:/|\?|$)", page.url, re.I):
        return "Authentication is required or the stored session expired"
    selectors = adapter.get("blocking_selectors", []) + [
        'iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]',
        'iframe[src*="challenges.cloudflare.com"]', 'input[type="password"]',
        '[data-sitekey]', '#challenge-running',
    ]
    for selector in selectors:
        loc = page.locator(selector)
        if any(loc.nth(i).is_visible() for i in range(loc.count())):
            return "Authentication, CAPTCHA or a platform security challenge blocks automation"
    return None


def _fill(control, kind: str, value, spec: dict) -> None:
    if kind in {"text", "email", "tel", "number", "textarea", "date"}:
        control.fill(str(value))
    elif kind == "select":
        mapped = spec.get("option_map", {}).get(str(value), value)
        if spec.get("select_by", "label") == "value":
            control.select_option(value=str(mapped))
        else:
            control.select_option(label=str(mapped))
    elif kind == "multiselect":
        if not isinstance(value, list):
            raise ValueError("A multiselect requires an approved list of answers")
        control.select_option(label=[str(item) for item in value])
    elif kind in {"checkbox", "radio"}:
        if kind == "radio":
            # Radio controls are resolved to the option whose exact answer was approved.
            control.check()
        else:
            if not isinstance(value, bool):
                raise ValueError("Checkbox answers must be explicit boolean facts")
            control.set_checked(value)
    else:
        raise ValueError(f"Unsupported field type: {kind}")


def _required_unmapped(page) -> list[dict]:
    return page.locator('input, select, textarea, [role="combobox"], [role="checkbox"], [role="radio"]').evaluate_all("""els => els.filter(el => {
      const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      return visible && !el.disabled && (el.required || el.getAttribute('aria-required') === 'true') && !el.dataset.workaiMapped;
    }).map(el => ({question: (el.labels && Array.from(el.labels).map(l => l.innerText).join(' ')) || el.getAttribute('aria-label') || el.name || el.id || 'Unlabelled required field', field_type: el.type || el.tagName.toLowerCase()}))""")


def _mark(control) -> None:
    control.evaluate("el => { el.dataset.workaiMapped = 'true'; if (el.type === 'radio' && el.name) { const form = el.form || document; form.querySelectorAll('input[type=radio]').forEach(peer => { if(peer.name === el.name) peer.dataset.workaiMapped = 'true'; }); } }")


def _positive_confirmation(page, adapter: dict) -> tuple[bool, str | None]:
    confirmation = adapter.get("confirmation", {})
    if not confirmation or not _allowed_url(page.url, adapter):
        return False, None
    # The configured marker MUST be absent before submission and present afterwards.
    if confirmation.get("url_pattern") and not re.search(confirmation["url_pattern"], page.url):
        return False, None
    if not confirmation.get("locator"):
        return False, None
    marker = _locator(page, confirmation["locator"])
    if marker.count() != 1 or not marker.is_visible():
        return False, None
    text = marker.inner_text().strip()
    if not text or (confirmation.get("text_pattern") and not re.search(confirmation["text_pattern"], text, re.I)):
        return False, None
    confirmation_id = None
    if confirmation.get("id_pattern"):
        match = re.search(confirmation["id_pattern"], text)
        if match:
            confirmation_id = match.group(1)
    return True, confirmation_id


def apply_job(root: Path, job: dict, profile: dict, cv: dict, *, submit: bool = False, before_submit=None) -> dict:
    """Fill an audited application and optionally submit under standing authorization.

    ``config/platforms.yaml`` is trusted operator configuration. Job descriptions
    cannot supply selectors, answers, executable code or an upload path. Root
    orchestration owns duplicate detection and durable pre-submit state.
    """
    adapter = _load_adapter(root, job.get("platform", ""))
    if not adapter:
        return _result("BLOCKED_BY_PLATFORM", reason="No enabled, audited browser adapter is configured for this platform")
    target = job.get("application_url") or job.get("job_url", "")
    if not _allowed_url(target, adapter):
        return _result("BLOCKED_BY_PLATFORM", reason="Application URL is outside configured allowed hosts")
    if not adapter.get("steps") or not adapter.get("confirmation", {}).get("locator"):
        return _result("FAILED", reason="Adapter requires application steps and a positive confirmation locator")
    pdf_path = Path(cv.get("pdf_path", ""))
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf" or not pdf_path.read_bytes().startswith(b"%PDF-"):
        return _result("FAILED", reason="CV upload must be an existing validated PDF")
    pdf_bytes = pdf_path.read_bytes()
    upload_evidence = {"path": str(pdf_path.resolve()), "sha256": hashlib.sha256(pdf_bytes).hexdigest(), "bytes": len(pdf_bytes)}
    questions, answers, unknowns = [], [], []
    submitted = False
    operation = "launch_browser"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=adapter.get("headless", True))
            context_args = {}
            state = adapter.get("storage_state")
            if state:
                state_path = Path(root) / state
                if not state_path.is_file():
                    browser.close()
                    return _result("BLOCKED_BY_PLATFORM", reason="Configured login session is unavailable")
                context_args["storage_state"] = str(state_path)
            context = browser.new_context(**context_args)
            # Keep redirects/resources scoped. A configured site can load its listed CDN hosts.
            navigation_hosts = set(adapter.get("allowed_hosts", []))
            def guard(route):
                if route.request.is_navigation_request() and urlparse(route.request.url).hostname not in navigation_hosts:
                    route.abort()
                else:
                    route.continue_()
            context.route("**/*", guard)
            page = context.new_page()
            page.set_default_timeout(adapter.get("timeout_ms", 15000))
            operation = "navigate_application"
            page.goto(target, wait_until="domcontentloaded")
            if reason := _blocked(page, adapter):
                return _result("BLOCKED_BY_PLATFORM", reason=reason)
            if _positive_confirmation(page, adapter)[0]:
                return _result("SUBMISSION_UNCONFIRMED", reason="Confirmation was already present on initial navigation; reconcile existing application before retry")
            for index, step in enumerate(adapter["steps"]):
                if reason := _blocked(page, adapter):
                    return _result("BLOCKED_BY_PLATFORM", reason=reason, questions=questions, answers=answers)
                operation = f"step_{index}_ready"
                if step.get("ready"):
                    _locator(page, step["ready"]).wait_for(state="visible")
                for spec in step.get("fields", []):
                    question = spec.get("question") or spec.get("label") or spec.get("name") or "Unlabelled field"
                    operation = f"step_{index}_field_{len(questions)}"
                    kind = spec.get("type", "text")
                    if kind == "file":
                        control = _locator(page, spec)
                        if control.count() != 1:
                            raise ValueError("Upload control must uniquely identify the CV field")
                        control.set_input_files({"name": pdf_path.name, "mimeType": "application/pdf", "buffer": pdf_bytes})
                        _mark(control)
                        continue
                    resolution = resolve_question(root, question, profile, job, spec.get("context"))
                    record = {"question": question, "step": index, **resolution,
                              "approved": resolution["status"] == "KNOWN", "scope": resolution.get("scope", {})}
                    questions.append(question)
                    answers.append(record)
                    if resolution["status"] != "KNOWN":
                        if spec.get("required", True):
                            unknowns.append(record["event"])
                        continue
                    value = resolution["answer"]
                    if kind == "radio":
                        option_spec = spec.get("options", {}).get(str(value))
                        if not option_spec:
                            unknowns.append({"type": "UNKNOWN_APPLICATION_FIELD", "question": question,
                                             "reason": "Approved answer has no exact configured radio option"})
                            continue
                        control = _locator(page, option_spec)
                    else:
                        control = _locator(page, spec)
                    if control.count() != 1:
                        raise ValueError(f"Configured control is missing or ambiguous: {question}")
                    _fill(control, kind, value, spec)
                    _mark(control)
                operation = f"step_{index}_validate_required"
                for field in _required_unmapped(page):
                    unknowns.append({"type": "UNKNOWN_APPLICATION_FIELD", **field,
                                     "reason": "Required field is outside audited adapter mapping"})
                if unknowns:
                    return _result("BLOCKED_UNKNOWN_FIELDS", reason="Required application fields lack approved answers or audited mappings",
                                   questions=questions, answers=answers, unknown_events=unknowns)
                invalid = page.locator("input:invalid,select:invalid,textarea:invalid")
                if any(invalid.nth(i).is_visible() for i in range(invalid.count())):
                    return _result("FAILED", reason="Browser form validation rejects one or more known answers",
                                   questions=questions, answers=answers)
                if index < len(adapter["steps"]) - 1:
                    if not step.get("next"):
                        raise ValueError("Intermediate adapter step lacks a next control")
                    operation = f"step_{index}_next"
                    _locator(page, step["next"]).click()
                else:
                    if not submit:
                        return _result("READY_TO_APPLY", questions=questions, answers=answers, upload_evidence=upload_evidence)
                    if not step.get("submit"):
                        raise ValueError("Final adapter step lacks a submit control")
                    if _positive_confirmation(page, adapter)[0]:
                        return _result("SUBMISSION_UNCONFIRMED", reason="Confirmation marker existed before submit; reconciliation required",
                                       questions=questions, answers=answers)
                    submit_control = _locator(page, step["submit"])
                    if submit_control.count() != 1:
                        raise ValueError("Submit control is missing or ambiguous")
                    # From this point on, transport failures might hide a successful submission.
                    if before_submit is not None:
                        before_submit({"job_id": job.get("id"), "questions": questions, "answers": answers, "upload_evidence": upload_evidence})
                    submitted = True
                    operation = "submit_click"
                    submit_control.click()
                    marker = _locator(page, adapter["confirmation"]["locator"])
                    operation = "submission_confirmation"
                    marker.wait_for(state="visible", timeout=adapter.get("confirmation_timeout_ms", 15000))
                    confirmed, confirmation_id = _positive_confirmation(page, adapter)
                    if confirmed:
                        return _result("SUBMITTED", questions=questions, answers=answers,
                                       confirmation_id=confirmation_id, confirmation_url=page.url, upload_evidence=upload_evidence,
                                       submitted_at=datetime.now(timezone.utc).isoformat(),
                                       confirmation_evidence={"marker_text": marker.inner_text().strip()[:2000], "url": page.url})
                    return _result("SUBMISSION_UNCONFIRMED", reason="Submit attempted but configured positive confirmation was not detected",
                                   questions=questions, answers=answers)
    except Exception as exc:
        # Exception text can contain DOM values or URL tokens; retain only a safe class/message.
        detail = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__} during {operation}"
        return _result("SUBMISSION_UNCONFIRMED" if submitted else "FAILED", reason=detail,
                       retryable=False if submitted else type(exc).__name__ in {"TimeoutError", "Error"},
                       questions=questions, answers=answers)
    return _result("FAILED", reason="Application workflow ended without a final state", questions=questions, answers=answers)
