"""Independent boundary review for private operation-catalog diagnostics."""
import json

from test_corporate_operation_source import Query, extract, full_fixture


def test_multiple_invalid_list_items_count_one_field_issue_without_values():
    query, profile = full_fixture()
    resource = 'oprt.portEquipment'
    mapping = profile['operation_sources'][resource]
    column = 'locationsJson'
    mapping['columns']['operationLocationTypeId'] = column
    mapping['transforms'] = {'operationLocationTypeId': 'json_array'}
    query.schema['SmartTOS']['Equipment'][column] = 'nvarchar'
    # The list parser accepts strings; their length is then checked by the
    # nested Pydantic Identifier constraint, with one error per list item.
    value = 'private-source-value-' * 20
    query.data['SmartTOS']['Equipment'][0][column] = json.dumps([value, value + 'second'])
    result = extract(query, [resource], profile)[resource]
    diagnostic = result['source_diagnostics'][0]
    assert not result['ready'] and result['rows'] == []
    assert diagnostic['invalid_row_count'] == 1
    assert diagnostic['issue_count'] == 1
    issue, = diagnostic['issue_groups']
    assert issue['fields'] == ['operationLocationTypeId']
    assert 'private-source-value' not in str(result)


def test_valid_first_duplicate_still_reports_later_field_failure_and_duplicate():
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(base), dict(base, cargoDirectCode=None)]
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert not result['ready'] and result['rows'] == []
    assert diagnostic['valid_row_count'] == diagnostic['invalid_row_count'] == 1
    assert diagnostic['issue_count'] == 2
    assert {tuple(issue['fields']) for issue in diagnostic['issue_groups']} == {
        ('cargoDirectCode',), ('cargoDirectId',)}
    assert all(issue['row_indexes'] == [2] and issue['source_ids'] == ['1']
               for issue in diagnostic['issue_groups'])
