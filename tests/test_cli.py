"""End-to-end tests: the `ut` CLI in a subprocess against real repositories."""

import json
import sys
import xml.etree.ElementTree as ET

from conftest import run_ut

FAILING_REPO = {
    "src/calc.py": """
        def add(a, b):
            return a + b + 1  # bug

        def divide(a, b):
            return a / b

        def write(path, text):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print("written")
    """,
    "tests/python/calc/add/add.test.yaml": """
        schema_version: 1
        target: {language: python, procedure: "calc:add"}
        cases:
          - id: small
            description: Adds two small numbers
            args: [1, 2]
            expect: {returns: {value: 3}}
    """,
    "tests/python/calc/divide/divide.test.yaml": """
        schema_version: 1
        target: {language: python, procedure: "calc:divide"}
        cases:
          - id: by_zero
            args: [1, 0]
            expect: {returns: {value: 0}}
          - id: half
            xfail: known rounding issue
            args: [1, 2]
            expect: {returns: {value: 0.5}}
    """,
    "tests/python/calc/write/write.test.yaml": """
        schema_version: 1
        target: {language: python, procedure: "calc:write"}
        cases:
          - id: text
            args: [{out: out.txt}, "new\\n"]
            expect:
              stdout: {contains: [written]}
              files: [{path: out.txt, golden: golden/out.txt}]
    """,
    "tests/python/calc/write/golden/out.txt": "old\n",
}


def test_demo_repo_validates_and_passes(demo_repo):
    result = run_ut(demo_repo, "validate")
    assert result.code == 0, result.out
    assert "8/8 spec(s) valid" in result.out

    result = run_ut(demo_repo, "run", "--junit", "report.xml")
    assert result.code == 0, result.out
    suite = ET.parse(demo_repo / "report.xml").getroot().find("testsuite")
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors"), suite.get("skipped")) == ("26", "0", "0", "2")
    case = next(tc for tc in suite.iter("testcase") if tc.get("name") == "three_lines")
    props = {p.get("name"): p.get("value") for p in case.iter("property")}
    assert props == {"procedure": "demo_procs.text:word_count", "language": "python"}


def test_language_and_suite_selection(demo_repo):
    assert "2 skipped" in run_ut(demo_repo, "run", "--lang", "skill").out
    python_only = run_ut(demo_repo, "run", "--lang", "python")
    assert python_only.code == 0 and "24 passed" in python_only.out and "skipped" not in python_only.out
    smoke = run_ut(demo_repo, "run", "--suite", "smoke")
    assert smoke.code == 0 and "12 passed" in smoke.out
    unknown = run_ut(demo_repo, "run", "--suite", "nope")
    assert unknown.code == 2 and "known: nightly, python, smoke" in unknown.out


def test_empty_selection_is_a_usage_error(demo_repo):
    assert run_ut(demo_repo, "run", "-m", "no_such_tag").code == 2
    assert run_ut(demo_repo, "run", "-m", "no_such_tag", "--allow-empty").code == 0


def test_plain_pytest_sees_the_same_tests(demo_repo):
    import subprocess

    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=demo_repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
    assert "24 passed, 2 skipped" in proc.stdout


def test_failures_are_reported_readably(repo):
    root = repo(FAILING_REPO)
    result = run_ut(root, "run", "--keep-sandbox", "failed")
    assert result.code == 1, result.out
    out = result.out
    # wrong return value; the short summary line names the failed check
    assert "add.test.yaml::small - failed: returns.value" in out
    assert "spec:    tests/python/calc/add/add.test.yaml:4  (python)" in out
    assert "about:   Adds two small numbers" in out
    assert "FAILED returns.value" in out and "expected: 3" in out and "actual:   4" in out
    # unexpected exception, with the traceback from the code under test
    assert "unexpected ZeroDivisionError: division by zero" in out and "calc.py" in out
    # file differing from its golden, as a diff, plus captured stdout
    assert "FAILED files[out.txt].golden" in out and "-old" in out and "+new" in out
    assert "--- captured stdout" in out
    # strict xfail: a case marked xfail that passes is a failure
    assert "XPASS(strict)" in out
    # sandboxes are kept for failures only, each with a record of the case
    records = sorted((root / "ut-out" / "sandbox").rglob("_ut_result.json"))
    assert [r.parent.name for r in records] == ["small", "by_zero", "text"]
    small = json.loads(records[0].read_text(encoding="utf-8"))
    assert (small["procedure"], small["args"], small["returned"], small["passed"]) == ("calc:add", [1, 2], 4, False)
    assert small["failures"][0]["where"] == "returns.value"


def test_bless_then_compare(repo):
    root = repo({
        "src/greet.py": """
            def greet(path, name):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"Hello, {name}!\\n")
                return {"name": name, "length": len(name)}
        """,
        "tests/python/greet/greet.test.yaml": """
            schema_version: 1
            target: {language: python, procedure: "greet:greet"}
            cases:
              - id: world
                args: [{out: hello.txt}, World]
                expect:
                  returns: {golden: golden/world.json}
                  files: [{path: hello.txt, golden: golden/world.txt}]
        """,
    })
    validate = run_ut(root, "validate")
    assert validate.code == 2 and "create it with `ut bless`" in validate.out

    bless = run_ut(root, "bless")
    assert bless.code == 0, bless.out
    assert "2 golden file(s) written" in bless.out
    assert (root / "tests/python/greet/golden/world.txt").read_text(encoding="utf-8") == "Hello, World!\n"
    assert json.loads((root / "tests/python/greet/golden/world.json").read_text()) == {"length": 5, "name": "World"}

    assert run_ut(root, "validate").code == 0
    assert run_ut(root, "run").code == 0

    src = root / "src/greet.py"
    src.write_text(src.read_text().replace("Hello", "Hi"), encoding="utf-8")
    changed = run_ut(root, "run")
    assert changed.code == 1 and "-Hello, World!" in changed.out and "+Hi, World!" in changed.out


def test_invalid_spec_is_a_usage_error(repo):
    root = repo({
        "src/m.py": "def f():\n    return 1\n",
        "tests/python/m/f/f.test.yaml": """
            schema_version: 1
            target: {language: python, procedure: "m:f"}
            cases:
              - id: a
                args: [{file: data/nope.txt}]
                expect: {returns: {valu: 1}}
        """,
    })
    validate = run_ut(root, "validate")
    assert validate.code == 2
    assert "unknown key 'valu' (allowed: value, approx," in validate.out
    run = run_ut(root, "run")
    assert run.code == 2 and "invalid test spec" in run.out


def test_setup_errors_fail_the_case(repo):
    root = repo({
        "src/m.py": "def f():\n    return 1\n",
        "tests/python/m/f/f.test.yaml": """
            schema_version: 1
            target: {language: python, procedure: "m:missing"}
            cases:
              - id: a
                mocks: {m.nothing_here: {return: 1}}
                expect: {returns: {value: 1}}
        """,
    })
    result = run_ut(root, "run")
    assert result.code == 1 and "SETUP ERROR: module 'm' has no attribute 'missing'" in result.out


def test_infrastructure_errors_exit_3(repo):
    root = repo({
        "src/m.py": "def f():\n    return 1\n",
        "conftest.py": """
            from ut.adapters.python import PythonAdapter
            from ut.errors import InfraError

            def broken_start(self):
                raise InfraError("licence server unreachable")

            PythonAdapter.start = broken_start
        """,
        "tests/python/m/f/f.test.yaml": """
            schema_version: 1
            target: {language: python, procedure: "m:f"}
            cases:
              - {id: a, expect: {returns: {value: 1}}}
              - {id: b, expect: {returns: {value: 1}}}
        """,
    })
    result = run_ut(root, "run")
    assert result.code == 3, result.out
    assert "INFRASTRUCTURE ERROR: licence server unreachable" in result.out
    assert "ut: 2 infrastructure error(s)" in result.out


def test_new_scaffolds_at_the_standard_location(repo):
    root = repo({"src/pkg/__init__.py": "", "src/pkg/reports.py": "def build(x):\n    return x\n"})
    created = run_ut(root, "new", "python", "pkg.reports:build")
    assert created.code == 0, created.out
    spec = root / "tests/python/pkg/reports/build/build.test.yaml"
    assert spec.is_file() and (spec.parent / "data").is_dir() and (spec.parent / "golden").is_dir()
    assert 'source: "src/pkg/reports.py"' in spec.read_text()

    assert run_ut(root, "validate").code == 0
    run = run_ut(root, "run")  # collected although pytest skips directories named `build` by default
    assert run.code == 0 and "1 skipped" in run.out

    assert run_ut(root, "new", "python", "pkg.reports:build").code == 2  # exists
    skill = run_ut(root, "new", "skill", "abcMakePins", "--area", "pins")
    assert skill.code == 0 and (root / "tests/skill/pins/abcMakePins/abcMakePins.test.yaml").is_file()
    assert run_ut(root, "new", "python", "no_colon").code == 2


def test_list_json(demo_repo):
    result = run_ut(demo_repo, "list", "--json", "--tag", "smoke")
    assert result.code == 0
    rows = json.loads(result.out)
    assert {r["procedure"] for r in rows} == {
        "demo_procs.text:word_count", "demo_procs.text:normalize_cell_name", "demo_procs.geometry:bbox_area"}
    word_count = next(r for r in rows if r["procedure"].endswith("word_count"))
    assert word_count["cases"][0]["nodeid"] == "tests/python/demo_procs/text/word_count/word_count.test.yaml::three_lines"

    text = run_ut(demo_repo, "list", "--lang", "skill")
    assert "abcWritePinReport  [skill]" in text.out and "1 spec(s), 2 case(s)" in text.out


def test_parallel_run(demo_repo):
    result = run_ut(demo_repo, "run", "-n", "2", "-q")
    assert result.code == 0 and "24 passed, 2 skipped" in result.out
