from datetime import datetime, timedelta, timezone
import pytest
from workai.fx import FXUnavailable, get_exchange_rate, validate_rate_evidence


NOW = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)


def payload(base='EGP'):
    return {"result": "success", "base_code": base, "time_last_update_unix": int((NOW - timedelta(hours=1)).timestamp()), "rates": {"SAR": 0.07}}


def test_current_rate_preserves_provider_date_and_cached_evidence(tmp_path):
    first = get_exchange_rate('EGP', 'SAR', root=tmp_path, now=NOW, fetch=lambda _: payload())
    assert first['rate'] == '0.07'
    assert first['from'] == 'EGP' and first['to'] == 'SAR'
    assert first['source'] == 'https://open.er-api.com/v6/latest/EGP'
    assert first['age_hours'] == 1
    second = get_exchange_rate('EGP', 'SAR', root=tmp_path, now=NOW, fetch=lambda _: pytest.fail('Valid cached rate should be reused'))
    assert second['date'] == first['date']


@pytest.mark.parametrize('mutation', [
    {'from': 'USD'}, {'to': 'AED'}, {'date': '2025-01-01'},
    {'date': '2030-01-01'}, {'rate': 'NaN'}, {'rate': '-1'}, {'source': ''},
])
def test_bad_rate_is_rejected(mutation):
    evidence = {'from': 'EGP', 'to': 'SAR', 'rate': '0.07', 'source': 'fixture', 'date': NOW.isoformat()}
    with pytest.raises(FXUnavailable):
        validate_rate_evidence({**evidence, **mutation}, 'EGP', 'SAR', now=NOW)


def test_mismatched_provider_base_and_unknown_quote_rejected():
    with pytest.raises(FXUnavailable):
        get_exchange_rate('EGP', 'SAR', now=NOW, fetch=lambda _: payload('USD'))
    with pytest.raises(FXUnavailable):
        get_exchange_rate('EGP', 'AED', now=NOW, fetch=lambda _: payload())
