"""Synthetic plan governance workflows. Every API request is intercepted; no live writes."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_auth_support import install_auth_fixture
from browser_management import open_workspace
from browser_smoke import fixture
from backend.control_store import milestone_pace, plan_period, saved_plan_period, throughput_progress_item
from playwright.sync_api import expect, sync_playwright


def plan_values(body):
    key, _, _ = plan_period(body)
    return {**body, **saved_plan_period(body['period_type'], key),
            'amount': float(body['amount']), 'amount_decimal': str(body['amount'])}


def setup(page, role='manager', permissions=None):
    page.clock.set_fixed_time(datetime(2026, 9, 18, 5, tzinfo=timezone.utc))
    auth = install_auth_fixture(page, role=role)
    auth['user']['plan_permissions'] = permissions if permissions is not None else []
    initial = {'start_date': '2026-09-01', 'end_date': '2026-09-18',
               'terminal': 'cua_lo', 'production_scope': 'nghe_tinh'}
    page.add_init_script(f"localStorage.setItem('port-report-filters-9001', JSON.stringify({json.dumps(initial)}));")
    base = plan_values({'terminal': 'cua_lo', 'period_type': 'month', 'month': '2026-09',
                        'metric': 'tonnage', 'amount': '2000', 'reference': 'SYNTHETIC', 'note': '', 'milestones': []})
    plans = [{**deepcopy(base), 'id': index, 'reference': f'VB-PAGE-{index:02}', 'status': 'draft',
              'version': index, 'revision': 1, 'is_current': False, 'created_by': 8000}
             for index in range(1, 29)]
    plans[0].update(status='approved', is_current=True, reference='VB-CURRENT')
    plans[1].update(status='approved', reference='VB-PREVIOUS')
    state = {'plans': plans, 'users': [auth['user']], 'calls': [], 'reports': {}, 'pace': False, 'errors': [], 'backup_status': 'stale'}
    page.on('pageerror', lambda error: state['errors'].append(str(error)))

    def respond(route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path.removeprefix('/api')
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        body = request.post_data_json if request.post_data else None
        state['calls'].append({'path': path, 'method': request.method, 'query': query, 'body': body})
        if path == '/auth/me':
            route.fulfill(json=auth['user'])
        elif path == '/dashboard':
            filters = {key: query[key] for key in initial}
            report = fixture(filters, cua_lo_tonnage=1000)
            report['meta'].update(report_id='synthetic-governance', source_read_at=report['meta']['generated_at'])
            if state['pace']:
                for row in report['daily_history']:
                    row['tonnage'] = 200 if row['date'] == '2026-09-01' else 800 if row['date'] == '2026-09-10' else 0
                    row['tonnage_status'] = 'ready' if row['tonnage'] else 'empty'
            state['reports']['synthetic-governance'] = report
            route.fulfill(json=report)
        elif path.endswith('/throughput-progress'):
            report = state['reports']['synthetic-governance']
            response = {'report_id': report['meta']['report_id'], 'period': report['meta']['filters'],
                        'production_scope': 'nghe_tinh', 'berth_rule_version': 'initial-berth-v1',
                        'eligible': True, 'items': [], 'available_periods': [], 'other_scope_periods': [], 'reason': None}
            if state['pace']:
                plan = {**deepcopy(base), 'id': 999, 'reference': 'SYNTHETIC-MILESTONE', 'version': 1,
                        'status': 'approved', 'is_current': True, 'milestones': [{'date': '2026-09-05', 'amount': '500'}]}
                item = throughput_progress_item(plan, [plan], report['overview']['total_tonnage'], report['overview']['tonnage_status'], 'terminal')
                item['pace'] = milestone_pace(report, plan, [plan])
                response['items'] = [item]
                response['available_periods'] = [{**{key: item[key] for key in ('key', 'period_type', 'period_key', 'start_date', 'end_date', 'target')}, 'terminal': 'cua_lo'}]
            route.fulfill(json=response)
        elif path == '/plans' and request.method == 'GET':
            rows = [deepcopy(plan) for plan in state['plans']]
            if query.get('status'):
                rows = [plan for plan in rows if plan['status'] == query['status']]
            if query.get('version_scope') == 'current':
                rows = [plan for plan in rows if plan['is_current']]
            elif query.get('version_scope') == 'previous':
                rows = [plan for plan in rows if plan['status'] == 'approved' and not plan['is_current']]
            if query.get('q'):
                rows = [plan for plan in rows if query['q'].casefold() in plan['reference'].casefold()]
            for plan in rows:
                plan['can_approve'] = 'approve' in auth['user']['plan_permissions'] and plan['created_by'] != auth['user']['id']
            number = int(query.get('page', 1))
            route.fulfill(json={'items': rows[(number - 1) * 25:number * 25], 'total': len(rows), 'page': number, 'page_size': 25})
        elif path == '/plans' and request.method == 'POST':
            assert 'create' in auth['user']['plan_permissions']
            plan = {**plan_values(body), 'id': max(plan['id'] for plan in state['plans']) + 1,
                    'version': 29, 'revision': 1, 'status': 'draft', 'is_current': False,
                    'created_by': auth['user']['id'], 'can_approve': False}
            state['plans'].insert(0, plan)
            route.fulfill(status=201, json=plan)
        elif path.startswith('/plans/') and request.method == 'PATCH':
            assert 'create' in auth['user']['plan_permissions']
            plan = next(plan for plan in state['plans'] if str(plan['id']) == path.split('/')[2])
            assert body['expected_revision'] == plan['revision']
            plan.update(plan_values(body), revision=plan['revision'] + 1)
            route.fulfill(json=plan)
        elif path.endswith('/approve'):
            assert auth['user']['plan_permissions'] == ['approve']
            plan = next(plan for plan in state['plans'] if str(plan['id']) == path.split('/')[2])
            assert body == {'expected_revision': plan['revision']}
            plan.update(status='approved', is_current=True, revision=plan['revision'] + 1)
            route.fulfill(json=plan)
        elif path == '/users' and request.method == 'GET':
            route.fulfill(json={'items': state['users']})
        elif path == '/users' and request.method == 'POST':
            assert body['plan_permissions'] == ['approve']
            assert body['role'] == 'manager' and body['terminals'] == ['cua_lo']
            created = {**body, 'id': 9100, 'is_active': True, 'must_change_password': True}
            state['users'].append(created)
            route.fulfill(status=201, json={'user': created, 'temporary_password': 'SYNTHETIC-NOT-A-CREDENTIAL'})
        elif path == '/admin/metrics':
            route.fulfill(json={'cache': {'snapshots': 1, 'stored_rows': 10, 'inflight': 0, 'ttl_seconds': 120,
                                         'snapshot_ttl_seconds': 86400, 'max_snapshots': 20, 'max_snapshot_rows': 500000},
                                'operations': {}, 'backup': {'enabled': True, 'status': state['backup_status'],
                                    'last_attempt_at': '2026-09-16T05:00:00Z', 'last_success_at': '2026-09-16T05:00:00Z',
                                    'restore_verified': True, 'offsite_verified': True, 'max_age_hours': 36,
                                    'message': 'Đã quá 36 giờ chưa có bản sao được xác minh.'},
                                'plan_approval_policy': 'separate_approver'})
        elif path == '/admin/events':
            before = {**auth['user'], 'plan_permissions': ['create', 'approve']}
            after = {**auth['user'], 'plan_permissions': ['approve']}
            route.fulfill(json={'items': [{'id': 1, 'action': 'user_updated', 'actor_id': 9001,
                'user_id': 9001, 'before': before, 'after': after, 'created_at': '2026-09-18T05:00:00Z'}],
                'total': 1, 'page': int(query.get('page', 1)), 'page_size': 25})
        else:
            route.fallback()

    page.route('**/api/**', respond)
    return state, auth


def latest_plan_get(state):
    return next(call for call in reversed(state['calls']) if call['path'] == '/plans' and call['method'] == 'GET')


def plan_mutations(state):
    return [call for call in state['calls'] if call['path'].startswith('/plans') and call['method'] in {'POST', 'PATCH'}]


def verify_filters_and_milestones(browser, url, output):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    state, auth = setup(page, permissions=['create'])
    page.goto(url.rstrip('/') + '#management')
    workspace = page.locator('#management-content')
    expect(workspace.get_by_text('28 phiên bản kế hoạch', exact=True)).to_be_visible()
    expect(workspace.get_by_role('row').filter(has_text='VB-CURRENT').get_by_role('button', name='Xóa', exact=True)).to_have_count(0)
    expect(workspace.get_by_role('row').filter(has_text='VB-PAGE-03').get_by_role('button', name='Xóa', exact=True)).to_be_visible()
    workspace.get_by_role('button', name='Sau', exact=True).click()
    expect(workspace.get_by_role('cell', name='VB-PAGE-28', exact=True)).to_be_visible()
    assert latest_plan_get(state)['query']['page'] == '2'
    expect(workspace.get_by_role('cell', name='VB-CURRENT', exact=True)).to_have_count(0)
    status = workspace.get_by_label('Trạng thái kế hoạch', exact=False)
    status.select_option('current')
    expect(workspace.get_by_role('cell', name='VB-CURRENT', exact=True)).to_be_visible()
    assert latest_plan_get(state)['query']['version_scope'] == 'current'
    assert latest_plan_get(state)['query']['page'] == '1'
    status.select_option('previous')
    expect(workspace.get_by_role('cell', name='VB-PREVIOUS', exact=True)).to_be_visible()
    assert latest_plan_get(state)['query']['version_scope'] == 'previous'
    status.select_option('draft')
    expect(workspace.get_by_text('26 phiên bản kế hoạch', exact=True)).to_be_visible()
    assert latest_plan_get(state)['query']['status'] == 'draft'
    assert 'version_scope' not in latest_plan_get(state)['query']
    workspace.get_by_label('Tìm theo văn bản', exact=True).fill('VB-PAGE-28')
    workspace.get_by_role('button', name='Tìm', exact=True).click()
    expect(workspace.get_by_text('1 phiên bản kế hoạch', exact=True)).to_be_visible()
    assert latest_plan_get(state)['query']['q'] == 'VB-PAGE-28'
    expect(workspace.get_by_role('button', name='Duyệt', exact=True)).to_have_count(0)
    workspace.get_by_role('button', name='Bỏ bộ lọc', exact=True).click()
    workspace.get_by_role('button', name='Tạo kế hoạch', exact=True).click()
    editor = workspace.locator('.plan-entry-editor[open]').filter(has=page.get_by_role('button', name='Lưu bản nháp', exact=True))
    editor.get_by_label('Xí nghiệp kế hoạch', exact=False).select_option('cua_lo')
    editor.get_by_label('Loại kế hoạch', exact=False).select_option('month')
    editor.get_by_label('Tháng áp dụng', exact=True).fill('2026-09')
    editor.get_by_label('Giá trị kế hoạch', exact=False).fill('2.000')
    editor.get_by_label('Số văn bản / nguồn phê duyệt', exact=False).fill('SYNTHETIC-MILESTONES-' + 'LONG_REFERENCE_' * 10)
    editor.get_by_role('button', name='Thêm mốc tiến độ', exact=True).click()
    editor.get_by_label('Ngày mốc 1', exact=True).fill('2026-09-05')
    editor.get_by_label('Tấn lũy kế mốc 1', exact=True).fill('500')
    editor.get_by_role('button', name='Thêm mốc tiến độ', exact=True).click()
    editor.get_by_label('Ngày mốc 2', exact=True).fill('2026-09-20')
    editor.get_by_label('Tấn lũy kế mốc 2', exact=True).fill('2.001')
    before = len(plan_mutations(state))
    editor.get_by_role('button', name='Lưu bản nháp', exact=True).click()
    expect(editor.get_by_text('Chưa lưu. Vui lòng kiểm tra các mục được đánh dấu.', exact=True)).to_be_visible()
    assert len(plan_mutations(state)) == before
    editor.get_by_label('Tấn lũy kế mốc 2', exact=True).fill('1.500')
    editor.get_by_role('button', name='Lưu bản nháp', exact=True).click()
    expect(workspace.get_by_role('button', name='Xem bản nháp vừa lưu', exact=True)).to_be_enabled()
    created = plan_mutations(state)[-1]['body']
    assert created['milestones'] == [{'date': '2026-09-05', 'amount': '500'}, {'date': '2026-09-20', 'amount': '1500'}]
    row = workspace.get_by_role('row').filter(has_text='SYNTHETIC-MILESTONES')
    row.get_by_role('button', name='Sửa nháp', exact=True).click()
    editing = workspace.get_by_role('region', name='Sửa bản nháp kế hoạch', exact=True)
    expect(editing.get_by_label('Tấn lũy kế mốc 1', exact=True)).to_have_value('500')
    editing.get_by_label('Tấn lũy kế mốc 1', exact=True).fill('650')
    editing.get_by_role('button', name='Lưu thay đổi', exact=True).click()
    expect(editing).to_have_count(0)
    assert plan_mutations(state)[-1]['body']['milestones'][0]['amount'] == '650'
    assert plan_mutations(state)[-1]['body']['expected_revision'] == 1
    for width in (390, 768):
        page.set_viewport_size({'width': width, 'height': 1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        workspace.locator('.plans-table tbody tr').first.screenshot(path=str(output / f'browser-governance-plan-row-{width}.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    workspace.locator('.plan-filter-panel').screenshot(path=str(output / 'browser-governance-plan-filters-desktop.png'))
    assert not auth['unexpected'] and not state['errors'], (auth['unexpected'], state['errors'])
    page.close()


def verify_approval_permissions(browser, url):
    for role, permissions in [('manager', ['approve']), ('viewer', [])]:
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        state, auth = setup(page, role=role, permissions=permissions)
        page.goto(url.rstrip('/') + '#management')
        workspace = page.locator('#management-content')
        expect(workspace.get_by_text('28 phiên bản kế hoạch', exact=True)).to_be_visible()
        for label in ('Tạo kế hoạch', 'Nhập Excel', 'Sửa nháp', 'Tạo phiên bản mới'):
            expect(workspace.get_by_role('button', name=label, exact=True)).to_have_count(0)
        if permissions:
            row = workspace.get_by_role('row').filter(has_text='VB-PAGE-03')
            expect(row.get_by_role('button', name='Xóa', exact=True)).to_have_count(0)
            expect(workspace.get_by_role('row').filter(has_text='VB-CURRENT').get_by_role('button', name='Xóa', exact=True)).to_be_visible()
            row.get_by_role('button', name='Duyệt', exact=True).click()
            expect(workspace.get_by_text('Đã duyệt kế hoạch.', exact=True)).to_be_visible()
            assert plan_mutations(state)[-1]['path'] == '/plans/3/approve'
        else:
            expect(workspace.get_by_role('button', name='Duyệt', exact=True)).to_have_count(0)
            expect(workspace.get_by_role('button', name='Xóa', exact=True)).to_have_count(0)
        assert not auth['unexpected'] and not state['errors'], (auth['unexpected'], state['errors'])
        page.close()


def verify_pace_and_backup(browser, url, output):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    state, auth = setup(page, role='admin', permissions=['create', 'approve'])
    state['pace'] = True
    page.goto(url)
    pace = page.locator('.throughput-pace')
    expect(pace.get_by_text('Thiếu 300 tấn so với mốc', exact=True)).to_be_visible()
    expect(pace.get_by_text('200 tấn', exact=True)).to_be_visible()
    expect(pace.get_by_text('500 tấn', exact=True)).to_be_visible()
    expect(pace.get_by_text('05/09/2026', exact=True)).to_be_visible()
    expect(page.get_by_role('progressbar', name='Hoàn thành kế hoạch sản lượng thông qua')).to_have_attribute('aria-valuenow', '50')
    assert state['reports']['synthetic-governance']['overview']['total_tonnage'] == 1000
    page.set_viewport_size({'width': 390, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    pace.screenshot(path=str(output / 'browser-governance-pace-mobile.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    workspace = open_workspace(page, 'admin')
    workspace.locator('summary').filter(has_text='Tạo tài khoản').click()
    user_form = workspace.locator('details.management-editor[open]')
    expect(user_form.get_by_label('Nhập và sửa kế hoạch', exact=True)).to_be_disabled()
    user_form.get_by_role('combobox', name='Quyền', exact=True).select_option('manager')
    expect(user_form.get_by_label('Nhập và sửa kế hoạch', exact=True)).to_be_checked()
    user_form.get_by_label('Nhập và sửa kế hoạch', exact=True).uncheck()
    expect(user_form.get_by_label('Duyệt kế hoạch', exact=True)).to_be_checked()
    user_form.get_by_label('Tên đăng nhập', exact=True).fill('synthetic_approver')
    user_form.get_by_label('Tên người dùng', exact=True).fill('NGƯỜI DUYỆT KIỂM THỬ')
    user_form.get_by_label('Cửa Lò', exact=True).check()
    page.set_viewport_size({'width': 390, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    user_form.screenshot(path=str(output / 'browser-governance-permissions-mobile.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    user_form.get_by_role('button', name='Tạo tài khoản', exact=True).click()
    expect(workspace.get_by_text('Đã tạo tài khoản.', exact=True)).to_be_visible()
    assert not any(call['path'] == '/admin/events' for call in state['calls'])
    workspace.get_by_text('Nhật ký quản trị tài khoản', exact=True).click()
    audit = workspace.locator('.admin-audit')
    expect(audit.get_by_text('1 sự kiện', exact=True)).to_be_visible()
    events_before = len([call for call in state['calls'] if call['path'] == '/admin/events'])
    audit.get_by_text('Xem thay đổi', exact=True).click()
    expect(audit.locator('.admin-audit-changes')).to_contain_text('Nhập và sửa kế hoạch, Duyệt kế hoạch')
    assert len([call for call in state['calls'] if call['path'] == '/admin/events']) == events_before
    audit.get_by_label('ID tài khoản', exact=True).fill('9001')
    audit.get_by_label('Thao tác', exact=False).select_option('user_updated')
    with page.expect_response(lambda response: '/admin/events?' in response.url and 'user_id=9001' in response.url):
        audit.get_by_role('button', name='Áp dụng', exact=True).click()
    latest = next(call for call in reversed(state['calls']) if call['path'] == '/admin/events')
    assert latest['query']['action'] == 'user_updated' and latest['query']['user_id'] == '9001'
    workspace.get_by_role('tab', name='Vận hành', exact=True).click()
    backup = workspace.get_by_role('region', name='Sao lưu dữ liệu', exact=True)
    expect(backup.get_by_text('Đã quá 36 giờ chưa có bản sao được xác minh.', exact=True)).to_be_visible()
    expect(backup.get_by_text('Đã kiểm tra', exact=True)).to_be_visible()
    expect(backup.get_by_text('Đã xác minh', exact=True)).to_be_visible()
    expect(backup.locator('.plan-field-error')).to_have_count(1)
    page.set_viewport_size({'width': 390, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(output / 'browser-governance-backup-mobile.png'), full_page=True)
    assert not auth['unexpected'] and not state['errors'], (auth['unexpected'], state['errors'])
    page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5173')
    args = parser.parse_args()
    output = Path('outputs')
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            verify_filters_and_milestones(browser, args.url, output)
            verify_approval_permissions(browser, args.url)
            verify_pace_and_backup(browser, args.url, output)
        finally:
            browser.close()
    print(json.dumps({'ok': True, 'checks': ['server plan filters and pagination', 'milestone validation/create/edit',
                                            'create-only and approve-only permissions', 'viewer read-only', 'milestone cutoff differs from full report',
                                            'audit opens and filters without extra nested fetch', 'stale backup warning', 'mobile overflow']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
