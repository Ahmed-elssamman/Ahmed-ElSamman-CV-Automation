"""Dated current FX evidence; refuse stale, future, invalid or mismatched rates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import urllib.request


class FXUnavailable(ValueError):
    pass


def validate_rate_evidence(evidence: dict, base: str, quote: str, *, now: datetime | None = None,
                           max_age_hours: int = 48) -> dict:
    now = now or datetime.now(timezone.utc)
    if evidence.get("from") != base or evidence.get("to") != quote or not evidence.get("source"):
        raise FXUnavailable("FX currency pair or source is missing or mismatched")
    try:
        rate = Decimal(str(evidence["rate"]))
        date = datetime.fromisoformat(evidence["date"].replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise FXUnavailable("FX evidence has invalid date or numeric rate") from exc
    if not rate.is_finite() or rate <= 0:
        raise FXUnavailable("FX rate must be finite and positive")
    age = now - date
    if age > timedelta(hours=max_age_hours) or age < -timedelta(minutes=5):
        raise FXUnavailable("FX rate is stale or future dated")
    return {**evidence, "rate": str(rate), "date": date.isoformat(), "age_hours": round(age.total_seconds() / 3600, 2)}


def get_exchange_rate(base: str, quote: str, *, root: Path | None = None,
                      now: datetime | None = None, fetch=None) -> dict:
    """Fetch current ExchangeRate-API open rates; cache only still-current evidence.

    Source: https://www.exchangerate-api.com/docs/free . Open endpoint requires
    attribution, no API key; published daily mid-market rates are estimates, not
    executable quotes. Never use this as a gross/net payroll conversion.
    """
    now = now or datetime.now(timezone.utc)
    if not (len(base) == len(quote) == 3 and base.isalpha() and quote.isalpha() and base.isupper() and quote.isupper()):
        raise FXUnavailable("FX currencies must be uppercase ISO-style three-letter codes")
    cache = Path(root) / "data" / "fx" / f"{base}_{quote}.json" if root else None
    if cache and cache.exists():
        try:
            return validate_rate_evidence(json.loads(cache.read_text()), base, quote, now=now)
        except (ValueError, KeyError, TypeError):
            pass
    url = f"https://open.er-api.com/v6/latest/{base}"
    try:
        if fetch:
            payload = fetch(url)
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "WORKAI/0.1 (candidate salary normalization)"})
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
        if payload.get("result") != "success" or payload.get("base_code") != base:
            raise FXUnavailable("FX provider did not return the requested base currency")
        evidence = {"from": base, "to": quote, "rate": str(payload["rates"][quote]), "source": url,
                    "provider": "ExchangeRate-API", "attribution_url": "https://www.exchangerate-api.com",
                    "date": datetime.fromtimestamp(payload["time_last_update_unix"], timezone.utc).isoformat(),
                    "retrieved_at": now.isoformat(), "rate_type": "indicative_mid_market"}
        evidence = validate_rate_evidence(evidence, base, quote, now=now)
    except Exception as exc:
        if isinstance(exc, FXUnavailable):
            raise
        raise FXUnavailable("Current sourced exchange rate is unavailable") from exc
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence
