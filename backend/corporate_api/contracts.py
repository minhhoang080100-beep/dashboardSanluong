"""S v3 wire contracts. No RORO routes or inferred ISO/operator values."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints, field_validator


def _calendar_day(value):
    datetime.strptime(value, '%Y%m%d')
    return value

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=255, strip_whitespace=True)]
Day = Annotated[str, StringConstraints(pattern=r"^[0-9]{8}$"), AfterValidator(_calendar_day)]
Number = Annotated[Decimal, Field(ge=0, le=Decimal('1000000000000000'), allow_inf_nan=False),
                   PlainSerializer(float, return_type=float, when_used='json')]
Flag = Annotated[bool, PlainSerializer(lambda value: int(value), return_type=int, when_used='json')]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ReportRow(Contract):
    reportDate: Day


class MasterRow(ReportRow):
    createdDate: str | None
    modifiedDate: str | None


class ContQuay(ReportRow):
    companyId: Identifier
    classId: Identifier
    originId: Identifier | None
    containerWeight: Number
    containerTEU: Number
    handlingMethodId: Identifier
    finishDate: Day
    shipId: Identifier
    shipOperatorId: Identifier | None
    containerOperatorId: Identifier | None
    containerSizeId: Identifier


class ContGate(ReportRow):
    companyId: Identifier
    originId: Identifier | None
    containerWeight: Number
    containerTEU: Number
    handlingMethodId: Identifier
    finishDate: Day
    containerOperatorId: Identifier | None
    containerSizeId: Identifier


class BulkQuay(ReportRow):
    finishDate: Day
    companyId: Identifier
    shipId: Identifier
    shipAgentId: Identifier | None
    cargoTypeId: Identifier
    cargoCategoryId: Identifier
    handlingMethodId: Identifier
    shipClassId: Identifier
    bulkOriginId: Identifier | None
    bulkWeight: Number


class BulkGate(ReportRow):
    finishDate: Day
    companyId: Identifier
    cargoTypeId: Identifier
    cargoCategoryId: Identifier
    handlingMethodId: Identifier
    bulkOriginId: Identifier | None
    bulkWeight: Number
    customerCode: Identifier | None


class Ship(MasterRow):
    shipId: Identifier
    shipIMO: str | None
    shipFullName: Identifier
    shipGroup: str | None
    flagState: str | None
    shipLOA: Number | None
    shipBeam: Number | None
    shipGRT: Number | None
    shipType: str | None
    shipDWT: Number | None
    shipOwner: str | None


class CustomerMetadata(Contract):
    isDeleted: Flag
    createdDate: str | None
    modifiedDate: str | None


class Customer(ReportRow):
    customerCode: Identifier
    customerNameVN: Identifier | None
    customerNameEN: str | None
    customerTaxCode: str | None
    customerPhoneNum: str | None
    customerAddress: str | None
    customerEmail: str | None
    isCarrier: Flag | None
    isAgent: Flag | None
    customerStatus: str | None
    metadata: CustomerMetadata


class CargoType(MasterRow):
    cargoTypeId: Identifier
    cargoTypeName: Identifier


class CargoCategory(MasterRow):
    cargoTypeId: Identifier
    cargoParentId: Identifier | None
    cargoId: Identifier
    cargoName: Identifier


class HandlingMethod(MasterRow):
    handlingMethodId: Identifier
    handlingMethodName: Identifier


class CargoClass(MasterRow):
    classId: Identifier
    className: Identifier


class Origin(MasterRow):
    originId: Identifier
    originName: Identifier


class ContainerSize(MasterRow):
    containerSizeId: Identifier
    localSzTp: Identifier
    isoSzTp: str | None
    sizeCode: str | None
    heightCode: str | None
    containerTypeCode: str | None


MODELS = {
    'contQuayVolumesCB': ContQuay, 'contGateVolumesCB': ContGate,
    'bulkQuayVolumesCB': BulkQuay, 'bulkGateVolumesCB': BulkGate,
    'shipDetails': Ship, 'customers': Customer, 'cargoType': CargoType,
    'cargoCategory': CargoCategory, 'handlingMethodList': HandlingMethod,
    'class': CargoClass, 'origins': Origin, 'containerSize': ContainerSize,
}
PRODUCTION = frozenset(('contQuayVolumesCB', 'contGateVolumesCB', 'bulkQuayVolumesCB', 'bulkGateVolumesCB'))
IDENTITY = {
    'shipDetails': 'shipId', 'customers': 'customerCode', 'cargoType': 'cargoTypeId',
    'cargoCategory': 'cargoId', 'handlingMethodList': 'handlingMethodId',
    'class': 'classId', 'origins': 'originId', 'containerSize': 'containerSizeId',
}


class Pagination(Contract):
    page: int
    limit: int
    total: int
    hasNext: bool
    snapshotId: str
    sourceReadAt: str
    warnings: list[str]
    ruleVersion: str


T = TypeVar('T')


class Page(Contract, Generic[T]):
    data: list[T]
    code: str = '1'
    message: str = 'Lấy dữ liệu thành công'


class Query(Contract):
    companyId: Identifier
    startDate: Day | None = None
    endDate: Day | None = None
    shipId: Identifier | None = None
    handlingMethodId: Identifier | None = None
    cargoTypeId: Identifier | None = None
    containerSizeId: Identifier | None = None
    customerTaxCode: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    customerType: Identifier | None = None
    page: int = Field(default=1, ge=1, le=1000000)
    limit: int = Field(default=20, ge=1, le=100)
    snapshotId: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')] | None = None

    @field_validator('startDate', 'endDate')
    @classmethod
    def valid_day(cls, value):
        if value is not None:
            datetime.strptime(value, '%Y%m%d')
        return value

    def check_resource(self, resource):
        from .errors import CorporateError
        if bool(self.startDate) != bool(self.endDate):
            raise CorporateError(422, 'INVALID_PERIOD', 'Cần truyền đủ startDate và endDate.')
        if resource in PRODUCTION and self.startDate is None:
            raise CorporateError(422, 'PERIOD_REQUIRED', 'startDate và endDate là bắt buộc.')
        if self.startDate and self.endDate < self.startDate:
            raise CorporateError(422, 'INVALID_PERIOD', 'endDate phải từ startDate trở đi.')
        common = {'companyId', 'page', 'limit', 'snapshotId'}
        allowed = {
            'contQuayVolumesCB': {'startDate', 'endDate', 'shipId', 'handlingMethodId'},
            'bulkQuayVolumesCB': {'startDate', 'endDate', 'shipId', 'handlingMethodId'},
            'contGateVolumesCB': {'startDate', 'endDate', 'handlingMethodId'},
            'bulkGateVolumesCB': {'startDate', 'endDate', 'handlingMethodId'},
            'customers': {'startDate', 'endDate', 'customerTaxCode', 'customerType'},
            'cargoCategory': {'cargoTypeId'}, 'containerSize': {'containerSizeId'},
        }.get(resource, set())
        unexpected = {key for key in self.model_fields_set - common - allowed if getattr(self, key) is not None}
        if unexpected:
            raise CorporateError(422, 'UNSUPPORTED_FILTER', 'Bộ lọc không áp dụng cho API này.')


def day(value: date) -> str:
    return value.strftime('%Y%m%d')
