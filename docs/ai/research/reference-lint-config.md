# Lint config reference: ApplicantFilterSystemO/manager_backend (commit 490eaae-era main, cloned 2026-11-26)

Source: https://github.com/MarkFazekas/ApplicantFilterSystemO/tree/main/manager_backend . Facts marked (verified) were checked by installing the pinned dev deps (ruff 0.16.0, flake8 7.3.0) in a throwaway venv.

## Repo reality check
The repo has NO: .github/ (no CI), CLAUDE.md, .claude/, .pre-commit-config.yaml, Makefile/justfile, tox.ini/setup.cfg, mypy.ini, ruff.toml, pytest config, coverage config, .editorconfig, commitlint, tests directory. Only README mentions gitlint / Conventional Commits badges (no .gitlint file committed). `gitlint==0.19.1` is in dev requirements only.
Not uv-project-style: pip-style requirements files (`requirements/{base,dev}.txt`, `uv pip install -r`), pyproject.toml has only [tool.mypy]/[tool.ruff*] (no [project], no lockfile). Root `.python-version` = `afs3146` (a pyenv virtualenv name).

## flake8 plugins (dev.txt pins) and codes (codes verified from installed entry points)
| package | pin | codes |
|---|---|---|
| flake8 (pyflakes 3.4.0, pycodestyle 2.14.0, mccabe 0.7.0) | 7.3.0 | F, E, W, C90 |
| wemake-python-styleguide | 1.7.0 | WPS (WPS1xx naming, 2xx complexity/consistency, 3xx, 4xx, 5xx, 6xx, 7xx) |
| flake8-print | 5.0.0 | T201 T202 T203 T204 |
| flake8-bugbear | 25.11.29 | B0xx, B9xx (B901 B902 B903 explicitly enabled) |
| flake8-assertive | 2.2.0 | A500-A504 |
| flake8-eradicate | 1.5.0 | E800 |
| flake8-builtins | 3.1.0 | A001-A006 |
| flake8-annotations | 3.2.0 | ANN001-003, ANN201-206, ANN401 (ANN101/102 removed upstream; ignores in config are moot) |
| flake8-comprehensions | 3.17.0 | C4xx (C400-C420) |
| flake8-spellcheck | 0.28.0 | SC100 (names), SC200 (comments) |
| flake8-pydantic | 0.4.0 | PYD001-006, PYD010 |
| pep8-naming | 0.15.1 | N801-N818 |
WPS pulls attrs, pygments. Other dev tools: ruff==0.16.0, mypy==2.3.0, gitlint==0.19.1, pytest==9.1.1, pytest-mock==3.15.1, moto==5.2.3, uv==0.12.0, chalice==1.33.0, python-dotenv==1.2.3.

## Ruff coverage of those plugins (checked with `ruff rule --all` json of 0.16.0)
Covered (same or near-same rule set): pyflakes F, mccabe C90 (C901), pep8-naming N (all except N808... see below), flake8-print T201/T203, flake8-builtins A001-A006, flake8-comprehensions C4 (all), flake8-annotations (ANN001-003, 201, 202, 204-206, 401; ANN101/102 exist only as removed stubs - cannot be selected), eradicate ERA001 (replaces E800), bugbear mostly (B002-B035, B039, B043, B901, B903, B904, B905, B909, B911, B912), pycodestyle E/W only partially (ruff's E/W subset; indentation/whitespace E1xx/E2xx/E3xx and W503/W504 are not in stable ruff E; formatter replaces them).
NOT covered by ruff (no equivalent rule code):
- wemake-python-styleguide: all WPS (ruff has none; `external = ["WPS"]` keeps `# noqa: WPS...` from being flagged as unused)
- flake8-assertive A500-A504
- flake8-spellcheck SC100/SC200 (project uses whitelist.txt + dictionaries setting)
- flake8-pydantic PYD001-006/010
- bugbear gaps: B001 (ruff maps to E722), B036, B037, B040, B041, B042, B902, B906, B907, B908, B910, B950
- flake8-print T202, T204; flake8-annotations ANN203, ANN402; pep8-naming N808
Caveat: ruff 0.17.6 (latest via uvx) also lacks B042/B902/PYD*; pin is 0.16.0.

## .flake8 (full copy: lintref/manager_backend.flake8)
extend-select B901,B902,B903. ignore: E501, B042, N818, ANN101, ANN102, WPS305, WPS237. per-file-ignores `**/tests/*: S101,WPS235,WPS201`. max_complexity=8, max_line_length=120, statistics, count, inline-quotes=double, max-string-usages=5, max-name-length=50, max-module-members=20, max-local-variables=10, max-try-body-length=2, max-line-complexity=13, max-imports=15, max-methods=10, max-import-from-members=15, docstring-convention=google, docstring_style=google, strictness=long, eradicate-aggressive=True, dictionaries=en_US,python,technical,pandas. Spellcheck whitelist: lintref/manager_backend.whitelist.txt (repo file `whitelist.txt`, picked up by flake8-spellcheck by default from cwd).
Verified behavior: because `ignore =` is set, flake8's default ignore list is replaced, so W503 (and probably W504, E121/E123/E126/E226/E24/E704) are ACTIVE; B904/B950 did not fire in a probe (bugbear B9xx stay off except B901-903). Per-file S101 in flake8 is a no-op (S not installed in flake8; ruff S101 is globally ignored). WPS111 flagged names `a`, `f` (default min name length 2).
Note: per-file-ignores glob `**/tests/*` targets a tests dir that does not exist in the repo.

## ruff (full: lintref/manager_backend.pyproject.toml)
[tool.ruff]: target-version py312, line-length 120, exclude = standard list (.git, .venv, venv, build, dist, node_modules, .mypy_cache, .ruff_cache, .tox, .nox, __pypackages__, etc.).
[tool.ruff.lint]: select = ["ALL"]; external = ["WPS"]; ignore = E501, D211, D213, D104, CPY001, S101, N818, COM812; fixable = ["I","TC"]; unfixable = ["B"]; dummy-variable-rgx = "^(_+|(_+[a-zA-Z0-9_]*[a-zA-Z0-9]+?))$".
mccabe max-complexity 10 (flake8 uses 8, stricter). pydocstyle convention google. No per-file-ignores. [tool.ruff.format]: quote-style double, indent-style space, docstring-code-format true.
(ALL selects CPY001 only in preview; ignored anyway. ruff 0.16 selects ANN101/102 not at all.)

## mypy (in pyproject.toml)
python_version 3.12, strict = true, warn_redundant_casts, warn_unused_ignores, disallow_any_generics, check_untyped_defs, no_implicit_reexport, disallow_untyped_defs, plugins = ['pydantic.mypy'] (only meaningful if pydantic is used). No per-module overrides.

## pytest / coverage
None configured. Dev deps pytest, pytest-mock, moto only; flake8 config anticipates `**/tests/*`. No tests exist.

## Invocation (README manager_backend, run from manager_backend/, sequential, no parallelism, no wrapper script)
```
python -m ruff check --select I,TC --fix
python -m ruff format
python -m ruff check .
python -m flake8 .
python -m mypy .
```
Setup: `pip install uv; uv pip install -r manager_backend/requirements/dev.txt`. README badges: ruff, wemake-python-styleguide, mypy, Conventional Commits, gitlint.

## Project-specific items to adapt for Transcribe-UI
- AWS Chalice/Lambda/DynamoDB/boto3/PyJWT/pydantic/PyOTP deps, `chalicelib/`, `app.py`, `.chalice/`, `deploy.py` (env-placeholder wrapper), dynamodb_local, Bruno, `.env.template`: drop.
- whitelist.txt words are all repo-specific (chalicelib, structlog, pyotp, pbkdf2 ...); start fresh (e.g. add torch/whisper/ffmpeg/pyside/etc as needed).
- mypy `plugins = ['pydantic.mypy']`: remove unless pydantic used. No ignore_missing_imports overrides exist; GPU/UI libs (torch, PySide/Qt, faster-whisper, etc.) will likely need `[[tool.mypy.overrides]]`.
- python_version/target-version 3.12 and `.python-version` (pyenv venv name, not a version): set to the project's version, use uv-native `.python-version`.
- Convert pip requirements to uv: [project] + [dependency-groups] dev, uv.lock, `uv run ruff|flake8|mypy` (flake8 plugins need to be in the dev group; WPS needs flake8>=7.3,<8).
- Paths `.` for lint (flake8 `.` also scans .venv unless `exclude`/`extend-exclude` is added to .flake8 - repo has none; add `exclude=.venv,.git,...`). Ruff excludes .venv by default.
- Complexity mismatch (flake8 8 vs ruff 10) and duplicated config between .flake8 and ruff is intentional-looking but undocumented.
- wemake's strict limits (max-methods 10, local vars 10, imports 15) may be tight for UI code; decide per project.
- Two apps (desktop UI + GPU worker): use one root config or per-app pyproject; tests glob `**/tests/*`.

## Conventions / Claude config worth reusing
None present (no CLAUDE.md, .claude/, hooks, agents, commands, docs/). Only conventions: Conventional Commits + gitlint (no config file), Google-style docstrings, double quotes, 120 cols, explicit-comment justification for each ignore, LF line endings via .gitattributes (`*.py *.ts *.js *.html *.po *.sh *.rst *.txt *.md *.json text eol=lf`).

## CI
None (no .github directory, no other CI files in repo).
