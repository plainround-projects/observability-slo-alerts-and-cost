# Git, .gitignore and .editorconfig

## What it is

Git is a version-control system. It records snapshots of a project's files (**commits**), each with an author, a message and a pointer to the previous commit, so the history is a chain you can read, compare and go back in. A **repository** is the project folder plus its hidden `.git` folder where Git keeps this history. Git works completely on your own machine; nothing is sent anywhere unless you push.

Two configuration files that Git and editors read live at the top of this repository: `.gitignore` (which files Git must never track) and `.editorconfig` (which layout editors must follow).

## Why this repository uses it

The repository is a Git repository with small, meaningful commits, so a learner can read how the project grew. Two small files keep it clean: `.gitignore` keeps generated files and secrets out of the history, and `.editorconfig` keeps the layout of every file consistent whatever editor you use.

## Where it appears

| Path | What it does |
| --- | --- |
| `.gitignore` | Lists what Git must not track. |
| `.editorconfig` | Lists indent and line-ending rules per file type. |
| `.git/` | The history (created by `git init`; not tracked itself). |

## How it works underneath

A commit stores the full content of every tracked file, deduplicated by hash, plus metadata. Each commit has an id that is the hash of its content, so changing history changes the ids. `git add` puts changes in the **staging area**, and `git commit` records what is staged. A **branch** is just a movable name for a commit.

`.gitignore` lists patterns. Git skips files that match when you `git add .`. A trailing `/` means a folder. `*` matches any characters inside a name. A file already tracked stays tracked even if a pattern later matches it.

`.editorconfig` is read by editors (through a plugin or built in), not by Git. `root = true` stops the search in parent folders. A `[*.{yml,yaml}]` section applies to files whose names match.

## Walkthrough of this repository's use

### `.gitignore`

```
.venv/
__pycache__/
*.pyc
*.egg-info/
build/
dist/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.import_linter_cache/
.coverage
.env
.DS_Store
logs/
.omc/
var/
```

One pattern per line. `.venv/` is the virtual environment. `__pycache__/` and `*.pyc` are Python's compiled caches. `*.egg-info/`, `build/` and `dist/` are packaging output. `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` and `.import_linter_cache/` are the caches of the tools; `.coverage` is the coverage data file. `.env` holds your local settings and passwords, which must never be committed (the tracked `.env.example` has placeholders). `.DS_Store` is a macOS Finder file. `logs/` is where local logs would go, `.omc/` is a tool-state folder, and `var/` is the folder where the replay writes its generated files (the simulated week as an OpenMetrics file, the pager logs).

### `.editorconfig`

```
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 4

[*.{yml,yaml,json,toml}]
indent_size = 2

[Makefile]
indent_style = tab

[*.md]
trim_trailing_whitespace = false
```

`root = true` stops editors from searching parent folders for more settings. The `[*]` section applies to every file: `charset = utf-8`, `end_of_line = lf` (Unix line breaks), `insert_final_newline = true` (a file ends with a line break), `trim_trailing_whitespace = true` (no spaces at the end of lines), `indent_style = space` and `indent_size = 4` (Python's usual indent). `[*.{yml,yaml,json,toml}]` overrides the indent size to 2 for those file types. `[Makefile]` sets `indent_style = tab`, because Make requires tabs in recipes. `[*.md]` allows trailing whitespace, because two trailing spaces mean a line break in some Markdown.

## Try it yourself

```bash
git log --oneline | head -5
```

Expected: the five latest commits, each a short message.

```bash
git status --short
```

Expected: no output (a clean tree).

```bash
git check-ignore -v .venv .env
```

Expected: both paths listed with the `.gitignore` line that ignores them.

```bash
git ls-files | grep -c ""
```

Expected: the number of tracked files; `.venv`, caches and `.env` are not among them.

```bash
git show --stat HEAD | head
```

Expected: the last commit's message and the files it changed.

## Mistakes people make

- **Committing generated files** (caches, virtual environments, build output). `.gitignore` here lists them.
- **Committing `.env`.** It holds local secrets; only `.env.example` with placeholder values is tracked.
- **Using tabs in YAML or spaces in a Makefile** because the editor guessed wrong. `.editorconfig` fixes the rule per file type.
- **Large commits with unrelated changes.** Each commit in this repository does one thing.

## Check yourself

1. What does a commit contain? *A snapshot of the tracked files, a message, an author and the id of the previous commit.*
2. What does `.gitignore` do? *Keeps matching files out of version control.*
3. Who reads `.editorconfig`? *Code editors, to apply indent and line-ending rules.*
4. Why is `.env` ignored but `.env.example` tracked? *`.env` holds your private values; the example documents the names with placeholders.*
5. What is a branch? *A movable name that points to a commit.*

## Further reading

- Pro Git book: https://git-scm.com/book/en/v2
- gitignore documentation: https://git-scm.com/docs/gitignore
- EditorConfig specification: https://spec.editorconfig.org/
