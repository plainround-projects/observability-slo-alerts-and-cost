# Docker (Dockerfile and .dockerignore)

## What it is

Docker is a tool that runs programs inside **containers**. A container is a normal process on your machine, but the operating system gives it its own private view: its own file system, its own network address, its own list of processes. The program inside cannot see the rest of your machine unless you let it. Because the container carries its own copy of every file the program needs (the Python interpreter, the libraries), the program runs the same way on every machine.

An **image** is the read-only package a container starts from: a stack of file-system layers plus a few settings (which command to run, which user, which environment variables). A **Dockerfile** is the text file with the recipe for building an image. A **registry** (for example Docker Hub) is a server that stores images; `docker pull` downloads one.

On macOS and Windows, Docker Desktop runs a small Linux virtual machine, and the containers run inside it. That is why container files are not on your normal disk, and why Docker has its own disk limit.

## Why this repository uses it

The lab runs eight containers (the checkout service, the gateway Collector, Tempo, Loki, Prometheus, Alertmanager, the fake pager and Grafana) and a ninth, the replay Prometheus, on demand. Containers make the stack one command and pin every version the lesson names. The checkout service and the fake pager share **one image**; Compose picks the command. The Dockerfile shows good habits: a base image pinned by digest, a dependency layer that rarely changes, and a non-root user.

## Where it appears

| Path | What it does with Docker |
| --- | --- |
| `Dockerfile` | The recipe for the app image. |
| `.dockerignore` | Files the build must not copy into the image. |
| `docker-compose.yml` | Builds the image and runs every container (see `docker-compose.md`). |
| `Makefile` | `docker-build` builds the image; `lint-config` and `test-rules` run the Prometheus, Alertmanager and Collector images as one-off tools. |
| `tests/observability/test_configuration_as_code.py` | Checks the digest pin and the non-root user. |

## How it works underneath

Docker uses two features of the Linux kernel. **Namespaces** give a process its own view of process ids, network interfaces, mount points and host name. **Cgroups** limit how much CPU and memory the process may use. There is no second operating system inside a container; it is one process of the host's kernel, wearing blinders.

An image is made of **layers**. Every instruction in a Dockerfile that changes files (`RUN`, `COPY`, `ADD`) creates one layer on top of the previous. Docker stores each layer once and caches it. When you build again, Docker reuses every layer whose instruction and inputs did not change, and rebuilds from the first one that did. That is why a Dockerfile copies the dependency list and installs the packages **before** it copies the source code: source code changes often, dependencies rarely, so the slow install layer stays cached.

When you start a container, Docker adds a thin writable layer on top of the image layers. Anything the program writes goes there and disappears when the container is removed. A **volume** is a folder managed by Docker that outlives the container; databases keep their data in volumes. A **bind mount** maps a folder or file from your machine into the container.

A container has a main process (**PID 1**). When that process exits, the container stops. `docker stop` sends it the signal `SIGTERM`, waits a grace period (10 seconds unless told otherwise), then sends `SIGKILL`, which cannot be caught.

A **health check** is a command Docker runs inside the container every few seconds. Exit code 0 means healthy, 1 means unhealthy. Docker records the state; Compose can wait for it.

A **digest** (`@sha256:...`) names an exact image by the hash of its contents. A **tag** (`python:3.12.8-slim-bookworm`) is a movable label; the owner of the image can point it at a different image later. A digest cannot move, so pinning by digest makes a build repeatable.

**BuildKit and cache mounts.** BuildKit is Docker's modern build engine (the default in current Docker). A `RUN --mount=type=cache,target=/root/.cache/pip` instruction gives that one command a folder that BuildKit keeps *between builds* on the build machine. It is never part of the image. For `pip` this means downloaded packages survive from one build to the next, so a rebuild after changing the lock file does not download everything again.

## Walkthrough of this repository's use

### `Dockerfile`

```dockerfile
# One image for the checkout service and the fake pager: the compose file picks the command.
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0
```

The comment says the image serves two programs. `FROM python:3.12.8-slim-bookworm@sha256:...` is the base: official Python 3.12.8 on Debian 12 ("bookworm"), "slim" (no compilers), pinned by digest so the bytes cannot change under you.

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

`ENV` sets variables for the build and the running container: `PYTHONDONTWRITEBYTECODE=1` (no `.pyc` files), `PYTHONUNBUFFERED=1` (output appears at once in `docker logs`) and `PIP_DISABLE_PIP_VERSION_CHECK=1` (pip does not call the internet to look for updates). The backslashes continue one instruction over several lines.

```dockerfile
WORKDIR /app
COPY requirements.lock ./
# The cache mount keeps downloaded wheels between builds; it is not part of the image.
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements.lock
```

`WORKDIR /app` sets the working folder. `COPY requirements.lock ./` copies only the lock file. The comment explains the next line: `RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements.lock` installs every locked package, refusing any file whose hash is not in the lock (see `uv-and-lock-files.md`), using the cache mount for downloads. This layer depends only on the lock file, so Docker reuses it until the lock changes.

```dockerfile
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .
```

The project files come after the dependencies: `pyproject.toml`, `README.md` (the package metadata names it as the readme) and `src`. `pip install --no-deps .` installs the project itself without dependencies and creates the commands `slo-service`, `slo-pager` and `slo-health` (and the lab commands, unused in the container) in the image.

```dockerfile
# /data is where the fake pager keeps its pages; a named volume mounted there inherits this owner.
RUN useradd --system --uid 10001 --no-create-home app && mkdir /data && chown app /data
USER app
```

`useradd --system --uid 10001 --no-create-home app` makes an unprivileged user with a fixed id. `mkdir /data && chown app /data` creates the folder where the fake pager keeps its pages and gives it to that user; the comment explains why: a named volume mounted at `/data` **inherits the owner** of the folder in the image, so the unprivileged pager can write there (an earlier version had a root-owned volume and the pager's webhook answered 500). `USER app` switches to the unprivileged user for the rest of the build and for the running container.

```dockerfile
# opentelemetry-instrument applies the automatic Flask instrumentation and reads the OTEL_* variables.
CMD ["opentelemetry-instrument", "slo-service"]
```

`CMD ["opentelemetry-instrument", "slo-service"]` is the default command, in **exec form** (a JSON list): the program is started directly as process 1 and receives `SIGTERM` itself. `opentelemetry-instrument` turns on the Flask instrumentation and reads the `OTEL_*` variables from the environment. Compose overrides the command for the pager (`slo-pager`).

### `.dockerignore`

```
.git
.venv
logs
tests
docs
observability
sql
**/__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
.coverage
.env
```

The build context (this folder) is sent to the builder; these entries are left out: the git history, the virtual environment, logs, tests, docs, the observability configs and SQL files (not needed in the image), Python caches (`**/__pycache__` matches at any depth), tool caches, the coverage file, and `.env`, which holds local secrets and must never end up in an image. There is no `sql` folder in this repository; the entry is harmless.

## Try it yourself

```bash
make docker-build
```

Expected: the build succeeds and ends with `naming to docker.io/library/observability-slo-alerts-and-cost:dev`.

```bash
docker image inspect observability-slo-alerts-and-cost:dev --format '{{.Config.User}} {{.Config.Cmd}}'
```

Expected: `app [opentelemetry-instrument slo-service]`.

```bash
docker run --rm --entrypoint id observability-slo-alerts-and-cost:dev
```

Expected: `uid=10001(app) gid=...`, not root.

```bash
docker history observability-slo-alerts-and-cost:dev | head -8
```

Expected: one row per layer; the dependency install is the largest.

```bash
touch src/slo_lab/__init__.py && make docker-build 2>&1 | grep -c CACHED
```

Expected: a number above zero: the layers before `COPY src` (including the dependency install) come from the cache.

## Mistakes people make

- **Running as root.** The default user inside a container is root. If the program is compromised, the attacker is root inside the container. This repository creates an unprivileged user and switches to it with `USER`.
- **Tag `latest`.** It changes silently. Every third-party image here is pinned to a version, and the base image is pinned by digest.
- **Copying everything before installing dependencies.** Any change to any file then invalidates the install layer. Copy the dependency list first.
- **Storing data in the container's writable layer.** It is deleted with the container. Use a volume.
- **Putting secrets in the image.** Anything in a layer can be read by anyone with the image. Secrets come in at run time through environment variables.
- **A shell-form `CMD`.** `CMD python app.py` runs through `/bin/sh -c`, so the shell becomes PID 1 and may swallow `SIGTERM`. The exec form (`CMD ["python", "app.py"]`) makes your program PID 1.
- **Forgetting that `localhost` inside a container is the container itself.** To reach another container, use its service name on the shared network.

## Check yourself

1. What is the difference between an image and a container? *An image is the read-only package; a container is a running process started from it.*
2. Why does a Dockerfile copy the dependency file before the source code? *So Docker can reuse the cached install layer when only the source changed.*
3. What happens to files a container writes when the container is removed? *They disappear, unless they were written to a volume or a bind mount.*
4. What does a digest give you that a tag does not? *A tag can be moved to a different image later; a digest always names the same bytes.*
5. Which signal does `docker stop` send first, and what comes after the grace period? *`SIGTERM`; then `SIGKILL`.*

## Further reading

- Docker overview: https://docs.docker.com/get-started/docker-overview/
- Dockerfile reference: https://docs.docker.com/reference/dockerfile/
- Build cache: https://docs.docker.com/build/cache/
- `docker run` reference: https://docs.docker.com/reference/cli/docker/container/run/
