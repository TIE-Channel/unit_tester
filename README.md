# ut: declarative unit tests for Python and SKILL procedures

`ut` tests individual procedures by calling them with arguments and checking
what they return, raise, print and write. Most tests are YAML **specs**, one
file per procedure, checked against a JSON Schema. The same format works for
Python and Cadence SKILL. Underneath it is a pytest plugin, so Jenkins gets
JUnit XML, parallel runs come from pytest-xdist, and IDE test explorers see the
specs as ordinary tests.

**Status: Phase 0.** Python procedures run end to end. SKILL specs are
validated, listed and reported as skipped; the Virtuoso adapter arrives in
Phase 1 (see [Roadmap](#roadmap)).

## Quick start

```bash
pip install "ut-engine[parallel] @ git+https://github.com/TIE-Channel/unit_tester.git@v0.1.0"
cd <your repository>
ut new python mypkg.reports:write_report      # creates tests/python/mypkg/reports/write_report/...
# edit the spec, then:
ut validate
ut run
```

A complete example repository lives in [examples/demo_repo](examples/demo_repo).
Run `ut run` inside it to see passing tests, and break something in `src/` to
see what failures look like.

## Repository layout

```
<repo>/
  ut.toml                         # optional configuration (see below)
  pytest.ini                      # marks the pytest root (can be empty)
  src/...                         # code under test
  tests/
    python/<module path>/<function>/
      <function>.test.yaml        # the spec
      data/                       # input files the cases use
      golden/                     # expected output files, written by `ut bless`
    skill/<area>/<procedure>/...
    support/                      # custom check functions (optional)
  ut-out/                         # sandboxes and reports; do not commit
```

`ut new` creates this structure, so specs end up in the same place whoever
writes them. Hand-written pytest tests (`test_*.py`) can sit next to the specs
and run in the same session. Use them for anything a spec cannot express.

## Writing a spec

```yaml
schema_version: 1
target:
  language: python                       # python | skill
  procedure: demo_procs.text:write_report # Python: module:function; SKILL: procedure name
  source: src/demo_procs/text.py          # informational; used by tooling
description: Writes a word-frequency report and returns the number of distinct words.
tags: [text, reports]                    # become pytest markers: ut run -m "reports and not slow"
mocks:
  demo_procs.text.getpass.getuser: {return: jenkins}
cases:
  - id: frequencies
    description: Typical input
    args: [{file: data/words.txt}, {out: out/report.txt}]
    kwargs: {title: Demo report}
    expect:
      returns: {value: 5}
      stdout: {contains: ["wrote 5 words"]}
      files:
        - path: out/report.txt
          golden: golden/frequencies/report.txt
          ignore: ['(?<=Generated on )\d{4}-\d\d-\d\d \d\d:\d\d:\d\d']

  - id: missing_source
    args: [{out: nothing.txt}, {out: out/report.txt}]
    expect:
      error: {type: FileNotFoundError}
      files_absent: [out/report.txt]
```

### Spec-level keys

| Key | Meaning |
|---|---|
| `schema_version` | Always `1` for now. |
| `target` | `language`, `procedure`, optional `source`. |
| `description`, `owner` | Free text. |
| `tags` | Lower-case marker names, merged with each case's `tags`. `python`, `skill`, `skip`, `xfail` and pytest's own marker names are reserved. |
| `skip`, `xfail` | A reason string; applies to every case. `xfail` is strict: a case that starts passing fails until the `xfail` is removed. |
| `timeout` | Seconds. Applied to Python cases when pytest-timeout is installed; enforced by the SKILL worker from Phase 1. |
| `mocks` | Python only. Dotted path → `{return: value}`, `{raises: "OSError: disk full"}` or `{value: replacement}`. Cases can add or override entries. |
| `stubs` | SKILL only. Procedure name → SKILL expression used as its body during the case. |
| `setup.load` | SKILL only. Files to load before the cases run. |
| `x-…` | Ignored by ut. Use these keys to hold YAML anchors shared by several cases (see `layers_used.test.yaml` in the demo). |

### Arguments

`args` is a list and `kwargs` a mapping. Scalars and lists are passed as they
are. A mapping is always a **token**:

| Token | Passes |
|---|---|
| `{file: data/x.txt}` | Copies the file (relative to the spec) into the sandbox and passes its absolute path. |
| `{dir: data/lib}` | Same, for a directory tree. |
| `{out: out/report.txt}` | Absolute path of a file in the sandbox that does not exist yet; parent directories are created. |
| `{tmpdir: work}` | Creates an empty directory in the sandbox and passes its path. |
| `{value: {...}}` | The value as is. This is how to pass a literal mapping. |

Python only: add `as: path` to a path token to pass a `pathlib.Path` instead of a `str`.

### Expectations (`expect`)

Every listed check must hold. An error that no `expect.error` asked for fails
the case on its own, with the traceback from the code under test.

| Key | Checks |
|---|---|
| `returns.value` | Exact equality. `1 == 1.0`, but `true != 1`. |
| `returns.approx` | `{value, rel, abs}`. Numbers anywhere in the structure compare within tolerance (default rel 1e-6). |
| `returns.unordered` | Same elements in any order (sets come back as lists). |
| `returns.subset` | Mappings: the listed keys match, recursively. Lists: every listed element matches some actual element. |
| `returns.regex`, `.type`, `.length`, `.schema` | Regex search on a string; JSON type (`null boolean integer number string array object`); length; a JSON Schema. |
| `returns.golden` | JSON file holding the expected value, written by `ut bless`. |
| `error` | `{type, match}`. `type` also matches base classes (`OSError` matches `FileNotFoundError`); `match` is a regex searched in the message. `error: {}` accepts any error. |
| `stdout`, `stderr` | A string (exact match) or `{equals, contains, not_contains, matches, empty, golden, ignore}`. |
| `files` | `{path, golden, compare: text|json|binary, ignore, contains, not_contains, matches}`. Paths are relative to the sandbox. |
| `files_absent` | Files that must not exist afterwards. |
| `custom` | `module:function`, called as `function(outcome=, case=, sandbox=)`. It fails the case by raising `AssertionError` or by returning `False` or a message. Modules are imported from `[python] paths`. |

`ignore` takes regexes. Matches are replaced by `<ignored>` on both sides
before comparing, so timestamps and similar values don't break goldens.

### How values are compared

Return values are turned into JSON-like data before any comparison: tuples
become lists, sets become sorted lists, `Path` becomes a string, and bytes
become `{"$bytes": hex}`. NaN becomes `{"$float": "nan"}`. Anything else becomes
`{"$repr": repr}`. In return values, output and files, the sandbox location is
replaced by `<sandbox>` and the rest of the path is written with forward
slashes, so goldens match on Windows and Linux.

### YAML rules

ut reads YAML 1.2 style. Only `true`/`false` are booleans, so `ON`, `yes`, `t`
and `nil` are strings. `010` is 10, `12:30` and `2024-01-01` are strings, and
`1e-9` is a float. Duplicate keys are an error.

## Goldens and blessing

Goldens are expected output files stored next to the spec. When a spec names
a golden that doesn't exist yet, `ut validate` reports it. To create or update
goldens, run:

```bash
ut bless tests/python/demo_procs/text/write_report
```

`ut bless` runs the selected cases and writes the current output into their
goldens instead of comparing. Review the result with `git diff` and commit
it. For legacy code with no documented expected output, this is the quickest
way to cover it: record the current behaviour, review it, then lock it in.

Keep input files and goldens byte-exact across platforms by excluding them
from line-ending conversion in the repository's `.gitattributes`:

```
**/tests/**/data/** -text
**/tests/**/golden/** -text
```

## Sandboxes

Each case runs in `ut-out/sandbox/<spec path>/<case id>/`, with that directory
as its working directory. The sandbox root is emptied at the start of every
run. Sandboxes of passing cases are deleted and those of failing cases are
kept (`--keep-sandbox always|never` changes this). A kept sandbox contains the
case's inputs and outputs plus `_ut_result.json`: the arguments, the failures,
the return value, the error and the captured output.

## Command line

| Command | |
|---|---|
| `ut validate [PATHS] [--json]` | Schema and semantic checks: inputs and goldens exist, regexes compile, ids are unique, language rules. Fast; run it first in CI and as a pre-commit hook. |
| `ut list [PATHS] [--lang L] [--tag T] [--json]` | Specs and cases. `--json` includes pytest node ids, for tools. |
| `ut run [PATHS] [-m EXPR] [-k EXPR] [--suite S] [--lang L] [--junit FILE] [-n N] [--keep-sandbox K] [--allow-empty]` | Runs the tests. Unknown options, and everything after `--`, go to pytest (`ut run -x --lf`, `ut run -- --maxfail 2`). |
| `ut bless [PATHS] [...]` | Like `run`, but writes goldens. |
| `ut new LANG PROCEDURE [--area A] [--source F] [--force]` | Creates a spec skeleton at the standard location. Its placeholder case is skipped until you fill it in. |
| `ut schema [--path]` | Prints the JSON Schema, or its path. |

Every command takes `--config PATH`. By default ut uses the nearest `ut.toml`
at or above the current directory.

**Exit codes:**
- 0: passed
- 1: test failures
- 2: invalid spec, configuration or usage, or nothing selected
- 3: infrastructure error (adapter could not start, licence, crash)

The Jenkins template maps 1 to UNSTABLE and 2 and 3 to FAILURE.

## Configuration (`ut.toml`)

```toml
[ut]
tests = ["tests"]          # where specs live (default: tests)
out_dir = "ut-out"         # sandboxes (default: ut-out)
keep_sandbox = "failed"    # failed | always | never

[python]
paths = ["src", "tests/support"]   # prepended to sys.path

[suites]                    # ut run --suite NAME
smoke = { markers = "smoke" }
skill_nightly = { languages = ["skill"], markers = "not slow", paths = ["tests/skill"] }

[layout]
spec_path = "{tests}/{language}/{area}/{name}/{name}.test.yaml"   # used by `ut new`
```

Unknown keys are errors.

## Editors and plain pytest

The plugin is registered through a pytest entry point, so plain `pytest` (and
VS Code's Test Explorer) collects specs as well. For autocompletion and
validation while editing, point the YAML extension at the schema:

```jsonc
// .vscode/settings.json; get the path from `ut schema --path`
{ "yaml.schemas": { "/path/to/site-packages/ut/schema/testspec.v1.json": "**/*.test.yaml" } }
```

## Jenkins

- [ci/Jenkinsfile.consumer](ci/Jenkinsfile.consumer) is the template for repositories that use ut.
  - It validates specs first, then runs the Python tests in parallel workers.
  - It publishes JUnit results and archives the sandboxes of failed cases.
  - The SKILL stage is prepared but commented out until Phase 1.
- [Jenkinsfile](Jenkinsfile) builds and tests the engine itself.

## Limitations (Phase 0)

- Python procedures run inside the pytest process. Global state a procedure
  changes can leak into later cases; mocks and the working directory are
  restored after each case.
- `timeout` for Python cases needs `pytest-timeout`.
- SKILL specs are validated but not executed yet.
- Developed and tested on Python 3.11+. The code targets 3.9+, but 3.9 and
  3.10 have not been run yet.

## Roadmap

1. **Phase 1, SKILL adapter.**
   - A persistent `virtuoso -nograph` worker per pytest worker.
   - SKILL calls generated from the spec, results written back as JSON.
   - `errset` around every call; stubs applied with `getd`/`putd`.
   - `probe:` expressions for database objects, and a scratch OA library fixture.
2. **Phase 2.**
   - Claude Code skills: `/ut-new` (interview, then spec), `/ut-fix` (failure triage) and `/ut-gaps` (untested procedures).
   - `ut catalog` and an untested-procedures report.
3. **Phase 3.**
   - `ut gui`: browse, run, view diffs, bless.
   - Allure reports, changed-only runs for pull requests, and splitting large suites across agents.

## Developing the engine

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[parallel]"   # .venv/bin on Linux
.venv/Scripts/python -m pytest tests -n auto
```

`tests/test_cli.py` runs the real CLI in subprocesses against small
repositories built in temporary directories, including the demo repository.
