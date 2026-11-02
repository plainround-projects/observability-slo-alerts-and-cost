# Alertmanager (0.27.0) and the fake pager

## What it is

Prometheus evaluates alert rules, but it does not send emails or pages itself. When an alert starts firing, Prometheus sends it to **Alertmanager**, a separate program whose job is to decide *who hears about it and how*. Alertmanager **groups** related alerts into one notification, **waits** a short time so a burst becomes one message, **inhibits** alerts that add nothing when a bigger one is already firing, **silences** alerts on request, **deduplicates** repeats, and finally **routes** each notification to a **receiver**: email, a chat tool, an on-call service, or a generic **webhook** (an HTTP POST with a JSON body to a URL you choose).

The **fake pager** in this repository is a tiny Flask app that plays the role of the on-call service. It is a webhook target that records every notification with its time, so the lab can count and time pages without any real paging service.

## Why this repository uses it

The lesson's last check is a real one: cause a real outage and see the fast-burn **page** arrive at a pager within minutes. That needs the whole chain: the service fails, the Collector forwards the metrics, Prometheus evaluates the generated rule, Alertmanager routes it, and the pager records it. The inhibit rule also implements a good habit: while the fast-burn page for an SLO is firing, its slow-burn ticket adds nothing.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/alertmanager/alertmanager.yml` | Alertmanager's routing, inhibit rule and receiver. |
| `docker-compose.yml` (services `alertmanager` and `pager`) | Run them. |
| `observability/prometheus/prometheus.yml` | The `alerting:` block tells Prometheus where Alertmanager is. |
| `src/slo_lab/interfaces/pager_app.py`, `pager_main.py` | The fake pager's HTTP surface and entry point. |
| `src/slo_lab/infrastructure/pager_store.py` | Where the pager keeps what it received. |
| `src/slo_lab/lab/stores.py` (`PagerClient`), `lab/live.py` | Poll the pager for the page. |
| `Makefile` (`lint-config`) | `amtool check-config` validates the file. |

## How it works underneath

Prometheus evaluates rules every `evaluation_interval` and, for each firing alert, sends a JSON list of alerts to Alertmanager's API (`/api/v2/alerts`), again and again while it fires. Alertmanager stores them, and applies, in this order: **inhibition** (drop alerts whose target matchers match while a source alert is firing and the `equal` labels agree), **routing** (a tree of routes, each with matchers; the default route is at the top), **grouping** (alerts with the same `group_by` labels are collected into one notification), and then the timing:

- `group_wait`: how long to wait after the first alert of a new group before sending, to collect more.
- `group_interval`: how long to wait before sending a notification about **new** alerts added to a group already notified.
- `repeat_interval`: how long to wait before re-sending a notification for alerts that are still firing.

The webhook receiver sends one POST per notification with a JSON document: `{"status": "firing", "alerts": [{"status": "firing", "labels": {...}, "annotations": {...}}, ...], ...}`. `send_resolved: false` means no message is sent when the alert stops.

`amtool` is Alertmanager's command-line tool; `amtool check-config` validates a configuration file.

## Walkthrough of this repository's use

### `alertmanager.yml`

```yaml
# Every alert goes to the fake pager's webhook, which counts and timestamps it.
route:
  receiver: fake-pager
  group_by: [alertname]
  group_wait: 5s
  group_interval: 15s
  repeat_interval: 12h
```

The comment says what the file does. The root `route` sends every alert to the receiver `fake-pager`. `group_by: [alertname]` makes one notification per alert name. `group_wait: 5s` and `group_interval: 15s` are short so the lab sees a page quickly (production values are often 30 s and 5 minutes). `repeat_interval: 12h` re-sends a still-firing alert twice a day.

```yaml
# While the fast-burn page for an SLO is firing, its slow-burn ticket adds nothing.
inhibit_rules:
  - source_matchers: [severity="page"]
    target_matchers: [severity="ticket"]
    equal: [slo]
```

The **inhibit rule**: while an alert matching `source_matchers` (`severity="page"`) is firing, alerts matching `target_matchers` (`severity="ticket"`) are muted, provided that the label `slo` is equal on both. So while `AvailabilityBudgetFastBurn` pages, `AvailabilityBudgetSlowBurn` does not also open a ticket for the same SLO; the other SLO's ticket is unaffected. This is why the generated rules carry the `slo` label.

```yaml
receivers:
  - name: fake-pager
    webhook_configs:
      - url: http://pager:8090/webhook
        send_resolved: false
```

The receiver `fake-pager` has one webhook: `url: http://pager:8090/webhook`, the fake pager on the Compose network; `send_resolved: false`.

### The Prometheus side

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

rule_files:
  - /etc/prometheus/rules/*.rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  # The gateway's Prometheus exporter: the service's SLI metrics. Metrics are never sampled.
  - job_name: otel-gateway
    static_configs:
      - targets: ["otel-gateway:8889"]
  - job_name: otel-gateway-internal
    static_configs:
      - targets: ["otel-gateway:8888"]
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

The live Prometheus scrapes every 5 seconds and evaluates rules every 5 seconds. `rule_files` loads the generated burn-rate rules only (the replay and threshold files live in another folder and are never loaded here). The block `alerting: alertmanagers: - static_configs: - targets: ["alertmanager:9093"]` tells it where to send firing alerts. The scrape jobs are the gateway's metrics exporter (port 8889: the service's SLI metrics, "metrics are never sampled"), the gateway's own health (8888) and Prometheus itself.

### The fake pager

```python
"""The fake pager: counts and timestamps every notification Alertmanager sends it."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from flask import Flask, Response, jsonify, request

from slo_lab.domain.pages import Page
from slo_lab.infrastructure.pager_store import JsonLinesPageStore


def create_pager_app(store: JsonLinesPageStore, clock: Any = time.time) -> Flask:
    """Build the Flask app: ``POST /webhook`` (Alertmanager), ``GET /pages``, ``DELETE /pages``, ``GET /healthz``."""
    app = Flask("pager")

    @app.post("/webhook")
    def webhook() -> tuple[Response, int]:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
            return jsonify({"error": "expected an Alertmanager webhook payload"}), 400
        now = float(clock())
        for alert in body["alerts"]:
            if alert.get("status") != "firing":
                continue
            labels = alert.get("labels", {})
            store.add(
                Page(str(labels.get("alertname", "unknown")), str(labels.get("severity", "page")), timedelta()), now
            )
        return jsonify({"recorded": len(body["alerts"])}), 200

    @app.get("/pages")
    def pages() -> Response:
        return jsonify(store.list())

    @app.delete("/pages")
    def clear() -> Response:
        store.clear()
        return jsonify({"cleared": True})

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})

    return app
```

`create_pager_app(store, clock=time.time)` builds a Flask app (see `flask.md`). `POST /webhook` reads the JSON body (`request.get_json(silent=True)` returns `None` instead of raising when the body is not JSON); anything that is not an object with an `alerts` list is a 400. For each alert whose `status` is `firing` it stores a `Page` with the alert name, the severity (default `page`) and the time it arrived (`clock()`; the clock is a parameter so a test can fix it). `GET /pages` lists them, `DELETE /pages` clears them, `GET /healthz` is the health check. Resolved alerts are ignored.

```python
"""Composition root of the fake pager."""

from __future__ import annotations

import os
from pathlib import Path

from slo_lab.infrastructure.logging_setup import configure_logging
from slo_lab.infrastructure.pager_store import JsonLinesPageStore
from slo_lab.infrastructure.settings import PagerSettings
from slo_lab.interfaces.pager_app import create_pager_app
from slo_lab.interfaces.server import run_server


def main() -> None:
    """Start the fake pager."""
    settings = PagerSettings.from_env(os.environ)
    configure_logging(settings.log_level, "pager")
    run_server(create_pager_app(JsonLinesPageStore(Path(settings.pages_file))), settings.bind_host, settings.port)
```

The composition root: read the pager settings (port 8090, the pages file), set up logging with the service name `pager`, build the store from the file path, create the app and run it with `run_server`.

### The store

```python
"""The fake pager's memory: an append-only JSON-lines file, so pages survive a restart and tests can read them."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from slo_lab.domain.pages import Page


class JsonLinesPageStore:
    """Appends one line per page and can list or clear them."""

    def __init__(self, path: Path) -> None:
        """Use ``path``, creating its folder if needed."""
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, page: Page, received_at: float) -> None:
        """Append a page with the time it arrived (Unix seconds)."""
        record = {
            "alert": page.alert,
            "severity": page.severity,
            "offset_seconds": page.offset.total_seconds(),
            "received_at": received_at,
        }
        with self._lock, self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def list(self) -> list[dict[str, object]]:
        """Every page recorded so far."""
        with self._lock:
            if not self._path.exists():
                return []
            return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line]

    def clear(self) -> None:
        """Forget every page."""
        with self._lock:
            self._path.write_text("", encoding="utf-8")
```

`JsonLinesPageStore` keeps one JSON object per line in a file (the **JSON Lines** format), so pages survive a restart and a test can read them. A `threading.Lock` makes appends, reads and clears safe when several requests arrive at once. `add` opens the file in append mode (`"a"`) inside the lock and writes `json.dumps(record) + "\n"`. `list` reads the whole file and parses each non-empty line. `clear` truncates the file by writing an empty string. The constructor creates the folder (`mkdir(parents=True, exist_ok=True)`). `with self._lock, self._path.open(...) as handle:` enters two context managers in one statement.

### The compose services

```yaml
  alertmanager:
    image: prom/alertmanager:v0.27.0
    command:
      - --config.file=/etc/alertmanager/alertmanager.yml
      - --storage.path=/alertmanager
    volumes:
      - ./observability/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9093/-/healthy"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
    depends_on:
      pager: { condition: service_healthy }

  pager:
    build: .
    image: observability-slo-alerts-and-cost:dev
    command: ["slo-pager"]
    environment:
      BIND_HOST: 0.0.0.0
      PAGER_PORT: "8090"
      PAGES_FILE: /data/pages.jsonl
    volumes:
      - pager-data:/data
    ports:
      - "127.0.0.1:${PAGER_HOST_PORT:-28084}:8090"
    healthcheck:
      test: ["CMD", "slo-health", "--url", "http://localhost:8090/healthz", "--timeout", "10"]
      interval: 10s
      timeout: 20s
      retries: 30
      start_period: 60s
```

`alertmanager` mounts its configuration read-only, stores its state in `/alertmanager` (`--storage.path`), has a `wget` health check on `/-/healthy` and waits for the pager to be healthy (so a first notification is never lost). `pager` runs the same image as the service with the command `slo-pager`, sets `PAGER_PORT` and `PAGES_FILE`, keeps its file on the named volume `pager-data` mounted at `/data`, and is published on 28084. The Dockerfile creates `/data` owned by the unprivileged user, so the volume inherits that owner.

## Try it yourself

```bash
make lint-config
```

Expected (needs Docker): `Checking '/etc/alertmanager/alertmanager.yml'  SUCCESS` among the lines of the other validators.

```bash
curl -s -X POST http://127.0.0.1:28084/webhook -H 'Content-Type: application/json' -d '{"alerts":[{"status":"firing","labels":{"alertname":"Probe","severity":"ticket"}}]}'; echo; curl -s http://127.0.0.1:28084/pages; curl -s -X DELETE http://127.0.0.1:28084/pages
```

Expected (needs `make up`): `{"recorded":1}`, then a list with one page `Probe` and a `received_at` time, then `{"cleared":true}`.

```bash
curl -s http://127.0.0.1:29090/api/v1/alertmanagers
```

Expected: `activeAlertmanagers` with the URL `http://alertmanager:9093/api/v2/alerts`.

```bash
make live
```

Expected (needs `make up`; about six minutes): `page 'AvailabilityBudgetFastBurn' (page) arrived N s after the outage began`, with N around 140.

```bash
.venv/bin/python -m pytest tests/unit/interfaces/test_apps.py -k pager -q
```

Expected: `2 passed`.

## Mistakes people make

- **Paging on every alert.** Group, wait and inhibit, as here.
- **Forgetting `equal` in an inhibit rule**, which would mute the tickets of every SLO whenever any page fires.
- **A webhook that takes long to answer.** Alertmanager retries; the pager answers at once.
- **Letting `repeat_interval` be short.** A still-firing alert would page again and again.
- **Not testing the chain.** A rule that is valid but never reaches a human is worse than none; the live check proves the path.

## Check yourself

1. What does Alertmanager do that Prometheus does not? *Group, deduplicate, inhibit, silence and route alerts to receivers.*
2. What does `group_wait` do? *Waits after the first alert of a group so related alerts can join the same notification.*
3. What does the inhibit rule here mute? *A slow-burn ticket for an SLO while that SLO's fast-burn page is firing.*
4. Why is `send_resolved` false? *The pager counts pages; resolved messages are not pages.*
5. What is JSON Lines? *One JSON object per line of a text file.*

## Further reading

- Alertmanager 0.27 documentation: https://prometheus.io/docs/alerting/0.27/alertmanager/
- Configuration: https://prometheus.io/docs/alerting/0.27/configuration/
- Webhook payload: https://prometheus.io/docs/alerting/0.27/configuration/#webhook_config
- JSON Lines: https://jsonlines.org/
