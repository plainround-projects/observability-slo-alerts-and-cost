# Grafana (11.2.0)

## What it is

Grafana is a web application that draws dashboards from data stores. It stores no telemetry itself. You connect **data sources** (Prometheus, Tempo, Loki), and each dashboard **panel** runs a query against one data source and draws the result as a graph, a table or a list of log lines. Grafana can also link data sources: a click on an exemplar dot opens a trace in Tempo; a click on "logs for this span" runs a Loki query. This repository pins Grafana 11.2.0.

## Why this repository uses it

Two dashboards make the lesson visible. **SLO overview** shows the live service: the bad-request ratio over the one-hour and five-minute windows with the page threshold drawn as a line, the burn rate, the share of slow requests, the request rate by status, and which alerts are firing. **Replayed week** draws the simulated week from the second Prometheus, so you can see the outage, the two-hour degradation and the 90-second blip as shapes, and the CPU spike that touches no user.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/grafana/provisioning/datasources/datasources.yaml` | Four data sources: the live Prometheus, the replay Prometheus, Tempo and Loki. |
| `observability/grafana/provisioning/dashboards/dashboards.yaml` | Tells Grafana to load dashboards from a folder. |
| `observability/grafana/dashboards/slo-overview.json` | The live dashboard (five panels). |
| `observability/grafana/dashboards/replayed-week.json` | The replay dashboard (three panels). |
| `docker-compose.yml` (service `grafana`) | The pinned image, mounts, admin password, health check. |
| `src/slo_lab/lab/health_cli.py` | Asks Grafana to query its data sources. |
| `tests/observability/test_configuration_as_code.py` | Lints the data sources and both dashboards. |

## How it works underneath

Grafana runs as one server process with a built-in web UI, a small database (SQLite in `/var/lib/grafana`) for its own settings, and an HTTP API. The browser sends requests to the Grafana server, and the server forwards the queries to the data sources (`access: proxy`), so the browser never needs to reach Prometheus directly.

**Provisioning** means Grafana reads YAML files from `/etc/grafana/provisioning` at start and creates data sources and dashboard providers from them, instead of someone clicking in the UI. A **dashboard provider** tells Grafana a folder where it should load dashboard JSON files. The result is the same on every start, and everything is code that can be reviewed.

Settings can come from environment variables named `GF_<SECTION>_<KEY>` (for example `GF_SECURITY_ADMIN_PASSWORD`).

A dashboard JSON has a `uid` (its stable id), a list of `panels`, each with a `gridPos` (x, y, width, height on a 24-column grid), `targets` (the queries), and display options. `schemaVersion` records the structure version Grafana expects.

Grafana's data-source API: `GET /api/datasources/uid/<uid>/health` asks a data source to test itself, and `GET /api/datasources/proxy/uid/<uid>/<path>` passes a request through to that data source.

## Walkthrough of this repository's use

### `datasources.yaml`

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    jsonData:
      httpMethod: POST
  - name: Prometheus replay
    uid: prometheus-replay
    type: prometheus
    access: proxy
    url: http://prometheus-replay:9090
    jsonData:
      httpMethod: POST
```

`apiVersion: 1` is the provisioning format. **Prometheus** (`uid: prometheus`, the default) points at `http://prometheus:9090`; **Prometheus replay** (`uid: prometheus-replay`) points at `http://prometheus-replay:9090`, the instance that serves the backfilled week. Both use `access: proxy` (Grafana's server makes the queries) and `httpMethod: POST` (long expressions fit in the body). The replay data source is only reachable while that profile's container runs.

```yaml
  - name: Tempo
    uid: tempo
    type: tempo
    access: proxy
    url: http://tempo:3200
    jsonData:
      tracesToLogsV2:
        datasourceUid: loki
        spanStartTimeShift: "-1m"
        spanEndTimeShift: "1m"
        customQuery: true
        query: '{service_name="checkout"} | trace_id="${__span.traceId}"'
  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://loki:3100
```

**Tempo** (`uid: tempo`, `http://tempo:3200`) with `tracesToLogsV2`: from a span, "Logs for this span" opens Loki (`datasourceUid: loki`) over a window of a minute either side of the span (`spanStartTimeShift: "-1m"`, `spanEndTimeShift: "1m"`) with the custom query `{service_name="checkout"} | trace_id="${__span.traceId}"`, where `${__span.traceId}` is filled in by Grafana. **Loki** (`uid: loki`, `http://loki:3100`).

### `dashboards.yaml`

```yaml
apiVersion: 1

providers:
  - name: slo-alerts
    type: file
    disableDeletion: true
    allowUiUpdates: false
    options:
      path: /etc/grafana/dashboards
```

A dashboard provider named `slo-alerts` reads JSON files from `/etc/grafana/dashboards`. `disableDeletion: true` and `allowUiUpdates: false` keep the dashboards exactly as in the repository.

### `slo-overview.json`

```json
{
  "uid": "slo-overview",
  "title": "SLO overview",
  "description": "Availability and latency SLIs, burn rates and firing alerts for the live checkout service.",
  "tags": [
    "observability",
    "slo"
  ],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "15s",
  "time": {
    "from": "now-1h",
    "to": "now"
  },
  "editable": false,
  "panels": [
```

`uid: slo-overview` is the stable id; `schemaVersion: 39` is the structure version for Grafana 11.2; `refresh: "15s"`; the default range is the last hour; `editable: false`.

**Panel 1, availability bad-request ratio.**

```json
    {
      "id": 1,
      "type": "timeseries",
      "title": "Availability: bad-request ratio (1h and 5m windows)",
      "description": "The page needs BOTH windows above the threshold line.",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 0,
        "y": 0,
        "w": 12,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percentunit",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[1h]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[1h])))",
          "legendFormat": "1h"
        },
        {
          "refId": "B",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[5m]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[5m])))",
          "legendFormat": "5m"
        },
        {
          "refId": "C",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "vector(14.4 * 0.005)",
          "legendFormat": "page threshold (14.4 x budget)"
        }
      ]
    },
```

A `timeseries` panel with three queries, each a `targets` entry with its own `refId`: `A` the one-hour bad ratio (legend `1h`), `B` the five-minute ratio (legend `5m`), and `C`, `vector(14.4 * 0.005)`, a constant line at the page threshold of 7.2 percent (legend `page threshold (14.4 x budget)`). The description tells the reader the page needs both windows above the line. `unit: "percentunit"` shows 0.05 as 5%. `gridPos` places it top-left on the 24-column grid (width 12, height 8).

**Panel 2, burn rate.**

```json
    {
      "id": 2,
      "type": "timeseries",
      "title": "Availability: burn rate (bad ratio / error budget)",
      "description": "1.0 spends exactly the monthly budget; the page rule fires above 14.4.",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 12,
        "y": 0,
        "w": 12,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "short",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "(1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[1h]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[1h])))) / 0.005",
          "legendFormat": "1h"
        },
        {
          "refId": "B",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "(1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[5m]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[5m])))) / 0.005",
          "legendFormat": "5m"
        }
      ]
    },
```

The same two windows divided by `0.005`, the error budget, so the value is the burn rate itself: 1.0 spends exactly the monthly budget, and the page rule fires above 14.4. Top-right (`x: 12`).

**Panel 3, latency.**

```json
    {
      "id": 3,
      "type": "timeseries",
      "title": "Latency: share of requests slower than 300 ms (1h and 5m)",
      "description": "",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 0,
        "y": 8,
        "w": 12,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percentunit",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "1 - sum(rate(http_request_duration_seconds_bucket{route!=\"/healthz\",le=\"0.3\"}[1h])) / sum(rate(http_request_duration_seconds_count{route!=\"/healthz\"}[1h]))",
          "legendFormat": "1h"
        },
        {
          "refId": "B",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "1 - sum(rate(http_request_duration_seconds_bucket{route!=\"/healthz\",le=\"0.3\"}[5m])) / sum(rate(http_request_duration_seconds_count{route!=\"/healthz\"}[5m]))",
          "legendFormat": "5m"
        }
      ]
    },
```

The share of requests slower than 300 ms over one hour and five minutes: `1 - sum(rate(..._bucket{...,le="0.3"}[1h])) / sum(rate(..._count{...}[1h]))`.

**Panel 4, requests per second by status.**

```json
    {
      "id": 4,
      "type": "timeseries",
      "title": "Requests per second by status",
      "description": "",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 12,
        "y": 8,
        "w": 12,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "reqps",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "sum by (status) (rate(http_requests_total{route!=\"/healthz\"}[1m]))",
          "legendFormat": "{{status}}"
        }
      ]
    },
```

`sum by (status) (rate(http_requests_total{route!="/healthz"}[1m]))`, unit `reqps`, legend by status.

**Panel 5, alerts firing.**

```json
    {
      "id": 5,
      "type": "timeseries",
      "title": "Alerts firing",
      "description": "",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 0,
        "y": 16,
        "w": 24,
        "h": 6
      },
      "fieldConfig": {
        "defaults": {
          "unit": "short",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "sum by (alertname, severity) (ALERTS{alertstate=\"firing\"})",
          "legendFormat": "{{alertname}} ({{severity}})"
        }
      ]
    }
  ],
  "templating": {
    "list": []
  },
  "annotations": {
    "list": []
  }
}
```

`sum by (alertname, severity) (ALERTS{alertstate="firing"})` across the full width (`w: 24`), legend `{{alertname}} ({{severity}})`. The last lines are empty `templating` and `annotations`.

### `replayed-week.json`

```json
{
  "uid": "replayed-week",
  "title": "Replayed week",
  "description": "The simulated week backfilled by make replay (start the replay Prometheus first).",
  "tags": [
    "observability",
    "slo"
  ],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "",
  "time": {
    "from": "now-7d",
    "to": "now"
  },
  "editable": false,
  "panels": [
```

`uid: replayed-week`; no auto-refresh (`refresh: ""`, the data is historical); the default range is the last seven days. The description says to start the replay Prometheus first.

```json
    {
      "id": 1,
      "type": "timeseries",
      "title": "Replayed week: 5-minute bad-request ratio",
      "description": "The full outage, the two-hour degradation and the 90-second blip are visible; the CPU spike is not.",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus-replay"
      },
      "gridPos": {
        "x": 0,
        "y": 0,
        "w": 24,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percentunit",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus-replay"
          },
          "expr": "1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[5m]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[5m])))",
          "legendFormat": "bad ratio"
        }
      ]
    },
```

Panel 1 is the five-minute bad-request ratio over the whole week on the replay data source (`uid: prometheus-replay`), full width: the full outage, the two-hour degradation and the 90-second blip are visible; the CPU spike is not.

```json
    {
      "id": 2,
      "type": "timeseries",
      "title": "Replayed week: 6-hour bad-request ratio",
      "description": "",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus-replay"
      },
      "gridPos": {
        "x": 0,
        "y": 8,
        "w": 12,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percentunit",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus-replay"
          },
          "expr": "1 - (sum(rate(http_requests_total{route!=\"/healthz\",status!~\"5..\"}[6h]))) / (sum(rate(http_requests_total{route!=\"/healthz\"}[6h])))",
          "legendFormat": "bad ratio"
        }
      ]
    },
```

Panel 2 is the six-hour bad-request ratio: here you can see why the lesson's six-hour ticket rule misses the degradation, because the long window flattens it.

```json
    {
      "id": 3,
      "type": "timeseries",
      "title": "Replayed week: CPU use",
      "description": "Bursts and one spike to 95%; none of it touches a user.",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus-replay"
      },
      "gridPos": {
        "x": 12,
        "y": 8,
        "w": 12,
        "h": 8
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percent",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus-replay"
          },
          "expr": "cpu_usage_percent",
          "legendFormat": "cpu %"
        }
      ]
    }
  ],
  "templating": {
    "list": []
  },
  "annotations": {
    "list": []
  }
}
```

Panel 3 is CPU use: bursts and one spike to 95 percent, none of which touches a user.

### The Compose service

```yaml
  grafana:
    image: grafana/grafana:11.2.0
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?run make up, which creates .env from .env.example}
      GF_ANALYTICS_REPORTING_ENABLED: "false"
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./observability/grafana/dashboards:/etc/grafana/dashboards:ro
      - grafana-data:/var/lib/grafana
    ports:
      - "127.0.0.1:${GRAFANA_HOST_PORT:-23000}:3000"
    depends_on:
      tempo: { condition: service_healthy }
      prometheus: { condition: service_healthy }
      loki: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3000/api/health | grep -q ok"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The admin password comes from `.env` (the `:?` form stops Compose if empty), analytics and sign-up are off, the provisioning and dashboards folders are mounted read-only, `grafana-data` holds Grafana's own database, and it waits for Tempo, Prometheus and Loki to be healthy. The health check looks for `ok` in `/api/health`.

## Try it yourself

Open <http://127.0.0.1:23000> (user `admin`, the password from `.env`) and the dashboards "SLO overview" and "Replayed week".

```bash
curl -s -u "admin:$(grep ^GRAFANA_ADMIN_PASSWORD .env | cut -d= -f2)" http://127.0.0.1:23000/api/datasources | python3 -c "import json,sys; print(sorted(d['uid'] for d in json.load(sys.stdin)))"
```

Expected (needs `make up`): `['loki', 'prometheus', 'prometheus-replay', 'tempo']`.

```bash
curl -s -u "admin:$(grep ^GRAFANA_ADMIN_PASSWORD .env | cut -d= -f2)" http://127.0.0.1:23000/api/search?type=dash-db | python3 -c "import json,sys; print(sorted(d['uid'] for d in json.load(sys.stdin)))"
```

Expected: `['replayed-week', 'slo-overview']`.

```bash
.venv/bin/python -m pytest tests/observability -q
```

Expected: all configuration tests pass.

```bash
make health
```

Expected: nine `ok` lines; the last three are Grafana querying Prometheus, Loki and Tempo.

```bash
python3 -m json.tool observability/grafana/dashboards/slo-overview.json > /dev/null && echo valid
```

Expected: `valid`.

## Mistakes people make

- **Editing a provisioned dashboard in the UI** and losing the change at the next restart. Here `allowUiUpdates: false` and `editable: false` make that explicit.
- **Using a data source's name in dashboards** instead of its `uid`. Names can change; uids are stable.
- **Default passwords.** The admin password here comes from `.env` and the compose file refuses to start without it.
- **Unlinked data sources.** Without `exemplarTraceIdDestinations`, exemplar dots are not clickable.

## Check yourself

1. Does Grafana store metrics, traces or logs? *No. It queries other stores.*
2. What is provisioning? *Configuring Grafana from files at start, instead of the UI.*
3. What does `access: proxy` mean for a data source? *Grafana's server makes the queries, not the browser.*
4. What is a panel's `gridPos`? *Its position and size on the 24-column grid.*
5. Why does the dashboard refer to data sources by `uid`? *The uid is stable even if the display name changes.*

## Further reading

- Grafana 11.2 documentation: https://grafana.com/docs/grafana/v11.2/
- Provisioning: https://grafana.com/docs/grafana/v11.2/administration/provisioning/
- Dashboard JSON model: https://grafana.com/docs/grafana/v11.2/dashboards/build-dashboards/view-dashboard-json-model/
- Data source HTTP API: https://grafana.com/docs/grafana/v11.2/developers/http_api/data_source/
- Configure via environment variables: https://grafana.com/docs/grafana/v11.2/setup-grafana/configure-grafana/#override-configuration-with-environment-variables
