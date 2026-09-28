"""Independent edge review: completion must retain strict source/date boundaries."""
from copy import deepcopy

import pytest

from backend.corporate_api import attribute_completion as completion
from backend.corporate_api.metadata_identity import POLICY as DATE_POLICY
from test_corporate_attribute_completion import RESOURCE, enable, run, setup_source


def test_native_zero_is_a_known_value_and_does_not_mutate_either_source():
    query, profile, field = setup_source()
    query.data['SmartTOS']['ContainerSizeType'][0][field] = 0
    original = deepcopy(query.data)
    enable(profile)
    result = run(query, profile)
    assert result['ready'] and result['rows'][0]['teu'] == 0
    assert query.data == original


@pytest.mark.parametrize('target,value', [('isDeleted',None), ('sizeCode','SOURCE-SIZE')])
def test_unknown_flag_or_other_optional_business_disagreement_never_completes(target,value):
    query, profile, _ = setup_source()
    enable(profile)
    if target=='sizeCode':
        profile['operation_sources'][RESOURCE]['columns'][target]='sourceSizeCode'
        for database in ('SmartTOS','SmartTOS_BenThuy'):
            query.schema[database]['ContainerSizeType']['sourceSizeCode']='nvarchar'
            query.data[database]['ContainerSizeType'][0]['sourceSizeCode']=None
    column = profile['operation_sources'][RESOURCE]['columns'][target]
    query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][column] = value
    result = run(query, profile)
    assert not result['ready'] and result['rows'] == []
    assert result['identity_conflict_count'] == 1
    assert 'attribute_completed_id_count' not in result


def test_absent_selected_teu_column_cannot_be_treated_as_unknown_attribute():
    query, profile, field = setup_source()
    enable(profile)
    del query.data['SmartTOS_BenThuy']['ContainerSizeType'][0][field]
    result = run(query, profile)
    assert not result['ready'] and result['rows'] == []
    assert any(b['code']=='SOURCE_DATA' for b in result['blockers'])
    assert 'attribute_completed_id_count' not in result


def test_failed_second_source_cannot_be_hidden_by_completion():
    query, profile, _ = setup_source()
    enable(profile)
    query.fail_db='SmartTOS_BenThuy'
    result=run(query,profile)
    assert not result['ready'] and result['rows']==[]
    assert result['source_diagnostics'][1]['raw_row_count'] is None


@pytest.mark.parametrize('marker,date_policy', [
    ([{'createdDate':'2020-01-01'}], DATE_POLICY),
    ([{'createdDate':'2026-01-01T00:00:00','modifiedDate':'2026-09-01T00:00:00'},
      {'createdDate':'2026-01-01T00:00:00','modifiedDate':'2026-08-01T00:00:00'}], None),
])
def test_identical_private_markers_cannot_bypass_date_variant_validation(marker,date_policy):
    left={'contSizeTypeId':'1','teu':2,'createdDate':'2026-01-01T00:00:00',
          'modifiedDate':'2026-09-01T00:00:00','_sourceDateVariants':marker}
    right=deepcopy(left)
    right['teu']=None
    try:
        result=completion.complete_rows(RESOURCE,left,right,completion.POLICY,date_policy)
    except ValueError:
        return  # Rejecting the invalid/unapproved marker is also fail-closed.
    assert result is None
