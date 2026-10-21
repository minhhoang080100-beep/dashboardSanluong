"""Delivery verifier exercises fake HTTP only, never the app or configured SQL."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from http.client import IncompleteRead
import hashlib
import io
import json

import pytest

from backend.corporate_api import verify_delivery as verify


TOKEN = 'synthetic-token-not-for-production'
CREDENTIALS = {'Username': 'synthetic', 'Password': 'not-a-real-password'}


def response(body, status=200, headers=None):
    return verify.Response(status, headers or {}, json.dumps(body, default=str).encode())


def row(resource, day='20260101', identity='1', weight='0.1', teu='2'):
    # Match the application's public JSON-number serialization. String input
    # here is fixture convenience, not permission to accept quoted wire values.
    def number(value):
        exact = Decimal(value)
        return int(exact) if exact == exact.to_integral_value() else float(exact)

    base = {'reportDate': '20261014', 'finishDate': day, 'companyId': 'CNT', 'handlingMethodId': identity}
    if resource.startswith('cont'):
        base.update(originId=None, containerWeight=number(weight), containerTEU=number(teu),
                    containerOperatorId=None, containerSizeId='20')
        if 'Quay' in resource:
            base.update(shipId='10', classId='1', shipOperatorId=None)
    else:
        base.update(cargoTypeId='1', cargoCategoryId='2', bulkOriginId=None, bulkWeight=number(weight))
        if 'Quay' in resource:
            base.update(shipId='10', shipAgentId=None, shipClassId='1')
        else:
            base.update(customerCode=None)
    return base


def page(rows, params, total=None, read_at='2026-10-14T04:00:00+00:00', digest='a' * 64, snapshot=None):
    total = len(rows) if total is None else total
    result = response({'data': rows, 'code': '1', 'message': 'ok'}, headers={
        'X-Page': str(params['page']), 'X-Limit': str(params['limit']), 'X-Total-Count': str(total),
        'X-Has-Next': str(params['page'] * params['limit'] < total).lower(), 'X-Source-Read-At': read_at,
        'X-Result-Digest': digest})
    if snapshot is not None:
        result.headers['X-Snapshot-Id'] = snapshot
    return result


class FakeTransport:
    def __init__(self, get=None, login=None, logout_status=200):
        self.get = get
        self.login = login
        self.logout_status = logout_status
        self.calls = []

    def request(self, method, path, *, params=None, headers=None, json_body=None):
        self.calls.append((method, path, deepcopy(params)))
        if path == '/api/login':
            assert json_body == CREDENTIALS
            return self.login or response({'code': '1', 'accessToken': TOKEN})
        assert headers == {'Authorization': 'Bearer ' + TOKEN}
        if path == '/api/logout':
            return response({'code': '1'}, self.logout_status)
        assert method == 'GET'
        resource = path.rsplit('/', 1)[1]
        return self.get(resource, params) if self.get else page([row(resource, params['startDate'])], params)


def run(transport=None, **kwargs):
    return verify.verify(transport or FakeTransport(), dict(CREDENTIALS),
                         **{'start': date(2026, 1, 1), 'end': date(2026, 1, 31),
                            'production_scope': 'nghe_tinh', **kwargs})


def reference(**entry):
    return {'source': 'MISBI', 'approved': True, 'referenceDate': '2026-10-14', 'comparisons': [{
        'startDate': '20260101', 'endDate': '20260131', 'scope': 'nghe_tinh',
        'resources': ['contQuayVolumesCB'], 'metric': 'TEU', 'expected': '2', **entry}]}


def test_calendar_windows_and_exact_totals_without_claiming_business_acceptance():
    transport = FakeTransport()
    report = run(transport, start=date(2026, 1, 31), end=date(2026, 2, 2))
    assert report['status'] == 'retrieved_without_reference'
    assert report['retrievalComplete'] is True
    assert report['suppliedComparisonsMatched'] is False
    assert 'benchmarkVerified' not in report
    assert report['consistentSnapshot'] is False
    assert report['scopeVerification'] == 'operator_declared_not_verified_by_public_api'
    assert 'readyForDelivery' not in report
    assert report['resources']['contQuayVolumesCB']['totals'] == {'tonnes': '0.2', 'TEU': '4'}
    assert report['resources']['bulkGateVolumesCB']['totals'] == {'tonnes': '0.2'}
    assert len(report['windows']) == 8
    assert [(r['startDate'], r['endDate']) for r in report['windows'][::4]] == [
        ('20260131', '20260131'), ('20260201', '20260202')]
    assert [call[1] for call in transport.calls[1:-1]] == ['/api/' + r for r in verify.RESOURCES] * 2
    assert transport.calls[-1][1] == '/api/logout'
    serialized = json.dumps(report)
    assert TOKEN not in serialized and CREDENTIALS['Password'] not in serialized and '_daily' not in serialized


def test_leap_february_windows_and_range_bound():
    assert list(verify.month_windows(date(2024, 2, 28), date(2024, 3, 1))) == [
        (date(2024, 2, 28), date(2024, 2, 29)), (date(2024, 3, 1), date(2024, 3, 1))]
    for first, last in [(date(2026, 2, 2), date(2026, 1, 1)), (date(2025, 1, 1), date(2026, 1, 2))]:
        with pytest.raises(verify.VerificationError, match='INVALID_PERIOD'):
            list(verify.month_windows(first, last))


def test_second_page_error_never_promotes_received_subtotal(monkeypatch):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)
    def get(resource, params):
        if resource != 'contQuayVolumesCB':
            return page([], params)
        if params['page'] == 1:
            return page([row(resource, identity='1'), row(resource, identity='2')], params, total=3)
        return response({'data': [], 'code': '0', 'message': 'private server text'}, 503,
                        {'X-Error-Code': 'WEIGHT_OR_UNIT_UNAVAILABLE'})
    report = run(FakeTransport(get), reference=reference())
    result = report['windows'][0]
    assert result['complete'] is False and result['totals'] is None
    assert result['receivedSubtotal'] == {'tonnes': '0.2', 'TEU': '4'}
    assert report['resources']['contQuayVolumesCB']['totals'] is None
    assert report['status'] == 'incomplete' and report['suppliedComparisonsMatched'] is False
    assert report['comparisons'][0]['actual'] is None
    assert report['comparisons'][0]['status'] == 'incomplete'
    assert 'private server text' not in json.dumps(report)


@pytest.mark.parametrize('change,code', [
    (lambda p: p.headers.pop('X-Page'), 'INVALID_PAGINATION'),
    (lambda p: p.headers.update({'X-Limit': '20'}), 'INVALID_PAGINATION'),
    (lambda p: p.headers.update({'X-Has-Next': 'true'}), 'MISSING_OR_UNEXPECTED_PAGE_ROWS'),
    (lambda p: p.headers.update({'X-Total-Count': '2'}), 'MISSING_OR_UNEXPECTED_PAGE_ROWS'),
    (lambda p: p.headers.update({'X-Source-Read-At': '2026-10-14T04:00:00'}), 'SOURCE_READ_TIME_MISSING'),
    (lambda p: p.headers.update({'X-Total-Count': '999999999'}), 'RESULT_LIMIT_EXCEEDED'),
])
def test_invalid_page_metadata_is_a_failure(change, code):
    def get(resource, params):
        result = page([row(resource)], params)
        change(result)
        return result
    report = run(FakeTransport(get))
    assert all(w['error'] == code and w['totals'] is None for w in report['windows'])


@pytest.mark.parametrize('duplicate,change_total', [(True, False), (False, True)])
def test_cross_page_duplicates_and_count_changes(monkeypatch, duplicate, change_total):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)
    def get(resource, params):
        if params['page'] == 1:
            return page([row(resource, identity='1'), row(resource, identity='2')], params, total=3)
        last = row(resource, identity='1' if duplicate else '3')
        last['reportDate'] = '20261015'  # reportDate is not part of the business identity.
        return page([last], params, total=4 if change_total else 3)
    report = run(FakeTransport(get))
    expected = 'DUPLICATE_BUSINESS_KEY' if duplicate else 'TOTAL_CHANGED_BETWEEN_PAGES'
    assert all(w['error'] == expected for w in report['windows'])


def test_full_paging_and_decimal_precision(monkeypatch):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)
    def get(resource, params):
        assert 'snapshotId' not in params
        rows = [row(resource, identity=str(i), weight=w) for i, w in enumerate(('0.1', '0.2', '0.0000001'), 1)]
        offset = (params['page'] - 1) * 2
        return page(rows[offset:offset + 2], params, total=3,
                    read_at=f'2026-10-14T04:00:0{params["page"]}+00:00')
    report = run(FakeTransport(get), reference=reference(metric='tonnes', expected='0.3000001'))
    assert report['suppliedComparisonsMatched'] is True
    assert report['resources']['contQuayVolumesCB']['totals']['tonnes'] == '0.3000001'
    assert report['windows'][0]['rowsReceived'] == 3
    assert len(report['windows'][0]['pages']) == 2
    assert report['consistentSnapshot'] is False


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('digest,error', [
    (None, 'RESULT_DIGEST_MISSING'), ('', 'RESULT_DIGEST_INVALID'),
    ('a' * 63, 'RESULT_DIGEST_INVALID'), ('a' * 65, 'RESULT_DIGEST_INVALID'),
    ('A' * 64, 'RESULT_DIGEST_INVALID'), (123, 'RESULT_DIGEST_INVALID'),
    ('PRIVATE_SOURCE_TEXT', 'RESULT_DIGEST_INVALID'),
])
def test_every_production_response_requires_a_valid_result_digest(empty, digest, error):
    def get(resource, params):
        result = page([] if empty else [row(resource)], params, digest=digest)
        if digest is None:
            result.headers.pop('X-Result-Digest')
        return result
    report = run(FakeTransport(get), reference=reference())
    assert all(w['error'] == error and w['totals'] is None for w in report['windows'])
    assert not report['retrievalComplete'] and not report['suppliedComparisonsMatched']
    assert 'PRIVATE_SOURCE_TEXT' not in json.dumps(report)


@pytest.mark.parametrize('change', ['business_keys', 'already_read_weight'])
def test_changed_full_result_with_stable_count_never_passes_as_complete(monkeypatch, change):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)

    def get(resource, params):
        before = [row(resource, identity=str(i), weight='1') for i in (1, 2, 3)]
        after = ([row(resource, identity=str(i), weight='1') for i in (0, 1, 4)]
                 if change == 'business_keys' else [{**before[0],
                     ('containerWeight' if resource.startswith('cont') else 'bulkWeight'): '2'}, *before[1:]])
        full_result = before if params['page'] == 1 else after
        digest = hashlib.sha256(json.dumps(full_result, sort_keys=True).encode()).hexdigest()
        offset = (params['page'] - 1) * 2
        return page(full_result[offset:offset + 2], params, total=3, digest=digest)

    transport = FakeTransport(get)
    report = run(transport, reference=reference(metric='tonnes', expected='3'))
    assert not report['retrievalComplete'] and not report['suppliedComparisonsMatched']
    assert report['comparisons'][0]['status'] == 'incomplete'
    assert all(w['error'] == 'RESULT_CHANGED_BETWEEN_PAGES' and w['totals'] is None
               and w['rowsReceived'] == 2 and w['receivedSubtotal']['tonnes'] == '2'
               for w in report['windows'])
    assert all(r['totals'] is None for r in report['resources'].values())
    # The verifier stops the window and never mixes retries with accepted pages.
    assert len([c for c in transport.calls if c[0] == 'GET']) == 8


def test_published_snapshot_is_propagated_and_reset_per_resource_and_period(monkeypatch):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)

    def get(resource, params):
        snapshot = hashlib.sha256((resource + params['startDate']).encode()).hexdigest()[:32]
        assert params.get('snapshotId') == (None if params['page'] == 1 else snapshot)
        rows = [row(resource, day=params['startDate'], identity=str(i)) for i in (1, 2, 3)]
        offset = (params['page'] - 1) * 2
        return page(rows[offset:offset + 2], params, total=3, snapshot=snapshot)

    report = run(FakeTransport(get), end=date(2026, 2, 28))
    assert report['retrievalComplete'] and len(report['windows']) == 8
    assert len({w['pages'][0]['snapshotId'] for w in report['windows']}) == 8
    assert all(w['pages'][0]['snapshotId'] == w['pages'][1]['snapshotId'] for w in report['windows'])


@pytest.mark.parametrize('first,second', [(None, 'a' * 32), ('a' * 32, None), ('a' * 32, 'b' * 32)])
def test_switching_live_published_mode_or_snapshot_invalidates_window(monkeypatch, first, second):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)

    def get(resource, params):
        values = (1, 2) if params['page'] == 1 else (3,)
        return page([row(resource, identity=str(i)) for i in values], params, total=3,
                    snapshot=first if params['page'] == 1 else second)

    report = run(FakeTransport(get))
    assert all(w['error'] == 'SNAPSHOT_CHANGED_BETWEEN_PAGES' and w['totals'] is None
               for w in report['windows'])


@pytest.mark.parametrize('snapshot', ['', 'A' * 32, 'a' * 31, 'a' * 33, 'PRIVATE_SNAPSHOT_TEXT', 123])
def test_invalid_snapshot_header_is_not_propagated_or_logged(snapshot):
    report = run(FakeTransport(lambda resource, params: page([row(resource)], params, snapshot=snapshot)))
    assert all(w['error'] == 'SNAPSHOT_HEADER_INVALID' for w in report['windows'])
    assert 'PRIVATE_SNAPSHOT_TEXT' not in json.dumps(report)


def test_missing_digest_on_later_page_is_not_a_successful_partial_delivery(monkeypatch):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 2)

    def get(resource, params):
        rows = [row(resource, identity=str(i)) for i in ((1, 2) if params['page'] == 1 else (3,))]
        result = page(rows, params, total=3)
        if params['page'] == 2:
            result.headers.pop('X-Result-Digest')
        return result

    report = run(FakeTransport(get))
    assert all(w['error'] == 'RESULT_DIGEST_MISSING' and w['totals'] is None
               for w in report['windows'])


@pytest.mark.parametrize('mutation,error', [
    ({'companyId': 'OTHER'}, 'ROW_OUTSIDE_REQUEST_SCOPE'),
    ({'finishDate': '20260201'}, 'ROW_OUTSIDE_REQUEST_SCOPE'),
    ({'_sourceTerminals': ['cua_lo']}, 'INVALID_ROW'),
    ({'sourceDatabase': 'private'}, 'INVALID_ROW'),
    ({'containerWeight': None}, 'INVALID_MEASURE'),
    ({'containerWeight': True}, 'INVALID_MEASURE'),
    ({'containerWeight': '-1'}, 'INVALID_MEASURE'),
    ({'containerTEU': 'NaN'}, 'INVALID_MEASURE'),
    ({'containerTEU': '1e-100000'}, 'INVALID_MEASURE'),
])
def test_invalid_rows_rejected_without_exposing_raw_values(mutation, error):
    def get(resource, params):
        value = row(resource)
        if resource == 'contQuayVolumesCB':
            value.update(mutation)
        return page([value], params)
    report = run(FakeTransport(get))
    assert report['windows'][0]['error'] == error
    assert report['windows'][0]['totals'] is None
    assert 'private' not in json.dumps(report)


@pytest.mark.parametrize('body', [
    b'{"data":[],"data":[],"code":"1","message":"ok"}',
    b'{"data":[],"code":"1","message":"ok","extra":"secret"}',
    b'{"data":[NaN],"code":"1","message":"ok"}',
    b'not JSON',
])
def test_invalid_json_or_extra_envelope_is_rejected(body):
    def get(resource, params):
        result = page([], params)
        result.body = body
        return result
    report = run(FakeTransport(get))
    assert report['retrievalComplete'] is False
    assert all(w['totals'] is None for w in report['windows'])
    assert 'secret' not in json.dumps(report)


def test_empty_verified_dataset_is_zero_not_missing():
    report = run(FakeTransport(lambda r, p: page([], p)), reference=reference(expected='0'))
    assert report['suppliedComparisonsMatched'] is True
    assert report['resources']['contQuayVolumesCB']['totals'] == {'tonnes': '0', 'TEU': '0'}


def test_reference_sum_is_explicit_same_cut_only_and_tolerance_exact():
    ref = reference(resources=['contQuayVolumesCB', 'bulkQuayVolumesCB'], metric='tonnes',
                    expected='0.1999', tolerance='0.0001')
    report = run(reference=ref)
    assert report['comparisons'][0]['actual'] == '0.2'
    assert report['comparisons'][0]['difference'] == '0.0001'
    assert report['suppliedComparisonsMatched'] is True
    assert report['referenceCoverage']['groupedComparisonIndexes'] == [0]
    assert report['referenceCoverage']['allIndividualMetricsCovered'] is False
    ref['comparisons'][0].pop('tolerance')
    mismatch = run(reference=ref)
    assert mismatch['status'] == 'reference_mismatch'
    assert mismatch['comparisons'][0]['tolerance'] == '0'


def test_partial_month_reference_uses_finish_date_and_excludes_other_days():
    def get(resource, params):
        return page([row(resource, day='20260101', identity='1', weight='10'),
                     row(resource, day='20260102', identity='2', weight='20')], params)
    ref = reference(startDate='20260102', endDate='20260102', metric='tonnes', expected='20')
    report = run(FakeTransport(get), reference=ref)
    assert report['suppliedComparisonsMatched'] is True
    assert report['comparisons'][0]['actual'] == '20'
    assert report['resources']['contQuayVolumesCB']['totals']['tonnes'] == '30'
    coverage = report['referenceCoverage']
    assert coverage['allIndividualMetricsCovered'] is False
    metric = next(c for c in coverage['individualMetrics']
                  if c['resource'] == 'contQuayVolumesCB' and c['metric'] == 'tonnes')
    assert metric['coveredPeriods'] == [['20260102', '20260102']]
    assert metric['uncoveredPeriods'] == [['20260101', '20260101'], ['20260103', '20260131']]
    assert metric['fullPeriodCovered'] is False
    assert report['status'] == 'supplied_references_matched'
    assert 'benchmarkVerified' not in report


def test_full_reference_coverage_requires_each_endpoint_metric_and_period():
    ref = reference()
    ref['comparisons'] = [{
        'startDate': '20260101', 'endDate': '20260131',
        'scope': 'nghe_tinh' if 'Quay' in resource else 'all_yards',
        'resources': [resource], 'metric': metric, 'expected': '2' if metric == 'TEU' else '0.1',
    } for resource in verify.RESOURCES
       for metric in (('tonnes', 'TEU') if resource.startswith('cont') else ('tonnes',))]
    report = run(reference=ref)
    assert report['suppliedComparisonsMatched'] is True
    assert report['referenceCoverage']['allIndividualMetricsCovered'] is True
    assert all(c['uncoveredPeriods'] == [] for c in report['referenceCoverage']['individualMetrics'])


@pytest.mark.parametrize('change,error', [
    ({'resources': ['contQuayVolumesCB', 'contGateVolumesCB']}, 'MIXED_QUAY_GATE_REFERENCE'),
    ({'scope': 'all_activity'}, 'REFERENCE_SCOPE_MISMATCH'),
    ({'resources': ['bulkQuayVolumesCB'], 'metric': 'TEU'}, 'INVALID_REFERENCE_METRIC'),
    ({'endDate': '20260201'}, 'INVALID_REFERENCE'),
    ({'tolerance': True}, 'INVALID_MEASURE'),
])
def test_invalid_reference_rejected_before_login(change, error):
    transport = FakeTransport()
    with pytest.raises(verify.VerificationError, match=error):
        run(transport, reference=reference(**change))
    assert not transport.calls


def test_reference_requires_approved_misbi_and_reference_date():
    for change, error in [({'approved': False}, 'REFERENCE_NOT_APPROVED'),
                          ({'source': 'old API'}, 'REFERENCE_NOT_APPROVED'),
                          ({'referenceDate': 'bad'}, 'INVALID_REFERENCE_DATE'),
                          ({'referenceDate': '2026-01-01'}, 'INVALID_REFERENCE')]:
        ref = reference(); ref.update(change)
        with pytest.raises(verify.VerificationError, match=error):
            run(reference=ref)
    ref = reference(); ref['comparisons'] *= 2
    with pytest.raises(verify.VerificationError, match='DUPLICATE_REFERENCE'):
        run(reference=ref)


def test_gate_reference_has_distinct_all_yards_scope():
    report = run(reference=reference(resources=['contGateVolumesCB'], scope='all_yards'))
    assert report['suppliedComparisonsMatched'] is True
    with pytest.raises(verify.VerificationError, match='REFERENCE_SCOPE_MISMATCH'):
        run(reference=reference(resources=['contGateVolumesCB']))


@pytest.mark.parametrize('url', ['http://remote.example', 'https://user:secret@example.com',
                                'https://example.com/?token=secret', 'https://example.com/api',
                                'https://@example.com', 'https://exam\nple.com'])
def test_base_url_disallows_insecure_remote_and_embedded_credentials(url):
    with pytest.raises(verify.VerificationError, match='INVALID_BASE_URL'):
        verify.validate_base_url(url)


def test_base_url_https_and_local_http_allowed():
    assert verify.validate_base_url('https://example.com/') == 'https://example.com'
    assert verify.validate_base_url('http://127.0.0.1:8000') == 'http://127.0.0.1:8000'


@pytest.mark.parametrize('login,error', [(response({'secret': 'private'}, 401), 'LOGIN_FAILED'),
                                        (response([]), 'INVALID_LOGIN_RESPONSE'),
                                        (response({'code': '1', 'accessToken': 'bad\nheader'}), 'INVALID_LOGIN_RESPONSE')])
def test_authentication_failures_are_safe(login, error):
    transport = FakeTransport(login=login)
    with pytest.raises(verify.VerificationError, match=error):
        run(transport)
    assert len(transport.calls) == 1


def test_transport_failure_and_logout_failure_are_explicit():
    def get(resource, params):
        raise verify.VerificationError('TRANSPORT_ERROR')
    transport = FakeTransport(get, logout_status=503)
    report = run(transport)
    assert report['retrievalComplete'] is False
    assert report['logoutHttp'] == 503
    assert all(w['error'] == 'TRANSPORT_ERROR' for w in report['windows'])


def test_unknown_server_error_header_and_body_are_not_logged():
    report = run(FakeTransport(lambda r, p: response(
        {'message': 'PRIVATE_CONNECTION_STRING'}, 503, {'X-Error-Code': 'PRIVATE_SECRET'})))
    serialized = json.dumps(report)
    assert 'PRIVATE_CONNECTION_STRING' not in serialized and 'PRIVATE_SECRET' not in serialized
    assert all(w['error'] == 'HTTP_ERROR' for w in report['windows'])


def test_cli_uses_stdin_safe_output_and_exclusive_create(tmp_path, monkeypatch, capsys):
    transport = FakeTransport()
    monkeypatch.setattr(verify, 'HttpTransport', lambda url: transport)
    monkeypatch.setattr(verify.sys, 'stdin', io.StringIO(json.dumps(CREDENTIALS)))
    output = tmp_path / 'check.json'
    args = ['--base-url', 'https://example.com', '--start', '2026-01-01', '--end', '2026-01-31',
            '--production-scope', 'nghe_tinh', '--output', str(output), '--credentials-stdin']
    assert verify.main(args) == 0
    assert json.loads(output.read_text())['suppliedComparisonsMatched'] is False
    original = output.read_bytes()
    assert verify.main(args) == 1
    assert output.read_bytes() == original
    captured = capsys.readouterr()
    assert 'OUTPUT_ALREADY_EXISTS' in captured.err
    assert CREDENTIALS['Password'] not in captured.out + captured.err + output.read_text()
    assert TOKEN not in captured.out + captured.err + output.read_text()


def test_cli_env_credentials_and_misbi_mismatch_exit_two(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, 'HttpTransport', lambda url: FakeTransport())
    monkeypatch.setenv('CORPORATE_API_USERNAME', CREDENTIALS['Username'])
    monkeypatch.setenv('CORPORATE_API_PASSWORD', CREDENTIALS['Password'])
    benchmark = tmp_path / 'misbi.json'
    benchmark.write_text(json.dumps(reference(expected='3')), encoding='utf-8')
    output = tmp_path / 'check.json'
    assert verify.main(['--base-url', 'https://example.com', '--start', '2026-01-01', '--end', '2026-01-31',
                        '--production-scope', 'nghe_tinh', '--benchmark', str(benchmark), '--output', str(output)]) == 2
    report = json.loads(output.read_text())
    assert report['status'] == 'reference_mismatch'
    assert report['comparisons'][0]['difference'] == '-1'


def test_no_redirect_following_or_unbounded_body_reads():
    assert verify._NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://other.example') is None
    class Remote(io.BytesIO):
        code = 200
        headers = {}
    class Opener:
        def open(self, request, timeout):
            return Remote(b'x' * (verify.MAX_PAGE_BYTES + 1))
    transport = verify.HttpTransport('https://example.com')
    transport.opener = Opener()
    with pytest.raises(verify.VerificationError, match='RESPONSE_TOO_LARGE'):
        transport.request('GET', '/api/contQuayVolumesCB')


def test_http_protocol_failure_does_not_leak_upstream_bytes():
    class Opener:
        def open(self, request, timeout):
            raise IncompleteRead(b'private-upstream-text', 10)
    transport = verify.HttpTransport('https://example.com')
    transport.opener = Opener()
    with pytest.raises(verify.VerificationError, match='^TRANSPORT_ERROR$'):
        transport.request('GET', '/api/contQuayVolumesCB')
