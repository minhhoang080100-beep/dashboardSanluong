from datetime import date, datetime

from backend.corporate_api.operation_source import extract_operation_catalogs


class ServiceSource:
    def __init__(self):
        self.calls = []
        self.described = []

    def describe_table(self, database, table):
        self.described.append((database, table))
        assert table == 'PortServiceType'
        return [{'column_name': name, 'data_type': kind} for name, kind in {
            'portServiceTypeId': 'int', 'portServiceTypeCode': 'nvarchar',
            'portServiceTypeName': 'nvarchar', 'rowDeleted': 'bit',
            'createTime': 'datetime', 'updateTime': 'datetime'}.items()]

    def __call__(self, database, sql, params):
        self.calls.append((database, sql, params))
        assert 'sys.columns' not in sql
        assert '[dbo].[PortServiceType]' in sql
        return [{'portServiceTypeId': 7, 'portServiceTypeCode': 'CAPNUOC',
                 'portServiceTypeName': 'Cấp nước', 'rowDeleted': False,
                 'createTime': datetime(2022, 1, 2), 'updateTime': None}]


def test_service_catalog_reads_native_service_type_not_billing_item():
    source = ServiceSource()
    result = extract_operation_catalogs(source, resources=['oprt.serviceType'],
                                         report_date=date(2026, 9, 25))['oprt.serviceType']
    assert result['ready']
    assert len(source.described) == len(source.calls) == 2
    assert len(result['rows']) == 1
    assert result['rows'][0]['serviceTypeId'] == '7'
    assert result['rows'][0]['serviceTypeCode'] == 'CAPNUOC'


def test_metadata_hook_failure_blocks_only_affected_source_and_sanitizes_errors():
    source = ServiceSource()
    original = source.describe_table
    def describe(database, table):
        if database == 'SmartTOS_BenThuy':
            raise RuntimeError('private-driver-connection-secret')
        return original(database, table)
    source.describe_table = describe
    result = extract_operation_catalogs(source, resources=['oprt.serviceType'])['oprt.serviceType']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'
    assert 'private-driver' not in str(result)


def test_malformed_description_does_not_fall_back_or_read_data():
    source = ServiceSource()
    source.describe_table = lambda *args: {'column_name': 'id'}
    result = extract_operation_catalogs(source, resources=['oprt.serviceType'])['oprt.serviceType']
    assert not result['ready'] and result['rows'] == []
    assert all(b['code'] == 'SOURCE_SCHEMA' for b in result['blockers'])
    assert source.calls == []
