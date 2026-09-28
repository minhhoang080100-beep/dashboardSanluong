"""Private, field-level comparison of native organization/staff captures.

This is review evidence, never an export mapping or an identity resolution rule.
Counts of missing attributes count source rows, not unique company employees.
No employment status is inferred from source deletion flags or missing dates.
"""

TERMINALS = ('cua_lo', 'ben_thuy')
KINDS = ('business_values', 'membership', 'missing_attributes', 'lifecycle_flags', 'dates_only')
TEAM_FIELDS = ('teamCode', 'teamName', 'parentId', 'isDeleted', 'createdDate', 'modifiedDate')
STAFF_FIELDS = ('staffCode', 'staffName', 'teamId', 'isDeleted', 'createdDate', 'modifiedDate')
DATES = ('createdDate', 'modifiedDate')
INVALID_CAPTURE = 'Invalid roster comparison capture.'


def _invalid():
    # Never include staff records, source values, or exception details here.
    raise ValueError(INVALID_CAPTURE)


def _id(value):
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and not value.startswith('0'):
        return value
    _invalid()


def _missing(value):
    return value is None or value == ''


def _validate_row(row, fields):
    if not isinstance(row, dict):
        _invalid()
    for field in fields:
        value = row.get(field)
        if field == 'isDeleted':
            if value is not None and type(value) is not bool:
                _invalid()
        elif field in ('teamId', 'parentId'):
            if value not in (None, ''):
                _id(value)
        elif value is not None and not isinstance(value, str):
            _invalid()


def _kind(changes, entity):
    identity = ('teamCode', 'teamName', 'parentId') if entity == 'teams' else ('staffCode', 'staffName')
    actual_identity_change = any(change['field'] in identity
        and not _missing(change['cua_lo']) and not _missing(change['ben_thuy']) for change in changes)
    if actual_identity_change:
        return 'business_values'
    if entity == 'staff' and any(change['field'] == 'teamId' for change in changes):
        return 'membership'
    if any(change['field'] in identity for change in changes):
        return 'missing_attributes'
    if any(change['field'] == 'isDeleted' for change in changes):
        return 'lifecycle_flags'
    return 'dates_only'


def build_reconciliation(review):
    """Compare shared native IDs only, preserving raw field values for review.

    Missing either source is explicitly incomplete, even if no differences can
    be computed. Duplicate IDs/sources and invalid memberships fail closed.
    The classification is a review priority; it does not declare two IDs to
    represent the same person. All changed fields remain in each result.
    """
    if not isinstance(review, dict) or not isinstance(review.get('sources'), list):
        _invalid()
    indexed = {}
    summary = {'teamConflicts': 0, 'staffConflicts': 0,
               'byKind': dict.fromkeys(KINDS, 0), 'missingTeamDates': 0,
               'missingStaffDates': 0, 'teamsNeedingConfirmation': 0,
               'undatedTeamRows': 0, 'undatedStaffRows': 0}
    for source in review['sources']:
        if not isinstance(source, dict):
            _invalid()
        terminal = source.get('terminal')
        if not isinstance(terminal, str) or terminal not in TERMINALS or terminal in indexed:
            _invalid()
        if not isinstance(source.get('teams'), list):
            _invalid()
        teams, staff = {}, {}
        for team in source['teams']:
            _validate_row(team, TEAM_FIELDS)
            team_id = _id(team.get('teamId'))
            if team_id in teams or not isinstance(team.get('staff'), list):
                _invalid()
            teams[team_id] = team
            summary['missingTeamDates'] += any(_missing(team.get(field)) for field in DATES)
            summary['undatedTeamRows'] += all(_missing(team.get(field)) for field in DATES)
            summary['teamsNeedingConfirmation'] += team.get('classification') == 'needs_confirmation'
            for person in team['staff']:
                _validate_row(person, STAFF_FIELDS)
                staff_id = _id(person.get('staffId'))
                if staff_id in staff or _id(person.get('teamId')) != team_id:
                    _invalid()
                staff[staff_id] = person
                summary['missingStaffDates'] += any(_missing(person.get(field)) for field in DATES)
                summary['undatedStaffRows'] += all(_missing(person.get(field)) for field in DATES)
        indexed[terminal] = {'teams': teams, 'staff': staff}
    complete = set(indexed) == set(TERMINALS)
    conflicts = {'teams': [], 'staff': []}
    if complete:
        left, right = (indexed[terminal] for terminal in TERMINALS)
        for entity, fields in (('teams', TEAM_FIELDS), ('staff', STAFF_FIELDS)):
            shared_ids = left[entity].keys() & right[entity].keys()
            for native_id in sorted(shared_ids, key=int):
                changes = [{'field': field, 'cua_lo': left[entity][native_id].get(field),
                            'ben_thuy': right[entity][native_id].get(field)}
                           for field in fields
                           if left[entity][native_id].get(field) != right[entity][native_id].get(field)]
                if changes:
                    kind = _kind(changes, entity)
                    conflicts[entity].append({'id': native_id, 'kind': kind,
                        'fields': [change['field'] for change in changes], 'changes': changes})
                    summary['byKind'][kind] += 1
    summary['teamConflicts'] = len(conflicts['teams'])
    summary['staffConflicts'] = len(conflicts['staff'])
    return {'comparisonComplete': complete, 'summary': summary, 'conflicts': conflicts}
