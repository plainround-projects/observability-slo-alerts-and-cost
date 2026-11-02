# GitHub Actions

## What it is

GitHub Actions is GitHub's built-in automation. A **workflow** is a YAML file in `.github/workflows/`. It says *when* to run (on a push, on a pull request) and *what* to run: one or more **jobs**, each made of **steps**. GitHub starts a fresh virtual machine (a **runner**) for each job, runs the steps in order, and shows pass or fail on the commit. This is **continuous integration** (CI): every change is checked automatically by the same commands a developer runs by hand.

## Why this repository uses it

The repository's promise is that the commands in the README work. CI runs them on a clean machine on every push, so a broken change is noticed at once. The workflow is a thin wrapper that calls the same `make` targets a learner uses, and its second job brings up the whole stack, replays the simulated week, compares the gateway's policies and waits for a real page (`make test-e2e`).

## Where it appears

| Path | What it does |
| --- | --- |
| `.github/workflows/ci.yml` | The only workflow. |

## How it works underneath

When the event named under `on:` happens, GitHub reads the workflow file from the commit and queues each job. A job runs on the runner type named in `runs-on` (`ubuntu-latest` is a Linux VM). A step is either `uses:` (run a reusable **action**, such as `actions/checkout@v4`, which downloads the repository into the VM) or `run:` (run shell commands). If a step exits non-zero, later steps are skipped and the job fails, unless a step has `if: always()` or `if: failure()`. `needs:` makes one job wait for another and only run if it passed. Jobs without `needs` run in parallel.

Nothing is deployed from this workflow. It only runs checks.

## Walkthrough of this repository's use

```yaml
name: ci

on: [push, pull_request]
```

`name: ci` is the label shown on GitHub. `on: [push, pull_request]` runs the workflow on every push and on every pull request.

```yaml
jobs:
  check:
    name: Format, lint, types, import rules, unit tests with coverage, rule tests, dependency audit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: make check
      - run: make audit

```

The first job is called `check` (its `name` is a long human description of what it covers). `runs-on: ubuntu-latest` picks a Linux virtual machine. `steps`: `actions/checkout@v4` downloads the repository; `actions/setup-python@v5` with `python-version: "3.12"` installs Python 3.12 (the version the image uses); `run: make check` runs lint, types, import rules, unit tests with coverage and the rule tests; `run: make audit` checks the dependencies for known vulnerabilities. The Python version is quoted: unquoted, YAML would read `3.10` as the number `3.1`.

```yaml
  stack:
    name: Bring up the stack, replay the week, compare gateway policies and wait for a live page
    runs-on: ubuntu-latest
    needs: check
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: make up
      - run: make test-integration test-e2e
      - if: failure()
        run: docker compose logs --no-color --tail=200
      - if: always()
        run: make down
```

The second job, `stack`, has `needs: check`: it starts only when the first job passed. It repeats checkout and Python setup, then `make up` (build and start the stack and wait until healthy), then `make test-integration test-e2e` (two targets in one command; the second one runs the replay of the week, the volume and redaction comparison and the live outage with its page), then two conditional steps: `if: failure()` prints the last 200 lines of every container's log if an earlier step failed; `if: always()` runs `make down` whether or not the earlier steps passed, so the runner is left clean. A step with `if:` followed by `run:` on the next line is one step: the `-` begins the step and the keys line up under it.

## Try it yourself

```bash
cat .github/workflows/ci.yml | head -16
```

Expected: the first job as shown above.

```bash
.venv/bin/python -c "import yaml; w = yaml.safe_load(open('.github/workflows/ci.yml')); print(list(w['jobs']), w['jobs']['stack']['needs'])"
```

Expected: `['check', 'stack'] check`. (Note that PyYAML reads the key `on` as the boolean `True`, a known YAML 1.1 quirk; GitHub does not.)

```bash
make check
```

Expected: the same result the first CI job would give.

```bash
make audit
```

Expected: `No known vulnerabilities found`.

On GitHub, open the repository's Actions tab: the two jobs appear as a small graph with the dependency between them.

## Mistakes people make

- **Pinning actions to a moving tag only** (`@v4`). A tag can be moved; for strict supply-chain safety pin to a commit hash.
- **CI and local commands that differ.** Here CI calls the same `make` targets a developer uses.
- **No cleanup of a stack that failed.** `if: always()` runs the `make down` step either way.
- **Missing logs when something fails.** `if: failure()` prints the container logs only on failure.

## Check yourself

1. What starts a workflow? *The events listed under `on:`.*
2. What is a runner? *The virtual machine that runs a job's steps.*
3. What does `needs: check` do? *The job waits for the `check` job and runs only if it passed.*
4. What do `if: failure()` and `if: always()` mean? *Run only after a failure; run no matter what.*
5. What does `uses: actions/checkout@v4` do? *Downloads the repository's code into the runner.*

## Further reading

- Workflow syntax: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions
- Understanding GitHub Actions: https://docs.github.com/en/actions/learn-github-actions/understanding-github-actions
