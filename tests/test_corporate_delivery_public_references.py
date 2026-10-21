"""Bounded public-FK checks; fake HTTP and isolated ASGI only, never source SQL."""
from copy import deepcopy
from datetime import date
import io
import json
from urllib.parse import unquote

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_verify_delivery import CREDENTIALS, TOKEN, page, reference, response, row


def master(resource, identity, *, parent=None):
    values = dict.fromkeys(verify.MODELS[resource].model_fields)
    values.update(reportDate='20261019')
    values[verify.IDENTITY[resource]] = identity
    names = {'shipDetails': 'shipFullName', 'customers': 'customerNameVN', 'cargoType': 'cargoTypeName',
             'cargoCategory': 'cargoName', 'handlingMethodList': 'handlingMethodName',
             'class': 'className', 'origins': 'originName', 'containerSize': 'localSzTp'}
    values[names[resource]] = 'synthetic-reference-name-never-exported'
    if resource == 'cargoCategory':
        values.update(cargoTypeId='1', cargoParentId=parent)
    if resource == 'customers':
        values['metadata'] = {'isDeleted': 0, 'createdDate': None, 'modifiedDate': None}
    return values


def catalog_page(resource, identity, params, *, parent=None):
    result = page([master(resource, identity, parent=parent)], params)
    result.headers['Cache-Control'] = 'no-store'
    result.headers.pop('X-Result-Digest')  # Catalog contract does not promise one.
    return result


class Transport:
    def __init__(self, production=None, catalog=None):
        self.production, self.catalog = production, catalog
        self.calls, self.catalog_calls = [], []

    def request(self, method, path, *, params=None, headers=None, json_body=None):
        self.calls.append((method, path, deepcopy(params)))
        if path == '/api/login':
            assert json_body == CREDENTIALS
            return response({'code': '1', 'accessToken': TOKEN})
        assert headers == {'Authorization': 'Bearer ' + TOKEN}
        if path == '/api/logout':
            return response({'code': '1'})
        assert method == 'GET'
        parts = path.split('/')
        resource = parts[2]
        if resource in verify.RESOURCES:
            return self.production(resource, params) if self.production else page([row(resource, params['startDate'])], params)
        identity = params['containerSizeId'] if resource == 'containerSize' else unquote(parts[3])
        self.catalog_calls.append((resource, identity, deepcopy(params)))
        return self.catalog(resource, identity, params) if self.catalog else catalog_page(resource, identity, params)


def run(transport=None, **options):
    return verify.verify(transport or Transport(), dict(CREDENTIALS),
        **{'start': date(2026, 1, 1), 'end': date(2026, 1, 31), 'production_scope': 'nghe_tinh',
           'verify_references': True, **options})


def window(report, resource):
    return next(item for item in report['windows'] if item['resource'] == resource)


def test_client_fk_map_matches_central_s_contract_and_does_not_include_self_ids():
    from backend.corporate_api.manage_exports import REFERENCES
    assert verify.PUBLIC_REFERENCES == {field: target[0] for field, target in REFERENCES.items()}


def test_opt_in_is_required_and_legacy_report_and_get_calls_stay_unchanged():
    transport = Transport()
    report = run(transport, verify_references=False)
    assert transport.catalog_calls == []
    assert 'publicReferences' not in report
    assert all('publicReferences' not in item for item in report['windows'])
    assert [path for method, path, _ in transport.calls if method == 'GET'] == ['/api/' + r for r in verify.RESOURCES]


def test_deduplicates_public_references_across_resources_and_complete_months():
    transport = Transport()
    report = run(transport, end=date(2026, 2, 28))
    keys = [(resource, identity) for resource, identity, _ in transport.catalog_calls]
    assert len(keys) == len(set(keys)) == 6
    assert report['publicReferences']['allCompleteWindowsPassed']
    assert report['publicReferences']['completeWindowsChecked'] == 8
    assert all(item['publicReferences']['status'] == 'passed' for item in report['windows'])
    assert report['retrievalComplete'] and report['status'] == 'retrieved_without_reference'
    assert transport.calls[-1][1] == '/api/logout'
    serialized = json.dumps(report)
    assert 'synthetic-reference-name-never-exported' not in serialized
    assert TOKEN not in serialized and CREDENTIALS['Password'] not in serialized
    assert '_publicReference' not in serialized and 'readyForDelivery' not in serialized


def test_optional_nulls_are_skipped_without_approving_them():
    transport = Transport()
    report = run(transport)
    assert not {'customers', 'origins'} & {resource for resource, _, _ in transport.catalog_calls}
    assert any('Null foreign keys are not looked up or approved' in line for line in report['limitations'])


def test_nonnull_optional_customer_and_origin_are_looked_up():
    def production(resource, params):
        item = row(resource, params['startDate'])
        if resource == 'contQuayVolumesCB':
            item.update(shipOperatorId='50', containerOperatorId='50', originId='60')
        return page([item], params)
    transport = Transport(production=production)
    assert run(transport)['publicReferences']['allCompleteWindowsPassed']
    assert [(r, i) for r, i, _ in transport.catalog_calls if r in {'customers', 'origins'}] == [
        ('customers', '50'), ('origins', '60')]


def test_container_size_uses_the_specified_exact_id_list_filter_and_checks_final_page():
    transport = Transport()
    run(transport)
    calls = [(path, params) for _, path, params in transport.calls if path.startswith('/api/containerSize')]
    assert calls == [('/api/containerSize', {'companyId': 'CNT', 'page': 1, 'limit': 100, 'containerSizeId': '20'})]


def test_parent_and_parent_type_are_checked_once_and_failure_propagates_to_bulk_windows():
    def catalog(resource, identity, params):
        if resource == 'cargoType' and identity == '7':
            return response({'data': [], 'code': '0', 'message': 'do-not-save-server-message'}, 404,
                            {'X-Error-Code': 'NOT_FOUND'})
        item = master(resource, identity, parent='3' if resource == 'cargoCategory' and identity == '2' else None)
        if resource == 'cargoCategory' and identity == '3':
            item['cargoTypeId'] = '7'
        result = page([item], params)
        result.headers['Cache-Control'] = 'no-store'
        return result
    transport = Transport(catalog=catalog)
    report = run(transport)
    assert len(transport.catalog_calls) == 8
    assert report['publicReferences']['blockedWindows'] == 2
    assert all(window(report, r)['publicReferences']['status'] == 'passed' for r in verify.RESOURCES if r.startswith('cont'))
    for resource in ('bulkQuayVolumesCB', 'bulkGateVolumesCB'):
        sample = window(report, resource)['publicReferences']['failureSamples'][0]
        assert sample == {'resource': 'cargoType', 'nativeId': '7', 'error': 'HTTP_ERROR', 'http': 404, 'serverErrorCode': 'NOT_FOUND'}
    assert 'do-not-save-server-message' not in json.dumps(report)


@pytest.mark.parametrize('status,code', [(503, 'SOURCE_ID_CONFLICT'), (502, 'private-host-token-error')])
def test_unresolvable_ship_does_not_change_retrieval_comparison_or_original_status(status, code):
    def catalog(resource, identity, params):
        if resource == 'shipDetails':
            return response({'data': [], 'code': '0', 'message': CREDENTIALS['Password']}, status,
                            {'X-Error-Code': code, 'Retry-After': '5', 'Authorization': TOKEN})
        return catalog_page(resource, identity, params)
    report = run(Transport(catalog=catalog), reference=reference())
    assert report['retrievalComplete'] and report['suppliedComparisonsMatched']
    assert report['status'] == 'supplied_references_matched'
    assert report['publicReferences']['blockedWindows'] == 2
    sample = window(report, 'contQuayVolumesCB')['publicReferences']['failureSamples'][0]
    assert sample['http'] == status and sample['retryAfterSeconds'] == 5
    assert sample.get('serverErrorCode') == (code if status == 503 else None)
    serialized = json.dumps(report)
    assert CREDENTIALS['Password'] not in serialized and TOKEN not in serialized and 'private-host-token-error' not in serialized


@pytest.mark.parametrize('mutation,expected', [
    (lambda r: r.headers.update({'X-Page': '2'}), 'INVALID_REFERENCE_PAGINATION'),
    (lambda r: r.headers.update({'X-Limit': '20'}), 'INVALID_REFERENCE_PAGINATION'),
    (lambda r: r.headers.update({'X-Has-Next': 'true'}), 'INVALID_REFERENCE_PAGINATION'),
    (lambda r: r.headers.pop('Cache-Control'), 'INVALID_REFERENCE_PAGINATION'),
    (lambda r: r.headers.pop('X-Total-Count'), 'INVALID_PAGINATION'),
    (lambda r: r.headers.update({'X-Total-Count': '2'}), 'REFERENCE_NOT_UNIQUE'),
    (lambda r: r.headers.pop('X-Source-Read-At'), 'SOURCE_READ_TIME_MISSING'),
    (lambda r: r.headers.update({'X-Snapshot-Id': 'bad'}), 'SNAPSHOT_HEADER_INVALID'),
    (lambda r: setattr(r, 'body', b'{not-json'), 'INVALID_JSON'),
])
def test_corrupt_catalog_metadata_never_passes_or_follows_an_unexpected_page(mutation, expected):
    def catalog(resource, identity, params):
        result = catalog_page(resource, identity, params)
        if resource == 'containerSize':
            mutation(result)
        return result
    transport = Transport(catalog=catalog)
    report = run(transport)
    assert window(report, 'contQuayVolumesCB')['publicReferences']['failureSamples'][0]['error'] == expected
    assert sum(r == 'containerSize' for r, _, _ in transport.catalog_calls) == 1


@pytest.mark.parametrize('change,expected', [
    (lambda body: body['data'][0].update(shipId='999'), 'REFERENCE_ID_MISMATCH'),
    (lambda body: body['data'][0].update(shipId='10 '), 'REFERENCE_ID_MISMATCH'),
    (lambda body: body['data'][0].update(privateConnection='secret'), 'INVALID_REFERENCE_ROW'),
    (lambda body: body['data'].append(deepcopy(body['data'][0])), 'REFERENCE_NOT_UNIQUE'),
    (lambda body: body.update(code='0'), 'INVALID_PUBLIC_ENVELOPE'),
])
def test_wrong_identity_or_invalid_catalog_body_fails_without_persisting_rows(change, expected):
    def catalog(resource, identity, params):
        result = catalog_page(resource, identity, params)
        if resource == 'shipDetails':
            body = json.loads(result.body)
            change(body)
            result.body = json.dumps(body).encode()
        return result
    report = run(Transport(catalog=catalog))
    assert window(report, 'contQuayVolumesCB')['publicReferences']['failureSamples'][0]['error'] == expected
    assert 'privateConnection' not in json.dumps(report)


def test_empty_exact_size_lookup_is_missing_not_successful_empty_catalog():
    def catalog(resource, identity, params):
        if resource == 'containerSize':
            result = page([], params)
            result.headers['Cache-Control'] = 'no-store'
            return result
        return catalog_page(resource, identity, params)
    report = run(Transport(catalog=catalog))
    assert window(report, 'contGateVolumesCB')['publicReferences']['failureSamples'][0]['error'] == 'REFERENCE_NOT_FOUND'


@pytest.mark.parametrize('self_cycle', [True, False])
def test_cargo_parent_cycle_blocks_every_dependent_window_without_recursive_loop(self_cycle):
    def catalog(resource, identity, params):
        parent = None
        if resource == 'cargoCategory':
            parent = '2' if self_cycle or identity == '3' else '3'
        return catalog_page(resource, identity, params, parent=parent)
    transport = Transport(catalog=catalog)
    report = run(transport)
    assert report['publicReferences']['blockedWindows'] == 2
    assert len(transport.catalog_calls) == (6 if self_cycle else 7)
    assert all(s['error'] == 'REFERENCE_PARENT_CYCLE' for s in window(report, 'bulkQuayVolumesCB')['publicReferences']['failureSamples'])


def test_long_ancestor_chain_uses_bounded_iterative_closure():
    def catalog(resource, identity, params):
        parent = str(int(identity) + 1) if resource == 'cargoCategory' and int(identity) < 1050 else None
        return catalog_page(resource, identity, params, parent=parent)
    transport = Transport(catalog=catalog)
    report = run(transport, reference_limit=1100)
    assert report['publicReferences']['allCompleteWindowsPassed']
    assert len(transport.catalog_calls) == 1054


def test_distinct_lookup_limit_blocks_references_without_changing_complete_production():
    transport = Transport()
    report = run(transport, reference_limit=2)
    assert len(transport.catalog_calls) <= 2
    assert report['retrievalComplete'] and report['status'] == 'retrieved_without_reference'
    assert report['publicReferences']['limitReached'] and not report['publicReferences']['allCompleteWindowsPassed']
    assert all(item['totals'] is not None for item in report['windows'])
    assert all(len(item['publicReferences']['failureSamples']) <= 10 for item in report['windows'])


def test_limit_reached_at_parent_blocks_bulk_but_does_not_block_independent_container_windows():
    def catalog(resource, identity, params):
        return catalog_page(resource, identity, params, parent='3' if resource == 'cargoCategory' else None)
    transport = Transport(catalog=catalog)
    report = run(transport, reference_limit=6)
    assert len(transport.catalog_calls) == 6 and report['publicReferences']['limitReached']
    assert report['publicReferences']['blockedWindows'] == 2
    assert window(report, 'contQuayVolumesCB')['publicReferences']['status'] == 'passed'
    assert window(report, 'bulkQuayVolumesCB')['publicReferences']['failureSamples'][0]['error'] == 'REFERENCE_LIMIT_EXCEEDED'


def test_partial_production_page_does_not_make_any_of_its_unique_reference_requests(monkeypatch):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 1)
    def production(resource, params):
        if resource == 'contQuayVolumesCB':
            if params['page'] == 1:
                return page([row(resource, identity='777')], params, total=2)
            return response({'data': [], 'code': '0', 'message': 'blocked'}, 503,
                            {'X-Error-Code': 'CONTAINER_SIZE_UNMAPPED'})
        return page([row(resource, params['startDate'])], params)
    transport = Transport(production=production)
    report = run(transport)
    assert not report['retrievalComplete']
    assert window(report, 'contQuayVolumesCB')['publicReferences']['status'] == 'skipped_incomplete'
    assert not any(identity == '777' for _, identity, _ in transport.catalog_calls)
    assert report['publicReferences']['skippedIncompleteWindows'] == 1


def test_all_incomplete_skips_all_reference_requests_but_empty_complete_windows_need_none():
    failed = Transport(production=lambda r, p: response({'data': [], 'code': '0', 'message': 'blocked'}, 503))
    blocked = run(failed)
    assert not blocked['publicReferences']['allCompleteWindowsPassed']
    assert blocked['publicReferences']['completeWindowsChecked'] == 0 and failed.catalog_calls == []
    empty = Transport(production=lambda r, p: page([], p))
    checked = run(empty)
    assert checked['publicReferences']['allCompleteWindowsPassed'] and empty.catalog_calls == []


def test_arbitrary_identifier_is_url_encoded_and_not_copied_to_failure_samples():
    identity = '../' + TOKEN + '?password=' + CREDENTIALS['Password']
    def production(resource, params):
        return page([row(resource, params['startDate'], identity=identity)], params)
    def catalog(resource, value, params):
        if resource == 'handlingMethodList':
            assert value == identity
            raise verify.VerificationError(CREDENTIALS['Password'])
        return catalog_page(resource, value, params)
    transport = Transport(production=production, catalog=catalog)
    report = run(transport)
    paths = [p for _, p, _ in transport.calls if p.startswith('/api/handlingMethodList/')]
    assert len(paths) == 1 and '%2F' in paths[0] and '%3F' in paths[0] and '?' not in paths[0]
    serialized = json.dumps(report)
    assert TOKEN not in serialized and CREDENTIALS['Password'] not in serialized
    assert report['publicReferences']['failureSamples'][0]['error'] == 'REFERENCE_CHECK_FAILED'


@pytest.mark.parametrize('options', [
    {'reference_limit': 0}, {'reference_limit': 2001}, {'reference_limit': True},
    {'reference_limit': '200'}, {'verify_references': 'yes'},
])
def test_bad_check_options_fail_before_login(options):
    transport = Transport()
    with pytest.raises(verify.VerificationError, match='INVALID_REFERENCE_CHECK_OPTIONS'):
        run(transport, **options)
    assert transport.calls == []


def test_cli_returns_two_for_reference_blocker_while_retaining_old_status(tmp_path, monkeypatch, capsys):
    transport = Transport(catalog=lambda r, i, p: response({'data': [], 'code': '0', 'message': 'blocked'}, 503))
    monkeypatch.setattr(verify, 'HttpTransport', lambda url: transport)
    monkeypatch.setattr(verify.sys, 'stdin', io.StringIO(json.dumps(CREDENTIALS)))
    output = tmp_path / 'verified.json'
    result = verify.main(['--base-url', 'https://example.com', '--start', '2026-01-01', '--end', '2026-01-31',
        '--production-scope', 'nghe_tinh', '--credentials-stdin', '--verify-references', '--reference-limit', '20',
        '--output', str(output)])
    assert result == 2
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['status'] == 'retrieved_without_reference' and report['retrievalComplete']
    summary = json.loads(capsys.readouterr().out)
    assert summary['publicReferencesPassed'] is False


def test_real_router_detail_and_size_filter_headers_match_checker_contract(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.corporate_api.auth import MachineStore
    from backend.corporate_api.live import LiveReader
    from backend.corporate_api.reconciliation import NULL_FIELDS
    from backend.corporate_api.router import router
    from test_corporate_delivery_http import AppTransport, BoundarySource
    from test_corporate_live import Source

    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    profile = {'approved': True, 'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy'],
               'date_basis': 'shiftDate', 'production_scope': 'all_activity', 'quay_selection': 'source_statistics',
               'gate_selection': 'vessel_type', 'cargo_catalog_source': 'native_groups',
               'container_size_source': 'native_cargo',
               'accepted_null_fields': {key: list(fields) for key, fields in NULL_FIELDS.items()}}
    source = Source(BoundarySource().rows)
    reader = LiveReader(profile=profile, extract_fn=source)
    machines = MachineStore(tmp_path / 'machines.sqlite3')
    machines.create_client(CREDENTIALS['Username'], CREDENTIALS['Password'], company_ids=['CNT'], resources=list(verify.MODELS))
    app = FastAPI()
    app.state.corporate_auth, app.state.corporate_exports = machines, reader
    app.include_router(router)
    try:
        with TestClient(app) as client:
            report = verify.verify(AppTransport(client), dict(CREDENTIALS), start=date(2026, 9, 16),
                end=date(2026, 9, 17), production_scope='all_activity', verify_references=True)
        assert report['retrievalComplete'] and report['publicReferences']['allCompleteWindowsPassed']
        assert report['publicReferences']['httpRequests'] > 0 and report['logoutHttp'] == 200
        assert set(p.name for p in tmp_path.iterdir()) <= {'machines.sqlite3', 'machines.sqlite3-wal', 'machines.sqlite3-shm'}
    finally:
        reader.close()
