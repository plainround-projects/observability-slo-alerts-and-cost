# Make (the Makefile)

## What it is

Make is a command-line tool that runs named recipes from a file called `Makefile`. Each recipe is a **target**: a name, optionally the files or other targets it needs (its **prerequisites**), and the shell commands that produce it. Make was designed to rebuild only what is out of date, by comparing file modification times. Teams also use it as a simple "task runner": `make test` is shorter and more memorable than the long command it hides.

## Why this repository uses it

The lesson's steps are `make rules`, `make replay`, `make volume` and `make live`, or all four as `make run`. The Makefile gives every step a name, chains them, and is what CI calls. It also runs the generated-rules freshness check inside `make lint`.

## Where it appears

| Path | What it does |
| --- | --- |
| `Makefile` | All targets. |
| `.editorconfig` | Keeps tab indentation for the Makefile. |
| `.github/workflows/ci.yml` | Calls `make check`, `make audit`, `make up`, `make test-integration test-e2e`, `make down`. |

## How it works underneath

You type `make up`. Make reads `Makefile` in the current folder, finds the target `up`, and first makes each of its prerequisites (right of the colon). A prerequisite that is a file is rebuilt only if it does not exist or is older than the files it depends on. A prerequisite that is only a name is always run. Then Make runs the target's **recipe**: each line indented with one **tab** character is handed to a shell (`/bin/sh`) as a separate command. If any command exits with a non-zero code, Make stops and reports the error.

Things to know:

- A **variable** is set with `NAME := value` (evaluated once, right away) or `NAME ?= value` (set only if not already set, so you can override it from the shell). `$(NAME)` reads it. `$@` is the name of the current target. `$(MAKE)` is the Make program itself, used to call Make again.
- `.PHONY` lists targets that are *not* files. Without it, a file called `test` in the folder would make `make test` say "nothing to do".
- A line starting with `@` is run without printing the command first.
- `-include .env` reads another Makefile-style file and does not fail if it is missing. `export` with no names passes every Make variable to the commands as environment variables.
- Each recipe line runs in its own shell, so `cd` or a variable set on one line is gone on the next. Joining lines with `\` makes them one command.
- `##` comments after a target are a convention: the `help` target greps them to print a menu.

## Walkthrough of this repository's use

### Variables and settings

```makefile
# make up | run | test | lint | down      (make help lists everything)
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV := .venv
PY := $(VENV)/bin/python
STAMP := $(VENV)/.installed
PROMETHEUS_IMAGE := prom/prometheus:v2.54.1
COLLECTOR_IMAGE := otel/opentelemetry-collector-contrib:0.111.0
ALERTMANAGER_IMAGE := prom/alertmanager:v0.27.0
```

Line 1 lists the main commands. `PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)` picks the first interpreter that exists. `VENV`, `PY` and `STAMP` name the virtual environment, its interpreter and a marker file that exists only after a successful install. The three image variables pin the Prometheus, Collector and Alertmanager images used by the validation targets to the versions in the Compose file.

```makefile
-include .env
export

.PHONY: help install up rules replay volume live run health lint format lint-config test test-rules \
        test-integration test-e2e audit check docker-build down clean
```

`-include .env` reads `.env` if it exists, `export` hands every Make variable to the commands as an environment variable. `.PHONY` lists the targets that are names, not files; the backslash continues the list.

### `help`, `.env`, the virtual environment

```makefile
help: ## list the targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort

.env:
	cp .env.example .env
	@echo "created .env from .env.example"

$(STAMP): requirements-dev.lock pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install -q --no-deps -e .
	touch $@

install: $(STAMP) ## create .venv from the lock file
```

`help` is the self-documenting menu: `grep -hE '^[a-z-]+:.*## '` finds every line that looks like `target: ... ## text` (`-h` drops the file names grep would add because `-include .env` puts `.env` in `$(MAKEFILE_LIST)`), `sed -E 's/:.*## /\t/'` makes it `target<TAB>text`, `sort` orders it. `.env:` copies `.env.example` when `.env` is missing. `$(STAMP)` depends on the lock file and `pyproject.toml`: when either changes it creates the venv, installs the locked packages with hash checking, installs the project in editable mode without dependencies, and `touch $@` refreshes the marker. `install` is just a friendly name for it.

### The stack

```makefile
up: .env $(STAMP) ## build and start the service, the gateway, the backends, Alertmanager, the fake pager and Grafana
	docker compose up -d --build --wait --wait-timeout 1800
	$(MAKE) --no-print-directory health

health: $(STAMP) ## every container and backend answers
	$(PY) -m slo_lab.lab.health_cli
```

`up` builds and starts everything and waits (see `docker-compose.md`), then `health` runs the stack check (see `argparse-and-urllib.md`).

### The lesson's steps

```makefile
rules: $(STAMP) ## regenerate the burn-rate rule files from observability/slo/*.yaml
	$(VENV)/bin/slo-rules
```

`rules` regenerates the burn-rate rule files from `observability/slo/*.yaml` (`slo-rules`, see `slo-and-burn-rate.md`).

```makefile
# Steps 1 to 4: a simulated week is backfilled into a second Prometheus with promtool, and three alert sets are replayed
# over it with real PromQL: the threshold alerts, the lesson's burn-rate numbers and this repository's policy.
replay: .env $(STAMP) ## replay the simulated week through the threshold and burn-rate alerts and compare them
	$(VENV)/bin/slo-replay
```

`replay` is steps 1 to 4: the comment says a simulated week is backfilled into a second Prometheus with promtool and three alert sets are replayed over it with real PromQL: the threshold alerts, the lesson's burn-rate numbers and this repository's policy. It depends on `.env` and the venv.

```makefile
# Steps 5 and 6: the same traffic through a gateway without and with tail sampling and redaction.
volume: .env $(STAMP) ## compare stored trace volume and personal data with and without the gateway's policies
	$(VENV)/bin/slo-volume --requests 6000
```

`volume` is steps 5 and 6: the same traffic through a gateway without and with tail sampling and redaction (`slo-volume --requests 6000`).

```makefile
live: .env $(STAMP) ## a real outage: warm up, fail every request, wait for the fast-burn page at the fake pager
	$(VENV)/bin/slo-live

run: replay volume live ## the whole lesson: replay, volume and redaction, and the live page
```

`live` runs a real outage and waits for the fast-burn page at the fake pager. `run` has no recipe, only prerequisites: it runs `replay`, `volume` and `live` in that order.

### Checks

```makefile
lint: $(STAMP) lint-config ## ruff, format check, mypy, import-linter, generated-rules freshness, and the configs
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/mypy
	$(VENV)/bin/lint-imports
	$(VENV)/bin/slo-rules --check

format: $(STAMP) ## apply ruff's formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .
```

`lint` depends on `lint-config` and the install, then runs `ruff check .`, `ruff format --check .`, `mypy`, `lint-imports` and `slo-rules --check`, which fails when a generated rule file is stale. `format` applies the fixes.

```makefile
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
```

`lint-config` runs one-off containers: `promtool check rules` on the live rules and both replay rule files (the three-line command continues with backslashes); a `sh -c` script that rewrites the rules path in a copy of the live configuration and runs `promtool check config` (explained in `bash.md`); `amtool check-config` on the Alertmanager file; and `validate` on both gateway files.

```makefile
# Unit tests need no containers. Coverage is reported; the domain and application must stay at 95% or more.
test: $(STAMP) ## unit and config-lint tests with coverage (no stack needed)
	$(PY) -m pytest tests/unit tests/observability --cov=slo_lab --cov-report=term
	$(PY) -m coverage report --include='src/slo_lab/domain/*,src/slo_lab/application/*' --fail-under=95
```

`test` runs pytest over the unit and observability tests with coverage for `slo_lab`, then fails if the domain and application layers are under 95 percent.

```makefile
test-rules: ## replay scenarios through promtool: a fast burn pages, a blip does not, a slow leak opens a ticket
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  test rules /etc/prometheus/tests/burn_rate.rules.test.yml

test-integration: $(STAMP) ## the service, the gateway, the pager and Alertmanager wired together (make up first)
	$(PY) -m pytest tests/integration -m integration

test-e2e: $(STAMP) ## the replay, the volume and redaction comparison and the live page, asserted (make up first)
	$(PY) -m pytest tests/e2e -m e2e

audit: $(STAMP) ## check the locked dependencies for known vulnerabilities
	$(VENV)/bin/pip-audit --no-deps --disable-pip -r requirements-dev.lock
```

`test-rules` replays the five promtool scenarios; `test-integration` and `test-e2e` run the tests with the matching marker; `audit` checks the locked dependencies for known vulnerabilities.

```makefile
check: lint test test-rules ## everything that needs no running stack

docker-build: ## build the service image
	docker build -t observability-slo-alerts-and-cost:dev .

down: ## stop the stack (including the replay Prometheus) and delete its data
	docker compose --profile replay down -v

clean: down
	rm -rf $(VENV) var logs .pytest_cache .mypy_cache .ruff_cache .coverage .import_linter_cache
```

`check` has no recipe, only prerequisites: `lint`, `test` and `test-rules`. `docker-build` builds the image. `down` stops the stack **including the replay profile** and deletes its data. `clean` depends on `down` and removes the virtual environment, `var` (the replay's generated files) and every cache.

## Try it yourself

```bash
make help
```

Expected: one line per target with its description.

```bash
make -n run
```

Expected: the commands `replay`, `volume` and `live` would run, in that order: `.venv/bin/slo-replay`, `.venv/bin/slo-volume --requests 6000`, `.venv/bin/slo-live`.

```bash
make rules && git status --short
```

Expected: two `wrote ...` lines and no change.

```bash
make install && ls .venv/.installed
```

Expected: the stamp exists; a second `make install` prints nothing.

```bash
make check
```

Expected: ruff, mypy, import-linter and the freshness check pass; pytest passes with coverage; `promtool` prints `SUCCESS`.

## Mistakes people make

- **Spaces instead of a tab** at the start of a recipe line: Make stops with "missing separator". The `.editorconfig` in this repository sets `indent_style = tab` for the Makefile.
- **Forgetting `.PHONY`** for a target that is not a file.
- **Assuming `cd` carries over** to the next line.
- **Using `$VAR` for a Make variable.** Make needs `$(VAR)`. A single `$` is for Make; to give the shell a `$`, write `$$`.
- **Silent failure in a pipeline.** In `a | b`, the exit code is that of `b`. Recipes here avoid hiding failures in pipes.

## Check yourself

1. What does `.PHONY` do? *It marks targets that are names, not files, so they always run.*
2. Why must recipe lines start with a tab? *Make uses the tab to tell a recipe command from other lines.*
3. What is the difference between `:=` and `?=`? *`:=` always sets; `?=` sets only if the variable has no value yet, so the environment can override it.*
4. What does a leading `@` do? *It hides the command line itself; only its output is shown.*
5. If a prerequisite file is newer than the target file, what does Make do? *It runs the recipe again.*

## Further reading

- GNU Make manual: https://www.gnu.org/software/make/manual/make.html (this repository works with GNU Make 3.81, the version macOS ships)
