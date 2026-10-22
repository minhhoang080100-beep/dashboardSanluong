"""Scoped public filter identities use only their exact source (synthetic SQL)."""
import pytest

from backend.corporate_api.errors import CorporateError
from backend.corporate_api.filter_validation import validate_production_filter_ids
from test_corporate_filter_identity_validation import ExistenceQuery


PREFIXED = {'source_identity_mode': 'terminal_prefixed_v1'}


@pytest.mark.parametrize('prefix,database', [('CL', 'SmartTOS'), ('BT', 'SmartTOS_BenThuy')])
@pytest.mark.parametrize('filters', [{'shipId': '10'}, {'handlingMethodId': '40'},
                                    {'shipId': '10', 'handlingMethodId': '40'}])
def test_scoped_filters_query_only_declared_source_with_native_bound_values(prefix, database, filters):
    membership = ExistenceQuery(found={name: {'shipId': {'10'}, 'handlingMethodId': {'40'}}
                                      for name in ('SmartTOS', 'SmartTOS_BenThuy')})
    validate_production_filter_ids({field: f'{prefix}:{value}' for field, value in filters.items()},
                                   membership, profile=PREFIXED)
    assert len(membership.calls) == 1
    actual_database, statement, params = membership.calls[0]
    assert actual_database == database
    assert params == tuple(filters.values())
    assert statement.count('CAST(? AS BIGINT)') == len(filters)
    assert 'CL:' not in statement and 'BT:' not in statement
    assert all(':' not in value for value in params)


def test_mixed_terminal_filters_validate_each_id_in_its_own_source():
    membership = ExistenceQuery(found={
        'SmartTOS': {'shipId': {'10'}, 'handlingMethodId': set()},
        'SmartTOS_BenThuy': {'shipId': set(), 'handlingMethodId': {'40'}}})
    validate_production_filter_ids({'shipId': 'CL:10', 'handlingMethodId': 'BT:40'},
                                   membership, profile=PREFIXED)
    assert [(database, params) for database, _, params in membership.calls] == [
        ('SmartTOS', ('10',)), ('SmartTOS_BenThuy', ('40',))]
    assert 'AS [handlingMethodId]' not in membership.calls[0][1]
    assert 'AS [shipId]' not in membership.calls[1][1]


def test_id_existing_in_other_source_does_not_validate_prefixed_filter():
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': 'BT:10'}, membership, profile=PREFIXED)
    assert caught.value.code == 'FILTER_ID_NOT_FOUND' and caught.value.status == 422
    assert [call[0] for call in membership.calls] == ['SmartTOS_BenThuy']


@pytest.mark.parametrize('value', ['10', 'cl:10', 'BT:0', 'CL:01', 'XX:10', ' CL:10', 'CL:10 ',
                                 'CL:10:2', 'CL:１０', 'CL:9223372036854775808',
                                 'CL:9999999999999999999', 'CL:10 OR 1=1', '', 10, None, True])
def test_malformed_scoped_filter_never_queries_or_broadens(value):
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': value}, membership, profile=PREFIXED)
    assert caught.value.code == 'FILTER_ID_NOT_FOUND' and caught.value.status == 422
    assert membership.calls == []
    assert 'CL:' not in caught.value.message


def test_unselected_source_failure_does_not_affect_exact_scoped_membership():
    membership = ExistenceQuery(error_database='SmartTOS_BenThuy')
    validate_production_filter_ids({'shipId': 'CL:10'}, membership, profile=PREFIXED)
    assert len(membership.calls) == 1


def test_selected_source_timeout_is_never_reported_as_absent():
    membership = ExistenceQuery(error_database='SmartTOS',
        error=CorporateError(503, 'SOURCE_TIMEOUT', 'private-native-key'))
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': 'CL:10'}, membership, profile=PREFIXED)
    assert caught.value.code == 'SOURCE_TIMEOUT' and caught.value.status == 503
    assert 'private-native-key' not in caught.value.message


def test_mixed_filters_fail_when_one_exact_source_does_not_contain_its_id():
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': 'CL:10', 'handlingMethodId': 'BT:40'},
                                       membership, profile=PREFIXED)
    assert caught.value.code == 'FILTER_ID_NOT_FOUND'
    assert len(membership.calls) == 2


def test_default_native_policy_still_rejects_prefixed_filter_without_query():
    membership = ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        validate_production_filter_ids({'shipId': 'CL:10'}, membership)
    assert caught.value.code == 'FILTER_ID_NOT_FOUND'
    assert membership.calls == []
