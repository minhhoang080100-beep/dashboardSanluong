"""Calendar targets, source coverage and frozen approvals; synthetic data only."""
from copy import deepcopy
from datetime import date
from io import BytesIO

from openpyxl import load_workbook
import pytest

from backend.control_store import ControlError, ControlStore, plan_period, throughput_progress_item
from backend.workbook_io import parse_plan_workbook, report_workbook
from test_control_store import state, account
from test_workbook_io import make_plan, month_row


def target(store, actor, terminal='all', period_type='year', amount=100, **period):
    row = store.create_plan(actor, terminal=terminal, period_type=period_type, metric='tonnage', amount=amount,
                            reference='SYNTHETIC APPROVED TARGET', **(period or {'year': 2026}))
    return store.approve_plan(actor, row['id'], row['revision'])


def report(terminal='all', start='2026-01-01', end='2026-01-13', value=50, status='ready', scope='nghe_tinh'):
    return {'meta': {'report_id': 'synthetic-snapshot', 'berth_rule_version': 'initial-berth-v1',
                     'filters': {'terminal': terminal, 'start_date': start, 'end_date': end, 'production_scope': scope}},
            'overview': {'total_tonnage': value, 'tonnage_status': status}}


@pytest.mark.parametrize('value,key,start,end', [
    ({'period_type':'month','month':'2024-02'}, '2024-02', '2024-02-01', '2024-02-29'),
    ({'period_type':'quarter','quarter':'2026-Q1'}, '2026-Q1', '2026-01-01', '2026-03-31'),
    ({'period_type':'quarter','quarter':'2026-Q4'}, '2026-Q4', '2026-10-01', '2026-12-31'),
    ({'period_type':'year','year':2024}, '2024', '2024-01-01', '2024-12-31'),
    ({'period_type':'custom','start_date':'2026-12-15','end_date':'2027-01-15'}, '2026-12-15/2027-01-15','2026-12-15','2027-01-15'),
])
def test_full_calendar_bounds_are_independent_of_today(value,key,start,end):
    result = plan_period(value)
    assert (result[0],result[1].isoformat(),result[2].isoformat()) == (key,start,end)


@pytest.mark.parametrize('value', [
    {'period_type':'quarter','quarter':'2026-Q5'}, {'period_type':'quarter','quarter':'2026-Q1','month':'2026-01'},
    {'period_type':'year','year':True}, {'period_type':'year','year':2100},
    {'period_type':'custom','start_date':'2026-02-30','end_date':'2026-03-01'},
    {'period_type':'custom','start_date':'2026-02-01','end_date':'2026-01-01'},
    {'period_type':'custom','start_date':'2024-01-01','end_date':'2025-01-01'},
])
def test_invalid_or_mixed_period_identity_is_rejected(value):
    with pytest.raises(ControlError):
        plan_period(value)


@pytest.mark.parametrize('actual,band,achieved', [(0,'red',False),(19.99,'red',False),(20,'orange',False),
    (39.99,'orange',False),(40,'yellow',False),(59.99,'yellow',False),(60,'light_green',False),
    (79.99,'light_green',False),(80,'dark_green',False),(99.99,'dark_green',False),(100,'dark_green',True),(125,'dark_green',True)])
def test_progress_bands_and_achievement_have_exact_boundaries(actual,band,achieved):
    period = {'period_type':'year','period_key':'2026','period_start':'2026-01-01','period_end':'2026-12-31'}
    plans = [{'amount_decimal':'100'}]
    result = throughput_progress_item(period,plans,actual,'ready','company')
    assert result['band'] == band and result['achieved'] == achieved
    partial = throughput_progress_item(period,plans,actual,'partial','company')
    assert partial['band'] == band and partial['achieved'] is False
    assert partial['completion_percent'] is None and partial['provisional_completion_percent'] == actual


@pytest.mark.parametrize('actual,coverage,amount,status', [(None,'unavailable',100,'unavailable'),
    (40,'unavailable',100,'unavailable'),(-1,'ready',100,'negative_actual'),(40,'ready',0,'zero_target')])
def test_invalid_actual_or_zero_target_does_not_create_percentage(actual,coverage,amount,status):
    period = {'period_type':'year','period_key':'2026','period_start':'2026-01-01','period_end':'2026-12-31'}
    value = throughput_progress_item(period,[{'amount_decimal':str(amount)}],actual,coverage,'company')
    assert value['status'] == status
    assert value['completion_percent'] is None and value['provisional_completion_percent'] is None
    assert value['band'] is None and value['achieved'] is False


def test_company_target_precedence_complete_terminal_sum_and_overlapping_periods(state):
    store, admin, _, _ = state
    left = target(store, admin, terminal='cua_lo', amount=100)
    one = store.throughput_progress(admin, report())['items'][0]
    assert one['status'] == 'missing_plan' and one['target'] is None
    right = target(store, admin, terminal='ben_thuy', amount=200)
    summed = store.throughput_progress(admin, report())['items'][0]
    assert summed['target'] == 300 and summed['target_source'] == 'terminals'
    company = target(store, admin, amount=400)
    target(store, admin, period_type='month', month='2026-01', amount=20)
    target(store, admin, period_type='quarter', quarter='2026-Q1', amount=200)
    target(store, admin, period_type='custom', start_date='2026-01-01', end_date='2026-04-30', amount=300)
    items = store.throughput_progress(admin, report())['items']
    assert len(items) == 4  # UI must select one; never combine overlapping targets.
    annual = next(item for item in items if item['period_type'] == 'year')
    assert annual['target'] == 400 and annual['plans'][0]['id'] == company['id']
    assert store.throughput_progress(admin, report(terminal='cua_lo'))['items'][0]['plans'][0]['id'] == left['id']
    assert store.throughput_progress(admin, report(start='2026-01-02'))['items'] == []
    assert not store.throughput_progress(admin, report(scope='vietsun'))['eligible']
    after_month = store.throughput_progress(admin, report(end='2026-02-01'))['items']
    assert {item['period_type'] for item in after_month} == {'quarter','year','custom'}


def test_approved_targets_are_available_for_navigation_without_mismatched_actuals(state):
    store, admin, _, _ = state
    annual = target(store,admin,amount=1000000000)
    quarter = target(store,admin,period_type='quarter',quarter='2026-Q3',amount=1000)
    target(store,admin,period_type='month',month='2026-09',amount=100)
    store.create_plan(admin,terminal='all',period_type='year',year=2027,metric='tonnage',amount=500,reference='DRAFT only')
    september = report(start='2026-09-01',end='2026-09-17',value=50)
    progress = store.throughput_progress(admin,september)
    assert [item['period_type'] for item in progress['items']] == ['month']
    assert {item['period_type'] for item in progress['available_periods']} == {'month','quarter','year'}
    for candidate in progress['available_periods']:
        assert candidate['terminal'] == 'all'
        assert not {'actual','actual_status','completion_percent','provisional_completion_percent'} & set(candidate)
    available_annual = next(item for item in progress['available_periods'] if item['period_type']=='year')
    assert available_annual['target'] == 1000000000 and available_annual['plans'][0]['id'] == annual['id']
    assert available_annual['start_date'] == '2026-01-01'
    # Once a fresh report starts at the selected target's start, actuals may be paired.
    annual_report = report(start='2026-01-01',end='2026-09-17',value=4000)
    matched = store.throughput_progress(admin,annual_report)['items']
    assert len(matched)==1 and matched[0]['actual']==4000 and matched[0]['plans'][0]['id']==annual['id']
    # Empty navigation under a different production scope, no leaking company targets.
    assert store.throughput_progress(admin,report(scope='vietsun'))['available_periods']==[]
    assert store.throughput_progress(admin,report(terminal='cua_lo'))['available_periods']==[]


def test_period_draft_lifecycle_and_scope_remain_protected(state):
    store, admin, _, _ = state
    manager = account(store, admin, role='manager')['user']
    with pytest.raises(ControlError) as failure:
        target(store, manager)
    assert failure.value.status_code == 403
    draft = store.create_plan(admin, terminal='all', period_type='quarter', quarter='2026-Q3', metric='tonnage', amount=50, reference='Synthetic')
    changed = store.update_plan(admin, draft['id'], 1, period_type='custom', quarter=None,
                                start_date='2026-07-01', end_date='2026-12-31')
    assert changed['period_key'] == '2026-07-01/2026-12-31'
    with pytest.raises(ControlError) as failure:
        store.approve_plan(admin, draft['id'], 1)
    assert failure.value.code == 'PLAN_CONFLICT'
    approved = store.approve_plan(admin, draft['id'], 2)
    with pytest.raises(ControlError):
        store.update_plan(admin, draft['id'], approved['revision'], amount=99)
    assert [event['action'] for event in store.get_plan(admin,draft['id'])['history']] == ['created','updated','approved']
    assert store.list_plans(manager)['items'] == []
    assert store.list_plans(admin,period_type='custom',start_date='2026-07-01',end_date='2026-12-31')['total'] == 1


def test_closed_period_targets_and_excel_stay_frozen_after_new_approval(state):
    store, admin, _, _ = state
    first = target(store, admin, amount=100)
    snapshot = report(status='partial',value=120)
    # Nonmonthly report still captures period targets from the closure transaction.
    snapshot['meta']['filters']['end_date'] = '2026-03-13'
    closed = store.close_report(admin,'all','2026-01-01','2026-03-13',snapshot,[],planning_actuals={})
    target(store, admin, amount=200)
    saved = store.get_closed_report(admin,closed['id'])
    frozen = saved['report']['throughput_progress']['items'][0]
    assert frozen['target'] == 100 and frozen['plans'][0]['id'] == first['id']
    assert frozen['completion_percent'] is None and frozen['provisional_completion_percent'] == 120
    assert frozen['achieved'] is False
    book = load_workbook(BytesIO(report_workbook(saved['report'],[],planning=saved['planning'])),data_only=True)
    sheet = book['Mục tiêu thông qua']
    assert sheet['E2'].value == 100 and sheet['G2'].value is None and sheet['H2'].value == 120
    assert sheet['I2'].value == 'Chưa xác nhận đạt'


def test_workbook_new_periods_and_legacy_eight_column_import():
    from backend.workbook_io import PLAN_COLUMNS
    legacy = parse_plan_workbook(make_plan([month_row()[:8]],headers=PLAN_COLUMNS[:8]))
    assert legacy['valid'] and legacy['rows'][0]['month'] == '2026-09'
    rows = [month_row(terminal='all',period_type='quarter',month=None,quarter='2026-Q3'),
            month_row(period_type='year',month=None,year=2026),
            month_row(period_type='custom',month=None,start_date=date(2026,9,1),end_date=date(2026,12,31))]
    preview = parse_plan_workbook(make_plan(rows))
    assert preview['valid'] and len(preview['rows']) == 3
    assert preview['rows'][2]['end_date'] == '2026-12-31'
