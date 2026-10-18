# SPDX-License-Identifier: GPL-2.0-or-later
"""Ports of rustledger's check_option_expiry tests (T01-T10).

Each test asserts an exact error count and that beancount itself reported
nothing, so a fixture that doesn't balance can't hide behind the plugin.
"""

import datetime
import pickle
from decimal import Decimal

import pytest
from beancount import loader
from helpers import HEADER, later, leg, leg_at_cost, load

from beancount_options import check_option_expiry as plugin_module

PUT = "AAPL261016P00230000"


def errors(body):
    ours, others, _ = load("check_option_expiry", body)
    assert others == [], others
    return ours


def test_t01_closed_before_expiry():
    assert (
        errors(leg("2026-09-02", "-1", PUT) + leg("2026-09-20", "1", PUT) + later("2026-11-01"))
        == []
    )


def test_t02_closed_on_expiry_day():
    assert (
        errors(leg("2026-09-02", "-1", PUT) + leg("2026-10-16", "1", PUT) + later("2026-11-01"))
        == []
    )


def test_t03_short_left_open():
    ours, others, _ = load(
        "check_option_expiry", leg("2026-09-02", "-1", PUT) + later("2026-10-17")
    )
    assert others == []
    assert len(ours) == 1
    err = ours[0]
    assert err.message == (
        "AAPL261016P00230000 expired 2026-10-16 but -1 contract(s) remain open "
        "in Assets:Broker:Options"
    )
    # Reported at the transaction that opened the position.
    assert err.entry.narration == "option leg"
    assert err.entry.date.isoformat() == "2026-09-02"
    assert err.source["filename"] == err.entry.meta["filename"]
    assert err.source["lineno"] == err.entry.meta["lineno"]


def test_t04_long_left_open():
    found = errors(leg("2026-09-03", "1", "SPXW261030C06800000") + later("2026-11-02"))
    assert len(found) == 1
    assert " but 1 contract(s) " in found[0].message


def test_t05_ledger_ends_on_or_before_expiry():
    assert errors(leg("2026-09-02", "-1", PUT) + later("2026-10-16")) == []
    assert errors(leg("2026-09-02", "-1", PUT)) == []


def test_t06_tracked_per_account():
    found = errors(
        leg("2026-09-02", "-1", PUT)
        + leg("2026-09-02", "-1", PUT, account="Assets:IRA:Options")
        + leg("2026-09-20", "1", PUT)
        + later("2026-11-01")
    )
    assert len(found) == 1
    assert found[0].message.endswith(" in Assets:IRA:Options")


def test_t07_partial_close():
    sym = "AAPL261016P00232500"
    found = errors(leg("2026-09-02", "-3", sym) + leg("2026-09-20", "2", sym) + later("2026-11-01"))
    assert len(found) == 1
    assert " but -1 contract(s) " in found[0].message


def test_t08_ignores_non_option_commodities():
    body = "".join(
        leg("2026-09-02", "-1", sym)
        for sym in [
            "AAPL261316P00230000",  # month 13
            "AAPL261032P00230000",  # day 32
            "AAPL261016X00230000",  # not C/P
            "AAPL261016P0023000",  # 7 strike digits
            "TOOLONGX261016P00230000",  # root > 6
        ]
    )
    assert errors(leg("2026-09-02", "100", "AAPL") + body + later("2027-01-01")) == []


def test_t09_roll():
    roll = """
2026-09-15 * "roll"
  Assets:Broker:Options  1 AAPL260918C00270000 @ 100 USD
  Assets:Broker:Options  -1 AAPL261016C00280000 @ 100 USD
"""
    assert errors(leg("2026-09-05", "-1", "AAPL260918C00270000") + roll + later("2026-10-01")) == []


def test_t10_two_contracts_in_symbol_order():
    found = errors(
        leg("2026-09-03", "1", "SPY261016C00700000")
        + leg("2026-09-02", "-1", PUT)
        + later("2026-11-01")
    )
    assert [e.message.split()[0] for e in found] == [PUT, "SPY261016C00700000"]


def test_reopened_position_reports_the_reopening_entry():
    found = errors(
        leg("2026-09-02", "-1", PUT)
        + leg("2026-09-10", "1", PUT)
        + leg("2026-09-12", "-2", PUT)
        + later("2026-11-01")
    )
    assert len(found) == 1
    assert found[0].entry.date.isoformat() == "2026-09-12"
    assert " but -2 contract(s) " in found[0].message


def test_entries_returned_unchanged():
    _, _, loaded = load("check_option_expiry", leg("2026-09-02", "-1", PUT) + later("2026-11-01"))
    out, _ = plugin_module.check_option_expiry(loaded, {})
    assert out is loaded


def test_empty_ledger():
    assert plugin_module.check_option_expiry([], {}) == ([], [])


def test_errors_sorted_by_account_then_symbol():
    found = errors(
        leg("2026-09-02", "-1", "SPY261016C00700000")
        + leg("2026-09-02", "-1", PUT, account="Assets:IRA:Options")
        + leg("2026-09-03", "-1", PUT)
        + later("2026-11-01")
    )
    assert [(e.message.split()[0], e.message.split()[-1]) for e in found] == [
        (PUT, "Assets:Broker:Options"),
        ("SPY261016C00700000", "Assets:Broker:Options"),
        (PUT, "Assets:IRA:Options"),
    ]


def test_unsorted_input_is_walked_in_date_order():
    # Walked backwards, the 09-02 entry would look like the opener.
    body = leg("2026-09-02", "-1", PUT) + leg("2026-09-10", "1", PUT) + leg("2026-09-12", "-1", PUT)
    _, _, loaded = load("check_option_expiry", body + later("2026-11-01"))
    _, found = plugin_module.check_option_expiry(list(reversed(loaded)), {})
    assert len(found) == 1
    assert found[0].entry.date.isoformat() == "2026-09-12"


def test_posting_with_missing_number_is_skipped():
    import datetime

    from beancount.core import amount, data
    from beancount.core.number import MISSING

    meta = data.new_metadata("ledger.beancount", 1)
    posting = data.Posting(
        "Assets:Broker:Options", amount.Amount(MISSING, PUT), None, None, None, None
    )
    txn = data.Transaction(
        meta, datetime.date(2026, 9, 2), "*", None, "x", frozenset(), frozenset(), [posting]
    )
    later_txn = txn._replace(date=datetime.date(2026, 11, 1), postings=[])
    assert plugin_module.check_option_expiry([txn, later_txn], {}) == ([txn, later_txn], [])


def _line(err):
    return err.source["lineno"]


def _lines(body):
    """Line number of each transaction header in a loaded body, by date."""
    _, _, loaded = load("check_option_expiry", body)
    return {e.date.isoformat(): e.meta["lineno"] for e in loaded if hasattr(e, "narration")}


def test_sign_change_reopens_the_position():
    body = """
2026-09-02 * "sell to open"
  Assets:Broker:Options  -1 AAPL261016P00230000 @ 300 USD
  Assets:Broker:Cash  300 USD

2026-09-10 * "buy two"
  Assets:Broker:Options  2 AAPL261016P00230000 @ 100 USD
  Assets:Broker:Cash  -200 USD
""" + later("2026-11-01")
    found = errors(body)
    assert len(found) == 1
    assert " but 1 contract(s) " in found[0].message
    assert _line(found[0]) == _lines(body)["2026-09-10"]


def test_long_to_short_sign_change_reopens():
    found = errors(leg("2026-09-02", "2", PUT) + leg("2026-09-10", "-3", PUT) + later("2026-11-01"))
    assert len(found) == 1
    assert " but -1 contract(s) " in found[0].message
    assert found[0].entry.date.isoformat() == "2026-09-10"


def _cancelling_entry(first, second):
    return f"""
2026-09-10 * "cancelling legs"
  Assets:Broker:Options  {first} {PUT} @ 100 USD
  Assets:Broker:Options  {second} {PUT} @ 100 USD
"""


def test_entry_legs_are_netted_before_applying():
    # From an open -1, an entry whose legs cancel is a no-op in either order.
    for first, second in [("1", "-1"), ("-1", "1")]:
        found = errors(
            leg("2026-09-02", "-1", PUT) + _cancelling_entry(first, second) + later("2026-11-01")
        )
        assert len(found) == 1
        assert found[0].entry.date.isoformat() == "2026-09-02", (first, second)


def test_entry_that_nets_to_zero_from_flat_opens_nothing():
    assert errors(_cancelling_entry("1", "-1") + later("2026-11-01")) == []


def test_stacked_openers_report_at_the_first():
    found = errors(
        leg("2026-09-02", "-1", PUT) + leg("2026-09-05", "-1", PUT) + later("2026-11-01")
    )
    assert len(found) == 1
    assert " but -2 contract(s) " in found[0].message
    assert found[0].entry.date.isoformat() == "2026-09-02"


def test_close_dated_after_expiry_is_not_reported():
    # The rule looks at the final position, as the spec says.
    assert (
        errors(leg("2026-09-02", "-1", PUT) + leg("2026-10-20", "1", PUT) + later("2026-11-01"))
        == []
    )


def test_roots_with_digits_dots_and_edge_lengths():
    roots = ["XYZ1", "BRK.B", "ABCDEF", "F"]
    body = "".join(leg("2026-09-02", "-1", f"{r}261016C00050000") for r in roots)
    found = errors(body + later("2026-11-01"))
    assert sorted(e.message.split()[0] for e in found) == sorted(
        f"{r}261016C00050000" for r in roots
    )


def test_non_transaction_entry_moves_today():
    body = leg("2026-09-02", "-1", PUT)
    price = "\n2026-10-20 price AAPL 200.00 USD\n"
    assert len(errors(body + price)) == 1
    balance = "\n2026-10-20 balance Assets:Broker:Cash 100 USD\n"
    assert len(errors(body + balance)) == 1


def test_hundred_contracts_print_plainly():
    found = errors(leg("2026-09-02", "100", PUT) + later("2026-11-01"))
    assert len(found) == 1
    assert found[0].message == (
        "AAPL261016P00230000 expired 2026-10-16 but 100 contract(s) remain open "
        "in Assets:Broker:Options"
    )


def test_fractional_units_print_plainly():
    body = f"""
2026-09-02 * "odd lot"
  Assets:Broker:Options  -1.50 {PUT} @ 100 USD
  Assets:Broker:Cash  150 USD
"""
    found = errors(body + later("2026-11-01"))
    assert len(found) == 1
    assert " but -1.5 contract(s) " in found[0].message


def test_cost_booked_legs():
    body = (
        leg_at_cost("2026-09-02", "-3", PUT)
        + leg_at_cost("2026-09-20", "2", PUT, cost=None)
        + later("2026-11-01")
    )
    found = errors(body)
    assert len(found) == 1
    assert " but -1 contract(s) " in found[0].message
    assert found[0].entry.date.isoformat() == "2026-09-02"
    assert (
        errors(
            leg_at_cost("2026-09-02", "-1", PUT)
            + leg_at_cost("2026-09-20", "1", PUT, cost=None)
            + later("2026-11-01")
        )
        == []
    )


def _with_config(config, body):
    """Load ``body`` with ``config`` as the plugin's config string."""
    text = f'plugin "beancount_options.check_option_expiry" "{config}"\n\n{HEADER}{body}'
    _, errs, _ = loader.load_string(text)
    return errs


@pytest.mark.parametrize("config", ["{}", "", " \t\n", " {} "])
def test_empty_config_keeps_the_default(config):
    errs = _with_config(config, leg("2026-09-02", "-1", PUT) + later("2026-11-01"))
    assert [e.message.split()[0] for e in errs] == [PUT]


def test_as_of_ignores_later_entries():
    # A forecast dated after as_of neither moves "today" nor closes the put.
    body = leg("2026-09-02", "-1", PUT) + later("2026-12-01")
    assert _with_config("{'as_of': '2026-10-16'}", body) == []
    body = leg("2026-09-02", "-1", PUT) + leg("2026-11-01", "1", PUT)
    errs = _with_config("{'as_of': '2026-10-20'}", body)
    assert [e.message for e in errs] == [
        f"{PUT} expired 2026-10-16 but -1 contract(s) remain open in Assets:Broker:Options"
    ]


def test_as_of_reports_without_a_later_entry():
    # The ledger stops before expiry; as_of says the expiry has passed.
    errs = _with_config("{'as_of': '2026-10-17'}", leg("2026-09-02", "-1", PUT))
    assert [e.message.split()[0] for e in errs] == [PUT]
    assert errs[0].entry.date.isoformat() == "2026-09-02"


def test_as_of_on_the_expiry_day_is_fine():
    assert _with_config("{'as_of': '2026-10-16'}", leg("2026-09-02", "-1", PUT)) == []


def test_as_of_counts_entries_dated_on_it():
    body = leg("2026-09-02", "-1", PUT) + leg("2026-10-20", "1", PUT)
    assert _with_config("{'as_of': '2026-10-20'}", body) == []


@pytest.mark.parametrize(
    "config, problem",
    [
        ("not a literal", "expected a dict such as {'as_of': '2026-10-16'}"),
        ("{'asof': '2026-10-16'}", "unknown key 'asof'"),
    ],
)
def test_invalid_config_reports_once_and_skips(config, problem):
    errs = _with_config(config, leg("2026-09-02", "-1", PUT) + later("2026-11-01"))
    assert [e.message for e in errs] == [
        f"Invalid configuration for check_option_expiry plugin: {problem}; skipping."
    ]
    assert errs[0].entry is None
    assert (errs[0].source["filename"], errs[0].source["lineno"]) == ("<check_option_expiry>", 0)


def test_as_of_after_surrounding_whitespace():
    # literal_eval strips leading blanks only from Python 3.10 on.
    errs = _with_config(" \t{'as_of': '2026-10-17'}\t", leg("2026-09-02", "-1", PUT))
    assert [e.message.split()[0] for e in errs] == [PUT]


def test_repeated_key_takes_its_last_value():
    config = "{'as_of': 'bad', 'as_of': '2026-10-17'}"
    errs = _with_config(config, leg("2026-09-02", "-1", PUT))
    assert [e.message.split()[0] for e in errs] == [PUT]


def test_position_opened_after_as_of_is_not_seen():
    # The put expired before as_of but was opened after it: without the
    # date filter it would be reported.
    body = later("2026-09-01") + leg("2026-11-01", "-1", PUT)
    assert _with_config("{'as_of': '2026-10-20'}", body) == []


@pytest.mark.parametrize("text", ["it's", "a\\b", 'say "hi"'])
def test_quotes_and_backslashes_are_never_quoted_back(text):
    # Called directly: beancount's own string parser would consume these.
    _, problem = plugin_module._parse_config(repr({"as_of": text}))
    assert problem == "as_of must be a date written YYYY-MM-DD"


def test_error_details_match_the_message():
    (err,) = errors(
        leg("2026-09-02", "-3", PUT) + leg("2026-09-10", "1", PUT) + later("2026-11-01")
    )
    assert err.details == {
        "symbol": PUT,
        "account": "Assets:Broker:Options",
        "expiry": datetime.date(2026, 10, 16),
        "net": Decimal(-2),
    }
    source, message, entry = err
    assert message.startswith(PUT)


def test_error_details_survive_pickling():
    # beancount's loader cache pickles errors.
    (err,) = errors(leg("2026-09-02", "-1", PUT) + later("2026-11-01"))
    copy = pickle.loads(pickle.dumps(err))
    assert copy == err
    assert copy.details == err.details


def test_config_error_has_no_details():
    (err,) = _with_config("[]", "")
    assert err.details is None
