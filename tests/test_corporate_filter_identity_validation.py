"""Existence is distinct from activity, using bounded read-only source checks."""
import pytest

from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.filter_validation import validate_production_filter_ids
from backend.corporate_api.live import LiveReader
from test_corporate_end_to_end import full_profile
from test_corporate_live_reference_sources import ProductionSourceQuery, source_fact_calls
from test_corporate_reference_scope import NATIVE_PROFILE


class ExistenceQuery:
    def __init__(self, *, found=None, error_database=None, error=None):
        self.calls = []
        self.found = found or {'SmartTOS': {'shipId': {'10'}, 'handlingMethodId': {'40'}},
                               'SmartTOS_BenThuy': {'shipId': set(), 'handlingMethodId': set()}}
        self.error_database = error_database
        self.error = error or RuntimeError('private-driver-secret')

    def __call__(self, database, statement, params):
        self.calls.append((database, statement, params))
        if database == self.error_database:
            raise self.error
        fields = [field for field in ('shipId', 'handlingMethodId') if 'AS [' + field + ']' in statement]
        assert len(fields) == len(params) == statement.count('?')
        return [{field: int(value in self.found[database][field]) for field, value in zip(fields, params)}]


def query(**filters):
    return Query(companyId='CNT', startDate='20260916', endDate='20260916', **filters)


def reader(source, membership):
    return LiveReader(profile={**full_profile(), **NATIVE_PROFILE}, extract_fn=source.extract,
                      filter_query_fn=membership)


@pytest.mark.parametrize('filters', [{'shipId': '10'}, {'handlingMethodId': '40'},
                                    {'shipId': '10', 'handlingMethodId': '40'}])
def test_membership_uses_one_bounded_parameterized_scalar_select_per_source(filters):
    membership = ExistenceQuery()
    validate_production_filter_ids(filters, membership)
    assert [item[0] for item in membership.calls] == ['SmartTOS', 'SmartTOS_BenThuy']
    for _, statement, params in membership.calls:
        assert statement.startswith('SELECT CASE WHEN EXISTS (SELECT 1 FROM ')
        assert statement.count('CASE WHEN EXISTS') == len(filters)
        assert statement.count('CAST(? AS BIGINT)') == len(filters)
        assert all(value not in statement for value in params)
        assert 'rowDeleted' in statement
        assert not any(word in statement.lower() for word in ('name', 'update ', 'delete ', 'insert ', 'select *'))
        if 'shipId' in filters:
            assert 'ISNULL([isVirtualVessel],0)=0' in statement


def test_two_filters_can_exist_in_different_sources_without_matching_activity():
    membership = ExistenceQuery(found={
        'SmartTOS': {'shipId': {'10'}, 'handlingMethodId': set()},
        'SmartTOS_BenThuy': {'shipId': set(), 'handlingMethodId': {'40'}}})
    validate_production_filter_ids({'shipId': '10', 'handlingMethodId': '40'}, membership)
    assert len(membership.calls) == 2


@pytest.mark.parametrize('filters', [{'shipId': '999'}, {'handlingMethodId': '999'},
                                    {'shipId': '10', 'handlingMethodId': '999'}])
def test_unknown_id_rejected_only_after_both_catalog_sources_checked(filters):
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids(filters, membership)
    assert caught.value.status == 422 and caught.value.code == 'FILTER_ID_NOT_FOUND'
    assert '999' not in caught.value.message
    assert len(membership.calls) == 2


@pytest.mark.parametrize('value', ['0', '09', '9e0', '9.0', '-1', '９', '9 OR 1=1',
                                 '9223372036854775808', '9' * 100, '', True, 9])
def test_non_native_identity_is_never_interpolated_bound_or_aliased(value):
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': value}, membership)
    assert caught.value.status == 422 and caught.value.code == 'FILTER_ID_NOT_FOUND'
    assert membership.calls == []


@pytest.mark.parametrize('identity', ['10', '999'])
@pytest.mark.parametrize('error', [RuntimeError('private-password'),
                                  CorporateError(503, 'SOURCE_TIMEOUT', 'private-query'),
                                  CorporateError(503, 'SOURCE_UNAVAILABLE', 'private-host')])
def test_one_database_failure_never_becomes_valid_or_absent_filter(identity, error):
    membership = ExistenceQuery(error_database='SmartTOS_BenThuy', error=error)
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': identity}, membership)
    assert caught.value.status == 503
    assert caught.value.code == ('SOURCE_TIMEOUT' if isinstance(error, CorporateError)
                                 and error.code == 'SOURCE_TIMEOUT' else 'SOURCE_UNAVAILABLE')
    assert 'private' not in caught.value.message
    assert len(membership.calls) == 2


@pytest.mark.parametrize('result', [[], [{}, {}], [{'shipId': '1'}], [{'shipId': 2}],
                                   [{'shipId': 1, 'extra': 0}], [{'wrong': 1}]])
def test_malformed_scalar_result_fails_closed(result):
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': '10'}, lambda *args: result)
    assert caught.value.status == 503 and caught.value.code == 'SOURCE_DATA_NOT_READY'


def test_no_filter_requires_no_membership_capability_and_no_source_read():
    validate_production_filter_ids({}, None)
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': '10'}, None)
    assert caught.value.status == 503 and caught.value.code == 'SOURCE_FILTER_VALIDATION_UNAVAILABLE'


def test_known_id_without_activity_returns_zero_and_rechecks_source_each_request():
    source = ProductionSourceQuery()
    source.facts = {'SmartTOS': [], 'SmartTOS_BenThuy': []}
    membership = ExistenceQuery()
    api = reader(source, membership)
    for _ in range(2):
        result = api.read('bulkQuayVolumesCB', query(shipId='10', handlingMethodId='40'))
        assert result['data'] == [] and result['pagination']['total'] == 0
        assert result['pagination']['hasNext'] is False
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy'] * 2
    assert len(membership.calls) == 4
    assert source.scopes == [None, None]


def test_unknown_filter_on_empty_production_raises422_without_full_catalog_read():
    source = ProductionSourceQuery()
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        reader(source, membership).read('bulkQuayVolumesCB', query(shipId='999'))
    assert caught.value.code == 'FILTER_ID_NOT_FOUND'
    assert len(membership.calls) == 2 and source.scopes == [None]


def test_source_failure_takes_priority_over_unknown_filter_and_no_membership_query_runs():
    source = ProductionSourceQuery()
    source.fail_fact_database = 'SmartTOS_BenThuy'
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        reader(source, membership).read('bulkQuayVolumesCB', query(shipId='999'))
    assert caught.value.status == 503 and caught.value.code == 'SOURCE_UNAVAILABLE'
    assert membership.calls == []


def test_nonempty_filtered_data_reuses_existing_reference_validation_without_extra_queries():
    source = ProductionSourceQuery()
    # Same ID, different metadata in an unused source does not affect membership.
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different unused vessel'
    membership = ExistenceQuery()
    result = reader(source, membership).read('bulkQuayVolumesCB', query(shipId='10', handlingMethodId='40'))
    assert len(result['data']) == 1 and membership.calls == []


def test_custom_extractor_without_query_capability_never_falls_through_to_live_sql(monkeypatch):
    import backend.corporate_api.live as live_module
    source = ProductionSourceQuery()
    monkeypatch.setattr(live_module, 'query_source', lambda *args: pytest.fail('Unexpected live SQL'))
    api = LiveReader(profile={**full_profile(), **NATIVE_PROFILE}, extract_fn=source.extract)
    with pytest.raises(CorporateError) as caught:
        api.read('bulkQuayVolumesCB', query(shipId='999'))
    assert caught.value.code == 'SOURCE_FILTER_VALIDATION_UNAVAILABLE'
