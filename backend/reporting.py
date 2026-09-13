"""Bounded report snapshots and drill-downs from the same source read."""

from collections import OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import isfinite
import os
from pathlib import Path
from threading import Event, RLock
from time import monotonic, perf_counter
from uuid import uuid4

if __package__:
    from .snapshot_store import SnapshotStore, SnapshotStorageError, SnapshotStorageCapacity
    from .database import DatabaseUnavailable
    from .repository import (TERMINALS, DashboardRepository, VoyageNotFound, _aggregate,
                             _decimal, _number, _panel_values, _timestamp, _voyage_daily_history,
                             dashboard_repo, date_range)
else:
    from snapshot_store import SnapshotStore, SnapshotStorageError, SnapshotStorageCapacity
    from database import DatabaseUnavailable
    from repository import (TERMINALS, DashboardRepository, VoyageNotFound, _aggregate,
                            _decimal, _number, _panel_values, _timestamp, _voyage_daily_history,
                            dashboard_repo, date_range)


class ReportSnapshotNotFound(LookupError):
    code = "REPORT_EXPIRED"
    message = "Phiên báo cáo đã hết hạn. Vui lòng tải lại báo cáo."

    def __init__(self):
        super().__init__(self.message)


class ReportCapacityError(DatabaseUnavailable):
    code = "REPORT_TOO_LARGE"
    message = "Báo cáo vượt giới hạn lưu phiên. Vui lòng chọn kỳ ngắn hơn."


class ReportBusy(DatabaseUnavailable):
    code = "REPORT_BUSY"
    message = "Máy chủ đang xử lý báo cáo. Vui lòng thử lại sau."


class ReportStorageUnavailable(DatabaseUnavailable):
    code = "REPORT_STORAGE_UNAVAILABLE"
    message = "Chưa truy cập được bản lưu báo cáo. Vui lòng thử lại hoặc liên hệ quản trị."


@dataclass(frozen=True)
class _Snapshot:
    report_id: str
    report: dict
    rows: tuple
    created: float
    expires: float


@dataclass
class _Flight:
    done: Event = field(default_factory=Event)
    value: object = None
    error: Exception | None = None


_OPERATION_FILTERS = frozenset({"all", "with_values", "missing_weight"})
_ISSUES = frozenset({"all", "missing_weight", "unknown_unit", "negative"})
_METRIC_OPERATIONS = ("get_report", "get_report_snapshot", "drilldown", "get_voyage_from_report",
                      "export_snapshot", "export_drilldown", "get_voyage_progress", "source_read")


def _cargo_group(row):
    name = row["cargo_name"]
    return "Hàng container" if str(name).strip().upper() in DashboardRepository.container_cargo_codes else name


def _has_values(row):
    return any(value is not None and _decimal(value) != 0
               for value in (row.get("native_weight"), row.get("quantity")))


def _sort_row(row):
    identifier = str(row["id"])
    stamp = row.get("latest_operation_at")
    stamp = stamp.isoformat() if isinstance(stamp, (date, datetime)) else str(stamp or "")
    return (row["operation_day"], stamp, row["terminal_id"],
            int(identifier) if identifier.isdigit() else -1, identifier)


def _operation(row):
    values = _panel_values(_aggregate([row]))
    identifier = str(row["id"])
    return {
        "id": identifier, "source_id": identifier, "source_type": "tally_shift",
        "row_key": f"{row['terminal_id']}:{identifier}",
        "terminal_id": row["terminal_id"], "terminal_name": row["terminal_name"],
        "voyage_id": str(row["vessel_id"]) if row.get("vessel_id") is not None else None,
        "source_voyage_id": str(row["source_voyage_id"]) if row.get("source_voyage_id") is not None else None,
        "vessel_name": row.get("vessel_name"), "voyage_code": row.get("voyage_code"),
        "operation_code": row.get("operation_code"), "operation_date": row["operation_day"].isoformat(),
        "shift_id": str(row["shift_id"]) if row.get("shift_id") is not None else None,
        "shift_code": row.get("shift_code"), "cargo_name": row["cargo_name"], "cargo_group": _cargo_group(row),
        "customer_id": row.get("customer_id"), "customer_name": row.get("customer_name"),
        "job_method": row.get("job_method"), "job_method_code": row.get("job_method_code"),
        "direction": "Hàng xếp" if int(row["direction_id"]) == 1 else "Hàng dỡ",
        "quantity": _number(row["quantity"]) if row.get("quantity") is not None else None,
        "quantity_unit": row.get("quantity_unit"), "quantity_unit_name": row.get("quantity_unit_name"),
        "weight": _number(row["native_weight"]) if row.get("native_weight") is not None else None,
        "weight_unit": row["unit_code"], "weight_unit_name": row["unit_name"],
        "tonnage": values["tonnage"], "teu": values["teu"],
        "tonnage_status": values["tonnage_status"], "teu_status": values["teu_status"],
    }


def _paged(rows, operation_filter="all", page=1, page_size=25):
    if not isinstance(operation_filter, str) or operation_filter not in _OPERATION_FILTERS:
        raise ValueError("Bộ lọc tác nghiệp không hợp lệ.")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("Số trang phải là số nguyên dương.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("Mỗi trang từ 1 đến 100 dòng.")
    groups = {"all": rows, "with_values": [row for row in rows if _has_values(row)],
              "missing_weight": [row for row in rows if row.get("native_weight") is None]}
    counts = {name: len(items) for name, items in groups.items()}
    selected = groups[operation_filter]
    total, offset = len(selected), (page - 1) * page_size
    pages = (total + page_size - 1) // page_size
    if page > max(1, pages):
        raise ValueError("Trang tác nghiệp nằm ngoài phạm vi.")
    return {"filter": operation_filter, "page": page, "page_size": page_size,
            "total": total, "total_all": len(rows), "total_pages": pages, "counts": counts,
            "rows": [_operation(row) for row in selected[offset:offset + page_size]]}


def _export_operation(row):
    # Display quantities are rounded to three decimals. Retain source precision
    # and time separately so a closed-report digest detects smaller source edits.
    return {**_operation(row), "source_operation_at": _timestamp(row.get("latest_operation_at")),
            "source_arrival_at": _timestamp(row.get("arrival_at")),
            "source_departure_at": _timestamp(row.get("departure_at")),
            **{f"source_{key}": str(row[key]) if row.get(key) is not None else None
               for key in ("native_weight", "quantity", "tonne_factor", "teu")}}


def _shift_totals(rows):
    groups = defaultdict(list)
    for row in rows:
        shift_id = str(row["shift_id"]) if row.get("shift_id") is not None else None
        groups[(row["terminal_id"], row["operation_day"], shift_id)].append(row)
    result = []
    for (terminal_id, day, shift_id), items in sorted(groups.items(), key=lambda item: (
            item[0][1], item[0][0], item[0][2] or "")):
        result.append({"terminal_id": terminal_id, "terminal_name": items[0]["terminal_name"],
                       "date": day.isoformat(), "shift_id": shift_id, "shift_code": items[0].get("shift_code"),
                       **_panel_values(_aggregate(items))})
    return result


def _enrich_report(report):
    names = {name: terminal for terminal, (_, name) in TERMINALS.items()}
    for row in report["terminals"]:
        row["terminal_id"] = names.get(row["name"])
    for row in report["customers"]:
        identifier = row["customer_id"] if row["customer_id"] is not None else "unassigned"
        row["customer_key"] = f"{row['terminal_id']}:{identifier}"
        row["drilldown_customer_id"] = identifier
    return report


class ReportingService:
    def __init__(self, repo=None, *, cache_ttl=30.0, snapshot_ttl=900.0,
                 max_snapshots=8, max_snapshot_rows=100_000, max_inflight=4,
                 wait_timeout=35.0, clock=monotonic, snapshot_store=None):
        if any(not isfinite(value) for value in (cache_ttl, snapshot_ttl, wait_timeout)) or cache_ttl < 0 or snapshot_ttl <= 0 or wait_timeout <= 0:
            raise ValueError("Cache durations must be nonnegative with positive retention/wait limits.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
               for value in (max_snapshots, max_snapshot_rows, max_inflight)):
            raise ValueError("Cache capacities must be positive integers.")
        self.repo = repo or dashboard_repo
        self.cache_ttl, self.snapshot_ttl = float(cache_ttl), float(snapshot_ttl)
        self.max_snapshots, self.max_snapshot_rows = max_snapshots, max_snapshot_rows
        self.max_inflight, self.wait_timeout = max_inflight, float(wait_timeout)
        self._clock = clock
        self.snapshot_store = snapshot_store
        self._lock = RLock()
        self._snapshots = OrderedDict()
        self._cache = OrderedDict()
        self._flights = {}
        self._progress = OrderedDict()
        self._stored_rows = 0
        self._counters = {key: 0 for key in ("hits", "misses", "coalesced", "refreshes", "evictions", "expired")}
        self._metrics = {name: {"success_count": 0, "failure_count": 0, "latency_ms_total": 0.0,
                               "latency_ms_max": 0.0} for name in _METRIC_OPERATIONS}

    def _measure(self, name, function):
        started = perf_counter()
        success = False
        try:
            result = function()
            success = True
            return result
        finally:
            elapsed = max(0.0, (perf_counter() - started) * 1000)
            with self._lock:
                metric = self._metrics[name]
                metric["success_count" if success else "failure_count"] += 1
                metric["latency_ms_total"] += elapsed
                metric["latency_ms_max"] = max(metric["latency_ms_max"], elapsed)

    def _drop_snapshot(self, report_id, reason):
        snapshot = self._snapshots.pop(report_id)
        self._stored_rows -= len(snapshot.rows)
        for key, (cached_id, _) in list(self._cache.items()):
            if cached_id == report_id:
                del self._cache[key]
        self._counters[reason] += 1

    def _prune(self, now):
        for report_id, snapshot in list(self._snapshots.items()):
            if snapshot.expires <= now:
                self._drop_snapshot(report_id, "expired")
        for key, (_, expires) in list(self._cache.items()):
            if expires <= now:
                del self._cache[key]
        for key, (_, expires) in list(self._progress.items()):
            if expires <= now:
                del self._progress[key]

    def _snapshot(self, report_id):
        if not isinstance(report_id, str):
            raise ReportSnapshotNotFound()
        with self._lock:
            self._prune(self._clock())
            snapshot = self._snapshots.get(report_id)
            if snapshot is not None:
                self._snapshots.move_to_end(report_id)
                return snapshot
        # File I/O and decompression must not serialize unrelated source reads.
        stored = self._storage_call(self.snapshot_store.get, report_id) if self.snapshot_store else None
        if stored is None:
            raise ReportSnapshotNotFound()
        snapshot = self._restore(stored)
        with self._lock:
            self._prune(self._clock())
            self._remember(snapshot)
        return snapshot

    @staticmethod
    def _storage_key(start, end, terminal):
        return f"{start.isoformat()}:{end.isoformat()}:{terminal}"

    @staticmethod
    def _storage_call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SnapshotStorageCapacity:
            raise ReportCapacityError() from None
        except SnapshotStorageError:
            raise ReportStorageUnavailable() from None

    def _restore(self, stored):
        if len(stored["rows"]) > self.max_snapshot_rows:
            raise ReportCapacityError()
        wall_now, now = self.snapshot_store.clock(), self._clock()
        return _Snapshot(stored["report"]["meta"]["report_id"], stored["report"], tuple(stored["rows"]),
                         now - max(0, wall_now - stored["created"]), now + max(0, stored["expires"] - wall_now))

    def _remember(self, snapshot):
        if len(snapshot.rows) > self.max_snapshot_rows:
            return
        existing = self._snapshots.pop(snapshot.report_id, None)
        if existing is not None:
            self._stored_rows -= len(existing.rows)
        while self._snapshots and (len(self._snapshots) >= self.max_snapshots
                or self._stored_rows + len(snapshot.rows) > self.max_snapshot_rows):
            self._drop_snapshot(next(iter(self._snapshots)), "evictions")
        self._snapshots[snapshot.report_id] = snapshot
        self._stored_rows += len(snapshot.rows)

    def _view(self, snapshot, status):
        report = deepcopy(snapshot.report)
        report["meta"]["cache"] = {"status": status,
            "age_seconds": round(max(0, self._clock() - snapshot.created), 3),
            "ttl_seconds": self.cache_ttl,
            "snapshot_expires_in_seconds": round(max(0, snapshot.expires - self._clock()), 3)}
        return report

    def _flight(self, key, loader):
        with self._lock:
            flight = self._flights.get(key)
            owner = flight is None
            if owner:
                if len(self._flights) >= self.max_inflight:
                    raise ReportBusy()
                flight = _Flight()
                self._flights[key] = flight
            else:
                self._counters["coalesced"] += 1
        if not owner:
            if not flight.done.wait(self.wait_timeout):
                raise ReportBusy()
            if flight.error is not None:
                raise flight.error
            return flight.value, "coalesced"
        try:
            flight.value = loader()
            return flight.value, "miss"
        except Exception as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
                flight.done.set()

    def get_report(self, start_date=None, end_date=None, terminal="all", refresh=False):
        return self._measure("get_report", lambda: self._get_report(start_date, end_date, terminal, refresh))

    def _get_report(self, start_date, end_date, terminal, refresh):
        start, end = date_range(start_date, end_date, terminal)
        key = ("report", start, end, terminal)
        with self._lock:
            self._prune(self._clock())
            observed_id = self._cache.get(key, (None, None))[0]
            if not refresh and key in self._cache:
                report_id, _ = self._cache[key]
                self._counters["hits"] += 1
                self._cache.move_to_end(key)
                return self._view(self._snapshot(report_id), "hit")
            self._counters["misses"] += 1
            if refresh:
                self._counters["refreshes"] += 1

        def load():
            # Another completed owner may have filled the cache between this
            # request's cache check and flight registration. Reuse that read.
            with self._lock:
                self._prune(self._clock())
                current_id = self._cache.get(key, (None, None))[0]
                if current_id is not None and (not refresh or current_id != observed_id):
                    return self._snapshot(current_id)
            if not refresh and self.snapshot_store is not None:
                stored = self._storage_call(self.snapshot_store.get_fresh, self._storage_key(start, end, terminal))
                if stored is not None:
                    snapshot = self._restore(stored)
                    with self._lock:
                        self._remember(snapshot)
                        remaining = max(0, stored["fresh_until"] - self.snapshot_store.clock())
                        self._cache[key] = (snapshot.report_id, self._clock() + remaining)
                    return snapshot
            data = self._measure("source_read", lambda: self.repo.read_report(start, end, terminal))
            rows = data["rows"]
            if len(rows) > self.max_snapshot_rows:
                raise ReportCapacityError()
            report = _enrich_report(deepcopy(data["report"]))
            report_id = uuid4().hex
            report["meta"].update(report_id=report_id, source_read_at=datetime.now(timezone.utc).isoformat(),
                                  read_consistency="single_fact_set")
            created = self._clock()
            snapshot = _Snapshot(report_id, report, tuple(deepcopy(sorted(rows, key=_sort_row, reverse=True))),
                                 created, created + self.snapshot_ttl)
            if self.snapshot_store is not None:
                self._storage_call(self.snapshot_store.put, report_id, self._storage_key(start, end, terminal), report,
                                   snapshot.rows, ttl=self.snapshot_ttl, fresh_ttl=self.cache_ttl)
            with self._lock:
                self._prune(created)
                self._remember(snapshot)
                self._cache[key] = (report_id, created + self.cache_ttl)
            return snapshot

        snapshot, status = self._flight(key, load)
        return self._view(snapshot, status)

    def get_report_snapshot(self, report_id):
        return self._measure("get_report_snapshot", lambda: self._view(self._snapshot(report_id), "snapshot"))

    def export_snapshot(self, report_id):
        def export():
            snapshot = self._snapshot(report_id)
            return {"report": deepcopy(snapshot.report), "operations": [_export_operation(row) for row in snapshot.rows]}
        return self._measure("export_snapshot", export)

    def _scope(self, snapshot, *, day=None, terminal=None, cargo=None, customer_id=None,
               customer_terminal=None, voyage_id=None, issue="all"):
        filters = snapshot.report["meta"]["filters"]
        if not isinstance(issue, str) or issue not in _ISSUES:
            raise ValueError("Nhóm dữ liệu cần đối soát không hợp lệ.")
        if terminal == "all":
            terminal = None
        if terminal is not None and terminal not in TERMINALS:
            raise ValueError("Xí nghiệp không hợp lệ.")
        if terminal is not None and filters["terminal"] not in {"all", terminal}:
            raise ValueError("Xí nghiệp nằm ngoài phạm vi phiên báo cáo.")
        if day is not None:
            if isinstance(day, str):
                day = date.fromisoformat(day)
            if isinstance(day, datetime) or not isinstance(day, date):
                raise ValueError("Ngày tác nghiệp không hợp lệ.")
            if not date.fromisoformat(filters["start_date"]) <= day <= date.fromisoformat(filters["end_date"]):
                raise ValueError("Ngày tác nghiệp nằm ngoài kỳ báo cáo.")
        if customer_terminal is not None and customer_terminal not in TERMINALS:
            raise ValueError("Xí nghiệp của khách hàng không hợp lệ.")
        if customer_id is not None:
            customer_terminal = customer_terminal or terminal or (filters["terminal"] if filters["terminal"] != "all" else None)
            if customer_terminal is None:
                raise ValueError("Chọn xí nghiệp cùng mã khách hàng để đối soát chính xác.")
            if filters["terminal"] not in {"all", customer_terminal} or terminal not in {None, customer_terminal}:
                raise ValueError("Khách hàng nằm ngoài xí nghiệp đã chọn.")
            customer_id = str(customer_id)
        if voyage_id is not None:
            voyage_id = str(voyage_id)
            if voyage_id != "unassigned" and (not voyage_id.isdigit() or int(voyage_id) < 1):
                raise ValueError("Mã chuyến tàu không hợp lệ.")
            if terminal is None and filters["terminal"] == "all":
                raise ValueError("Chọn xí nghiệp cùng mã chuyến tàu.")
        rows = []
        for row in snapshot.rows:
            if day is not None and row["operation_day"] != day:
                continue
            if terminal is not None and row["terminal_id"] != terminal:
                continue
            if cargo is not None and _cargo_group(row) != cargo:
                continue
            selected_customer = None if customer_id == "unassigned" else customer_id
            if customer_id is not None and (row["terminal_id"] != customer_terminal or row.get("customer_id") != selected_customer):
                continue
            if voyage_id == "unassigned" and row.get("vessel_id") is not None:
                continue
            if voyage_id not in {None, "unassigned"} and row.get("vessel_id") != voyage_id:
                continue
            if issue == "missing_weight" and row.get("native_weight") is not None:
                continue
            if issue == "unknown_unit" and row.get("mass_unit_known"):
                continue
            if issue == "negative" and not any(value is not None and _decimal(value) < 0
                    for value in (row.get("native_weight"), row.get("quantity"))):
                continue
            rows.append(row)
        selected = {"day": day.isoformat() if day is not None else None, "terminal": terminal,
                    "cargo": cargo, "customer_id": customer_id, "customer_terminal": customer_terminal,
                    "voyage_id": voyage_id, "issue": issue}
        return rows, selected

    def _aggregate_scope(self, snapshot, rows, terminal=None):
        filters = snapshot.report["meta"]["filters"]
        start, end = date.fromisoformat(filters["start_date"]), date.fromisoformat(filters["end_date"])
        report = self.repo._dashboard_from_rows(deepcopy(rows), start, end, terminal or filters["terminal"])
        return _enrich_report(report)

    def drilldown(self, report_id, *, day=None, terminal=None, cargo=None, customer_id=None,
                  customer_terminal=None, voyage_id=None, issue="all", operation_filter="all", page=1, page_size=25):
        def drill():
            snapshot = self._snapshot(report_id)
            rows, selected = self._scope(snapshot, day=day, terminal=terminal, cargo=cargo,
                customer_id=customer_id, customer_terminal=customer_terminal, voyage_id=voyage_id, issue=issue)
            operations = _paged(rows, operation_filter, page, page_size)
            report = self._aggregate_scope(snapshot, rows, selected["terminal"])
            selected["operation_filter"] = operation_filter
            return {"report_id": report_id, "filters": selected,
                "summary": _panel_values(_aggregate(rows)), "cargo": report["cargo"],
                "daily": report["daily_history"], "terminals": report["terminals"],
                "native_units": report["native_units"], "shifts": _shift_totals(rows), "operations": operations,
                "meta": {"report_id": report_id, "source_read_at": snapshot.report["meta"]["source_read_at"],
                         "filters": selected, "snapshot_filters": deepcopy(snapshot.report["meta"]["filters"]),
                         "read_consistency": "immutable_report_snapshot"}}
        return self._measure("drilldown", drill)

    def export_drilldown(self, report_id, *, day=None, terminal=None, cargo=None, customer_id=None,
                         customer_terminal=None, voyage_id=None, issue="all", operation_filter="all"):
        """Export one selected bounded fact set without repeating per-page aggregation."""
        def export():
            if not isinstance(operation_filter, str) or operation_filter not in _OPERATION_FILTERS:
                raise ValueError("Bộ lọc tác nghiệp không hợp lệ.")
            snapshot = self._snapshot(report_id)
            rows, selected = self._scope(snapshot, day=day, terminal=terminal, cargo=cargo,
                customer_id=customer_id, customer_terminal=customer_terminal, voyage_id=voyage_id, issue=issue)
            report = self._aggregate_scope(snapshot, rows, selected["terminal"])
            included = rows if operation_filter == "all" else [row for row in rows if (
                _has_values(row) if operation_filter == "with_values" else row.get("native_weight") is None)]
            report["summary"] = _panel_values(_aggregate(rows))
            report["meta"].update(report_id=report_id,
                source_read_at=snapshot.report["meta"]["source_read_at"],
                snapshot_filters=deepcopy(snapshot.report["meta"]["filters"]), selection=selected,
                operations_filter=operation_filter, exported_row_count=len(included),
                read_consistency="immutable_report_snapshot")
            return {"report": report, "operations": [_operation(row) for row in included],
                    "shifts": _shift_totals(rows)}
        return self._measure("export_drilldown", export)

    def get_voyage_from_report(self, report_id, terminal, voyage_id, page=1, page_size=25, operation_filter="all"):
        def detail():
            if terminal not in TERMINALS:
                raise ValueError("Chọn một xí nghiệp hợp lệ.")
            snapshot = self._snapshot(report_id)
            rows, _ = self._scope(snapshot, terminal=terminal, voyage_id=voyage_id)
            if not rows:
                raise VoyageNotFound("Không có chuyến tàu hợp lệ trong phiên báo cáo.")
            report = self._aggregate_scope(snapshot, rows, terminal)
            header = next((row for row in report["voyages"] if row["voyage_id"] == str(voyage_id)), None)
            if header is None:
                raise VoyageNotFound("Không có chuyến tàu hợp lệ trong phiên báo cáo.")
            filters = snapshot.report["meta"]["filters"]
            start, end = date.fromisoformat(filters["start_date"]), date.fromisoformat(filters["end_date"])
            return {"header": header, "summary": _panel_values(_aggregate(rows)), "cargo": report["cargo"],
                "daily": _voyage_daily_history(report["daily_history"], header, start, end),
                "native_units": report["native_units"], "shifts": _shift_totals(rows),
                "operations": _paged(rows, operation_filter, page, page_size),
                "meta": {"report_id": report_id, "source_read_at": snapshot.report["meta"]["source_read_at"],
                    "generated_at": snapshot.report["meta"]["generated_at"], "metric_coverage": report["meta"]["metric_coverage"],
                    "filters": {**filters, "terminal": terminal, "voyage_id": str(voyage_id), "operation_filter": operation_filter},
                    "operations_order": "shiftDate DESC, tallyShiftId DESC", "read_consistency": "immutable_report_snapshot"}}
        return self._measure("get_voyage_from_report", detail)

    def get_voyage_progress(self, terminal, voyage_id, refresh=False):
        def progress():
            key = ("progress", terminal, str(voyage_id))
            with self._lock:
                self._prune(self._clock())
                observed = self._progress.get(key)
                if not refresh and key in self._progress:
                    value, _ = self._progress[key]
                    self._progress.move_to_end(key)
                    return deepcopy(value)
            def load():
                with self._lock:
                    self._prune(self._clock())
                    current = self._progress.get(key)
                    if current is not None and (not refresh or current is not observed):
                        return deepcopy(current[0])
                result = self._measure("source_read", lambda: self.repo.read_voyage_lifetime(terminal, voyage_id))
                if len(result["rows"]) > self.max_snapshot_rows:
                    raise ReportCapacityError()
                value = {"header": result["header"], "summary": result["summary"],
                         "shifts": _shift_totals(result["rows"]), "meta": result["meta"]}
                with self._lock:
                    while len(self._progress) >= self.max_snapshots:
                        self._progress.popitem(last=False)
                    self._progress[key] = (deepcopy(value), self._clock() + self.cache_ttl)
                return value
            value, _ = self._flight(key, load)
            return deepcopy(value)
        return self._measure("get_voyage_progress", progress)

    def get_metrics(self):
        with self._lock:
            self._prune(self._clock())
            metrics = deepcopy(self._metrics)
            for values in metrics.values():
                count = values["success_count"] + values["failure_count"]
                values["request_count"] = count
                values["latency_ms_mean"] = round(values["latency_ms_total"] / count, 3) if count else 0
                values["latency_ms_total"] = round(values["latency_ms_total"], 3)
                values["latency_ms_max"] = round(values["latency_ms_max"], 3)
            return {"operations": metrics, "storage": self._storage_call(self.snapshot_store.stats) if self.snapshot_store else None,
                "cache": {**self._counters,
                "entries": len(self._cache), "snapshots": len(self._snapshots), "stored_rows": self._stored_rows,
                "inflight": len(self._flights), "progress_entries": len(self._progress),
                "max_snapshots": self.max_snapshots, "max_snapshot_rows": self.max_snapshot_rows,
                "ttl_seconds": self.cache_ttl, "snapshot_ttl_seconds": self.snapshot_ttl}}


_state_path = Path(os.environ.get('DASHBOARD_STATE_PATH', str(Path(__file__).resolve().parent / '.data' / 'control.sqlite3')))
reporting_service = ReportingService(snapshot_store=SnapshotStore(_state_path.with_name('report-cache.sqlite3')))
