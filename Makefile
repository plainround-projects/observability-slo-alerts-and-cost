# make up | run | test | lint | down      (make help lists everything)
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV := .venv
PY := $(VENV)/bin/python
STAMP := $(VENV)/.installed
PROMETHEUS_IMAGE := prom/prometheus:v2.54.1
COLLECTOR_IMAGE := otel/opentelemetry-collector-contrib:0.111.0
ALERTMANAGER_IMAGE := prom/alertmanager:v0.27.0

-include .env
export

.PHONY: help install up rules replay volume live run health lint format lint-config test test-rules \
        test-integration test-e2e audit check docker-build down clean

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort

.env:
	cp .env.example .env
	@echo "created .env from .env.example"

$(STAMP): requirements-dev.lock pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install -q --no-deps -e .
	touch $@

install: $(STAMP) ## create .venv from the lock file

up: .env $(STAMP) ## build and start the service, the gateway, the backends, Alertmanager, the fake pager and Grafana
	docker compose up -d --build --wait --wait-timeout 900
	$(MAKE) --no-print-directory health

health: $(STAMP) ## every container and backend answers
	$(PY) -m slo_lab.lab.health_cli

rules: $(STAMP) ## regenerate the burn-rate rule files from observability/slo/*.yaml
	$(VENV)/bin/slo-rules

# Steps 1 to 4: a simulated week is backfilled into a second Prometheus with promtool, and three alert sets are replayed
# over it with real PromQL: the threshold alerts, the lesson's burn-rate numbers and this repository's policy.
replay: .env $(STAMP) ## replay the simulated week through the threshold and burn-rate alerts and compare them
	$(VENV)/bin/slo-replay

# Steps 5 and 6: the same traffic through a gateway without and with tail sampling and redaction.
volume: .env $(STAMP) ## compare stored trace volume and personal data with and without the gateway's policies
	$(VENV)/bin/slo-volume --requests 6000

live: .env $(STAMP) ## a real outage: warm up, fail every request, wait for the fast-burn page at the fake pager
	$(VENV)/bin/slo-live

run: replay volume live ## the whole lesson: replay, volume and redaction, and the live page

lint: $(STAMP) lint-config ## ruff, format check, mypy, import-linter, generated-rules freshness, and the configs
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/mypy
	$(VENV)/bin/lint-imports
	$(VENV)/bin/slo-rules --check

format: $(STAMP) ## apply ruff's formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

# promtool, amtool and the Collector validate their own files, so a typo fails here and not at 3 a.m.
lint-config: ## promtool checks every rule file and config; amtool and the Collector validate theirs
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check rules /etc/prometheus/rules/burn_rate.rules.yml /etc/prometheus/replay/threshold.rules.yml \
	  /etc/prometheus/replay/burn_rate_lesson.rules.yml
	docker run --rm --entrypoint sh -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) -c \
	  'mkdir -p /tmp/rules && cp /etc/prometheus/rules/*.yml /tmp/rules/ && sed "s#/etc/prometheus/rules#/tmp/rules#" /etc/prometheus/prometheus.yml > /tmp/prometheus.yml && promtool check config /tmp/prometheus.yml'
	docker run --rm --entrypoint amtool -v "$(CURDIR)/observability/alertmanager:/etc/alertmanager:ro" $(ALERTMANAGER_IMAGE) \
	  check-config /etc/alertmanager/alertmanager.yml
	docker run --rm -v "$(CURDIR)/observability/collector/gateway.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
	docker run --rm -v "$(CURDIR)/observability/collector/gateway.raw.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml

# Unit tests need no containers. Coverage is reported; the domain and application must stay at 95% or more.
test: $(STAMP) ## unit and config-lint tests with coverage (no stack needed)
	$(PY) -m pytest tests/unit tests/observability --cov=slo_lab --cov-report=term
	$(PY) -m coverage report --include='src/slo_lab/domain/*,src/slo_lab/application/*' --fail-under=95

test-rules: ## replay scenarios through promtool: a fast burn pages, a blip does not, a slow leak opens a ticket
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  test rules /etc/prometheus/tests/burn_rate.rules.test.yml

test-integration: $(STAMP) ## the service, the gateway, the pager and Alertmanager wired together (make up first)
	$(PY) -m pytest tests/integration -m integration

test-e2e: $(STAMP) ## the replay, the volume and redaction comparison and the live page, asserted (make up first)
	$(PY) -m pytest tests/e2e -m e2e

audit: $(STAMP) ## check the locked dependencies for known vulnerabilities
	$(VENV)/bin/pip-audit --no-deps --disable-pip -r requirements-dev.lock

check: lint test test-rules ## everything that needs no running stack

docker-build: ## build the service image
	docker build -t observability-slo-alerts-and-cost:dev .

down: ## stop the stack (including the replay Prometheus) and delete its data
	docker compose --profile replay down -v

clean: down
	rm -rf $(VENV) var logs .pytest_cache .mypy_cache .ruff_cache .coverage .import_linter_cache
