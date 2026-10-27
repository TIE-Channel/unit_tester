"""Hand-written pytest tests run in the same session as the specs.

Use them when a case needs real code: loops over many inputs, complex setup,
or properties a declarative spec cannot express.
"""

import pytest

from demo_procs.text import normalize_cell_name, write_report


@pytest.mark.parametrize("raw", ["INV", " inv ", "Nand2  X1"])
def test_normalize_is_idempotent(raw):
    once = normalize_cell_name(raw)
    assert normalize_cell_name(once) == once


def test_report_lists_most_frequent_word_first(tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("b a b\n", encoding="utf-8")
    dest = tmp_path / "out.txt"
    write_report(str(src), str(dest))
    body = [line for line in dest.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert body[0].split() == ["b", "2"]
