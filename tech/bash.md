# Bash and shell commands

## What it is

A **shell** is a program that reads commands you type (or that a file contains) and runs them. Bash and `sh` are shells. On Linux and macOS, almost every command-line task goes through a shell: it finds the program, starts it as a process, connects its input and output, and reports its **exit code**.

This repository has no standalone `.sh` file. Shell appears in four places: in the recipes of the `Makefile`, in the `test:` lines of Docker health checks, in the `run:` lines of the CI workflow, and in the commands you type in your terminal. All of them are plain shell, so this note teaches the shell features they use.

## Why this repository uses it

Shell commands glue the pieces together: the Makefile recipes call `docker`, `grep`, `sed` and a small inline `sh` script; Docker health checks run `wget` and `grep` inside containers; the CI workflow runs `make`. There are no separate shell script files, so every command is explained here where it appears.

## Where it appears

| Path | The shell it contains |
| --- | --- |
| `Makefile` | Recipe lines, run by `/bin/sh`; the inline script in `lint-config`. |
| `docker-compose.yml` | The `test:` health-check commands of tempo, loki, prometheus, prometheus-replay, alertmanager and grafana (`CMD-SHELL`). |
| `.github/workflows/ci.yml` | `run:` steps. |
| `tests/integration/test_redaction_in_the_collector.py` | Calls `docker run`, `docker logs` and `docker rm` from Python. |

## How it works underneath

- A **command** is a program name followed by **arguments**: `ls -l /tmp`. The shell splits the line into words at spaces, then starts the program with those words.
- A **flag** (or option) is an argument that starts with `-` or `--` and changes what the program does.
- **Exit code.** Every program ends with a number. `0` means success; anything else means failure. `$?` holds the last exit code. `&&` runs the next command only if the previous succeeded; `||` only if it failed.
- **Streams.** Each process has standard input (stdin), standard output (stdout) and standard error (stderr). `>` sends stdout to a file; `<` reads stdin from a file; `|` (a **pipe**) connects the stdout of the left command to the stdin of the right one; `2>&1` sends stderr to the same place as stdout.
- **Quoting.** Inside double quotes `"..."` variables are still replaced (`"$HOME"`); inside single quotes `'...'` nothing is replaced. Quoting protects spaces and special characters from being split or interpreted.
- **Environment variables.** Named strings that every process inherits from its parent. `NAME=value command` sets one only for that command.
- **Command substitution.** `$(command)` is replaced by the output of the command.
- **Globs.** `*.rules.yml` is expanded by the shell into every matching file name.
- **`set -euo pipefail`** (not used in this repository because there are no script files, but you will see it everywhere): `-e` stops at the first failing command, `-u` makes using an unset variable an error, `-o pipefail` makes a pipeline fail if any part of it fails.

## Walkthrough of this repository's use

### Health checks in `docker-compose.yml`

`CMD-SHELL` means: run the following string with the container's `/bin/sh`. Exit code 0 means healthy.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3200/ready | grep -q ready"]
```

Tempo: `wget -q -O - http://localhost:3200/ready | grep -q ready`. `wget` downloads a URL: `-q` suppresses progress text, `-O -` writes the body to standard output. The `|` pipe gives that output to `grep -q ready`, which searches for the word and, with `-q`, prints nothing and exits 0 if found, 1 if not. If `wget` fails, `grep` gets no input and exits 1.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3100/ready | grep -q ready"]
```

Loki: the same on port 3100.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
```

Prometheus: `wget -q -O - http://localhost:9090/-/healthy` with no `grep`: `wget` exits non-zero on any HTTP error status, so its exit code is the answer. The replay Prometheus (line 115) and Alertmanager (line 129, port 9093) use the same form.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3000/api/health | grep -q ok"]
```

Grafana: `wget -q -O - http://localhost:3000/api/health | grep -q ok` looks for `ok` in the JSON.

### The inline script in `lint-config`

```makefile
	docker run --rm --entrypoint sh -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) -c \
	  'mkdir -p /tmp/rules && cp /etc/prometheus/rules/*.yml /tmp/rules/ && sed "s#/etc/prometheus/rules#/tmp/rules#" /etc/prometheus/prometheus.yml > /tmp/prometheus.yml && promtool check config /tmp/prometheus.yml'
```

`docker run --rm --entrypoint sh -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" IMAGE -c '...'` starts the Prometheus image with `sh` as the program and passes the quoted script to `sh -c`. Inside the single quotes, connected with `&&` (each step runs only if the previous succeeded):

1. `mkdir -p /tmp/rules` creates a folder (`-p`: no error if it exists).
2. `cp /etc/prometheus/rules/*.yml /tmp/rules/` copies the rule files; the shell expands the glob.
3. `sed "s#/etc/prometheus/rules#/tmp/rules#" /etc/prometheus/prometheus.yml > /tmp/prometheus.yml` rewrites the rules path in a copy of the configuration (`s#old#new#` substitutes; `#` is the separator so the slashes need no escaping; `>` redirects the output into a file).
4. `promtool check config /tmp/prometheus.yml` validates the copy.

The rewrite exists because the configuration names a rules path that only exists inside the real Prometheus container.

### Other Makefile recipes

```makefile
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort
```

`grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort`: a **pipeline** of three commands. `grep` prints the matching lines (`-h` drops the file-name prefix), `sed` replaces everything from the colon to `##` with a tab, and `sort` orders the lines.

```makefile
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
```

`$(shell command -v python3.13 || command -v python3.12 || command -v python3)`: `command -v` prints where a command is; `||` runs the next only if the previous failed.

```makefile
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check rules /etc/prometheus/rules/burn_rate.rules.yml /etc/prometheus/replay/threshold.rules.yml \
	  /etc/prometheus/replay/burn_rate_lesson.rules.yml
```

`docker run --rm --entrypoint promtool -v ...:ro IMAGE check rules FILE FILE FILE`: `--rm` removes the container afterwards, `--entrypoint promtool` replaces the image's default program, `-v host:container:ro` mounts a folder read-only, and everything after the image name is `promtool`'s arguments. The backslashes continue the command over several lines.

```makefile
	rm -rf $(VENV) var logs .pytest_cache .mypy_cache .ruff_cache .coverage .import_linter_cache
```

`rm -rf $(VENV) var logs ...` removes files and folders: `-r` recursive, `-f` no questions and no error if absent.

### `docker run` in the redaction test

```python
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:4318",
            "-v", f"{path}:/etc/otelcol/config.yaml:ro", IMAGE, "--config=/etc/otelcol/config.yaml",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
```

`docker run -d --name NAME -p 127.0.0.1:PORT:4318 -v FILE:/etc/otelcol/config.yaml:ro IMAGE --config=...` starts a container in the background (`-d`) with a name, publishes a port on loopback, mounts the generated configuration read-only and passes `--config` to the Collector. `docker logs NAME` prints what it wrote; `docker rm -f NAME` stops and removes it.

### CI

```yaml
      - run: make up
      - run: make test-integration test-e2e
      - if: failure()
        run: docker compose logs --no-color --tail=200
      - if: always()
        run: make down
```

`docker compose logs --no-color --tail=200` prints the last 200 log lines of each container without color codes, for when a run fails. See `github-actions.md`.

## Try it yourself

```bash
echo "hello" | grep -q hello; echo "exit $?"
```

Expected: `exit 0`.

```bash
echo "hello" | grep -q world; echo "exit $?"
```

Expected: `exit 1`.

```bash
echo "path /etc/prometheus/rules" | sed "s#/etc/prometheus/rules#/tmp/rules#"
```

Expected: `path /tmp/rules`.

```bash
make -n lint-config | head -3
```

Expected: the `docker run` commands that would run.

```bash
docker compose exec tempo wget -q -O - http://localhost:3200/ready; echo " exit $?"
```

Expected (needs `make up`): `ready exit 0`.

## Mistakes people make

- **Unquoted variables.** `rm -rf $DIR/` with an empty `DIR` deletes `/`. Quote: `"$DIR"`.
- **Ignoring exit codes.** A step that fails but is followed by another command hides the error. Chain with `&&` or stop on error.
- **Pipes that hide failures.** `curl ... | grep ...` returns grep's code, not curl's.
- **Assuming Bash features in `sh`.** The `CMD-SHELL` health checks run in `/bin/sh`, which on Alpine is BusyBox ash, not Bash. Use only portable features there.
- **`cd` in a script without checking it worked.**

## Check yourself

1. What does an exit code of 0 mean? *Success.*
2. What does `a | b` do? *It connects a's standard output to b's standard input.*
3. What is the difference between `'$X'` and `"$X"`? *Single quotes keep `$X` as text; double quotes replace it with the value.*
4. What does `a && b` do? *Runs b only if a exited with 0.*
5. What does `2>&1` do? *Sends standard error to wherever standard output goes.*

## Further reading

- GNU Bash reference manual: https://www.gnu.org/software/bash/manual/bash.html
- POSIX shell command language: https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html
