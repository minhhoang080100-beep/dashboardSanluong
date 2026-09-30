"""Real adapters validate only actual production references in each source."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from backend.corporate_api.manage_exports import extract
from backend.corporate_api.source import REQUIRED_COLUMNS, SCHEMA_SQL
from test_corporate_end_to_end import full_profile
from test_corporate_reference_scope import ScopedQuery, NATIVE_PROFILE
from test_corporate_source import fact, berth_rows_for


class ProductionSourceQuery(ScopedQuery):
    def __init__(self):
        super().__init__()
        self.facts = {'SmartTOS': [fact(cargo_id=30, cargo_name='Bulk commodity',
            cargo_group_id=1, method_id=40, ship_id=10,
            native_weight=Decimal('12.75'), quantity_unit_code='TAN')],
            'SmartTOS_BenThuy': []}
        self.fail_fact_database = None
        self.scopes = []
        self.production_previews = []

    def __call__(self, database, statement, params):
        if statement == SCHEMA_SQL:
            self.calls.append((database, statement, params))
            return [{'table_name': table, 'column_name': column}
                    for table, columns in REQUIRED_COLUMNS.items() for column in columns]
        if 't.tallyShiftId AS source_id' in statement:
            self.calls.append((database, statement, params))
            if database == self.fail_fact_database:
                raise CorporateError(503, 'SOURCE_UNAVAILABLE', 'private-driver-detail')
            return deepcopy(self.facts[database])
        if 'berth_scope.vesselVoyageId AS voyage_id' in statement:
            self.calls.append((database, statement, params))
            return berth_rows_for(self.facts[database])
        return super().__call__(database, statement, params)

    def extract(self, profile, start, end, resources, **kwargs):
        self.scopes.append(deepcopy(kwargs.get('reference_scope')))
        preview = extract(profile, start, end, resources, self, **kwargs)
        if 'bulkQuayVolumesCB' in preview['datasets']:
            self.production_previews.append(deepcopy(preview['datasets']['bulkQuayVolumesCB']))
        return preview


def read(source):
    reader = LiveReader(profile={**full_profile(), **NATIVE_PROFILE}, extract_fn=source.extract)
    return reader.read('bulkQuayVolumesCB',
        Query(companyId='CNT', startDate='20260916', endDate='20260916'))


def source_fact_calls(source):
    return [database for database, statement, _ in source.calls if 't.tallyShiftId AS source_id' in statement]


def test_unused_other_source_identity_does_not_block_actual_live_production():
    source = ProductionSourceQuery()
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Unrelated vessel with the same native ID'
    result = read(source)
    assert result['code'] == '1' and len(result['data']) == 1
    assert result['data'][0]['shipId'] == '10' and result['data'][0]['bulkWeight'] == 12.75
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy']
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': []}
    vessel_reads = [(database, params) for database, statement, params in source.calls
                    if 'FROM [dbo].[Vessel]' in statement]
    assert vessel_reads == [('SmartTOS', ('10',))]
    assert not any(key.startswith('_') for row in result['data'] for key in row)
    assert 'snapshotId' not in result['pagination']


def test_unused_catalog_source_schema_does_not_become_a_dependency():
    source = ProductionSourceQuery()
    del source.schemas['SmartTOS_BenThuy']['Vessel']['vesselName']
    result = read(source)
    assert result['data'][0]['bulkWeight'] == 12.75
    catalog_metadata = [(database, params) for database, statement, params in source.calls
                        if 'INFORMATION_SCHEMA' in statement and statement != SCHEMA_SQL]
    assert catalog_metadata and all(database == 'SmartTOS' for database, _ in catalog_metadata)
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy']


def test_same_public_dimensions_from_both_sources_keep_provenance_and_block_real_identity_conflict():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different actual contributing vessel'
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_ID_CONFLICT'
    aggregate, = source.production_previews[0]['rows']
    assert aggregate['bulkWeight'] == Decimal('25.50')
    assert aggregate['_sourceTerminals'] == ['ben_thuy', 'cua_lo']
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': ['10']}


def test_same_used_identity_with_matching_business_data_in_both_sources_can_be_aggregated():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    result = read(source)
    assert result['code'] == '1' and len(result['data']) == 1
    assert result['data'][0]['bulkWeight'] == 25.5
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': ['10']}


def test_actual_source_missing_reference_cannot_use_matching_id_from_other_database():
    source = ProductionSourceQuery()
    source.data['SmartTOS']['Vessel'] = []
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_REFERENCE_NOT_FOUND'
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': []}


def test_required_reference_missing_in_one_of_two_contributors_blocks_aggregate():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    source.data['SmartTOS_BenThuy']['Vessel'] = []
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_REFERENCE_NOT_FOUND'
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': ['10']}


def test_unused_catalog_does_not_allow_skipping_other_database_facts_or_failures():
    source = ProductionSourceQuery()
    source.fail_fact_database = 'SmartTOS_BenThuy'
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_UNAVAILABLE'
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy']
    assert source.scopes == [None]
    assert 'private-driver' not in str(caught.value)


def test_parent_and_group_reference_closure_keeps_the_actual_contributing_source():
    source = ProductionSourceQuery()
    source.data['SmartTOS']['Cargo'][0]['cargoParentId'] = 31
    source.data['SmartTOS_BenThuy']['Cargo'][1]['cargoName'] = 'Unreferenced conflicting parent'
    source.data['SmartTOS_BenThuy']['CargoGroup'][0]['cargoGroupName'] = 'Unreferenced conflicting group'
    result = read(source)
    assert result['data'][0]['bulkWeight'] == 12.75
    catalog_scopes = [scope for scope in source.scopes if scope is not None]
    assert all(targets['ben_thuy'] == [] for scope in catalog_scopes for targets in scope.values())
    assert any(scope.get('cargoCategory') == {'cua_lo': ['30', '31'], 'ben_thuy': []}
               for scope in catalog_scopes)


def test_standalone_catalog_still_rejects_cross_database_business_identity_conflicts():
    source = ProductionSourceQuery()
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different standalone vessel'
    preview = extract({**full_profile(), **NATIVE_PROFILE}, date(2026, 9, 16),
                      date(2026, 9, 16), ['shipDetails'], source)
    result = preview['datasets']['shipDetails']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'
