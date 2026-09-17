"""Report scopes must remain isolated across cache, snapshots and drill-downs."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import repository
from backend.database import DatabaseQueryError
from backend.reporting import ReportingService, ReportSnapshotNotFound
from backend.snapshot_store import SnapshotStore
from test_reporting import fact


class ScopeRepository(repository.DashboardRepository):
    def __init__(self):
        self.calls = []
        self.rows = [fact(1, weight="780"),
                     fact(2, weight="8954.97", voyage="102", production_scope="vietsun"),
                     fact(3, weight="5", voyage="103", production_scope="unclassified")]
        self.rows[2].update(initial_berth_id=None, initial_berth_code=None,
                            initial_berth_at=None, berth_assignment_status="ambiguous")

    def read_report(self, start, end, terminal, production_scope="nghe_tinh"):
        self.calls.append(production_scope)
        previous_start = start - timedelta(days=(end-start).days+1)
        rows = deepcopy([r for r in self.rows if r["production_scope"] == production_scope
                         and previous_start <= r["operation_day"] <= end])
        report = self._dashboard_from_rows(rows, start, end, terminal, production_scope=production_scope)
        return {"report":report,"rows":[r for r in rows if r["operation_day"]>=start]}


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(repository,"vietnam_today",lambda:date(2026,9,17))


def request(service, scope):
    return service.get_report("2026-09-12","2026-09-12","all",production_scope=scope)


def test_each_scope_has_independent_snapshot_totals_and_evidence(tmp_path):
    repo = ScopeRepository()
    path = tmp_path/"scope-cache.sqlite3"
    service = ReportingService(repo,snapshot_store=SnapshotStore(path))
    reports = {s:request(service,s) for s in repository.PRODUCTION_SCOPES}
    assert len({r["meta"]["report_id"] for r in reports.values()}) == 3
    for scope, total in [("nghe_tinh",780),("vietsun",8954.97),("unclassified",5)]:
        report = reports[scope]
        identifier = report["meta"]["report_id"]
        assert report["overview"]["total_tonnage"] == total
        assert request(service,scope)["meta"]["report_id"] == identifier
        drill = service.drilldown(identifier,production_scope=scope)
        assert drill["summary"]["tonnage"] == total
        assert drill["meta"]["filters"]["production_scope"] == scope
        assert drill["meta"]["berth_rule_version"] == repository.BERTH_RULE_VERSION
        assert {r["production_scope"] for r in drill["operations"]["rows"]} == {scope}
        exported = service.export_drilldown(identifier,production_scope=scope)
        assert exported["report"]["meta"]["filters"]["production_scope"] == scope
    assert repo.calls == list(repository.PRODUCTION_SCOPES)
    vietsun = reports["vietsun"]["meta"]["report_id"]
    vessel = service.get_voyage_from_report(vietsun,"cua_lo",102)
    assert vessel["header"]["initial_berth_code"] == "CẦU 5"
    assert vessel["summary"]["tonnage"] == 8954.97
    with pytest.raises(ValueError,match="Phạm vi"):
        service.drilldown(vietsun,production_scope="nghe_tinh")
    with pytest.raises(ValueError,match="Phạm vi"):
        service.export_drilldown(vietsun,production_scope="nghe_tinh")
    repo.rows[1]["native_weight"] = 1
    restarted = ReportingService(repo,snapshot_store=SnapshotStore(path))
    assert request(restarted,"vietsun")["overview"]["total_tonnage"] == 8954.97
    assert restarted.export_snapshot(vietsun)["operations"][0]["initial_berth_code"] == "CẦU 5"


def test_old_unpartitioned_snapshot_requires_new_source_read(tmp_path):
    path = tmp_path/"scope-cache.sqlite3"
    repo = ScopeRepository()
    store = SnapshotStore(path)
    service = ReportingService(repo)
    report = request(service,"nghe_tinh")
    old = deepcopy(report)
    old["meta"].pop("berth_rule_version")
    old["meta"]["filters"].pop("production_scope")
    old["meta"]["report_id"] = "legacy-before-berth-rule"
    store.put(old["meta"]["report_id"],"2026-09-12:2026-09-12:all",old,
              [],ttl=900,fresh_ttl=30)
    restarted = ReportingService(repo,snapshot_store=store)
    with pytest.raises(ReportSnapshotNotFound):
        restarted.get_report_snapshot("legacy-before-berth-rule")
    fresh = request(restarted,"nghe_tinh")
    assert fresh["meta"]["report_id"] != report["meta"]["report_id"]
    assert fresh["meta"]["berth_rule_version"] == repository.BERTH_RULE_VERSION
    assert repo.calls == ["nghe_tinh","nghe_tinh"]


def test_source_rows_from_other_scope_fail_instead_of_being_relabelled():
    class Broken(ScopeRepository):
        def read_report(self,*args,**kwargs):
            result = super().read_report(*args,**kwargs)
            result["rows"][0]["production_scope"] = "vietsun"
            return result
    with pytest.raises(DatabaseQueryError):
        request(ReportingService(Broken()),"nghe_tinh")


@pytest.mark.parametrize("scope",["all","unknown",None,[],True])
def test_invalid_scope_is_rejected_without_query(scope):
    repo = ScopeRepository()
    with pytest.raises(ValueError):
        request(ReportingService(repo),scope)
    assert repo.calls == []


def test_http_scope_is_required_to_match_voyage_snapshot(monkeypatch):
    from backend.main import app
    from backend.control_api import require_user
    from backend.integration import get_reporting
    service = ReportingService(ScopeRepository())
    actor = {"id":1,"username":"scope-test","role":"admin", "terminals":["cua_lo","ben_thuy"],
             "must_change_password":False,"is_active":True}
    old = dict(app.dependency_overrides)
    app.dependency_overrides[require_user] = lambda:actor
    app.dependency_overrides[get_reporting] = lambda:service
    try:
        with TestClient(app) as client:
            query = {"start_date":"2026-09-12","end_date":"2026-09-12","terminal":"all"}
            main = client.get("/api/dashboard",params=query)
            assert main.status_code == 200
            assert main.json()["overview"]["total_tonnage"] == 780
            assert main.json()["meta"]["filters"]["production_scope"] == "nghe_tinh"
            result = client.get("/api/dashboard",params={**query,"production_scope":"vietsun"})
            assert result.status_code == 200
            identifier = result.json()["meta"]["report_id"]
            options = {"start_date":query["start_date"],"end_date":query["end_date"],"report_id":identifier}
            assert client.get("/api/voyages/cua_lo/102",params=options).status_code == 422
            detail = client.get("/api/voyages/cua_lo/102",params={**options,"production_scope":"vietsun"})
            assert detail.status_code == 200
            assert detail.json()["header"]["initial_berth_code"] == "CẦU 5"
            assert detail.json()["summary"]["tonnage"] == 8954.97
            assert client.get("/api/dashboard",params={**query,"production_scope":"all"}).status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)
