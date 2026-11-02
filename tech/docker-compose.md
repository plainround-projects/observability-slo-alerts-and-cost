# Docker Compose

## What it is

Docker Compose is a tool that starts several containers together from one file. The file is usually named `docker-compose.yml`. In it you list **services** (each service is one container definition: which image, which command, which environment variables, which ports, which volumes), and Compose creates a private network for them, starts them in the right order, and lets you stop them all with one command.

This repository uses **Compose v2**, which you run as `docker compose` (with a space). It is part of the Docker command-line program. The older v1 was a separate program called `docker-compose` with a hyphen.

## Why this repository uses it

The lab is eight containers that must find each other, plus a ninth that only runs on demand. The checkout service sends telemetry to the gateway; the gateway splits it between Tempo, Prometheus (by scrape) and Loki; Prometheus evaluates the generated rules and sends alerts to Alertmanager; Alertmanager posts to the fake pager; Grafana reads everything. The Compose file is that picture as configuration. It also makes two lab moves possible without editing a file: the gateway's configuration is chosen by a variable (`GATEWAY_CONFIG`), and the replay Prometheus belongs to a **profile**, so it starts only when asked.

## Where it appears

| Path | What it does |
| --- | --- |
| `docker-compose.yml` | The services and volumes. |
| `src/slo_lab/lab/compose.py` | Runs `docker compose` with `GATEWAY_CONFIG` set, and with `--profile replay` (see `python-subprocess.md`). |
| `Makefile` | `up` and `down`. |
| `tests/observability/test_configuration_as_code.py` | Lints it: pinned images, loopback ports, variables declared in `.env.example`, the replay profile. |

## How it works underneath

`docker compose up` reads the YAML file, resolves `${VARIABLE}` placeholders from your shell and from a file named `.env` in the same folder, and builds a plan. For every service it creates a container. For the whole project it creates one **network**. Each container joins the network under its service name, and Docker runs a small DNS server on that network. So the container `back` can call `http://postgres:5432` and the name `postgres` resolves to the database container's address. These names only exist inside the network.

A **published port** (`"127.0.0.1:23000:3000"`) opens a door from your machine into a container: connections to port 23000 on your machine's own loopback address are forwarded to port 3000 inside the container. Without the `127.0.0.1:` part, Docker would listen on every network interface and other computers could connect.

`depends_on` with `condition: service_healthy` makes Compose wait until the other service's health check passes before starting this one. `--wait` makes `docker compose up` itself wait until all started services are healthy (or running, when they have no health check).

A **named volume** listed at the bottom of the file is created once and kept between runs. `docker compose down -v` stops the containers, removes them and the network, and deletes the named volumes.

A **project name** (the `name:` key, or the folder name by default) is a prefix Compose puts on every container, network and volume it creates, so two projects do not clash.

## Walkthrough of this repository's use

### The header

```yaml
# The lab on one laptop: the checkout service, a Collector gateway that samples and redacts, Tempo, Loki, Prometheus
# with Alertmanager and a fake pager, Grafana, and (profile "replay") a second Prometheus that serves the replayed week.
# Every host port is bound to 127.0.0.1 only. Versions are pinned to the ones the lesson names.
name: observability-slo-alerts-and-cost
```

The comments list what runs and the two rules (ports on `127.0.0.1` only; versions pinned to the lesson's). `name:` is the project name used as a prefix for containers, networks and volumes.

### `checkout`: the service

```yaml
services:
  checkout:
    build: .
    image: observability-slo-alerts-and-cost:dev
    command: ["opentelemetry-instrument", "slo-service"]
    environment:
      BIND_HOST: 0.0.0.0
      SERVICE_PORT: "8080"
      # Lab only: lets a request ask to fail or run slowly, so the scenarios can plant known incidents.
      ALLOW_SIMULATION: "true"
      OTEL_SERVICE_NAME: checkout
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-gateway:4318
      OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
      OTEL_TRACES_EXPORTER: otlp
      OTEL_METRICS_EXPORTER: otlp
      OTEL_LOGS_EXPORTER: otlp
      OTEL_METRIC_EXPORT_INTERVAL: "5000"
      OTEL_RESOURCE_ATTRIBUTES: service.version=0.1.0,deployment.environment=local
      OTEL_PYTHON_FLASK_EXCLUDED_URLS: healthz
      LOG_LEVEL: INFO
```

`build: .` builds the image from the `Dockerfile`; `image:` names it so the pager can reuse it; `command` starts the service under `opentelemetry-instrument`. The `environment`: the bind address `0.0.0.0`, the port 8080, and `ALLOW_SIMULATION: "true"` with a comment that it is lab-only (it lets a request ask to fail or run slowly). Then the OpenTelemetry settings, explained in `opentelemetry.md`: the endpoint is the gateway `http://otel-gateway:4318`, all three signals are exported, metrics every 5 seconds, `OTEL_SERVICE_NAME: checkout`.

```yaml
    ports:
      - "127.0.0.1:${SERVICE_HOST_PORT:-28083}:8080"
    depends_on:
      otel-gateway: { condition: service_started }
    healthcheck:
      test: ["CMD", "slo-health", "--url", "http://localhost:8080/healthz", "--timeout", "10"]
      interval: 10s
      timeout: 20s
      retries: 30
      start_period: 60s
    stop_grace_period: 15s
```

`ports: "127.0.0.1:${SERVICE_HOST_PORT:-28083}:8080"` publishes the service on loopback only. `depends_on` waits for the gateway to have started (it has no health check of its own). The health check runs the service's own probe `slo-health --url http://localhost:8080/healthz --timeout 10` in exec form; the generous timing (20 s timeout, 30 retries, 60 s start period) tolerates a busy machine. `stop_grace_period: 15s` lets in-flight requests finish after `SIGTERM`.

### `otel-gateway`

```yaml
  otel-gateway:
    image: otel/opentelemetry-collector-contrib:0.111.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      # make run switches this between gateway.raw.yaml and gateway.yaml.
      - ${GATEWAY_CONFIG:-./observability/collector/gateway.yaml}:/etc/otelcol/config.yaml:ro
    ports:
      - "127.0.0.1:${GATEWAY_HEALTH_HOST_PORT:-23133}:13133"
    depends_on:
      tempo: { condition: service_healthy }
      loki: { condition: service_healthy }
```

Explained in `opentelemetry-collector.md`: the mounted configuration is `${GATEWAY_CONFIG:-./observability/collector/gateway.yaml}` (the comment: `make run` switches it between the raw and the protected file), only the health port is published (`23133`), and it waits for Tempo and Loki.

### `tempo` and `loki`

```yaml
  tempo:
    image: grafana/tempo:2.6.1
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    volumes:
      - ./observability/tempo/tempo.yaml:/etc/tempo/tempo.yaml:ro
      - tempo-data:/var/tempo
    ports:
      - "127.0.0.1:${TEMPO_HOST_PORT:-23200}:3200"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3200/ready | grep -q ready"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s

  loki:
    image: grafana/loki:3.1.1
    command: ["-config.file=/etc/loki/loki.yaml"]
    volumes:
      - ./observability/loki/loki.yaml:/etc/loki/loki.yaml:ro
      - loki-data:/loki
    ports:
      - "127.0.0.1:${LOKI_HOST_PORT:-23100}:3100"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3100/ready | grep -q ready"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

Pinned images, `-config.file` flags, configuration mounted read-only, data volumes, loopback ports 23200 and 23100, and `wget ... | grep -q ready` health checks. See `tempo.md` and `loki.md`.

### `prometheus` and `prometheus-replay`

```yaml
  prometheus:
    image: prom/prometheus:v2.54.1
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=2d
    volumes:
      - ./observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./observability/prometheus/rules:/etc/prometheus/rules:ro
      - prometheus-data:/prometheus
    ports:
      - "127.0.0.1:${PROMETHEUS_HOST_PORT:-29090}:9090"
    depends_on:
      alertmanager: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s

  # Serves the backfilled week (make replay). It loads no rules: the replay evaluates them at each historical step.
  prometheus-replay:
    image: prom/prometheus:v2.54.1
    profiles: ["replay"]
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=30d
    volumes:
      - ./observability/prometheus/replay/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - replay-data:/prometheus
    ports:
      - "127.0.0.1:${REPLAY_HOST_PORT:-29091}:9090"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The live Prometheus (waits for Alertmanager, loads the generated rules) and the replay one. The line `profiles: ["replay"]` makes `prometheus-replay` part of the profile `replay`: a plain `docker compose up` does not start it, `docker compose --profile replay up prometheus-replay` does. Its retention is 30 days so the backfilled week is not deleted, its configuration scrapes nothing, and its port is 29091. See `prometheus.md` and `openmetrics.md`.

### `alertmanager` and `pager`

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

Alertmanager (pinned `prom/alertmanager:v0.27.0`) mounts its file and waits for the pager to be healthy. The pager reuses the service image with the command `slo-pager`, a data volume at `/data` and a published port 28084. See `alertmanager.md`.

### `grafana`

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

See `grafana.md`.

### Volumes

```yaml
volumes:
  tempo-data:
  loki-data:
  prometheus-data:
  replay-data:
  pager-data:
  grafana-data:
```

Six named volumes: `tempo-data`, `loki-data`, `prometheus-data`, `replay-data`, `pager-data` and `grafana-data`, all deleted by `make down`.

### The Makefile side

```makefile
up: .env $(STAMP) ## build and start the service, the gateway, the backends, Alertmanager, the fake pager and Grafana
	docker compose up -d --build --wait --wait-timeout 1800
	$(MAKE) --no-print-directory health
```

`make up` runs `docker compose up -d --build --wait --wait-timeout 1800` (build, start everything except the replay profile, wait up to thirty minutes for all health checks), then `make health`.

```makefile
down: ## stop the stack (including the replay Prometheus) and delete its data
	docker compose --profile replay down -v
```

`make down` runs `docker compose --profile replay down -v`: the profile flag makes sure the replay container and its volume are removed too.

## Try it yourself

```bash
docker compose config --quiet && echo valid
```

Expected: `valid` (needs `.env`, which `make up` creates).

```bash
docker compose config --services
```

Expected: eight services (the replay one is hidden because of its profile); with `--profile replay` nine.

```bash
docker compose --profile replay config --services | sort
```

Expected: `alertmanager`, `checkout`, `grafana`, `loki`, `otel-gateway`, `pager`, `prometheus`, `prometheus-replay`, `tempo`.

```bash
GATEWAY_CONFIG=./observability/collector/gateway.raw.yaml docker compose config | grep gateway.raw
```

Expected: the raw configuration is the one mounted: a shell variable overrides the default.

```bash
docker compose ps
```

Expected (after `make up`): eight containers, healthy or running (the gateway has no health check).

## Mistakes people make

- **Expecting `depends_on` to wait for readiness.** The short form only waits for the container to *start*. Use `condition: service_healthy` together with a health check.
- **Publishing a port on every interface.** `"3000:3000"` listens on all interfaces. Prefix `127.0.0.1:` unless other machines must connect.
- **Putting secrets in the YAML.** Keep them in `.env` (which is git-ignored) and reference them as `${NAME}`. This repository uses `${NAME:?message}` so Compose stops with a clear message when a required variable is missing.
- **Forgetting `-v` on `down`.** Without it the volumes survive, and a later `up` starts with the old data. Sometimes that is wanted; for a repeatable lab it is not.
- **Using `localhost` between containers.** Use the service name.
- **Reading the YAML indentation wrongly.** One space too many silently makes a key belong to another block. Lint the file (`docker compose config` prints the final, resolved version).

## Check yourself

1. How does one container find another by name? *Compose puts all services on one network with a DNS server; the service name resolves to that container's address.*
2. What does `127.0.0.1:23000:3000` mean? *Port 3000 inside the container is reachable only from your own machine, on port 23000.*
3. What is the difference between `depends_on: [db]` and `depends_on: {db: {condition: service_healthy}}`? *The first waits for the container to start; the second waits until its health check passes.*
4. What does `docker compose down -v` delete that `docker compose down` keeps? *The named volumes, and so the data stored in them.*
5. What does `${NAME:?text}` do when `NAME` is empty or unset? *Compose stops and prints the text as an error.*

## Further reading

- Compose file reference: https://docs.docker.com/reference/compose-file/
- `docker compose up`: https://docs.docker.com/reference/cli/docker/compose/up/
- Compose networking: https://docs.docker.com/compose/how-tos/networking/
- Variable interpolation: https://docs.docker.com/reference/compose-file/interpolation/
