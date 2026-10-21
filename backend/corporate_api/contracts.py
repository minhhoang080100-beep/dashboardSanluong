"""S v3 wire contracts. No RORO routes or inferred ISO/operator values."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints, create_model, field_validator


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


class ErrorResult(Contract):
    """The same compact envelope used by validation, auth and source failures."""
    data: list = Field(max_length=0)
    code: Literal['0']
    message: str


COMMON_QUERY_FIELDS = frozenset({'companyId', 'page', 'limit', 'snapshotId'})
RESOURCE_QUERY_FIELDS = {
    'contQuayVolumesCB': frozenset({'startDate', 'endDate', 'shipId', 'handlingMethodId'}),
    'bulkQuayVolumesCB': frozenset({'startDate', 'endDate', 'shipId', 'handlingMethodId'}),
    'contGateVolumesCB': frozenset({'startDate', 'endDate', 'handlingMethodId'}),
    'bulkGateVolumesCB': frozenset({'startDate', 'endDate', 'handlingMethodId'}),
    'customers': frozenset({'startDate', 'endDate', 'customerTaxCode', 'customerType'}),
    'cargoCategory': frozenset({'cargoTypeId'}),
    'containerSize': frozenset({'containerSizeId'}),
    'origins': frozenset({'origin'}),
}


class Query(Contract):
    companyId: Identifier = Field(description='Mã đơn vị được cấp quyền; Cảng Nghệ Tĩnh sử dụng CNT.')
    startDate: Day | None = Field(default=None, description='Ngày bắt đầu, dạng YYYYMMDD. Bao gồm ngày này.')
    endDate: Day | None = Field(default=None, description='Ngày kết thúc, dạng YYYYMMDD. Bao gồm ngày này.')
    shipId: Identifier | None = None
    handlingMethodId: Identifier | None = None
    cargoTypeId: Identifier | None = None
    containerSizeId: Identifier | None = None
    customerTaxCode: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    customerType: Identifier | None = None
    origin: Annotated[str, StringConstraints(min_length=1, max_length=254, strip_whitespace=True)] | None = Field(
        default=None, description='Tìm chuỗi trong tên nguồn gốc hàng (originName), không phân biệt hoa/thường. Ký tự % và _ được hiểu theo nghĩa đen; độ dài dưới 255 ký tự.')
    page: int = Field(default=1, ge=1, le=1000000, description='Số trang, bắt đầu từ 1.')
    limit: int = Field(default=20, ge=1, le=100, description='Số bản ghi mỗi trang, tối đa 100.')
    snapshotId: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')] | None = Field(
        default=None, description='Chỉ dùng ở chế độ bản công bố; lấy từ X-Snapshot-Id trang 1. Chế độ trực tiếp không nhận tham số này.')

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
        if resource in PRODUCTION and self.endDate == '99991231':
            # Production SQL uses the next day as an exclusive upper bound.
            # Reject the unrepresentable boundary before profile/source reads,
            # rather than disguising date overflow as a SmartTOS outage.
            raise CorporateError(422, 'INVALID_PERIOD', 'endDate của API sản lượng phải trước 99991231.')
        allowed = RESOURCE_QUERY_FIELDS.get(resource, frozenset())
        unexpected = {key for key in self.model_fields_set - COMMON_QUERY_FIELDS - allowed if getattr(self, key) is not None}
        if unexpected:
            raise CorporateError(422, 'UNSUPPORTED_FILTER', 'Bộ lọc không áp dụng cho API này.')


def _public_query_model(resource):
    """Share field validation with readers without advertising unsupported filters."""
    allowed = COMMON_QUERY_FIELDS | RESOURCE_QUERY_FIELDS.get(resource, frozenset())
    fields = {}
    for name, definition in Query.model_fields.items():
        if name not in allowed:
            continue
        field = deepcopy(definition)
        annotation = field.annotation
        if resource in PRODUCTION and name in {'startDate', 'endDate'}:
            # These are required, non-null dates in all four production sheets.
            annotation = Day
            field.default = ...
        fields[name] = (annotation, field)
    return create_model(resource + 'Query', __base__=Contract, **fields)


PUBLIC_QUERY_MODELS = {resource: _public_query_model(resource) for resource in MODELS}


def day(value: date) -> str:
    return value.strftime('%Y%m%d')
