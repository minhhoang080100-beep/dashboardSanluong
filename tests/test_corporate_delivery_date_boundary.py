"""Verifier CLI rejects unrepresentable API end dates before any login/read."""
from datetime import date

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_verify_delivery import CREDENTIALS, FakeTransport


@pytest.mark.parametrize('start', [date.max, date(9999, 12, 1)])
def test_maximum_end_date_is_rejected_before_yielding_a_window(start):
    windows = verify.month_windows(start, date.max)
    with pytest.raises(verify.VerificationError, match='^INVALID_PERIOD$'):
        next(windows)


@pytest.mark.parametrize('start', [date.max, date(9999, 12, 1)])
def test_unrepresentable_end_is_a_configuration_error_before_login(start):
    transport = FakeTransport()
    with pytest.raises(verify.VerificationError, match='^INVALID_PERIOD$'):
        verify.verify(transport, dict(CREDENTIALS), start=start, end=date.max,
                      production_scope='nghe_tinh', verify_references=True)
    assert transport.calls == []


def test_cli_maximum_end_returns_clean_configuration_error_without_output(tmp_path, monkeypatch, capsys):
    transport = FakeTransport()
    monkeypatch.setattr(verify, 'HttpTransport', lambda *args, **kwargs: transport)
    monkeypatch.setenv('CORPORATE_API_USERNAME', CREDENTIALS['Username'])
    monkeypatch.setenv('CORPORATE_API_PASSWORD', CREDENTIALS['Password'])
    output = tmp_path / 'must-not-be-created.json'
    code = verify.main(['--base-url', 'https://example.com', '--start', '9999-12-01',
                        '--end', '9999-12-31', '--production-scope', 'nghe_tinh',
                        '--verify-references', '--output', str(output)])
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == 'INVALID_PERIOD\n'
    assert transport.calls == [] and not output.exists()


def test_latest_supported_api_day_still_yields_complete_calendar_windows():
    assert list(verify.month_windows(date(9999, 11, 30), date(9999, 12, 30))) == [
        (date(9999, 11, 30), date(9999, 11, 30)),
        (date(9999, 12, 1), date(9999, 12, 30)),
    ]


def test_latest_supported_day_can_be_verified_and_covered_by_reference():
    day = date(9999, 12, 30)
    reference = {'source': 'MISBI', 'approved': True, 'referenceDate': '9999-12-31',
        'comparisons': [{'startDate': '99991230', 'endDate': '99991230',
            'scope': 'nghe_tinh', 'resources': ['contQuayVolumesCB'], 'metric': 'TEU',
            'expected': '2', 'tolerance': '0'}]}
    transport = FakeTransport()
    report = verify.verify(transport, dict(CREDENTIALS), start=day, end=day,
                           production_scope='nghe_tinh', reference=reference)
    assert report['retrievalComplete'] and report['suppliedComparisonsMatched']
    assert report['logoutHttp'] == 200
    coverage = next(item for item in report['referenceCoverage']['individualMetrics']
                    if item['resource'] == 'contQuayVolumesCB' and item['metric'] == 'TEU')
    assert coverage['fullPeriodCovered']
    assert coverage['coveredPeriods'] == [['99991230', '99991230']]
    assert coverage['uncoveredPeriods'] == []
