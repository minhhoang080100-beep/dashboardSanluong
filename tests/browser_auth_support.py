"""Synthetic authentication and a deny-by-default API fallback for browser tests."""
import json
from urllib.parse import urlparse


def install_auth_fixture(page, *, role="viewer", signed_in=True):
    user = {"id": 9001, "username": "browser_test", "display_name": "KIỂM THỬ TRÌNH DUYỆT",
            "role": role, "terminals": ["cua_lo", "ben_thuy"], "must_change_password": False,
            "is_active": True}
    state = {"user": user, "unexpected": []}
    if signed_in:
        page.add_init_script("try { sessionStorage.setItem('port-dashboard-session', 'synthetic-browser-token'); } catch {}")
    else:
        page.add_init_script("try { sessionStorage.removeItem('port-dashboard-session'); } catch {}")

    def respond(route):
        path = urlparse(route.request.url).path
        if path.endswith("/auth/me"):
            route.fulfill(json=state["user"])
        elif path.endswith("/auth/logout"):
            route.fulfill(json={"ok": True})
        elif path.endswith("/throughput-progress") and route.request.method == "GET":
            # Unrelated browser suites do not supply a planning snapshot. Keep
            # this expected read intercepted; browser_targets covers its data.
            route.fulfill(status=503, json={"detail": {
                "code": "SYNTHETIC_TARGET_UNAVAILABLE",
                "message": "Kế hoạch không được cung cấp trong ca kiểm thử này."}})
        elif path.endswith(("/plans", "/issues", "/closed-reports", "/users")) and route.request.method == "GET":
            route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 25})
        else:
            state["unexpected"].append({"method": route.request.method, "path": path})
            route.fulfill(status=501, content_type="application/json", body=json.dumps({"detail": {"code": "UNMOCKED_TEST_API", "message": "Unmocked synthetic browser endpoint"}}))

    # Install before each test's specific route handlers: Playwright runs the last
    # matching handler first. No unknown API request can reach a real service.
    page.route("**/api/**", respond)
    return state
