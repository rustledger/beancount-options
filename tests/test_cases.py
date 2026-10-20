# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the shared cases in tests/cases.

The files are meant to be read by rustledger's native plugins too, so a
rule change shows up on both sides.
"""

import datetime
import json
import pathlib

import pytest
from beancount import loader

from beancount_options import check_option_assignment as assignment_module
from beancount_options import check_option_expiry as plugin_module

CASES = pathlib.Path(__file__).parent / "cases"
CONFIG_CASES = json.loads((CASES / "expiry_config.json").read_text(encoding="utf-8"))["cases"]
LEDGERS = sorted((CASES / "expiry").glob("*.beancount"))
ASSIGNMENT_LEDGERS = sorted((CASES / "assignment").glob("*.beancount"))


def _id(case):
    return case["config"].encode("unicode_escape").decode("ascii")[:50] or "<empty>"


@pytest.mark.parametrize("case", CONFIG_CASES, ids=_id)
def test_config_case(case):
    as_of, problem = plugin_module._parse_config(case["config"])
    expected = datetime.date.fromisoformat(case["as_of"]) if case["as_of"] else None
    assert (as_of, problem) == (expected, case["problem"])


def _ledger_string(text):
    """``text`` as a ledger string literal, which decodes these two escapes."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


@pytest.mark.parametrize("case", CONFIG_CASES, ids=_id)
def test_config_case_through_bean_check(case):
    text = f'plugin "beancount_options.check_option_expiry" {_ledger_string(case["config"])}\n'
    _, errs, _ = loader.load_string(text)
    if case["problem"] is None:
        assert errs == []
    else:
        assert [e.message for e in errs] == [
            f"Invalid configuration for check_option_expiry plugin: {case['problem']}; skipping."
        ]


def _assert_ledger(ledger, error_type):
    _, errs, _ = loader.load_file(str(ledger))
    got = [f"{e.source['lineno']}: {e.message}" for e in errs]
    expected = ledger.with_suffix(".expected").read_text(encoding="utf-8").splitlines()
    assert got == expected
    # Every error is the plugin's: a fixture that does not balance would fail here.
    assert all(isinstance(e, error_type) for e in errs)


@pytest.mark.parametrize("ledger", LEDGERS, ids=lambda p: p.stem)
def test_ledger_case(ledger):
    _assert_ledger(ledger, plugin_module.OptionExpiryError)


@pytest.mark.parametrize("ledger", ASSIGNMENT_LEDGERS, ids=lambda p: p.stem)
def test_assignment_ledger_case(ledger):
    _assert_ledger(ledger, assignment_module.OptionAssignmentError)


def test_case_files_are_not_empty():
    # Guards against a glob or path change silently running nothing.
    assert len(CONFIG_CASES) >= 40
    assert {p.stem for p in LEDGERS} >= {"clean", "mixed", "as_of"}
    assert {p.stem for p in ASSIGNMENT_LEDGERS} >= {"assignment", "assignment-fees"}
    assert all(p.with_suffix(".expected").exists() for p in LEDGERS + ASSIGNMENT_LEDGERS)
