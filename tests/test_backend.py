"""Regression coverage uses fake read-only DB connections, never production SQL."""

from datetime import date, datetime
from decimal import Decimal
import importlib

from fastapi.testclient import TestClient
import pytest

from backend import database, repository
from backend.config import Settings
from backend.database import DatabaseQueryError, DatabaseUnavailable
from backend.repository import DashboardRepository, VoyageNotFound

main = importlib.import_module("backend.main")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(repository, "vietnam_today", lambda: date(2026, 9, 9))


def fact(day="2026-09-01", terminal="cua_lo", vessel="0007", cargo="Than", customer="0012", customer_id="customer1", tonnage="100", measured="80", teu="2", records=1, direction=1):
    return {
        "kind": "fact", "terminal_id": terminal,
        "terminal_name": "Cửa Lò" if terminal == "cua_lo" else "Bến Thủy",
        "operation_day": date.fromisoformat(day), "vessel_id": vessel,
        "cargo_name": cargo, "direction_id": direction, "customer_name": customer, "customer_id": customer_id,
        "tonnage": Decimal(tonnage), "measured_tonnage": Decimal(measured),
        "native_weight": Decimal(tonnage), "tonne_factor": Decimal(1),
        "unit_code": "TAN", "unit_name": "Tấn", "known_weight_count": records,
        "container_row_count": records if Decimal(teu) else 0,
        "teu": Decimal(teu), "record_count": records,
        "latest_operation_at": datetime.fromisoformat(day + "T19:30:00"),
        "missing_weight_count": 0,
    }


def make_repository(monkeypatch, rows):
    repo = DashboardRepository()
    calls = []

    def execute(query, params):
        calls.append((query, params))
        return rows

    monkeypatch.setattr(repo, "_execute_query", execute)
    return repo, calls


def test_all_panels_reconcile_and_preserve_numeric_labels(monkeypatch):
    rows = [
        fact(), fact(day="2026-09-02", tonnage="0.125", measured="0.005", records=2),
        fact(terminal="ben_thuy", tonnage="25", measured="12", cargo="20F", direction=2),
        fact(day="2026-08-23", tonnage="50", measured="40", teu="1"),
    ]
    repo, calls = make_repository(monkeypatch, rows)
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert len(calls) == 1
    total = result["overview"]["total_tonnage"]
    assert total == 125.125
    assert result["overview"]["total_measured_tonnage"] == 125.125
    assert result["overview"]["vessel_calls"] == 2  # same ID at different terminals
    assert result["overview"]["record_count"] == 4
    assert result["overview"]["trend_tonnage"] == 150.2
    assert result["overview"]["trend_vessels"] == 100
    for section in ("cargo", "directions", "terminals", "history", "daily_history"):
        assert sum(row["tonnage"] for row in result[section]) == pytest.approx(total)
    assert result["customers"][0]["name"] == "0012"
    assert result["meta"]["previous_period"]["start_date"] == "2026-08-23"
    assert result["meta"]["previous_period"]["end_date"] == "2026-08-31"
    assert result["meta"]["previous_period"]["label"] == "9 ngày liền trước"
    assert result["meta"]["previous_period"]["record_count"] == 1


def test_zero_data_is_empty_not_synthetic_and_missing_baseline_is_null(monkeypatch):
    repo, _ = make_repository(monkeypatch, [])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert result["meta"]["status"] == "empty"
    assert result["overview"]["record_count"] == 0
    assert result["overview"]["total_tonnage"] == 0
    assert result["overview"]["trend_tonnage"] is None
    assert result["overview"]["trend_teu"] is None
    assert result["overview"]["trend_vessels"] is None
    assert all(result[section] == [] for section in ("cargo", "terminals", "directions", "customers", "yard"))
    assert result["efficiency"]["turnaround_time"] is None
    assert result["efficiency"]["productivity"] is None
    assert result["meta"]["unavailable"]["yard"]
    assert result["history"][0]["tonnage"] == 0
    assert len(result["daily_history"]) == 9
    assert all(row["tonnage"] == 0 for row in result["daily_history"])


def test_empty_current_period_can_show_real_decrease(monkeypatch):
    repo, _ = make_repository(monkeypatch, [fact(day="2026-08-25")])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert result["meta"]["status"] == "empty"
    assert result["overview"]["trend_tonnage"] == -100


def test_cargo_percentages_use_all_categories_not_top_five(monkeypatch):
    repo, _ = make_repository(monkeypatch, [fact(cargo=f"Hàng {index}", tonnage="10") for index in range(6)])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert len(result["cargo"]) == 6
    assert all(row["value"] == 16.7 for row in result["cargo"])
    assert sum(row["tonnage"] for row in result["cargo"]) == 60


def test_container_cargo_variants_form_one_group_without_changing_totals(monkeypatch):
    codes = ["20E", "20F", "20R", "40E", "40F", "40R", "45E", "45F"]
    rows = [fact(cargo=code, tonnage=str(index + 1), teu="1" if code.startswith("20") else "2")
            for index, code in enumerate(codes)]
    rows.append(fact(cargo="Clinker", tonnage="64", teu="0"))
    repo, _ = make_repository(monkeypatch, rows)
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert {row["name"] for row in result["cargo"]} == {"Hàng container", "Clinker"}
    containers = next(row for row in result["cargo"] if row["name"] == "Hàng container")
    assert containers["tonnage"] == 36
    assert containers["teu"] == 13
    assert containers["record_count"] == 8
    assert containers["value"] == 36
    assert result["overview"]["total_tonnage"] == 100
    assert result["overview"]["total_teu"] == 13
    assert sum(row["record_count"] for row in result["cargo"]) == result["overview"]["record_count"] == 9
    assert result["voyages"][0]["cargo_names"] == sorted([*codes, "Clinker"])


def test_container_group_normalizes_codes_but_preserves_other_cargo_names(monkeypatch):
    other_names = ["Dăm gỗ", "Than", "20E-OTHER", "Container 40F", " Chưa phân loại "]
    rows = [fact(cargo=name, tonnage="1", teu="0") for name in other_names]
    rows.extend([fact(cargo=" 20e ", tonnage="2", teu="1"), fact(cargo="40f", tonnage="3", teu="2")])
    repo, _ = make_repository(monkeypatch, rows)
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert {row["name"] for row in result["cargo"]} == {*other_names, "Hàng container"}
    containers = next(row for row in result["cargo"] if row["name"] == "Hàng container")
    assert containers["tonnage"] == 5
    assert " 20e " in result["voyages"][0]["cargo_names"]
    assert "40f" in result["voyages"][0]["cargo_names"]


@pytest.mark.parametrize("known_weight", [True, False])
def test_grouped_container_missing_weights_keep_partial_or_unknown_status(monkeypatch, known_weight):
    first = fact(cargo="20E", tonnage="10", teu="1")
    second = fact(cargo="40F", tonnage="30", teu="2")
    second.update(native_weight=None, known_weight_count=0, missing_weight_count=1)
    if not known_weight:
        first.update(native_weight=None, known_weight_count=0, missing_weight_count=1)
    repo, _ = make_repository(monkeypatch, [first, second])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert len(result["cargo"]) == 1
    containers = result["cargo"][0]
    assert containers["name"] == "Hàng container"
    assert containers["tonnage"] == (10 if known_weight else None)
    assert containers["tonnage_status"] == ("partial" if known_weight else "unavailable")
    assert containers["value"] == (100 if known_weight else None)
    assert containers["teu"] == result["overview"]["total_teu"] == 3
    assert containers["record_count"] == 2


def test_container_group_keeps_negative_adjustments_and_native_units(monkeypatch):
    rows = [fact(cargo="20F", tonnage="100", teu="4"), fact(cargo="40E", tonnage="-20", teu="-2"),
            fact(cargo="Than", tonnage="20", teu="0"), fact(cargo="Mặt hàng theo diện tích", tonnage="7", teu="0")]
    rows[1]["negative_value_count"] = 1
    rows[3].update(unit_code="M2", unit_name="M2", tonne_factor=None)
    repo, _ = make_repository(monkeypatch, rows)
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    containers = next(row for row in result["cargo"] if row["name"] == "Hàng container")
    assert containers["tonnage"] == 80
    assert containers["teu"] == 2
    assert containers["value"] == 80
    assert result["overview"]["total_tonnage"] == 100
    assert result["overview"]["total_teu"] == 2
    assert result["native_units"][0]["value"] == 7
    assert next(row for row in result["cargo"] if row["name"] == "Mặt hàng theo diện tích")["tonnage"] is None
    assert result["meta"]["data_quality"]["negative_value_count"] == 1


def test_same_name_customers_keep_source_and_id_identity(monkeypatch):
    repo, _ = make_repository(monkeypatch, [
        fact(customer="Công ty A", customer_id="1", tonnage="100"),
        fact(customer="Công ty A", customer_id="2", tonnage="50"),
        fact(customer="Công ty A", customer_id="1", terminal="ben_thuy", tonnage="25"),
        fact(customer="Chưa xác định", customer_id=None, tonnage="10"),
    ])
    customers = repo.get_dashboard("2026-09-01", "2026-09-09")["customers"]
    assert len(customers) == 4
    assert [(row["terminal_id"], row["customer_id"], row["volume"]) for row in customers] == [
        ("cua_lo", "1", 100), ("cua_lo", "2", 50), ("ben_thuy", "1", 25), ("cua_lo", None, 10),
    ]


def test_missing_container_quantity_and_negative_adjustments_are_flagged(monkeypatch):
    row = fact(tonnage="-5")
    row["missing_quantity_count"] = 2
    row["container_row_count"] = 2
    row["negative_value_count"] = 1
    repo, _ = make_repository(monkeypatch, [row])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert result["overview"]["total_tonnage"] == -5
    assert result["meta"]["data_quality"]["missing_quantity_count"] == 2
    assert result["meta"]["data_quality"]["negative_value_count"] == 1
    assert any("quantityTotalSum" in warning for warning in result["meta"]["warnings"])
    assert any("âm" in warning for warning in result["meta"]["warnings"])


def test_half_open_sql_dates_same_formula_and_terminal_allowlist(monkeypatch):
    repo, calls = make_repository(monkeypatch, [])
    repo.get_dashboard("2026-03-01", "2026-03-31", "ben_thuy")
    query, params = calls[0]
    assert params == (date(2026, 1, 29), date(2026, 4, 1))
    assert "t.shiftDate >= ? AND t.shiftDate < ?" in query
    assert "2026-03" not in query
    assert "SmartTOS_BenThuy.dbo" in query
    assert "SmartTOS.dbo" not in query
    assert "GETDATE" not in query
    assert "ISNULL(t.rowDeleted, 0) = 0" in query
    assert "LEFT JOIN SmartTOS_BenThuy.dbo.Partner" in query
    assert "t.weightNetSum > 0" not in query
    assert "SANLUONG-QUACANG" in query
    assert "jobMethodName LIKE" not in query
    assert "CAST(sg.statisticsGroupTypeId AS varchar(20))" in query
    assert repo.tonne_factor_logic in query
    assert "* 25" not in query and "* 30" not in query
    assert repo.teu_logic in query
    with pytest.raises(ValueError):
        repo.get_dashboard("2026-09-01", "2026-09-09", "all; DROP TABLE TallyShift")
    assert len(calls) == 1


def test_defaults_use_vietnam_month_to_date_and_same_previous_length(monkeypatch):
    repo, calls = make_repository(monkeypatch, [])
    result = repo.get_dashboard()
    assert result["meta"]["filters"]["start_date"] == "2026-09-01"
    assert result["meta"]["filters"]["end_date"] == "2026-09-09"
    assert calls[0][1] == (date(2026, 8, 23), date(2026, 9, 10)) * 2


def test_history_is_selected_range_and_zero_fills_only_months_in_range(monkeypatch):
    repo, _ = make_repository(monkeypatch, [fact(day="2026-07-20"), fact(day="2026-09-01")])
    result = repo.get_dashboard("2026-07-15", "2026-09-02")
    assert [row["date"] for row in result["history"]] == ["2026-07", "2026-08", "2026-09"]
    assert [row["tonnage"] for row in result["history"]] == [100, 0, 100]


def test_daily_history_keeps_inclusive_boundaries_and_zero_fills_missing_days(monkeypatch):
    repo, _ = make_repository(monkeypatch, [fact(day="2026-09-01"), fact(day="2026-09-03", tonnage="25")])
    result = repo.get_dashboard("2026-09-01", "2026-09-03")
    assert [row["date"] for row in result["daily_history"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert [row["tonnage"] for row in result["daily_history"]] == [100, 0, 25]
    assert sum(row["teu"] for row in result["daily_history"]) == result["overview"]["total_teu"]
    assert sum(row["record_count"] for row in result["daily_history"]) == result["overview"]["record_count"]


def test_source_freshness_is_independent_from_selected_period(monkeypatch):
    missing = fact()
    missing["missing_weight_count"] = 1
    repo, _ = make_repository(monkeypatch, [missing, {
        "kind": "source", "terminal_id": "cua_lo", "latest_operation_at": datetime(2026, 9, 8, 22),
    }])
    result = repo.get_dashboard("2026-09-01", "2026-09-03", "cua_lo")
    source = result["meta"]["sources"][0]
    assert source["latest_operation_at"] == "2026-09-08T22:00:00"
    assert source["latest_selected_operation_at"] == "2026-09-01T19:30:00"
    assert source["days_since_latest_operation"] == 1
    assert source["record_count"] == 1
    assert result["meta"]["data_quality"]["missing_weight_count"] == 1
    assert result["meta"]["data_quality"]["days_without_records"] == 2
    assert result["meta"]["business_rules_verified"] is False


@pytest.mark.parametrize("unit,fake_factor", [("M2", None), ("M3", None), ("GÀU", None), ("GIO", "0.01"), ("LAN", "1"), ("VOBICH", "1")])
def test_non_mass_units_never_enter_tonnes_even_with_positive_metadata(monkeypatch, unit, fake_factor):
    row = fact(tonnage="5207", teu="0")
    row.update(unit_code=unit, unit_name=unit, tonne_factor=fake_factor)
    repo, _ = make_repository(monkeypatch, [fact(tonnage="100", teu="0"), row])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert result["overview"]["total_tonnage"] == 100
    assert result["overview"]["tonnage_status"] == "partial"
    assert result["meta"]["metric_coverage"]["tonnage"]["excluded_native_rows"] == 1
    assert result["native_units"][0]["value"] == 5207
    assert result["native_units"][0]["unit_code"] == unit


def test_kg_uses_evidenced_generic_conversion_and_container_keeps_source_weight(monkeypatch):
    kg = fact(tonnage="1500", teu="0")
    kg.update(unit_code="KG", unit_name="Kg", tonne_factor=Decimal("0.001"))
    container = fact(cargo="45E", tonnage="4.8", teu="2")
    repo, calls = make_repository(monkeypatch, [kg, container])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert result["overview"]["total_tonnage"] == 6.3
    assert result["overview"]["total_teu"] == 2
    assert "cu.cargoId = 0" in calls[0][0]
    assert "COUNT(DISTINCT cu.unitValue) = 1" in calls[0][0]
    assert "source_unit.baseUnitCode = N'KG'" in calls[0][0]
    assert "target_unit.baseUnitCode = N'TAN'" in calls[0][0]
    assert "ISNULL(cu.goodsSpecificationId, 0) = 0" not in calls[0][0]


def test_all_missing_mass_is_unknown_throughout_panels_not_zero(monkeypatch):
    row = fact()
    row.update(native_weight=None, known_weight_count=0, missing_weight_count=1)
    repo, _ = make_repository(monkeypatch, [row])
    result = repo.get_dashboard("2026-09-01", "2026-09-01")
    assert result["overview"]["total_tonnage"] is None
    assert result["overview"]["tonnage_status"] == "unavailable"
    for section in ("cargo", "terminals", "directions", "history", "daily_history"):
        assert result[section][0]["tonnage"] is None
    assert result["cargo"][0]["value"] is None
    assert result["customers"][0]["volume"] is None


def test_all_native_units_preserve_value_but_have_no_tonne_total(monkeypatch):
    row = fact(tonnage="1.757", teu="0")
    row.update(unit_code="M2", unit_name="M2", tonne_factor=None)
    repo, _ = make_repository(monkeypatch, [row])
    result = repo.get_dashboard("2026-09-01", "2026-09-01")
    assert result["overview"]["total_tonnage"] is None
    assert result["native_units"][0]["value"] == 1.757
    assert result["overview"]["total_teu"] == 0


def test_all_missing_container_quantity_is_unknown_teu_and_true_weight_zero_is_zero(monkeypatch):
    row = fact(cargo="20F", tonnage="0", teu="0")
    row.update(container_row_count=1, missing_quantity_count=1)
    repo, _ = make_repository(monkeypatch, [row])
    result = repo.get_dashboard("2026-09-01", "2026-09-01")
    assert result["overview"]["total_tonnage"] == 0
    assert result["overview"]["tonnage_status"] == "ready"
    assert result["overview"]["total_teu"] is None
    assert result["overview"]["teu_status"] == "unavailable"
    assert result["daily_history"][0]["teu"] is None


def test_partial_previous_period_suppresses_weight_trend(monkeypatch):
    previous = fact(day="2026-08-31")
    previous.update(unit_code="M3", unit_name="M3", tonne_factor=None)
    repo, _ = make_repository(monkeypatch, [fact(), fact(day="2026-08-31"), previous])
    result = repo.get_dashboard("2026-09-01", "2026-09-01")
    assert result["overview"]["tonnage_status"] == "ready"
    assert result["meta"]["previous_period"]["metric_coverage"]["tonnage"]["status"] == "partial"
    assert result["overview"]["trend_tonnage"] is None


def test_unqualified_voyage_does_not_remove_reported_weight(monkeypatch):
    row = fact(vessel=None, tonnage="735")
    repo, calls = make_repository(monkeypatch, [row])
    result = repo.get_dashboard("2026-09-01", "2026-09-01")
    assert result["overview"]["vessel_calls"] == 0
    assert result["overview"]["total_tonnage"] == 735
    assert "isVirtualVesselVoyage" in calls[0][0]
    assert "isVirtualVessel" in calls[0][0]


class FakeCursor:
    description = [("identifier",), ("amount",)]

    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False
        self.done = False

    def execute(self, query, params=()):
        if self.fail:
            raise RuntimeError("PWD=secret; SERVER=private-host; SQL leaked")
        self.executed = (query, params)

    def fetchmany(self, size):
        if self.done:
            return []
        self.done = True
        return [("0012", Decimal("0.125"))]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail=False):
        self.fake_cursor = FakeCursor(fail)
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def close(self):
        self.closed = True


@pytest.mark.parametrize("fail", [False, True])
def test_sql_closes_resources_and_redacts_errors(monkeypatch, caplog, fail):
    connection = FakeConnection(fail)
    monkeypatch.setattr(repository, "get_db_connection", lambda: connection)
    repo = DashboardRepository()
    if fail:
        with pytest.raises(DatabaseQueryError) as caught:
            repo._execute_query("SELECT private_schema", ())
        assert "secret" not in str(caught.value)
    else:
        assert repo._execute_query("SELECT ?", (1,)) == [{"identifier": "0012", "amount": Decimal("0.125")}]
    assert connection.closed
    assert connection.fake_cursor.closed
    assert "secret" not in caplog.text
    assert "private-host" not in caplog.text
    assert "private_schema" not in caplog.text


def test_connection_failure_is_not_empty_data(monkeypatch):
    monkeypatch.setattr(repository, "get_db_connection", lambda: None)
    with pytest.raises(DatabaseUnavailable):
        DashboardRepository().get_dashboard("2026-09-01", "2026-09-09")


def test_database_credentials_escaped_and_timeouts_applied(monkeypatch):
    for key, value in {"DB_SERVER": "host", "DB_USERNAME": "user", "DB_PASSWORD": "x};Encrypt=no", "DB_CONNECT_TIMEOUT_SECONDS": 3, "DB_QUERY_TIMEOUT_SECONDS": 7}.items():
        monkeypatch.setattr(database.settings, key, value)
    connection = FakeConnection()
    calls = []

    def connect(connection_string, **kwargs):
        calls.append((connection_string, kwargs))
        return connection

    monkeypatch.setattr(database.pyodbc, "connect", connect)
    assert database.get_db_connection("SmartTOS_BenThuy") is connection
    assert "PWD={x}};Encrypt=no}" in calls[0][0]
    assert calls[0][1] == {"timeout": 3, "autocommit": True}
    assert connection.timeout == 7
    with pytest.raises(ValueError):
        database.get_db_connection("master")


def test_driver_error_is_sanitized(monkeypatch, caplog):
    for key in ("DB_SERVER", "DB_USERNAME", "DB_PASSWORD"):
        monkeypatch.setattr(database.settings, key, "test")

    def fail(*args, **kwargs):
        raise RuntimeError("PWD=secret-password; SERVER=private-host")

    monkeypatch.setattr(database.pyodbc, "connect", fail)
    with pytest.raises(DatabaseUnavailable) as caught:
        database.get_db_connection()
    assert "secret-password" not in str(caught.value)
    assert "secret-password" not in caplog.text
    assert "private-host" not in caplog.text


@pytest.mark.parametrize(("state", "driver_message", "category"), [
    ("08001", "SSL Provider: The certificate chain was issued by an authority that is not trusted.", "tls_certificate"),
    ("08001", "SSL routines::certificate verify failed:unable to get local issuer certificate", "tls_certificate"),
    ("08001", "SSL Provider: The target principal name is incorrect.", "tls_certificate"),
    ("08001", "SSL Provider: handshake failed", "tls_handshake"),
    ("28000", "Login failed for user.", "authentication"),
    ("08001", "Login timeout expired", "timeout"),
    ("HYT00", "Connection attempt expired", "timeout"),
    ("08001", "TCP Provider: network-related error", "network"),
    ("08S01", "Communication link failure", "network"),
    ("IM002", "Data source name not found", "driver_configuration"),
    ("01000", "Can't open lib: file not found", "driver_configuration"),
    ("08001", "Client unable to establish connection", "connection"),
    ("HY000", "Unclassified driver failure", "unknown"),
    ("PRIV8", "Unclassified driver failure", "unknown"),
])
def test_safe_connection_diagnostics_preserve_tls_and_do_not_retry(monkeypatch, caplog, state, driver_message, category):
    for key, value in {"DB_SERVER": "private-host", "DB_USERNAME": "private-user", "DB_PASSWORD": "secret-password",
                       "DB_ENCRYPT": True, "DB_TRUST_SERVER_CERTIFICATE": False}.items():
        monkeypatch.setattr(database.settings, key, value)
    calls = []

    def fail(connection_string, **kwargs):
        calls.append(connection_string)
        raise database.pyodbc.Error(state, driver_message + "; SERVER=private-host; UID=private-user; PWD=secret-password")

    monkeypatch.setattr(database.pyodbc, "connect", fail)
    with pytest.raises(DatabaseUnavailable) as caught:
        database.get_db_connection()
    assert len(calls) == 1
    assert ";Encrypt=yes;TrustServerCertificate=no;" in calls[0]
    assert database.settings.DB_ENCRYPT is True
    assert database.settings.DB_TRUST_SERVER_CERTIFICATE is False
    assert f"category={category}" in caplog.text
    expected_state = "unknown" if state == "PRIV8" else state
    assert f"sqlstate={expected_state}" in caplog.text
    assert "encrypt=True trust_server_certificate=False" in caplog.text
    for private_value in ("private-host", "private-user", "secret-password", driver_message, "PRIV8"):
        assert private_value not in caplog.text
        assert private_value not in str(caught.value)
    assert all(record.exc_info is None for record in caplog.records)


def test_missing_configuration_logs_only_missing_field_names(monkeypatch, caplog):
    monkeypatch.setattr(database.settings, "DB_SERVER", "")
    monkeypatch.setattr(database.settings, "DB_USERNAME", "private-user")
    monkeypatch.setattr(database.settings, "DB_PASSWORD", "")

    def forbidden(*args, **kwargs):
        pytest.fail("Incomplete configuration must not reach the driver")

    monkeypatch.setattr(database.pyodbc, "connect", forbidden)
    with pytest.raises(DatabaseUnavailable):
        database.get_db_connection()
    assert "category=missing_configuration missing_fields=DB_SERVER,DB_PASSWORD" in caplog.text
    assert "DB_USERNAME" not in caplog.text
    assert "private-user" not in caplog.text


def test_driver_diagnostic_does_not_turn_arbitrary_exception_text_into_sqlstate():
    assert database._connection_diagnostic(RuntimeError("TOKEN", "private text")) == ("unknown", "unknown")
    assert database._connection_diagnostic(database.pyodbc.Error("[08001] TCP Provider: private text")) == ("network", "08001")


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize("query", [
    "start_date=2026-09-09&end_date=2026-09-01",
    "start_date=2026-09-01&end_date=2026-09-10",
    "start_date=2024-01-01&end_date=2026-09-09",
    "start_date=invalid", "terminal=unknown",
    "start_date=1899-01-01&end_date=1899-01-02",
])
def test_api_rejects_invalid_filters_before_database(monkeypatch, client, query):
    def forbidden(**kwargs):
        pytest.fail("Invalid filter reached the database")

    monkeypatch.setattr(main.dashboard_repo, "get_dashboard", forbidden)
    assert client.get("/api/dashboard?" + query).status_code == 422


@pytest.mark.parametrize("error", [DatabaseUnavailable, DatabaseQueryError])
def test_api_failure_has_503_code_and_no_mock_metrics(monkeypatch, client, error):
    def fail(**kwargs):
        raise error()

    monkeypatch.setattr(main.dashboard_repo, "get_dashboard", fail)
    response = client.get("/api/dashboard")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == error.code
    assert "overview" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_api_snapshot_and_legacy_routes_receive_identical_filters(monkeypatch, client):
    calls = []

    def snapshot(**kwargs):
        calls.append(kwargs)
        return {"overview": {"total_tonnage": 12}, "meta": {"status": "ok"}}

    monkeypatch.setattr(main.dashboard_repo, "get_dashboard", snapshot)
    params = {"start_date": "2026-09-01", "end_date": "2026-09-02", "terminal": "ben_thuy"}
    response = client.get("/api/dashboard", params=params)
    legacy = client.get("/api/overview", params=params)
    assert response.status_code == 200
    assert legacy.json() == response.json()["overview"]
    assert calls[0] == calls[1] == {"start_date": date(2026, 9, 1), "end_date": date(2026, 9, 2), "terminal": "ben_thuy"}


def test_health_failure_returns_503(monkeypatch, client):
    def fail(database=None):
        raise DatabaseUnavailable()

    monkeypatch.setattr(main, "get_db_connection", fail)
    assert client.get("/api/health").status_code == 503


def test_cors_requires_exact_configured_origins(client):
    for origin in main.settings.allowed_cors_origins:
        allowed = client.options("/api/dashboard", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == origin
        assert allowed.headers["access-control-allow-methods"] == "GET"
        assert "access-control-allow-credentials" not in allowed.headers
    for origin in ("https://attacker.example", "https://dashboard-sanluong.vercel.app.attacker.example", "https://unapproved-preview.vercel.app"):
        denied = client.options("/api/dashboard", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers
        assert "access-control-allow-origin" not in client.get("/", headers={"Origin": origin}).headers


def test_production_preflight_rejects_write_methods_and_unapproved_headers(client):
    origin = "https://dashboard-sanluong.vercel.app"
    for extra in ({"Access-Control-Request-Method": "POST"},
                  {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "X-Unapproved"}):
        response = client.options("/api/dashboard", headers={"Origin": origin, **extra})
        assert response.status_code == 400


@pytest.mark.parametrize(("path", "expected_status"), [
    ("/", 200), ("/api/dashboard", 503),
    ("/api/dashboard?terminal=unknown", 422), ("/api/unknown", 404),
])
def test_production_frontend_can_read_success_and_handled_errors(monkeypatch, client, path, expected_status):
    def unavailable(**kwargs):
        raise DatabaseUnavailable()

    monkeypatch.setattr(main.dashboard_repo, "get_dashboard", unavailable)
    origin = "https://dashboard-sanluong.vercel.app"
    response = client.get(path, headers={"Origin": origin})
    assert response.status_code == expected_status
    assert response.headers["access-control-allow-origin"] == origin
    assert "origin" in response.headers["vary"].lower()
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    if expected_status == 503:
        assert response.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"


def test_cors_defaults_cover_production_and_actual_vite_port(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.allowed_cors_origins == [
        "https://dashboard-sanluong.vercel.app",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ]


def test_old_cors_environment_keeps_production_and_explicit_origins(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3000"]')
    settings = Settings(_env_file=None)
    assert settings.allowed_cors_origins == [
        "https://dashboard-sanluong.vercel.app", "http://localhost:3000",
    ]


def test_deployment_can_replace_frontend_and_deduplicate_additional_origins():
    settings = Settings(_env_file=None, FRONTEND_ORIGIN="https://dashboard.example",
                        CORS_ORIGINS=["https://dashboard.example", "http://localhost:5173"])
    assert settings.allowed_cors_origins == ["https://dashboard.example", "http://localhost:5173"]


@pytest.mark.parametrize("origin", ["*", "https://*.vercel.app", "https://dashboard.example/", "https://user:password@dashboard.example", "https://dashboard.example?debug=1", "https://dashboard.example#section"])
def test_cors_rejects_non_origins_in_both_configuration_fields(origin):
    with pytest.raises(ValueError):
        Settings(_env_file=None, FRONTEND_ORIGIN=origin)
    with pytest.raises(ValueError):
        Settings(_env_file=None, CORS_ORIGINS=[origin])


def test_settings_reject_wildcard_and_ignore_legacy_env_keys():
    with pytest.raises(ValueError):
        Settings(_env_file=None, CORS_ORIGINS=["*"])
    settings = Settings(_env_file=None, OLD_UNUSED_KEY="ignored", CORS_ORIGINS=["https://dashboard.example"])
    assert settings.CORS_ORIGINS == ["https://dashboard.example"]


def test_voyage_list_matches_kpi_without_discarding_unassigned_tonnage(monkeypatch):
    first = fact(vessel="101", day="2026-09-01", cargo="Than", tonnage="100")
    first.update(vessel_name="Tàu A", voyage_code="A-001", arrival_at=datetime(2026, 8, 31, 10), departure_at=None)
    second = fact(vessel="101", day="2026-09-03", cargo="Clinker", tonnage="25")
    second.update(vessel_name="Tàu A", voyage_code="A-001", arrival_at=first["arrival_at"], departure_at=None)
    other_terminal = fact(vessel="101", terminal="ben_thuy", tonnage="10")
    unassigned = fact(vessel=None, tonnage="7")
    repo, calls = make_repository(monkeypatch, [first, second, other_terminal, unassigned])
    result = repo.get_dashboard("2026-09-01", "2026-09-09")
    assert len(calls) == 1
    assert len(result["voyages"]) == result["overview"]["vessel_calls"] == 2
    assert result["overview"]["total_tonnage"] == 142
    assert sum(row["tonnage"] for row in result["voyages"]) == 135
    assert result["meta"]["unassigned_voyage_totals"]["tonnage"] == 7
    voyage = next(row for row in result["voyages"] if row["terminal_id"] == "cua_lo")
    assert voyage["cargo_names"] == ["Clinker", "Than"]
    assert voyage["first_operation_date"] == "2026-09-01"
    assert voyage["last_operation_date"] == "2026-09-03"
    assert voyage["arrival_at"] == "2026-08-31T10:00:00"
    assert voyage["departure_at"] is None
    assert voyage["vessel_name"] == "Tàu A"
    assert voyage["record_count"] == 2


def test_voyage_header_preserves_missing_and_sentinel_timestamps(monkeypatch):
    row = fact(vessel="101")
    row.update(vessel_name=None, voyage_code=None, arrival_at=datetime(1, 1, 1), departure_at=None)
    repo, _ = make_repository(monkeypatch, [row])
    header = repo.get_dashboard("2026-09-01", "2026-09-09")["voyages"][0]
    assert header["arrival_at"] is None
    assert header["departure_at"] is None
    assert header["vessel_name"] is None
    assert header["voyage_code"] is None


def operation_row(identifier="3", total=3, unit="TAN", weight="25", factor="1"):
    row = fact(vessel="101", tonnage=weight, teu="0")
    row.update(id=identifier, total_count=total, operation_code="P-003", job_method="Xe - Tàu", job_method_code="XE-TAU",
               quantity=Decimal("5"), quantity_unit="CHIEC", quantity_unit_name="Chiếc",
               unit_code=unit, unit_name=unit, tonne_factor=Decimal(factor) if factor else None)
    return row


def test_voyage_detail_summary_is_full_period_and_operations_are_paged(monkeypatch):
    repo = DashboardRepository()
    calls = []
    rows = [operation_row(identifier="1", weight="50"), operation_row(identifier="2", weight="50"), operation_row(identifier="3", weight="25")]

    def execute(query, params):
        calls.append((query, params))
        return rows

    monkeypatch.setattr(repo, "_execute_query", execute)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-09", page=2, page_size=2)
    assert detail["summary"]["tonnage"] == 125
    assert detail["summary"]["record_count"] == 3
    assert detail["operations"]["total"] == 3
    assert detail["operations"]["total_pages"] == 2
    assert len(detail["operations"]["rows"]) == 1
    assert detail["operations"]["rows"][0]["tonnage"] == 25
    assert detail["operations"]["rows"][0]["id"] == "3"
    assert detail["meta"]["filters"]["terminal"] == "cua_lo"
    assert detail["meta"]["filters"]["voyage_id"] == "101"
    assert len(calls) == 1
    assert calls[0][1] == (date(2026, 9, 1), date(2026, 9, 10), 101)
    for query, _ in calls:
        assert "SANLUONG-QUACANG" in query
        assert "jobMethodName LIKE" not in query
        assert "t.vesselVoyageId = ?" in query
        assert "SmartTOS_BenThuy.dbo" not in query
    assert "ORDER BY t.shiftDate DESC, t.tallyShiftId DESC" in calls[0][0]
    assert detail["meta"]["read_consistency"] == "single_fact_set"


def test_voyage_operation_preserves_native_units_and_unknown_tonnes(monkeypatch):
    row = fact(vessel="101", tonnage="7", teu="0")
    row.update(unit_code="M3", unit_name="M3", tonne_factor=None)
    repo = DashboardRepository()
    monkeypatch.setattr(repo, "_execute_query", lambda query, params: [operation_row(total=1, unit="M3", weight="7", factor=None)])
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-01")
    assert detail["summary"]["tonnage"] is None
    assert detail["native_units"][0]["value"] == 7
    op = detail["operations"]["rows"][0]
    assert op["weight"] == 7
    assert op["weight_unit"] == "M3"
    assert op["tonnage"] is None
    assert op["tonnage_status"] == "unavailable"


@pytest.mark.parametrize("rows", [[], [fact(vessel=None)]])
def test_voyage_detail_not_found_outside_valid_scope(monkeypatch, rows):
    repo, calls = make_repository(monkeypatch, rows)
    with pytest.raises(VoyageNotFound):
        repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-09")
    assert len(calls) == 1


def test_voyage_invalid_page_never_runs_operation_query(monkeypatch):
    repo, calls = make_repository(monkeypatch, [fact(vessel="101")])
    with pytest.raises(ValueError):
        repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-09", page=2)
    assert len(calls) == 1


def test_voyage_summary_and_operations_share_one_read_when_source_changes(monkeypatch):
    repo = DashboardRepository()
    calls = []
    def changing_source(query, params):
        calls.append(1)
        return [operation_row(total=1, weight="10" if len(calls) == 1 else "20")]
    monkeypatch.setattr(repo, "_execute_query", changing_source)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-09")
    assert len(calls) == 1
    assert detail["summary"]["tonnage"] == detail["operations"]["rows"][0]["tonnage"] == 10
    assert sum(row["tonnage"] or 0 for row in detail["cargo"]) == 10
    assert sum(row["tonnage"] or 0 for row in detail["daily"]) == 10


@pytest.mark.parametrize("path", [
    "/api/voyages/all/101", "/api/voyages/unknown/101", "/api/voyages/cua_lo/0",
    "/api/voyages/cua_lo/-1", "/api/voyages/cua_lo/not-a-number", "/api/voyages/cua_lo/2147483648",
    "/api/voyages/cua_lo/101?page=0", "/api/voyages/cua_lo/101?page_size=101",
])
def test_voyage_route_rejects_invalid_path_and_pagination(monkeypatch, client, path):
    monkeypatch.setattr(main.dashboard_repo, "get_voyage_detail", lambda *args: pytest.fail("Invalid request reached repository"))
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("error,status_code", [(VoyageNotFound, 404), (DatabaseUnavailable, 503), (DatabaseQueryError, 503)])
def test_voyage_route_distinguishes_missing_from_database_error(monkeypatch, client, error, status_code):
    def fail(*args):
        raise error()
    monkeypatch.setattr(main.dashboard_repo, "get_voyage_detail", fail)
    response = client.get("/api/voyages/ben_thuy/101?start_date=2026-09-01&end_date=2026-09-09")
    assert response.status_code == status_code
    assert response.headers["cache-control"] == "no-store"


def test_voyage_route_passes_scope_and_page_to_repository(monkeypatch, client):
    calls = []
    def detail(*args):
        calls.append(args)
        return {"header": {"voyage_id": "101"}, "operations": {"page": 2, "rows": []}}
    monkeypatch.setattr(main.dashboard_repo, "get_voyage_detail", detail)
    response = client.get("/api/voyages/ben_thuy/101?start_date=2026-09-01&end_date=2026-09-09&page=2&page_size=5")
    assert response.status_code == 200
    assert calls == [("ben_thuy", 101, date(2026, 9, 1), date(2026, 9, 9), 2, 5)]


def test_voyage_http_contract_serializes_single_fact_set(monkeypatch, client):
    calls = []
    def execute(query, params):
        calls.append((query, params))
        return [operation_row(total=1)]
    monkeypatch.setattr(main.dashboard_repo, "_execute_query", execute)
    response = client.get("/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-09&page_size=1")
    assert response.status_code == 200
    body = response.json()
    assert len(calls) == 1
    assert body["header"]["arrival_at"] is None
    assert body["header"]["departure_at"] is None
    assert body["header"]["voyage_id"] == "101"
    assert body["summary"]["tonnage"] == body["operations"]["rows"][0]["tonnage"] == 25
    assert body["operations"]["rows"][0]["operation_date"] == "2026-09-01"
    assert body["operations"]["rows"][0]["quantity"] == 5
    assert body["meta"]["read_consistency"] == "single_fact_set"


def test_container_group_is_shared_by_dashboard_legacy_and_voyage_detail(monkeypatch, client):
    first = operation_row(identifier="1", total=2, weight="2.25")
    first.update(cargo_name="20E", teu=Decimal(1), container_row_count=1)
    second = operation_row(identifier="2", total=2, weight="30")
    second.update(cargo_name="40F", teu=Decimal(2), container_row_count=1)
    monkeypatch.setattr(main.dashboard_repo, "_execute_query", lambda query, params: [dict(first), dict(second)])
    scope = "start_date=2026-09-01&end_date=2026-09-09"
    dashboard_response = client.get(f"/api/dashboard?{scope}&terminal=cua_lo")
    legacy_response = client.get(f"/api/cargo-breakdown?{scope}&terminal=cua_lo")
    detail_response = client.get(f"/api/voyages/cua_lo/101?{scope}")
    assert dashboard_response.status_code == legacy_response.status_code == detail_response.status_code == 200
    dashboard, legacy, detail = dashboard_response.json(), legacy_response.json(), detail_response.json()
    assert dashboard["cargo"] == legacy == detail["cargo"]
    assert len(dashboard["cargo"]) == 1
    assert dashboard["cargo"][0]["name"] == "Hàng container"
    assert dashboard["cargo"][0]["tonnage"] == detail["summary"]["tonnage"] == 32.25
    assert dashboard["cargo"][0]["teu"] == detail["summary"]["teu"] == 3
    assert detail["header"]["cargo_names"] == ["20E", "40F"]
    assert [row["cargo_name"] for row in detail["operations"]["rows"]] == ["20E", "40F"]
    assert "Hàng container" in dashboard["meta"]["definitions"]["cargo"]
