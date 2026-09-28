from copy import deepcopy

import pytest

from backend.corporate_api.manage_exports import FORMAT, profile_digest, publish_preview
from backend.corporate_api.store import ExportStore


def preview(profile):
    return {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
            'sourceReadAt': '2026-09-21T10:00:00+07:00', 'startDate': '20260901', 'endDate': '20260930',
            'datasets': {'origins': {'ready': True, 'blockers': [], 'coverage': [], 'rows': [
                {'reportDate': '20260921', 'originId': 'DOMESTIC', 'originName': 'Nội địa',
                 'createdDate': None, 'modifiedDate': None}]}}}


def test_only_reviewed_unchanged_mapping_profile_can_publish(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    profile = {'approved': True}
    exported = preview(profile)
    assert publish_preview(exported, profile, store, ['origins'])['origins']['rows'] == 1
    with pytest.raises(ValueError, match='changed'):
        publish_preview(exported, {**profile, 'date_basis': 'different'}, store, ['origins'])
    profile = {'approved': False}
    with pytest.raises(ValueError, match='review'):
        publish_preview(preview(profile), profile, store, ['origins'])


def test_ready_flag_cannot_bypass_unresolved_reference(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    profile = {'approved': True}
    exported = preview(profile)
    exported['datasets']['cargoCategory'] = {'ready': True, 'blockers': [], 'rows': [
        {'reportDate': '20260921', 'cargoTypeId': 'MISSING', 'cargoParentId': None,
         'cargoId': 'CNT-CL-1', 'cargoName': 'Example', 'createdDate': None, 'modifiedDate': None}]}
    with pytest.raises(ValueError, match='unresolved'):
        publish_preview(exported, profile, store, ['origins', 'cargoCategory'])
    assert store.describe() == []


def test_blocked_extraction_does_not_change_export(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    profile = {'approved': True}
    exported = preview(profile)
    publish_preview(exported, profile, store, ['origins'])
    before = store.describe()
    exported['datasets']['origins']['blockers'] = ['SOURCE_UNAVAILABLE']
    with pytest.raises(ValueError, match='not ready'):
        publish_preview(exported, profile, store, ['origins'])
    assert store.describe() == before
