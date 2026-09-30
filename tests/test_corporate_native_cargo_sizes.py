"""Native reporting Cargo codes are not silently equated with ISO size types."""
from copy import deepcopy
from datetime import date, datetime

import pytest

from backend.corporate_api.catalog_source import extract_catalogs, NATIVE_CARGO_SIZE_CODES


class CargoQuery:
    def __init__(self):
        self.calls = []
        schema = {'cargoId':'int','cargoCode':'nvarchar','cargoName':'nvarchar',
                  'cargoGroupId':'int','rowDeleted':'bit','createTime':'datetime','updateTime':'datetime'}
        rows = [dict(cargoId=2125+i,cargoCode=code,cargoName=code,cargoGroupId=1034,
                     rowDeleted=False,createTime=datetime(2026,9,1,12),updateTime=None)
                for i,code in enumerate(NATIVE_CARGO_SIZE_CODES)]
        rows += [dict(rows[0],cargoId=3000,cargoCode='OTHER',cargoName='Other'),
                 dict(rows[0],cargoId=3001,cargoCode='test',cargoName=''),
                 dict(rows[0],cargoId=3002,cargoCode='20F',cargoName='20F',cargoGroupId=1),
                 dict(rows[0],cargoId=3003,cargoCode='20GP',cargoName='20GP'),
                 dict(rows[0],cargoId=3004,rowDeleted=True)]
        self.schemas = {db:deepcopy(schema) for db in ('SmartTOS','SmartTOS_BenThuy')}
        self.rows = {db:deepcopy(rows) for db in self.schemas}

    def __call__(self, db, sql, params):
        self.calls.append((db,sql,params))
        assert sql.lstrip().startswith('SELECT')
        if 'INFORMATION_SCHEMA' in sql:
            assert params == ('Cargo',)
            return [{'table_name':'Cargo','column_name':key,'data_type':kind}
                    for key,kind in self.schemas[db].items()]
        assert 'FROM [dbo].[Cargo]' in sql
        rows = [row for row in self.rows[db] if not row['rowDeleted']]
        if '[cargoId] IN (' in sql:
            rows = [row for row in rows if str(row['cargoId']) in params]
        else:
            assert '[cargoGroupId]=1034' in sql and '[cargoName]=[cargoCode]' in sql
            rows = [row for row in rows if row['cargoGroupId']==1034
                    and row['cargoCode'] in NATIVE_CARGO_SIZE_CODES and row['cargoName']==row['cargoCode']]
        return deepcopy(rows)


def run(query=None, scope=None):
    return extract_catalogs(query or CargoQuery(), profile={'container_size_source':'native_cargo'},
        resources=['containerSize'],report_date=date(2026,9,30),reference_scope=scope)['containerSize']


def used(identity='2125', terminal='cua_lo'):
    return {'containerSize': {key:([identity] if key==terminal else [])
                              for key in ('cua_lo','ben_thuy')}}


def used_by_both(identity='2125'):
    return {'containerSize': {'cua_lo':[identity], 'ben_thuy':[identity]}}


def test_full_catalog_keeps_native_ids_known_codes_and_unknown_physical_attributes():
    query = CargoQuery()
    result = run(query)
    assert result['ready'] and len(result['rows']) == len(NATIVE_CARGO_SIZE_CODES)
    for i,row in enumerate(result['rows']):
        assert row['containerSizeId'] == str(2125+i)
        assert row['sizeCode'] == NATIVE_CARGO_SIZE_CODES[row['localSzTp']]
        assert row['createdDate'] == '2026-09-01T12:00:00' and row['modifiedDate'] is None
        assert row['isoSzTp'] is row['heightCode'] is row['containerTypeCode'] is None
    assert result['warnings']
    assert all('ContainerSizeType' not in sql for _,sql,_ in query.calls)


def test_reference_scope_reads_only_used_native_ids_in_actual_source():
    query = CargoQuery()
    result = run(query,used())
    assert result['ready'] and len(result['rows'])==1
    row = result['rows'][0]
    assert row['containerSizeId']=='2125' and row['_sourceTerminals']==['cua_lo']
    data_calls = [(sql,params) for _,sql,params in query.calls if 'INFORMATION_SCHEMA' not in sql]
    assert len(data_calls)==1
    assert {db for db,_,_ in query.calls}=={'SmartTOS'}
    assert all('[cargoId] IN (?)' in sql and params==('2125',) for sql,params in data_calls)


@pytest.mark.parametrize('identity', ['3000','3001','3002','3003'])
def test_used_noncanonical_code_or_wrong_group_blocks_instead_of_inventing_size(identity):
    result = run(scope=used(identity))
    assert not result['ready'] and result['rows']==[]
    assert any(item['code']=='MAPPING_REQUIRED' for item in result['blockers'])


@pytest.mark.parametrize('identity', ['3004','99999'])
def test_deleted_or_missing_native_id_blocks_referencing_source(identity):
    result = run(scope=used(identity))
    assert not result['ready'] and result['rows']==[]
    assert any(item['code']=='SOURCE_REFERENCE_NOT_FOUND' for item in result['blockers'])


def test_conflicting_used_id_in_other_database_is_not_ignored_or_prefixed():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0].update(cargoCode='40F',cargoName='40F')
    result = run(query,used_by_both())
    assert not result['ready'] and result['rows']==[]
    assert result['identity_conflicts']==['2125']
    assert result['blockers'][0]['code']=='SOURCE_ID_CONFLICT'


def test_unused_other_database_size_identity_does_not_override_actual_source():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0].update(cargoCode='40F',cargoName='40F')
    result = run(query,used())
    assert result['ready'] and len(result['rows'])==1
    assert result['rows'][0]['localSzTp']=='20F'
    assert result['rows'][0]['_sourceTerminals']==['cua_lo']
    assert {db for db,_,_ in query.calls}=={'SmartTOS'}


def test_actual_missing_size_is_not_replaced_by_other_database_record():
    query = CargoQuery()
    query.rows['SmartTOS'] = []
    result = run(query,used())
    assert not result['ready'] and result['rows']==[]
    assert result['blockers'][0]['code']=='SOURCE_REFERENCE_NOT_FOUND'
    assert result['blockers'][0]['terminal']=='cua_lo'


def test_same_code_different_native_ids_are_preserved():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0]['cargoId']=4000
    result = run(query)
    assert result['ready']
    assert {row['containerSizeId'] for row in result['rows'] if row['localSzTp']=='20F'}=={'2125','4000'}


@pytest.mark.parametrize('column', ['cargoId','cargoCode','cargoName','cargoGroupId'])
def test_required_native_identity_and_classification_schema_is_verified(column):
    query = CargoQuery()
    del query.schemas['SmartTOS'][column]
    result = run(query)
    assert not result['ready'] and result['rows']==[]
    assert result['blockers'][0]['code']=='SOURCE_SCHEMA'


def test_standalone_size_dates_coalesce_to_latest_whole_original_metadata():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0].update(createTime=datetime(2025,1,1),updateTime=datetime(2026,9,20))
    result = run(query)
    assert result['ready'] and len(result['rows'])==len(NATIVE_CARGO_SIZE_CODES)
    row = next(row for row in result['rows'] if row['containerSizeId']=='2125')
    assert row['createdDate']=='2025-01-01T00:00:00'
    assert row['modifiedDate']=='2026-09-20T00:00:00'
    assert '_sourceTerminals' not in row


def test_standalone_size_business_conflict_remains_strict_despite_newer_metadata():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0].update(cargoCode='40F',cargoName='40F',updateTime=datetime(2026,9,20))
    result = run(query)
    assert not result['ready'] and result['blockers'][0]['code']=='SOURCE_ID_CONFLICT'


def test_scoped_size_reference_uses_original_latest_metadata_and_both_sources():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0].update(createTime=datetime(2025,1,1),updateTime=datetime(2026,9,20))
    result = run(query,used_by_both())
    assert result['ready'] and len(result['rows'])==1
    row=result['rows'][0]
    assert row['containerSizeId']=='2125' and row['localSzTp']=='20F'
    assert row['createdDate']=='2025-01-01T00:00:00'
    assert row['modifiedDate']=='2026-09-20T00:00:00'
    assert row['_sourceTerminals']==['ben_thuy','cua_lo']


def test_scoped_timestamp_handling_does_not_hide_invalid_dates():
    query = CargoQuery()
    query.rows['SmartTOS_BenThuy'][0]['updateTime']='not-a-date'
    result = run(query,used_by_both())
    assert not result['ready'] and result['rows']==[]
    assert result['blockers'][0]['code']=='SOURCE_DATA'


def test_missing_selected_field_is_an_error_while_explicit_null_dates_are_preserved():
    query = CargoQuery()
    for rows in query.rows.values():
        rows[0]['createTime']=None
    assert run(query,used())['rows'][0]['createdDate'] is None
    del query.rows['SmartTOS'][0]['createTime']
    result = run(query,used())
    assert not result['ready'] and result['blockers'][0]['code']=='SOURCE_DATA'


def test_reference_metadata_selection_is_deterministic_and_retains_signed_offsets():
    from backend.corporate_api.catalog_source import _merge_reference_rows
    left={'containerSizeId':'2125','localSzTp':'20F','reportDate':'20260930',
          'createdDate':'2025-01-01T00:00:00','modifiedDate':'2026-09-20T01:00:00+07:00',
          '_sourceTerminals':['cua_lo']}
    right={**left,'createdDate':'2024-01-01T00:00:00','modifiedDate':'2026-09-19T23:00:00Z',
           '_sourceTerminals':['ben_thuy']}
    first=_merge_reference_rows('containerSize',left,right)
    second=_merge_reference_rows('containerSize',right,left)
    assert first==second
    assert first['createdDate']==right['createdDate'] and first['modifiedDate']==right['modifiedDate']
    assert first['_sourceTerminals']==['ben_thuy','cua_lo']
    assert left['_sourceTerminals']==['cua_lo']


@pytest.mark.parametrize('field,value', [('localSzTp','40F'),('sizeCode','40'),
    ('isoSzTp','22G0'),('heightCode','86'),('containerTypeCode','GP')])
def test_scoped_coalescing_keeps_every_size_business_attribute_strict(field,value):
    from backend.corporate_api.catalog_source import _merge_reference_rows
    left={'containerSizeId':'2125','localSzTp':'20F','sizeCode':'20',
          'isoSzTp':None,'heightCode':None,'containerTypeCode':None,
          'createdDate':None,'modifiedDate':'2026-09-20T00:00:00'}
    assert _merge_reference_rows('containerSize',left,{**left,field:value}) is None


def test_scoped_customer_dates_coalesce_but_lifecycle_and_other_fields_do_not():
    from backend.corporate_api.catalog_source import _merge_reference_rows
    left={'customerCode':'7','customerNameVN':'Synthetic customer',
          'metadata':{'isDeleted':False,'createdDate':'2025-01-01T00:00:00','modifiedDate':None},
          '_createdDate':'20250101','_customerType':'carrier','_sourceTerminals':['cua_lo']}
    right={**left,'metadata':{'isDeleted':False,'createdDate':'2024-01-01T00:00:00',
                             'modifiedDate':'2026-09-20T00:00:00'},
           '_createdDate':'20240101','_sourceTerminals':['ben_thuy']}
    result=_merge_reference_rows('customers',left,right)
    assert result['metadata']==right['metadata'] and result['_createdDate']=='20240101'
    assert result['_sourceTerminals']==['ben_thuy','cua_lo']
    deleted={**right,'metadata':{**right['metadata'],'isDeleted':True}}
    assert _merge_reference_rows('customers',left,deleted) is None
    assert _merge_reference_rows('customers',left,{**right,'_customerType':None}) is None
