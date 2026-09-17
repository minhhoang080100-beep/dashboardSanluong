"""Exercise real request models and plan mutations; only temporary SQLite state."""
from fastapi.testclient import TestClient
import pytest

from backend.control_api import get_repository, get_store
from backend.control_store import ControlError, ControlStore
from backend.main import app
from test_control_store import state


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


def test_four_period_http_create_edit_approve_and_history_preserve_exact_values(plan_api):
    client, store, actor, headers = plan_api
    for period in [{'period_type':'month','month':'2026-09'}, {'period_type':'quarter','quarter':'2026-Q3'},
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
    assert store.list_plans(actor)['total']==4


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
