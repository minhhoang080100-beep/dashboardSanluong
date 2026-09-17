"""Authenticated reporting, plan comparisons, imports and immutable closures."""
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

if __package__:
    from .control_api import get_repository, get_store, require_user, validate_plan_voyages
    from .control_store import ControlStore, plan_progress_rows, production_scope_context, require_admin, require_editor, require_scope
    from .reporting import reporting_service
    from .repository import TERMINALS, date_range
    from .workbook_io import MAX_UPLOAD_BYTES, parse_plan_workbook, plan_template, report_workbook
else:
    from control_api import get_repository, get_store, require_user, validate_plan_voyages
    from control_store import ControlStore, plan_progress_rows, production_scope_context, require_admin, require_editor, require_scope
    from reporting import reporting_service
    from repository import TERMINALS, date_range
    from workbook_io import MAX_UPLOAD_BYTES, parse_plan_workbook, plan_template, report_workbook

router = APIRouter(prefix='/api')
Terminal = Literal['all', 'cua_lo', 'ben_thuy']
ProductionScope = Literal['nghe_tinh', 'vietsun', 'unclassified']
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def get_reporting(request: Request):
    return getattr(request.app.state, 'reporting_service', reporting_service)


def report_scope(service, user, report_id):
    report = service.get_report_snapshot(report_id)
    require_scope(user, report['meta']['filters']['terminal'])
    return report


def selection(day: date | None = None, terminal: Terminal | None = None,
              production_scope: ProductionScope | None = None,
              cargo: str | None = Query(default=None, max_length=250),
              customer_id: str | None = Query(default=None, max_length=128),
              customer_terminal: Literal['cua_lo', 'ben_thuy'] | None = None,
              voyage_id: str | None = Query(default=None, max_length=128),
              issue: Literal['all', 'missing_weight', 'unknown_unit', 'negative'] = 'all',
              operation_filter: Literal['all', 'with_values', 'missing_weight'] = 'all'):
    return dict(day=day, terminal=terminal, production_scope=production_scope, cargo=cargo, customer_id=customer_id,
                customer_terminal=customer_terminal, voyage_id=voyage_id, issue=issue,
                operation_filter=operation_filter)


def scoped_detail(service, user, report_id, chosen, page=1, page_size=25):
    report_scope(service, user, report_id)
    for terminal in [chosen.get('terminal'), chosen.get('customer_terminal')]:
        if terminal is not None:
            require_scope(user, terminal)
    try:
        return service.drilldown(report_id, **chosen, page=page, page_size=page_size)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/reports/{report_id}/operations')
def report_operations(report_id: str, chosen: dict = Depends(selection),
                      page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                      user: dict = Depends(require_user), service=Depends(get_reporting)):
    return scoped_detail(service, user, report_id, chosen, page, page_size)


def workbook_response(content, filename):
    return Response(content, media_type=XLSX, headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@router.get('/reports/{report_id}/export.xlsx')
def export_report(report_id: str, chosen: dict = Depends(selection), user: dict = Depends(require_user),
                  service=Depends(get_reporting), store: ControlStore = Depends(get_store)):
    report_scope(service, user, report_id)
    for terminal in [chosen.get('terminal'), chosen.get('customer_terminal')]:
        if terminal is not None:
            require_scope(user, terminal)
    try:
        exported = service.export_drilldown(report_id, **chosen)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if (chosen['issue'] == 'all' and chosen['operation_filter'] == 'all'
            and not any(chosen.get(field) is not None for field in ['day', 'cargo', 'customer_id', 'customer_terminal', 'voyage_id'])):
        exported['report']['throughput_progress'] = store.throughput_progress(user, exported['report'])
    return workbook_response(report_workbook(exported['report'], exported['operations'], shifts=exported['shifts']), 'chi-tiet-san-luong.xlsx')


@router.get('/plans/template.xlsx')
def template(user: dict = Depends(require_user)):
    require_editor(user)
    return workbook_response(plan_template(), 'mau-ke-hoach.xlsx')


@router.post('/plans/import/preview')
async def preview_import(file: UploadFile = File(...), user: dict = Depends(require_user), store: ControlStore = Depends(get_store), repo=Depends(get_repository)):
    require_editor(user)
    try:
        if not (file.filename or '').lower().endswith('.xlsx'):
            raise HTTPException(422, 'Chỉ nhận tệp .xlsx theo mẫu kế hoạch.')
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        result = parse_plan_workbook(content)
        for row in result['rows']:
            require_scope(user, row['terminal'])
            store.validate_plan(row)
        await run_in_threadpool(validate_plan_voyages, user, result['rows'], repo)
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        await file.close()


def planning_scope(report):
    context = production_scope_context(report)
    eligible = context['production_scope'] == 'nghe_tinh' and not context['legacy_scope']
    reason = None if eligible else (
        'Bản dữ liệu chưa lưu quy tắc cầu cập đầu tiên; tải lại báo cáo để đối chiếu kế hoạch.' if context['legacy_scope'] else
        'Kế hoạch hiện tại áp dụng cho Cảng Nghệ Tĩnh; chưa có kế hoạch riêng cho phạm vi ' + context['production_scope_label'] + '.')
    return {**context, 'eligible': eligible, 'reason': reason}


@router.get('/reports/{report_id}/throughput-progress')
def throughput_progress(report_id: str, user: dict = Depends(require_user),
                        service=Depends(get_reporting), store: ControlStore = Depends(get_store)):
    # Read actuals from the selected immutable snapshot, with no new TOS query.
    report = report_scope(service, user, report_id)
    return store.throughput_progress(user, report)


@router.get('/reports/{report_id}/plan-progress')
def monthly_progress(report_id: str, user: dict = Depends(require_user), service=Depends(get_reporting), store: ControlStore = Depends(get_store)):
    report = report_scope(service, user, report_id)
    filters = report['meta']['filters']
    start, end = date.fromisoformat(filters['start_date']), date.fromisoformat(filters['end_date'])
    month = start.strftime('%Y-%m')
    scope = planning_scope(report)
    period_eligible = start.day == 1 and start.strftime('%Y-%m') == end.strftime('%Y-%m')
    eligible = scope['eligible'] and period_eligible
    response = {'eligible': eligible, 'reason': scope['reason'] if not scope['eligible'] else None if period_eligible else 'Chọn từ đầu tháng đến ngày cần xem để đối chiếu kế hoạch tháng.',
                'period': {**filters, 'month': month}, 'rows': [], 'production_scope': scope['production_scope'],
                'scope_label': scope['production_scope_label'], 'berth_rule_version': scope['berth_rule_version']}
    if not eligible:
        return response
    terminals = list(TERMINALS) if filters['terminal'] == 'all' else [filters['terminal']]
    actuals, plans = {}, []
    for terminal in terminals:
        detail = service.drilldown(report_id, terminal=terminal)
        actuals[terminal] = detail['summary']
        plans.extend(store.effective_plans(user, terminal, month=month))
    response['rows'] = plan_progress_rows(plans, actuals)
    return response


@router.get('/voyages/{terminal}/{voyage_id}/progress')
def voyage_progress(terminal: Literal['cua_lo', 'ben_thuy'], voyage_id: int,
                    production_scope: ProductionScope = 'nghe_tinh',
                    user: dict = Depends(require_user), service=Depends(get_reporting), store: ControlStore = Depends(get_store)):
    require_scope(user, terminal)
    try:
        progress = service.get_voyage_progress(terminal, voyage_id, production_scope=production_scope)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    scope = planning_scope(progress)
    plans = store.effective_plans(user, terminal, voyage_id=voyage_id) if scope['eligible'] else []
    return {**progress, 'planning': plan_progress_rows(plans, {terminal: progress['summary']}) if scope['eligible'] else [],
            'planning_eligible': scope['eligible'], 'planning_reason': scope['reason'], 'planning_scope_label': scope['production_scope_label'],
            'productivity': None, 'estimated_completion': None,
            'timing_status': 'Chưa có giờ làm hàng và thời gian dừng được xác nhận.'}


class CloseBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=4000)


class CompareBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)


@router.post('/closed-reports', status_code=201)
def close_report(body: CloseBody, user: dict = Depends(require_user), service=Depends(get_reporting), store: ControlStore = Depends(get_store)):
    require_editor(user)
    report = report_scope(service, user, body.report_id)
    snapshot = service.export_snapshot(body.report_id)
    filters = report['meta']['filters']
    start, end = date.fromisoformat(filters['start_date']), date.fromisoformat(filters['end_date'])
    scope = planning_scope(snapshot['report'])
    if not scope['eligible']:
        frozen = {**snapshot['report'], 'planning': {
            'captured': True, 'captured_at': datetime.now(timezone.utc).isoformat(), 'eligible': False,
            'reason': scope['reason'], 'period': {**filters, 'month': start.strftime('%Y-%m')},
            'production_scope': scope['production_scope'], 'scope_label': scope['production_scope_label'],
            'berth_rule_version': scope['berth_rule_version'], 'rows': [], 'plans': []}}
        return store.close_report(user, filters['terminal'], filters['start_date'], filters['end_date'],
                                  frozen, snapshot['operations'], body.title, body.note)
    actuals = {}
    if start.day == 1 and start.strftime('%Y-%m') == end.strftime('%Y-%m'):
        terminals = list(TERMINALS) if filters['terminal'] == 'all' else [filters['terminal']]
        actuals = {terminal: service.drilldown(body.report_id, terminal=terminal)['summary'] for terminal in terminals}
    return store.close_report(user, filters['terminal'], filters['start_date'], filters['end_date'],
                              snapshot['report'], snapshot['operations'], body.title, body.note, planning_actuals=actuals)


@router.get('/closed-reports')
def closed_reports(terminal: Terminal | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100),
                   user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.list_closed_reports(user, terminal, page, page_size)


@router.get('/closed-reports/{closed_id}')
def closed_report(closed_id: int, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    return store.get_closed_report(user, closed_id)


@router.get('/closed-reports/{closed_id}/export.xlsx')
def export_closed(closed_id: int, user: dict = Depends(require_user), store: ControlStore = Depends(get_store)):
    closed = store.get_closed_report(user, closed_id)
    report = closed['report']
    title = closed['title'] or f"Báo cáo đã chốt, phiên bản {closed['version']}"
    return workbook_response(report_workbook(report, closed['source_facts'], title=title, planning=closed['planning']), f"bao-cao-chot-{closed_id}.xlsx")


@router.post('/closed-reports/{closed_id}/compare')
def compare_closed(closed_id: int, body: CompareBody, user: dict = Depends(require_user), service=Depends(get_reporting), store: ControlStore = Depends(get_store)):
    closed = store.get_closed_report(user, closed_id)
    report = report_scope(service, user, body.report_id)
    filters = report['meta']['filters']
    if any(str(filters.get(key)) != str(closed[key]) for key in ['terminal', 'start_date', 'end_date']):
        raise HTTPException(422, 'Chọn đúng kỳ và xí nghiệp của báo cáo đã chốt để so sánh.')
    current_scope = production_scope_context(report)
    if closed['legacy_scope'] or current_scope['legacy_scope'] or any(
            closed[key] != current_scope[key] for key in ['production_scope', 'berth_rule_version']):
        raise HTTPException(422, detail={'code': 'REPORT_SCOPE_MISMATCH',
            'message': 'Chỉ so sánh báo cáo cùng phạm vi sản lượng và quy tắc cầu cập đầu tiên. Bản chốt cũ chưa lưu phạm vi vẫn có thể xem và xuất Excel.'})
    current = service.export_snapshot(body.report_id)
    return store.compare_closed_report(user, closed_id, current['report'], current['operations'])


@router.get('/admin/metrics')
def metrics(user: dict = Depends(require_user), service=Depends(get_reporting)):
    require_admin(user)
    return service.get_metrics()
