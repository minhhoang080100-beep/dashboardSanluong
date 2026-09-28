from copy import deepcopy

import pytest

from backend.corporate_api.roster_reconciliation import build_reconciliation, INVALID_CAPTURE


def capture():
    team = {'teamId': '34', 'teamCode': 'TO34', 'teamName': 'Tổ hỗ trợ',
            'parentId': '27', 'classification': 'team', 'isDeleted': None,
            'createdDate': '2025-01-01', 'modifiedDate': '2026-09-01',
            'staff': [{'staffId': '1', 'staffCode': 'NV1', 'staffName': 'Nhân viên kiểm thử',
                       'teamId': '34', 'isDeleted': None, 'createdDate': '2025-01-01',
                       'modifiedDate': '2026-09-01'}]}
    return {'sources': [{'terminal': source, 'teams': [deepcopy(team)]}
                        for source in ('cua_lo', 'ben_thuy')]}


def person(review, source=1):
    return review['sources'][source]['teams'][0]['staff'][0]


def test_identical_rows_are_not_conflicts_and_input_is_unchanged():
    review = capture()
    before = deepcopy(review)
    result = build_reconciliation(review)
    assert review == before
    assert result['comparisonComplete'] is True
    assert result['conflicts'] == {'teams': [], 'staff': []}
    assert all(value == 0 for value in result['summary']['byKind'].values())


@pytest.mark.parametrize('field,value,kind', [
    ('staffName', 'Nhân viên khác', 'business_values'),
    ('staffName', 'nhân viên kiểm thử', 'business_values'),
    ('staffCode', 'NV2', 'business_values'),
    ('staffName', None, 'missing_attributes'),
    ('staffCode', '', 'missing_attributes'),
    ('isDeleted', False, 'lifecycle_flags'),
    ('isDeleted', True, 'lifecycle_flags'),
    ('createdDate', None, 'dates_only'),
    ('modifiedDate', '2026-09-02', 'dates_only'),
])
def test_staff_changes_preserve_raw_values_and_classify(field, value, kind):
    review = capture()
    original = person(review)[field]
    person(review)[field] = value
    row, = build_reconciliation(review)['conflicts']['staff']
    assert row == {'id': '1', 'kind': kind, 'fields': [field],
                   'changes': [{'field': field, 'cua_lo': original, 'ben_thuy': value}]}


def test_primary_membership_change_is_separate_from_name_change():
    review = capture()
    other = review['sources'][1]['teams'][0]
    other['teamId'] = '35'
    person(review)['teamId'] = '35'
    row, = build_reconciliation(review)['conflicts']['staff']
    assert row['kind'] == 'membership'
    assert row['fields'] == ['teamId']
    person(review)['staffName'] = 'Different identity'
    row, = build_reconciliation(review)['conflicts']['staff']
    assert row['kind'] == 'business_values'
    assert row['fields'] == ['staffName', 'teamId']


def test_all_fields_are_retained_when_kind_has_priority():
    review = capture()
    person(review).update(staffCode='NEW', isDeleted=True, modifiedDate=None)
    row, = build_reconciliation(review)['conflicts']['staff']
    assert row['kind'] == 'business_values'
    assert row['fields'] == ['staffCode', 'isDeleted', 'modifiedDate']


def test_deletion_flag_is_not_employment_state_and_false_differs_from_null():
    review = capture()
    team = review['sources'][1]['teams'][0]
    team.update(isDeleted=False, modifiedDate=None)
    result = build_reconciliation(review)
    assert result['conflicts']['teams'][0]['kind'] == 'lifecycle_flags'
    assert 'status' not in str(result)


def test_missing_dates_and_confirmation_count_each_source_row_once():
    review = capture()
    for source in review['sources']:
        source['teams'][0].update(createdDate=None, modifiedDate=None, classification='needs_confirmation')
    person(review)['modifiedDate'] = ''
    summary = build_reconciliation(review)['summary']
    assert summary['missingTeamDates'] == 2
    assert summary['missingStaffDates'] == 1
    assert summary['teamsNeedingConfirmation'] == 2
    assert summary['undatedTeamRows'] == 2
    assert summary['undatedStaffRows'] == 0
    person(review)['createdDate'] = None
    assert build_reconciliation(review)['summary']['undatedStaffRows'] == 1


@pytest.mark.parametrize('count', [0, 1])
def test_missing_source_is_incomplete_not_successful_reconciliation(count):
    review = capture()
    review['sources'] = review['sources'][:count]
    result = build_reconciliation(review)
    assert result['comparisonComplete'] is False
    assert result['conflicts'] == {'teams': [], 'staff': []}


def test_source_only_ids_are_not_cross_source_conflicts():
    review = capture()
    person(review)['staffId'] = '2'
    assert build_reconciliation(review)['summary']['staffConflicts'] == 0


def test_conflicts_sorted_by_numeric_native_id():
    review = capture()
    for source in review['sources']:
        source['teams'][0]['staff'] = [dict(person(review, 0), staffId=n) for n in ('10', '2', '1')]
    for row in review['sources'][1]['teams'][0]['staff']:
        row['staffCode'] = 'changed'
    assert [row['id'] for row in build_reconciliation(review)['conflicts']['staff']] == ['1', '2', '10']


def test_private_extra_fields_never_appear_in_comparison():
    review = capture()
    person(review)['phone'] = 'DO_NOT_EXPOSE'
    person(review)['staffName'] = 'Changed'
    assert 'DO_NOT_EXPOSE' not in str(build_reconciliation(review))


@pytest.mark.parametrize('malformation', ['source_duplicate', 'source_unknown', 'source_unhashable',
    'team_duplicate', 'staff_duplicate', 'membership_wrong', 'flag_numeric',
    'id_bool', 'id_noncanonical', 'team_not_dict', 'staff_not_list'])
def test_invalid_captures_fail_without_echoing_private_data(malformation):
    review = capture()
    team = review['sources'][0]['teams'][0]
    if malformation == 'source_duplicate':
        review['sources'].append(deepcopy(review['sources'][0]))
    elif malformation == 'source_unknown':
        review['sources'][0]['terminal'] = 'PRIVATE_VALUE'
    elif malformation == 'source_unhashable':
        review['sources'][0]['terminal'] = []
    elif malformation == 'team_duplicate':
        review['sources'][0]['teams'].append(deepcopy(team))
    elif malformation == 'staff_duplicate':
        team['staff'].append(deepcopy(team['staff'][0]))
    elif malformation == 'membership_wrong':
        team['staff'][0]['teamId'] = '35'
    elif malformation == 'flag_numeric':
        team['isDeleted'] = 1
    elif malformation == 'id_bool':
        team['teamId'] = True
    elif malformation == 'id_noncanonical':
        team['teamId'] = '034'
    elif malformation == 'team_not_dict':
        review['sources'][0]['teams'][0] = 'PRIVATE_VALUE'
    elif malformation == 'staff_not_list':
        team['staff'] = 'PRIVATE_VALUE'
    with pytest.raises(ValueError, match=f'^{INVALID_CAPTURE}$'):
        build_reconciliation(review)
