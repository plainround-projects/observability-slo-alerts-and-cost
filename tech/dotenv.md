# The .env and .env.example files

## What it is

A **dotenv file** is a plain text file of `NAME=value` lines, one setting per line, kept next to the program. Tools read it and turn each line into an environment variable (see `python.md` for what an environment variable is). It is a convention, not a standard: Docker Compose reads a file called `.env` in the project folder to fill `${NAME}` placeholders in `docker-compose.yml`, and GNU Make can read the same file with `include` because `NAME=value` is also valid Make syntax. Lines starting with `#` are comments.

Two files play different roles. `.env.example` is **tracked in git**: it documents every setting the project reads and holds harmless local placeholder values. `.env` is **ignored by git**: it is your private copy, created from the example, where real values and passwords live.

## Why this repository uses it

The stack needs one secret (the Grafana admin password) and a handful of optional settings: the published ports and the choice of the gateway's configuration, which the volume scenario overrides from Python when it switches between the raw and the protected gateway. Putting them in one ignored file keeps the secret out of the YAML and the history, and gives one place to change a port when yours is taken. The value in `.env.example` is a placeholder for a laptop lab; nothing here is a real credential.

## Where it appears

| Path | What it does with the file |
| --- | --- |
| `.env.example` | The documented template. Tracked. |
| `.env` | Your copy. Created by `make up` (the `.env:` target copies the example). Ignored by git and by `.dockerignore`. |
| `docker-compose.yml` | Reads `.env` for every `${NAME}` placeholder. |
| `Makefile` | `-include .env` and `export` pass the values to commands and to the Python lab tools. |
| `src/slo_lab/lab/compose.py` | Starts `docker compose` with `GATEWAY_CONFIG` overridden (see `python-subprocess.md`). |
| `tests/observability/test_configuration_as_code.py` | Checks that every variable the Compose file reads is declared in `.env.example`. |
| `.gitignore`, `.dockerignore` | Keep `.env` out of git and out of the image. |

## How it works underneath

When you run a `docker compose` command, Compose reads `.env` first, builds a table of names and values, and substitutes `${NAME}` in the YAML before it does anything else. `${NAME:-default}` uses `default` when `NAME` is unset or empty. `${NAME:?message}` stops with the message when `NAME` is unset or empty.

When you run `make`, `-include .env` (the leading hyphen ignores a missing file) reads the same lines as Make variables, and `export` hands them to every command Make runs as environment variables. That is how the Python tools (`FRONT_HOST_PORT`, `GRAFANA_ADMIN_PASSWORD`) find the stack without a second configuration file.

A real environment variable set in your shell takes precedence over `.env` for Compose, which lets you override one value for one command: `GRAFANA_HOST_PORT=33000 make up`.

## Walkthrough of this repository's use

The variables the repository reads, collected from the files that use them:

| Variable | Read by | Meaning |
| --- | --- | --- |
| `GRAFANA_ADMIN_PASSWORD` | Compose (`grafana`), `lab/health_cli.py` | The Grafana `admin` password. Required. |
| `SERVICE_HOST_PORT`, `GATEWAY_HEALTH_HOST_PORT`, `TEMPO_HOST_PORT`, `LOKI_HOST_PORT`, `PROMETHEUS_HOST_PORT`, `REPLAY_HOST_PORT`, `PAGER_HOST_PORT`, `GRAFANA_HOST_PORT` | Compose, the lab tools | The ports published on `127.0.0.1`. Optional; defaults 28083, 23133, 23200, 23100, 29090, 29091, 28084, 23000. |
| `GATEWAY_CONFIG` | Compose (`otel-gateway`) | Which gateway configuration to mount: the protected one (default) or `gateway.raw.yaml`. The volume scenario sets it. |

The placeholders for these are quoted and explained in `docker-compose.md` (for example the `${GRAFANA_ADMIN_PASSWORD:?...}` line in the Grafana service, the `${SERVICE_HOST_PORT:-28083}` port mapping and the file choice `${GATEWAY_CONFIG:-./observability/collector/gateway.yaml}`). The configuration test enforces that none is used without being declared:

```python
def test_every_variable_the_compose_file_reads_is_in_env_example():
    used = set(re.findall(r"\$\{([A-Z_]+)", (ROOT / "docker-compose.yml").read_text()))
    declared = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line}
    assert used <= declared, sorted(used - declared)
```

`re.findall(r"\$\{([A-Z_]+)", text)` collects every name that follows `${` in the Compose file; the set of names declared in `.env.example` (the part of each `NAME=value` line before the `=`) must contain them all.

The Makefile creates the private copy:

```makefile
.env:
	cp .env.example .env
	@echo "created .env from .env.example"
```

Note on this note: the shell tool used while writing these notes was denied read access to `.env.example` itself, so its lines are not quoted here; the table above is derived from the files that read the variables, and the test above guarantees that the file declares each of them.

## Try it yourself

```bash
make .env && ls -l .env
```

Expected: `created .env from .env.example` on the first run (nothing if it exists), and the file is listed.

```bash
git check-ignore -v .env
```

Expected: `.gitignore:12:.env	.env`.

```bash
GRAFANA_HOST_PORT=33000 docker compose config | grep -B1 -A2 33000
```

Expected: the Grafana port mapping shows `published: "33000"`: a variable in your shell beats the file.

```bash
.venv/bin/python -m pytest tests/observability -k env_example -q
```

Expected: `1 passed`.

```bash
docker compose config --quiet 2>&1 | head -2
```

Expected: nothing when `.env` exists; with `.env` renamed, an error `run make up, which creates .env from .env.example` from the `:?` placeholder.

## Mistakes people make

- **Committing `.env`.** It holds passwords. It is in `.gitignore` and `.dockerignore`.
- **Adding a variable to the Compose file but not to `.env.example`.** Other people's clones then fail. The test catches it.
- **Spaces around `=`.** `NAME = value` is not valid in a dotenv file read by Compose.
- **Quoting differences.** Compose strips quotes, Make does not. This repository keeps its values free of spaces and quotes.
- **Expecting a change in `.env` to reach a running container.** Variables are read at `up`. Run `docker compose up -d` again to apply.

## Check yourself

1. Which of the two files is tracked in git, and why? *`.env.example`: it documents the settings with placeholder values. `.env` holds private values.*
2. What does `${NAME:-default}` do? *Uses `default` when `NAME` is unset or empty.*
3. How does the volume scenario switch the gateway's configuration without editing `.env`? *It runs `docker compose` with `GATEWAY_CONFIG` set in the process environment, which beats the file.*
4. What happens if `GRAFANA_ADMIN_PASSWORD` is empty? *Compose stops with the message from the `:?` placeholder.*
5. How can you change one value for one command? *Set it in your shell in front of the command; the shell wins over `.env`.*

## Further reading

- Compose, environment variables and `.env`: https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/
- Compose, variable interpolation: https://docs.docker.com/reference/compose-file/interpolation/
- The twelve-factor app, config: https://12factor.net/config
