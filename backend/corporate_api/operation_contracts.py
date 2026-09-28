"""Bulk/Container v1.5 catalog contracts, separate from the S payloads.

The field tables take precedence over stale examples. Struck-out fields are
excluded. Optional source attributes stay null when they cannot be established;
required values must never be fabricated. Catalog IDs remain native source IDs.
"""
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, PlainSerializer, StringConstraints, field_validator

from .contracts import Contract, Day, Flag, Identifier, Number

def _finite_json_number(value: Decimal) -> Decimal:
    # A finite Decimal can overflow the JSON float serializer. Reject it at
    # validation rather than emitting Infinity or inventing a business limit.
    if not isfinite(float(value)):
        raise ValueError('Number must remain finite when serialized to JSON.')
    return value


SignedNumber = Annotated[Decimal, Field(allow_inf_nan=False), AfterValidator(_finite_json_number),
                         PlainSerializer(float, return_type=float, when_used='json')]
Count = Annotated[int, Field(ge=0)]


def _reject_boolean_status(value):
    if isinstance(value, bool):
        raise ValueError('Trạng thái tổ/nhân sự phải là mã số 1, 2 hoặc 3, không phải cờ boolean.')
    return value


WorkStatus = Annotated[Literal[1, 2, 3], BeforeValidator(_reject_boolean_status)]


class OperationMaster(Contract):
    reportDate: date
    companyId: Identifier
    isDeleted: Flag | None = None
    createdDate: datetime | None = None
    modifiedDate: datetime | None = None


class ChangedMaster(OperationMaster):
    isUpdated: Flag | None = None


class PortEquipment(ChangedMaster):
    equipmentId: Identifier
    equipmentCode: Identifier
    equipmentName: str | None = None
    equipmentTypeId: Identifier
    manufacturerName: str | None = None
    serialCode: Identifier
    registrationCode: Identifier
    lastMaintainHistory: str | None = None
    spec: str | None = None
    maxCapacity: str | None = None
    installDate: date | None = None
    validDate: date | None = None
    currentRunningHour: Number | None = None
    runningHourCheckDate: date | None = None
    # Retained in the field table; comment proposes removing it. No inferred value.
    workPerHour: Number | None = None
    operationLocationTypeId: list[Identifier] | None = None
    backColor: str | None = None
    foreColor: str | None = None
    remark: str | None = None
    isRent: Flag
    rentPeriod: str | None = None
    status: Flag | None = None
    staffCd: str | None = None


class PortEquipType(ChangedMaster):
    equipmentTypeId: Identifier
    equipmentTypeCode: Identifier
    equipmentTypeName: str | None = None


class YardBase(ChangedMaster):
    whYardId: Identifier
    whYardCode: Identifier
    storageType: Literal['W', 'Y'] | None = None
    whYardTypeId: Identifier | None = None
    cargoType: str | None = None
    zoneCode: str | None = None
    maxSlotCapacity: Count | None = None
    x: SignedNumber | None = None
    y: SignedNumber | None = None
    staffCd: str | None = None


class PortWHYard(YardBase):
    status: str | None = None


class PortWHYardType(ChangedMaster):
    # The parent ID is named whTypeId in both specs, despite whYardTypeId FKs.
    whTypeId: Identifier
    whYardTypeCode: Identifier
    whYardTypeName: str | None = None


class Berth(ChangedMaster):
    berthId: Identifier
    berthCode: Identifier
    berthSeq: Count | None = None
    # The workbook does not require positive-only depths. SmartTOS
    # stores signed depths (for example -13); preserve the source datum/sign.
    berthDepth: SignedNumber | None = None
    capacity: str | None = None
    posFrom: SignedNumber | None = None
    posTo: SignedNumber | None = None
    status: Flag | None = None
    remark: str | None = None
    portCode: str | None = None
    staffCd: str | None = None


class JobType(ChangedMaster):
    jobTypeId: Identifier
    jobTypeCode: Identifier
    jobTypeName: str | None = None


class JobMethod(ChangedMaster):
    jobMethodId: Identifier
    jobMethodCode: Identifier
    jobMethodName: str | None = None


class DeliveryMethod(ChangedMaster):
    deliveryMethodId: Identifier
    deliveryMethodCode: Identifier
    deliveryMethodName: str | None = None
    deliveryMethodGroup: Literal['T', 'B', 'S'] | None = None


class ServiceType(ChangedMaster):
    serviceTypeId: Identifier
    serviceTypeCode: Identifier
    serviceTypeName: str | None = None


class CargoItem(OperationMaster):
    cargoItemId: Identifier
    cargoItemCode: Identifier
    cargoItemName: str | None = None
    cargoItemShortName: str | None = None
    cargoGroupId: Identifier
    dangerousGoodsCheck: Flag
    dangerousGoodsCode: str | None = None


class CargoGroup(OperationMaster):
    cargoGroupId: Identifier
    cargoGroupCode: Identifier
    cargoGroupName: str | None = None
    # D59 duplicates cargoGroupName with a different description; no invented key.


class UnitMeasurement(ChangedMaster):
    unitId: Identifier
    unitCode: Identifier
    unitName: str | None = None


class CargoDirect(ChangedMaster):
    cargoDirectId: Identifier
    cargoDirectCode: Identifier
    cargoDirectName: str | None = None


class OperationLocationType(ChangedMaster):
    operationLocationTypeId: Identifier
    operationLocationTypeCode: Identifier
    operationLocationTypeName: str | None = None


class PortOpTeam(ChangedMaster):
    teamId: Identifier
    teamCode: str | None = None
    teamName: str | None = None
    teamType: str | None = None
    status: WorkStatus


class PortOpStaff(ChangedMaster):
    staffId: Identifier
    staffCode: str | None = None
    staffName: str | None = None
    teamId: Identifier
    position: str | None = None
    status: WorkStatus


class VesselType(ChangedMaster):
    vesselTypeId: Identifier
    vesselTypeCode: Identifier
    vesselTypeName: str | None = None


class ContainerEquipment(ChangedMaster):
    # Container equipment has its own contract, not an alias of portEquipment.
    equipmentId: Identifier
    equipmentCode: Identifier
    equipmentName: Identifier
    equipmentTypeCode1: Identifier
    equipmentTypeName1: Identifier
    equipmentTypeCode2: Identifier
    equipmentTypeName2: Identifier
    manufacturerName: Identifier
    serialCode: Identifier
    registrationCode: Identifier
    lastMaintainHistory: str | None = None
    spec: str | None = None
    maxWGT: Number | None = None
    installDate: date
    validDate: date | None = None
    currentRunningHour: Number
    runningHourCheckDate: date | None = None
    blockCode: str | None = None
    bayCode: str | None = None
    rowCode: str | None = None
    tierCode: str | None = None
    area: str | None = None
    whYardCode: str | None = None
    accessDir: str | None = None
    bayDirection: str | None = None
    rowDirection: str | None = None
    status1: str | None = None
    status2: str | None = None
    rsCheck: Flag | None = None
    autoCheck: Flag | None = None
    passTier: Flag | None = None
    workPerHour: Number | None = None
    maxJob: Count | None = None
    gateIOType: str | None = None
    quayYTCheck: Flag | None = None
    whYardYTCheck: Flag | None = None
    railYTCheck: Flag | None = None
    railTally: Flag | None = None
    backColor: str | None = None
    foreColor: str | None = None
    remark: str | None = None
    isRent: Flag
    rentPeriod: str | None = None
    staffCd: str | None = None


class ContainerWHYard(YardBase):
    blockCode: str | None = None
    bayFrom: str | None = None
    bayTo: str | None = None
    rowFrom: str | None = None
    rowTo: str | None = None
    tierFrom: str | None = None
    tierTo: str | None = None
    equipment: str | None = None
    maxStackTier: Count | None = None
    importChk: Flag | None = None
    exportChk: Flag | None = None
    emptyChk: Flag | None = None
    whYardStatus: str | None = None
    occupancyRate: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False),
                            PlainSerializer(float, return_type=float, when_used='json')] | None = None


class ContSizeType(ChangedMaster):
    contSizeTypeId: Identifier
    contSizeTypeCode: Identifier
    contSizeTypeName: str | None = None
    sizeCode: str | None = None
    typeCode: str | None = None
    typeName: str | None = None
    heightCode: str | None = None
    teu: Number | None = None
    contWeight: Number | None = None
    maxWeightTon: Number | None = None
    remark: str | None = None


_CATALOGS = (
    ('portEquipment', PortEquipment, 'equipmentId'),
    ('portEquipType', PortEquipType, 'equipmentTypeId'),
    ('portWHYard', PortWHYard, 'whYardId'),
    ('portWHYardType', PortWHYardType, 'whTypeId'),
    ('berths', Berth, 'berthId'),
    ('jobType', JobType, 'jobTypeId'),
    ('jobMethod', JobMethod, 'jobMethodId'),
    ('deliveryMethod', DeliveryMethod, 'deliveryMethodId'),
    ('serviceType', ServiceType, 'serviceTypeId'),
    ('cargoItems', CargoItem, 'cargoItemId'),
    ('cargoGroups', CargoGroup, 'cargoGroupId'),
    ('unitMeasurement', UnitMeasurement, 'unitId'),
    ('cargoDirect', CargoDirect, 'cargoDirectId'),
    ('operationLocationType', OperationLocationType, 'operationLocationTypeId'),
    ('portOpTeam', PortOpTeam, 'teamId'),
    ('portOpStaff', PortOpStaff, 'staffId'),
    ('vesselType', VesselType, 'vesselTypeId'),
    ('equipments', ContainerEquipment, 'equipmentId'),
    ('contwhYards', ContainerWHYard, 'whYardId'),
    ('contSizeType', ContSizeType, 'contSizeTypeId'),
)
OPERATION_MODELS = {'oprt.' + name: model for name, model, _ in _CATALOGS}
OPERATION_IDENTITY = {'oprt.' + name: identity for name, _, identity in _CATALOGS}
OPERATION_PATHS = {
    'oprt.' + name: '/api/oprt/' + ('' if name in {'portOpTeam', 'portOpStaff'} else 'catalog/') + name
    for name, _, _ in _CATALOGS
}
# The workbook only defines collection GET endpoints, not detail or write routes.
OPERATION_DETAIL_PATHS = {}
OPERATION_FILTERS = {key: frozenset({'startDate', 'endDate'}) for key in OPERATION_MODELS}
OPERATION_REFERENCES = {
    key: {} for key in OPERATION_MODELS
}
OPERATION_REFERENCES.update({
    'oprt.portEquipment': {
        'equipmentTypeId': 'oprt.portEquipType',
        'operationLocationTypeId': 'oprt.operationLocationType',
    },
    'oprt.portWHYard': {'whYardTypeId': 'oprt.portWHYardType'},
    'oprt.contwhYards': {'whYardTypeId': 'oprt.portWHYardType'},
    'oprt.cargoItems': {'cargoGroupId': 'oprt.cargoGroups'},
    'oprt.portOpStaff': {'teamId': 'oprt.portOpTeam'},
})

# Resolutions recorded here so examples cannot silently change the wire schema.
SPEC_DECISIONS = (
    'Field tables override examples; strike-through fields are omitted.',
    'Queries require YYYYMMDD; reportDate uses ISO YYYY-MM-DD as in payload examples.',
    'Compact data/code/message body follows user instruction; pagination belongs in headers.',
    'portWHYardType identity is whTypeId; whYardTypeId foreign keys reference it.',
    'cargoGroups duplicate cargoGroupName appears once; ambiguous short-name field is omitted.',
    'cargoItems/cargoGroups have no isUpdated in field tables; example-only values are omitted.',
    'Bulk workPerHour remains optional: its deletion comment conflicts with the unstruck table.',
    'Bulk operationLocationTypeId follows list description rather than the scalar example.',
    'Container yard metadata stays top-level; stale Metadata and rowguid examples are omitted.',
    'Equipment and warehouse contracts differ by domain and must not be aliases.',
)


class OperationQuery(Contract):
    companyId: Identifier
    startDate: Day
    endDate: Day
    page: int = Field(default=1, ge=1, le=1000000)
    limit: int = Field(default=20, ge=1, le=100)
    snapshotId: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')] | None = None

    @field_validator('startDate', 'endDate')
    @classmethod
    def valid_day(cls, value):
        parsed = datetime.strptime(value, '%Y%m%d').date()
        if parsed < date(1970, 1, 1):
            raise ValueError('Ngày bắt đầu phải từ 19700101.')
        return value

    def check_resource(self, resource):
        from .errors import CorporateError
        if resource not in OPERATION_MODELS:
            raise CorporateError(422, 'UNSUPPORTED_RESOURCE', 'API danh mục vận hành không hợp lệ.')
        if self.endDate < self.startDate:
            raise CorporateError(422, 'INVALID_PERIOD', 'endDate phải từ startDate trở đi.')
