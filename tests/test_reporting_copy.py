"""Fact copies keep retained snapshots isolated from mutable source records."""

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

from backend.reporting import ReportingService, _copy_fact_rows
from test_reporting import StubRepository, fact


def test_flat_fact_copy_preserves_exact_values_and_row_isolation():
    row = fact(1, weight="123.456789")
    row.update(stamp=datetime(2026, 9, 12, tzinfo=timezone.utc),
               raw=b"evidence", flag=True, optional=None, fraction=0.5)
    copied = _copy_fact_rows([row])

    assert copied == deepcopy([row])
    assert copied[0] is not row
    assert type(copied[0]["native_weight"]) is Decimal
    assert copied[0]["native_weight"] == Decimal("123.456789")
    assert type(copied[0]["operation_day"]) is date
    assert type(copied[0]["stamp"]) is datetime
    copied[0]["native_weight"] = Decimal(999)
    assert row["native_weight"] == Decimal("123.456789")


def test_nested_fact_copy_preserves_shared_graph_without_source_aliases():
    flat = {"id": "flat", "amount": Decimal("1.000001")}
    notes = ["original"]
    nested = {"id": "nested", "related": flat, "notes": notes}
    nested["self"] = nested
    rows = [flat, nested, flat, {"notes": notes}]

    copied = _copy_fact_rows(rows)
    assert copied[0] is copied[2] is copied[1]["related"]
    assert copied[1]["self"] is copied[1]
    assert copied[1]["notes"] is copied[3]["notes"]
    copied[1]["notes"].append("changed")
    copied[0]["amount"] = Decimal(2)
    assert notes == ["original"]
    assert flat["amount"] == Decimal("1.000001")


def test_scalar_subclass_with_mutable_attributes_uses_isolated_copy():
    class TaggedInt(int):
        pass

    value = TaggedInt(3)
    value.notes = ["original"]
    copied = _copy_fact_rows([{"value": value}])
    copied[0]["value"].notes.append("changed")
    assert copied[0]["value"] == 3
    assert value.notes == ["original"]


def test_repeated_report_and_drilldown_keep_snapshot_source_immutable():
    class RetainingRepository(StubRepository):
        mutate_aggregation = False

        def read_report(self, *args, **kwargs):
            self.last_read = super().read_report(*args, **kwargs)
            return self.last_read

        def _dashboard_from_rows(self, rows, *args, **kwargs):
            result = super()._dashboard_from_rows(rows, *args, **kwargs)
            if self.mutate_aggregation:
                for row in rows:
                    row["native_weight"] = Decimal(999)
                    row["evidence"]["notes"].append("aggregation mutation")
            return result

    row = fact(1, weight="10.123456")
    row["evidence"] = {"notes": ["original"]}
    repo = RetainingRepository([row])
    service = ReportingService(repo, clock=lambda: 0)
    first = service.get_report("2026-09-11", "2026-09-12", "all")
    identifier = first["meta"]["report_id"]
    expected_export = service.export_snapshot(identifier)

    # A repository may retain and later reuse its returned mutable records.
    repo.last_read["rows"][0]["native_weight"] = Decimal(500)
    repo.last_read["rows"][0]["evidence"]["notes"].append("source mutation")
    first["overview"]["total_tonnage"] = -1
    first["meta"]["filters"]["terminal"] = "invalid"
    second = service.get_report("2026-09-11", "2026-09-12", "all")
    assert second["meta"]["report_id"] == identifier
    assert second["overview"] == expected_export["report"]["overview"]
    assert len(repo.calls) == 1

    # Aggregation may normalize rows in place; it must never receive the
    # retained snapshot's dictionaries or nested evidence objects.
    repo.mutate_aggregation = True
    first_drill = service.drilldown(identifier)
    second_drill = service.drilldown(identifier)
    assert first_drill == second_drill
    assert first_drill["summary"]["tonnage"] == second["overview"]["total_tonnage"]
    assert service.export_snapshot(identifier) == expected_export
    assert service._snapshot(identifier).rows[0]["evidence"]["notes"] == ["original"]
    assert len(repo.calls) == 1
