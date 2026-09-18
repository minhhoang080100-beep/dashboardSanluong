"""Exercise real request models and plan mutations; only temporary SQLite state."""
from fastapi.testclient import TestClient
import pytest

from backend.control_api import get_repository, get_store
from backend.control_store import ControlError, ControlStore
from backend.main import app
from test_control_store import state, account


@pytest.fixture
def plan_api(state):
    store, actor, session, _ = state
    class NoSource:
        def validate_voyages(self, *args):
            raise AssertionError('Calendar plans must not query TOS')
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_repository] = lambda: NoSource()
    try:
        with TestClient(app) as client:
            yield client, store, actor, {'Authorization':'Bearer '+session['token']}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def payload(**period):
    return {'terminal':'all','metric':'tonnage','amount':'150000.123456',
            'reference':'SYNTHETIC HTTP ONLY','note':'',**period}


def test_calendar_period_http_create_edit_approve_and_history_preserve_exact_values(plan_api):
    client, store, actor, headers = plan_api
    for period in [{'period_type':'week','week':'2026-W38'}, {'period_type':'month','month':'2026-09'}, {'period_type':'quarter','quarter':'2026-Q3'},
                   {'period_type':'year','year':2026}, {'period_type':'custom','start_date':'2026-09-01','end_date':'2026-09-17'}]:
        created = client.post('/api/plans',headers=headers,json=payload(**period))
        assert created.status_code==201,created.text
        draft = created.json()
        assert draft['status']=='draft' and draft['amount_decimal']=='150000.123456'
        path = f"/api/plans/{draft['id']}"
        changed = client.patch(path,headers=headers,json={'expected_revision':1,'amount':'150001.123456','note':'Correction'})
        assert changed.status_code==200,changed.text
        assert client.post(path+'/approve',headers=headers,json={'expected_revision':1}).status_code==409
        approved = client.post(path+'/approve',headers=headers,json={'expected_revision':2})
        assert approved.status_code==200,approved.text
        assert approved.json()['amount_decimal']=='150001.123456'
        assert client.patch(path,headers=headers,json={'expected_revision':3,'amount':'1'}).status_code==409
        history = client.get(path,headers=headers).json()['history']
        assert [item['action'] for item in history]==['created','updated','approved']
        assert history[-1]['snapshot']['amount_decimal']=='150001.123456'
    assert store.list_plans(actor)['total']==5


def test_weekly_http_import_listing_rekey_and_validation(plan_api):
    client, store, actor, headers = plan_api
    records = [payload(period_type='week', week='2026-W38'), payload(period_type='week', week='2030-W01'),
               payload(period_type='month', month='2026-09')]
    imported = client.post('/api/plans/import', headers=headers, json={'rows':records})
    assert imported.status_code == 201, imported.text
    drafts = imported.json()['items']
    future = drafts[1]
    assert future['week'] == future['period_key'] == '2030-W01'
    assert future['period_start'] == '2029-12-31' and future['period_end'] == '2030-01-06'
    assert all(future[field] is None for field in ['month','quarter','year','start_date','end_date','voyage_id'])
    assert client.get('/api/plans?period_type=week', headers=headers).json()['total'] == 2
    filtered = client.get('/api/plans?period_type=week&week=2026-W38', headers=headers)
    assert filtered.status_code == 200 and [row['id'] for row in filtered.json()['items']] == [drafts[0]['id']]
    assert client.get('/api/plans?week=2030-W01', headers=headers).json()['total'] == 1
    assert client.get('/api/plans?week=2021-W53', headers=headers).status_code == 422
    for week in ['2021-W53', '2026-W00', '2026-W54', '2026-W1', None]:
        response = client.post('/api/plans', headers=headers, json=payload(period_type='week', week=week))
        assert response.status_code == 422, response.text
    for extra in [{'month':'2026-09'}, {'year':2026}, {'start_date':'2026-09-14'}, {'voyage_id':1}]:
        assert client.post('/api/plans', headers=headers, json=payload(period_type='week', week='2026-W38', **extra)).status_code == 422
    # Failed batches are atomic, and moving between period types must clear the old field.
    failed = client.post('/api/plans/import', headers=headers, json={'rows':[records[0], payload(period_type='week', week='2021-W53')]})
    assert failed.status_code == 422 and store.list_plans(actor)['total'] == 3
    path = f"/api/plans/{drafts[2]['id']}"
    change = {'expected_revision':1, 'period_type':'week', 'week':'2026-W38'}
    assert client.patch(path, headers=headers, json=change).status_code == 422
    moved = client.patch(path, headers=headers, json={**change,'month':None})
    assert moved.status_code == 200, moved.text
    assert moved.json()['week'] == '2026-W38' and moved.json()['version'] == 2
    assert client.post(path+'/approve', headers=headers, json={'expected_revision':2}).status_code == 200
    assert client.get('/api/plans?week=2026-W38&status=approved', headers=headers).json()['total'] == 1


def test_http_amount_precision_zeroes_and_invalid_formats_are_consistent(plan_api):
    client, store, actor, headers = plan_api
    base = payload(period_type='month',month='2026-09')
    for entered,expected in [('1.0000000','1'),('150000.123456','150000.123456'),
                             ('999999999999.123456','999999999999.123456'),('0.0000000','0')]:
        response = client.post('/api/plans',headers=headers,json={**base,'amount':entered})
        assert response.status_code==201,response.text
        assert response.json()['amount_decimal']==expected
    count = store.list_plans(actor)['total']
    for amount in ['0.0000001','150.000,5',True,'NaN','Infinity','1000000000000.000001']:
        response = client.post('/api/plans',headers=headers,json={**base,'amount':amount})
        assert response.status_code==422,response.text
        assert response.json()['detail']
    assert store.list_plans(actor)['total']==count
    # Never round away a seventh significant decimal even beyond Decimal context precision.
    for amount in ['1.123456000000000000000000000000001','1E-1000000']:
        with pytest.raises(ControlError):
            ControlStore.validate_plan({**base,'amount':amount})


def test_http_bulk_custom_dates_and_inactive_fields_are_not_silently_reinterpreted(plan_api):
    client, store, actor, headers = plan_api
    records = [payload(period_type='year',year=2026),payload(period_type='custom',start_date='2026-09-01',end_date='2026-12-31')]
    imported = client.post('/api/plans/import',headers=headers,json={'rows':records})
    assert imported.status_code==201 and imported.json()['count']==2
    draft = imported.json()['items'][0]
    path = f"/api/plans/{draft['id']}"
    invalid = client.patch(path,headers=headers,json={'expected_revision':1,'period_type':'quarter','quarter':'2026-Q3'})
    assert invalid.status_code==422
    fixed = client.patch(path,headers=headers,json={'expected_revision':1,'period_type':'quarter','year':None,'quarter':'2026-Q3'})
    assert fixed.status_code==200 and fixed.json()['period_key']=='2026-Q3'
    assert client.post(path+'/cancel',headers=headers,json={'expected_revision':2,'note':'Synthetic cancellation'}).status_code==200
    assert client.post(path+'/approve',headers=headers,json={'expected_revision':3}).status_code==409
    assert store.list_plans(actor)['total']==2


def test_approved_annual_target_is_discoverable_from_month_report_over_http(plan_api):
    from backend.integration import get_reporting
    from test_target_periods import report
    client, _, _, headers = plan_api
    created = client.post('/api/plans',headers=headers,json=payload(period_type='year',year=2026)).json()
    assert client.post(f"/api/plans/{created['id']}/approve",headers=headers,json={'expected_revision':1}).status_code==200
    snapshot = report(start='2026-09-01',end='2026-09-17')
    class FrozenReport:
        def get_report_snapshot(self,identifier):
            assert identifier=='synthetic-snapshot'
            return snapshot
    app.dependency_overrides[get_reporting] = lambda: FrozenReport()
    response = client.get('/api/reports/synthetic-snapshot/throughput-progress',headers=headers)
    assert response.status_code==200,response.text
    data = response.json()
    assert data['items']==[]  # September actual must not be divided by the annual target.
    available = data['available_periods']
    assert len(available)==1 and available[0]['key']=='year:2026'
    assert available[0]['start_date']=='2026-01-01' and available[0]['end_date']=='2026-12-31'
    assert available[0]['plans'][0]['id']==created['id'] and 'actual' not in available[0]


def test_unfiltered_http_listing_spans_all_periods_years_versions_and_authorized_scopes(plan_api):
    client, store, actor, headers = plan_api
    active = []
    # Seed only the temporary control store; voyage listing must not consult TOS.
    old_month = payload(period_type='month', month='2025-12')
    for status in ['approved', 'approved', 'draft']:
        item = store.create_plan(actor, **old_month)
        if status == 'approved':
            item = store.approve_plan(actor, item['id'], item['revision'])
        active.append(item)
    mixed = [
        ('cua_lo', {'period_type':'quarter', 'quarter':'2026-Q3'}, 'cancelled'),
        ('ben_thuy', {'period_type':'year', 'year':2027}, 'draft'),
        ('all', {'period_type':'custom', 'start_date':'2026-09-01', 'end_date':'2026-09-17'}, 'approved'),
        ('cua_lo', {'period_type':'voyage', 'voyage_id':101}, 'draft'),
    ]
    for terminal, period, status in mixed:
        item = store.create_plan(actor, **{**payload(**period), 'terminal':terminal})
        if status == 'approved':
            item = store.approve_plan(actor, item['id'], item['revision'])
        if status == 'cancelled':
            item = store.cancel_plan(actor, item['id'], item['revision'], 'Synthetic cancelled version')
        active.append(item)
    removed = store.create_plan(actor, **payload(period_type='year', year=2024))
    store.delete_plan(actor, removed['id'], removed['revision'])

    response = client.get('/api/plans?terminal=all', headers=headers)
    assert response.status_code == 200, response.text
    listed = response.json()
    expected_ids = sorted((item['id'] for item in active), reverse=True)
    assert listed['total'] == 7 and listed['page'] == 1 and listed['page_size'] == 50
    assert [item['id'] for item in listed['items']] == expected_ids
    assert {item['period_type'] for item in listed['items']} == {'month', 'quarter', 'year', 'custom', 'voyage'}
    assert {item['status'] for item in listed['items']} == {'draft', 'approved', 'cancelled'}
    assert {item['terminal'] for item in listed['items']} == {'all', 'cua_lo', 'ben_thuy'}
    assert any(item['year'] == 2027 for item in listed['items'])
    month_versions = sorted((item for item in listed['items'] if item['period_type'] == 'month'), key=lambda item:item['version'])
    assert [item['version'] for item in month_versions] == [1, 2, 3]
    assert [item['is_current'] for item in month_versions] == [False, True, False]
    assert all(item['month'] == '2025-12' for item in month_versions)
    assert client.get('/api/plans', headers=headers).json() == listed

    pages = [client.get(f'/api/plans?terminal=all&page_size=3&page={page}', headers=headers).json()
             for page in [1, 2, 3]]
    assert all(page['total'] == 7 and page['page_size'] == 3 for page in pages)
    assert [item['id'] for page in pages for item in page['items']] == expected_ids
    assert client.get('/api/plans?terminal=all&page=99', headers=headers).json()['items'] == []
    assert client.get('/api/plans?page_size=101', headers=headers).status_code == 422
    included = client.get('/api/plans?terminal=all&include_deleted=true', headers=headers).json()
    assert included['total'] == 8 and included['items'][0]['id'] == removed['id']
    assert included['items'][0]['is_deleted'] is True

    reader = account(store, actor, terminals=['cua_lo'], username='mixed-plan-reader')
    reader_headers = {'Authorization':'Bearer '+reader['token']}
    scoped = client.get('/api/plans?terminal=all&include_deleted=true', headers=reader_headers)
    assert scoped.status_code == 200
    expected_scoped = [item['id'] for item in reversed(active) if item['terminal'] == 'cua_lo']
    assert scoped.json()['total'] == 2
    assert [item['id'] for item in scoped.json()['items']] == expected_scoped
    assert client.get('/api/plans?terminal=ben_thuy', headers=reader_headers).status_code == 403
    assert client.get('/api/plans?terminal=all').status_code == 401
