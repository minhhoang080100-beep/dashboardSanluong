"""Legacy API data contracts authenticate explicitly; security tests use real auth."""
import pytest


@pytest.fixture(autouse=True)
def legacy_plan_approval_policy(monkeypatch):
    # Historical fixtures intentionally use one account to create/approve.
    # Production defaults to separate people; policy tests explicitly clear
    # this test-only override to exercise that default and its enforcement.
    monkeypatch.setenv('DASHBOARD_PLAN_APPROVAL_POLICY', 'allow_self')


@pytest.fixture(autouse=True)
def legacy_api_auth(request):
    if request.path.name not in {'test_backend.py', 'test_database_diagnostics.py', 'test_voyage_daily_window.py', 'test_voyage_operations.py'}:
        yield
        return
    from backend import main
    from backend.control_api import require_user
    from backend.integration import get_reporting

    class LegacyReporting:
        def get_report(self, **kwargs):
            kwargs.pop('refresh', None)
            return main.dashboard_repo.get_dashboard(**kwargs)

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[require_user] = lambda: {
        'id': 1, 'username': 'synthetic-admin', 'display_name': 'Test administrator',
        'role': 'admin', 'terminals': ['cua_lo', 'ben_thuy'], 'must_change_password': False, 'is_active': True,
    }
    main.app.dependency_overrides[get_reporting] = lambda: LegacyReporting()
    try:
        yield
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
