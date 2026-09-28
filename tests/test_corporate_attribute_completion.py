from copy import deepcopy
from datetime import date, datetime

import pytest

from backend.corporate_api import attribute_completion as completion
from backend.corporate_api.operation_source import extract_operation_catalogs
from test_corporate_operation_source import full_fixture

RESOURCE = completion.RESOURCE


def setup_source():
    query, profile = full_fixture()
    profile['terminals'] = ['cua_lo', 'ben_thuy']
    query.schema['SmartTOS_BenThuy'] = deepcopy(query.schema['SmartTOS'])
    query.data['SmartTOS_BenThuy'] = deepcopy(query.data['SmartTOS'])
    field = profile['operation_sources'][RESOURCE]['columns']['teu']
    query.data['SmartTOS']['ContainerSizeType'][0][field] = 2
    query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][field] = None
    return query, profile, field


def run(query, profile):
    return extract_operation_catalogs(query, profile=profile, resources=[RESOURCE],
                                      report_date=date(2026,9,25))[RESOURCE]


def enable(profile):
    profile['operation_attribute_completion'] = {RESOURCE: completion.POLICY}


def test_completion_requires_explicit_opt_in_and_preserves_known_native_value():
    query, profile, field = setup_source()
    strict = run(query, profile)
    assert not strict['ready'] and strict['rows'] == []
    enable(profile)
    result = run(query, profile)
    assert result['ready'] and len(result['rows']) == 1
    assert result['rows'][0]['teu'] == 2
    assert result['rows'][0]['contSizeTypeId'] == '1'
    assert result['attribute_completed_id_count'] == 1
    assert result['attribute_completed_fields'] == ['teu']
    assert query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][field] is None


def test_completion_is_symmetric_and_does_not_choose_latest_or_first_source():
    query, profile, _ = setup_source()
    enable(profile)
    first = run(query, profile)
    profile['terminals'].reverse()
    second = run(query, profile)
    assert first['rows'] == second['rows']


@pytest.mark.parametrize('target,value', [
    ('contSizeTypeCode','DIFFERENT'), ('contSizeTypeName','OTHER'), ('isDeleted',True),
    ('modifiedDate',None), ('teu',1)])
def test_disagreement_in_any_known_business_or_date_field_still_blocks(target,value):
    query, profile, _ = setup_source()
    enable(profile)
    column = profile['operation_sources'][RESOURCE]['columns'][target]
    query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][column] = value
    result = run(query, profile)
    assert not result['ready'] and result['rows'] == []
    assert result['identity_conflict_count'] == 1


def test_both_unknown_stay_unknown_without_inference():
    query, profile, field = setup_source()
    enable(profile)
    query.data['SmartTOS']['ContainerSizeType'][0][field] = None
    result = run(query, profile)
    assert result['ready'] and result['rows'][0]['teu'] is None
    assert 'attribute_completed_id_count' not in result


@pytest.mark.parametrize('configured', [None, [], {'oprt.jobType':completion.POLICY},
    {RESOURCE:'newer_source'}, {RESOURCE:{'fields':['contSizeTypeId']}}])
def test_invalid_policy_fails_before_any_source_read(configured):
    query, profile, _ = setup_source()
    profile['operation_attribute_completion'] = configured
    with pytest.raises(ValueError):
        run(query, profile)
    assert query.calls == []


def test_unapproved_profile_cannot_activate_completion():
    query, profile, _ = setup_source()
    enable(profile)
    profile['approved'] = False
    with pytest.raises(ValueError):
        run(query, profile)
    assert query.calls == []


def test_helper_refuses_opaque_date_variants():
    left={'contSizeTypeId':'1','teu':2,'_sourceDateVariants':[{'createdDate':'2020-01-01'}]}
    right={'contSizeTypeId':'1','teu':None}
    assert completion.complete_rows(RESOURCE,left,right,completion.POLICY) is None


def test_different_dates_need_separate_explicit_policy_and_preserve_both_pairs():
    from backend.corporate_api.metadata_identity import POLICY as DATE_POLICY
    query, profile, _ = setup_source()
    enable(profile)
    column = profile['operation_sources'][RESOURCE]['columns']['modifiedDate']
    query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][column] = datetime(2021,1,1)
    assert not run(query,profile)['ready']
    profile['operation_metadata_merge'] = {RESOURCE:DATE_POLICY}
    result = run(query,profile)
    assert result['ready'] and result['attribute_completed_id_count'] == 1
    assert result['metadata_merged_id_count'] == 1
    row = result['rows'][0]
    assert row['teu'] == 2
    assert {pair['modifiedDate'] for pair in row['_sourceDateVariants']} == {
        '2026-09-20T00:00:00','2021-01-01T00:00:00'}


def test_completed_native_value_survives_publication_and_each_source_date_filter(tmp_path):
    from backend.corporate_api import manage_exports
    from backend.corporate_api.metadata_identity import POLICY as DATE_POLICY
    from backend.corporate_api.operation_contracts import OperationQuery
    from backend.corporate_api.store import ExportStore
    query, profile, _ = setup_source()
    enable(profile)
    profile['operation_metadata_merge']={RESOURCE:DATE_POLICY}
    column=profile['operation_sources'][RESOURCE]['columns']['modifiedDate']
    query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][column]=datetime(2021,1,1)
    draft=manage_exports.extract(profile,date(2026,9,1),date(2026,9,30),[RESOURCE],query)
    store=ExportStore(tmp_path/'completion.sqlite3')
    manage_exports.publish_preview(draft,profile,store,[RESOURCE])
    for day in ('20210101','20260920'):
        response=store.read(RESOURCE,OperationQuery(companyId='CNT',startDate=day,endDate=day))
        assert response['pagination']['total']==1
        assert response['data'][0]['teu']==2
        assert all(not field.startswith('_') for field in response['data'][0])
