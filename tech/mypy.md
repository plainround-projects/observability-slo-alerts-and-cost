# mypy

## What it is

mypy is a **static type checker** for Python. It reads your code and your type hints, without running the program, and reports places where the types do not fit: calling a function with a string where an int is required, returning `None` where a `str` is promised, calling a method that does not exist. It catches this class of mistake before the program ever starts.

## Why this repository uses it

Type hints are documentation that a tool checks. In a layered design they also protect the boundaries: a `Protocol` in the application layer is only meaningful if someone verifies that the adapters really match it. mypy does that for every adapter, and for the replay and volume scenarios, whose collaborators (Prometheus, Tempo, Loki, Docker) are all `Protocol`s.

## Where it appears

| Path | What it does with mypy |
| --- | --- |
| `pyproject.toml` (`[tool.mypy]` and overrides) | The settings. |
| `Makefile` (`lint`) | Runs `mypy` with no arguments, which uses `files` from the configuration. |
| Every `src/**/*.py` | Fully annotated. |

## How it works underneath

mypy parses every file it is told to check (`files = ["src", "tests"]` in the configuration), builds a model of every function and class from the type hints, then infers the type of each expression and compares it with what is expected. For third-party libraries it needs **type information**. Many libraries ship it inside the package (a marker file named `py.typed`). Others have a separate "stubs" package (`types-requests`). A library with neither is reported as "missing imports" unless you list it under `ignore_missing_imports`.

`strict = true` turns on a bundle of strict options: every function must be annotated, no implicit `Any`, no untyped calls from typed code, unused ignores are errors, and so on. `# type: ignore[code]` on one line tells mypy to skip that line's named error; a test uses it when it passes a wrong type on purpose.

Python never checks hints at run time. A wrong hint changes nothing about how the program runs.

## Walkthrough of this repository's use

```toml
[tool.mypy]
python_version = "3.12"
strict = true
files = ["src", "tests"]
```

`python_version = "3.12"` makes mypy check against Python 3.12's library definitions. `strict = true` turns on all strict options. `files = ["src", "tests"]` is what gets checked when you run `mypy` with no arguments.

```toml
[[tool.mypy.overrides]]
module = ["opentelemetry.*", "waitress", "waitress.*"]
ignore_missing_imports = true
```

`[[tool.mypy.overrides]]` (double brackets: one item of a list) applies settings to some modules only. `module = ["opentelemetry.*", "waitress", "waitress.*"]` names modules mypy has no complete type information for, and `ignore_missing_imports = true` stops it from reporting them. `opentelemetry.*` is a wildcard for every sub-module; the `opentelemetry` namespace is split across several installed packages, so mypy cannot always resolve it.

```toml
# Test functions are named for the behavior they check; annotating every one adds noise, not safety.
[[tool.mypy.overrides]]
module = ["tests.*"]
disallow_untyped_defs = false
disallow_incomplete_defs = false
disallow_untyped_calls = false
```

The comment explains the second override: test functions are named for the behavior they check; annotating every one adds noise. For `tests.*`, `disallow_untyped_defs`, `disallow_incomplete_defs` and `disallow_untyped_calls` are set to `false`, so unannotated test functions are allowed, while the rest of strict mode still applies.

Examples in the code of what strict mode demands: every function in `src` has parameter and return types (`-> None` included); `dict[str, Any]` instead of a bare `dict`; `Mapping[str, str]` for read-only mappings; `Callable[[], float]` for the clock; and `bool(response.status == 200)` in `lab/health_cli.py` and `lab/volume_cli.py` because the library's `status` is typed loosely and strict mode refuses to return `Any` from a function declared to return `bool`.

## Try it yourself

```bash
.venv/bin/mypy
```

Expected: `Success: no issues found in N source files`.

```bash
printf 'def f(x: int) -> str:\n    return x\n' > /tmp/demo_mypy.py && .venv/bin/mypy /tmp/demo_mypy.py; rm /tmp/demo_mypy.py
```

Expected: `error: Incompatible return value type (got "int", expected "str")`.

```bash
.venv/bin/mypy --strict --no-incremental src/three_signals/domain/orders.py
```

Expected: `Success: no issues found in 1 source file`.

```bash
.venv/bin/mypy --config-file= --show-error-codes -c "x: int = 'a'"
```

Expected: an error line ending in `[assignment]`, the code you would put in `# type: ignore[assignment]`. (`--config-file=` with nothing after it ignores `pyproject.toml`, whose `files` setting would otherwise conflict with `-c`.)

```bash
grep -rn "type: ignore" src tests | head
```

Expected: nothing; this repository needs no `# type: ignore` at all.

## Mistakes people make

- **Using `Any` to make errors go away.** It switches checking off for that value and everything derived from it.
- **Blanket `# type: ignore`.** Always name the error code so only that error is hidden.
- **Thinking passing mypy proves correctness.** It proves the types are consistent, not that the logic is right.
- **Not pinning the stub packages**, so a new stub release suddenly fails the build.

## Check yourself

1. Does mypy run your program? *No. It reads the code and the hints only.*
2. What does `strict = true` do? *It enables the strict checks, such as requiring annotations on every function.*
3. Why is `types-requests` installed? *It supplies type information for `requests`, which does not ship its own in this version of the setup.*
4. What does `ignore_missing_imports` do for a module? *It stops mypy from reporting an error when it has no type information for it.*
5. What happens at run time to a type hint? *Nothing; Python ignores it.*

## Further reading

- mypy documentation (2.4): https://mypy.readthedocs.io/en/stable/
- Configuration file: https://mypy.readthedocs.io/en/stable/config_file.html
- The strict flag: https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict
