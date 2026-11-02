"""``python -m slo_lab.lab.health_cli``: the service, the gateway, the backends, the pager and Grafana all answer."""

from __future__ import annotations

import base64
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping


def _http_ok(url: str, auth: tuple[str, str] | None = None) -> bool:
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def run_checks(env: Mapping[str, str]) -> list[tuple[str, bool]]:
    """One (name, answered) pair per part of the stack, using the ports the compose file publishes."""
    local = "http://127.0.0.1:"
    grafana = f"{local}{env.get('GRAFANA_HOST_PORT', '23000')}"
    login = ("admin", env.get("GRAFANA_ADMIN_PASSWORD", ""))
    return [
        ("checkout /healthz", _http_ok(f"{local}{env.get('SERVICE_HOST_PORT', '28083')}/healthz")),
        ("gateway health_check", _http_ok(f"{local}{env.get('GATEWAY_HEALTH_HOST_PORT', '23133')}/")),
        ("tempo /ready", _http_ok(f"{local}{env.get('TEMPO_HOST_PORT', '23200')}/ready")),
        ("loki /ready", _http_ok(f"{local}{env.get('LOKI_HOST_PORT', '23100')}/ready")),
        ("prometheus /-/healthy", _http_ok(f"{local}{env.get('PROMETHEUS_HOST_PORT', '29090')}/-/healthy")),
        ("pager /healthz", _http_ok(f"{local}{env.get('PAGER_HOST_PORT', '28084')}/healthz")),
        ("grafana queries prometheus", _http_ok(f"{grafana}/api/datasources/uid/prometheus/health", login)),
        ("grafana queries loki", _http_ok(f"{grafana}/api/datasources/uid/loki/health", login)),
        # Grafana 11.2 has no backend health check for Tempo; query it through the proxy, as "Save & test" does.
        ("grafana queries tempo", _http_ok(f"{grafana}/api/datasources/proxy/uid/tempo/api/search?limit=1", login)),
    ]


def main() -> int:
    """Print one line per check and exit 1 when any fails."""
    checks = run_checks(os.environ)
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
