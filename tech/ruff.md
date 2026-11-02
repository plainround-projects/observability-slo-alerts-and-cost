# Ruff

## What it is

Ruff is a very fast Python **linter** and **formatter**, written in Rust. A linter reads your source code without running it and reports likely mistakes and style problems (an unused import, a variable named in the wrong style, a missing docstring). A formatter rewrites the file so that spacing, quotes and line breaks follow one fixed style, so people never argue about layout. Ruff replaces several older tools (flake8, isort, pyupgrade, black).

## Why this repository uses it

A learner reads this code, so every file should look the same and carry no avoidable mistakes. Ruff enforces that in a fraction of a second and replaces four older tools with one. The docstring rules (`D100`, `D101`, `D103`, `D104`) force every module, class, public function and package to say what it is for; in this lesson that matters most in `place_checkout.py`, whose docstring says in plain words that the use case is deliberately naive about personal data.

## Where it appears

| Path | What it does with Ruff |
| --- | --- |
| `pyproject.toml` (`[tool.ruff]` and below) | The settings. |
| `Makefile` (`lint`, `format`) | Runs `ruff check .`, `ruff format --check .`, and the fixers. |
| `.github/workflows/ci.yml` | `make check` runs them in CI. |

## How it works underneath

`ruff check .` parses each Python file into a tree, then runs rules over the tree. Each rule has a code: a few letters naming the rule family and a number. `E` is pycodestyle errors, `F` is pyflakes (undefined names, unused imports), `I` is import sorting, `B` is bugbear (likely bugs), `UP` is pyupgrade (use newer syntax), `SIM` is simplifications, `N` is naming, `C4` is comprehension style, `RUF` is Ruff's own rules, `PT` is pytest style, and `D100`, `D101`, `D103`, `D104` require docstrings on modules, classes, public functions and packages.

`ruff format .` rewrites files; `ruff format --check .` only reports what it would change and exits non-zero if anything differs. `ruff check --fix .` applies the fixes Ruff considers safe.

The `line-length` setting decides where the formatter breaks long lines. `target-version` tells Ruff which Python version to assume, so it only suggests syntax that exists in that version. `src` tells the import sorter which folders hold first-party code.

## Walkthrough of this repository's use

```toml
[tool.ruff]
line-length = 120
target-version = "py312"
src = ["src", "tests"]
extend-exclude = ["tech"]
```

`line-length = 120` lets lines be up to 120 characters before the formatter wraps them. `target-version = "py312"` means "assume Python 3.12 syntax is allowed". `src = ["src", "tests"]` tells the import sorter that `slo_lab` (under `src`) is first-party code, so it sorts its imports in a separate group after the third-party ones. `extend-exclude = ["tech"]` leaves this `tech/` folder out of every Ruff command. Ruff 0.17 also formats the Python code blocks inside Markdown files, and these notes quote excerpts of the code (single functions, a few lines, sometimes indented) that are not complete programs, so reformatting them would change the quotes.

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "N", "C4", "RUF", "PT", "D100", "D101", "D103", "D104"]
```

`select` lists the rule families that are on: `E` (pycodestyle errors), `F` (pyflakes), `I` (isort: import order), `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify), `N` (pep8-naming), `C4` (comprehensions), `RUF` (Ruff-specific) and `PT` (pytest style). `D100`, `D101`, `D103`, `D104` are four specific docstring rules from the `pydocstyle` family: a docstring is required in every public module (D100), public class (D101), public function (D103) and package (D104). Other docstring rules (`D102` for public methods, `D105` for magic methods, `D107` for `__init__`) are left off to keep the code readable.

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["D"]
```

`per-file-ignores` turns the docstring rules off for everything under `tests/`: a test's name already says what it checks. The glob `tests/**` matches all files below the folder.

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

`make lint` runs `ruff check .` (look at every file under the current folder), `ruff format --check .` (fail if any file would be reformatted), and `make format` runs the fixing versions. The formatter also decides things like where a long call is wrapped, so a reader never sees two styles.

## Try it yourself

```bash
make lint
```

Expected: `All checks passed!` from `ruff check`, `... files already formatted` from `ruff format --check`, then the mypy and import-linter results.

```bash
printf 'import os\nx=1\n' > /tmp/demo_ruff.py && .venv/bin/ruff check /tmp/demo_ruff.py; rm /tmp/demo_ruff.py
```

Expected: a complaint `F401 'os' imported but unused` and the unformatted-code rules (the `/tmp` file is outside `src`, so only general rules apply).

```bash
.venv/bin/ruff check src --select D --statistics
```

Expected: a count per docstring rule not enabled in the project (for example D102 for methods), to show what the project chooses not to require.

```bash
.venv/bin/ruff format --diff src | head
```

Expected: no output when everything is formatted.

```bash
.venv/bin/ruff rule UP006
```

Expected: the explanation of the rule that asks you to write `list[int]` instead of `typing.List[int]`.

## Mistakes people make

- **Running only the linter** and forgetting `format --check`. This repository's `make lint` runs both.
- **Silencing a rule with `# noqa` everywhere.** Prefer fixing the code. Where a rule does not make sense (docstrings in tests), configure it once in `per-file-ignores`.
- **Different Ruff versions on different machines** giving different results. The version is pinned in the lock file.

## Check yourself

1. What is the difference between a linter and a formatter? *A linter reports problems; a formatter rewrites layout.*
2. What does `ruff format --check` do? *It reports files that would change and exits with a non-zero code, without changing them.*
3. What does the rule family `I` do? *It checks that imports are sorted.*
4. Why is `target-version` set? *So Ruff only suggests syntax valid for that Python version.*
5. Why are docstring rules turned off for `tests/**`? *Test names already say what they check.*

## Further reading

- Ruff documentation (version 0.17): https://docs.astral.sh/ruff/
- Rules: https://docs.astral.sh/ruff/rules/
- Settings: https://docs.astral.sh/ruff/settings/
