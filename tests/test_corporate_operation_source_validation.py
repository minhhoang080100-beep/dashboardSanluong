"""Source configuration errors must not become a successful or partial export."""
from datetime import date, datetime, timezone

import pytest

from backend.corporate_api import operation_source as source
from test_corporate_operation_source import Query


RESOURCE = 'oprt.cargoDirect'


@pytest.mark.parametrize('invalid', [False, 0, '', [], {}, '2026-09-27',
    datetime(2026, 9, 27), datetime(2026, 9, 26, 23, tzinfo=timezone.utc)])
def test_invalid_reporting_day_is_rejected_before_reading_source(invalid):
    query = Query()
    with pytest.raises(ValueError, match='Invalid report date'):
        source.extract_operation_catalogs(query, resources=[RESOURCE], report_date=invalid)
    assert query.calls == []


def test_explicit_calendar_day_is_preserved_and_only_none_defaults():
    before = datetime.now(source.VIETNAM_TIMEZONE).date()
    default = source.extract_operation_catalogs(Query(), resources=[RESOURCE], report_date=None)
    after = datetime.now(source.VIETNAM_TIMEZONE).date()
    explicit = source.extract_operation_catalogs(Query(), resources=[RESOURCE], report_date=date(2026, 9, 1))
    assert default[RESOURCE]['rows'][0]['reportDate'] in {before.isoformat(), after.isoformat()}
    assert explicit[RESOURCE]['rows'][0]['reportDate'] == '2026-09-01'


@pytest.mark.parametrize('invalid', [False, 0, RESOURCE, {RESOURCE: False}, {RESOURCE},
    [], (), [RESOURCE, RESOURCE], [['oprt.cargoDirect']], [None], ['oprt.unknown']])
def test_resource_selection_requires_unique_known_names_in_a_list_or_tuple(invalid):
    query = Query()
    with pytest.raises(ValueError, match='Invalid operation resources'):
        source.extract_operation_catalogs(query, resources=invalid)
    assert query.calls == []


def test_iterator_is_not_consumed_as_an_unbounded_resource_selection():
    def resources():
        raise AssertionError('An unsupported iterable must never be consumed')
        yield RESOURCE

    query = Query()
    with pytest.raises(ValueError, match='Invalid operation resources'):
        source.extract_operation_catalogs(query, resources=resources())
    assert query.calls == []


@pytest.mark.parametrize('selected', [[RESOURCE], (RESOURCE,)])
def test_supported_resource_sequences_still_extract(selected):
    result = source.extract_operation_catalogs(Query(), resources=selected)
    assert list(result) == [RESOURCE]
    assert result[RESOURCE]['ready']


@pytest.mark.parametrize('invalid_table', [[], {}, ['CargoDirect'], {'name': 'CargoDirect'}, False, 0, None])
def test_malformed_resource_table_blocks_only_that_resource(invalid_table):
    query = Query()
    profile = {'approved': True, 'operation_sources': {
        'oprt.jobType': {'table': invalid_table}}}
    result = source.extract_operation_catalogs(query, profile=profile,
        resources=['oprt.jobType', RESOURCE], report_date=date(2026, 9, 27))
    broken = result['oprt.jobType']
    assert not broken['ready'] and broken['rows'] == []
    assert [blocker['code'] for blocker in broken['blockers']] == ['SOURCE_SCHEMA']
    assert result[RESOURCE]['ready'] and len(result[RESOURCE]['rows']) == 1
    assert len(query.calls) == 4
    assert all('JobMethodType' not in sql for _, sql, _ in query.calls)
