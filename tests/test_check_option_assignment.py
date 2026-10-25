# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for check_option_assignment.

Every fixture goes through the loader, so it balances at beancount's
weight (cost first, then price, then units); an elided
``Income:Trading:Gains`` leg takes whatever residual is left and
disappears when there is none. Cash is never adjusted: the fee window
reads it.
"""

import pickle
from decimal import Decimal

from beancount import loader
from helpers import HEADER, load

from beancount_options import check_option_assignment as plugin_module

PUT = "AAPL261016P00225000"
CALL = "AAPL261016C00280000"
PUT_CLOSE = f"Assets:Broker:Options   1 {PUT} {{300.00 USD}}"


def errors(body):
    ours, others, _ = load("check_option_assignment", body)
    assert others == [], others
    return ours


def txn(date, narration, *postings, balance=True):
    """One transaction; ``balance`` adds an elided Gains leg."""
    body = "".join(f"  {line}\n" for line in postings)
    if balance:
        body += "  Income:Trading:Gains\n"
    return f'{date} * "{narration}"\n{body}'


def sto(date, symbol, premium):
    """Sell to open one contract at ``premium``, its whole premium."""
    return txn(
        date,
        "sell to open",
        f"Assets:Broker:Options  -1 {symbol} {{{premium} USD}}",
        f"Assets:Broker:Cash  {premium} USD",
    )


def put_assignment(stock_leg, *extra, cash="-22500.00", option_leg=PUT_CLOSE):
    """Assign the short put: close it at cost, take the stock leg."""
    return txn(
        "2026-10-16",
        "assignment",
        option_leg,
        stock_leg,
        *extra,
        f"Assets:Broker:Cash  {cash} USD",
    )


def call_setup():
    """Buy 100 shares, then sell one call against them."""
    return txn(
        "2026-01-02",
        "buy stock",
        "Assets:Broker:Stock   100 AAPL {200.00 USD}",
        "Assets:Broker:Cash  -20000.00 USD",
    ) + sto("2026-09-04", CALL, "220.00")


def call_assignment(stock_leg, *extra, cash="28000.00"):
    """Assign the short call: close it at cost, give up the stock leg."""
    return call_setup() + txn(
        "2026-10-16",
        "assignment",
        f"Assets:Broker:Options   1 {CALL} {{220.00 USD}}",
        stock_leg,
        *extra,
        f"Assets:Broker:Cash  {cash} USD",
    )


def sold_aapl(price=None):
    leg = "Assets:Broker:Stock  -100 AAPL {200.00 USD}"
    return leg if price is None else f"{leg} @ {price} USD"


def test_put_assignment_folds_the_premium_into_the_basis():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {222.00 USD}"
    )
    assert errors(body) == []


def test_put_basis_at_strike_is_off_by_the_premium():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {225.00 USD}"
    )
    (err,) = errors(body)
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 22500.00 USD, expected 22200.00 USD "
        "(strike 225 × 100 − premium 300.00)"
    )
    assert err.source["lineno"] == err.entry.meta["lineno"]


def test_call_assignment_folds_the_premium_into_the_proceeds():
    assert errors(call_assignment(sold_aapl("282.20"))) == []


def test_call_proceeds_at_strike_are_off_by_the_premium():
    (err,) = errors(call_assignment(sold_aapl("280.00")))
    assert err.message == (
        "AAPL261016C00280000 assignment: AAPL proceeds 28000.00 USD, expected 28220.00 USD "
        "(strike 280 × 100 + premium 220.00)"
    )


def test_a_total_price_is_read_as_its_total():
    assert errors(call_assignment(f"{sold_aapl()} @@ 28220.00 USD")) == []
    (err,) = errors(call_assignment(f"{sold_aapl()} @@ 28000.00 USD"))
    assert err.message.startswith("AAPL261016C00280000 assignment: AAPL proceeds 28000.00 USD")


def test_option_leg_priced_below_its_cost_realizes_the_premium():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {225.00 USD}",
        option_leg="Assets:Broker:Options   1 AAPL261016P00225000 {300.00 USD} @ 0.00 USD",
    )
    (err,) = errors(body)
    assert err.message == (
        "AAPL261016P00225000 assignment: option leg priced at 0.00 USD, not its cost 300.00 USD; "
        "close it at cost and fold the premium into the AAPL leg"
    )


def test_option_leg_priced_at_its_cost_is_not_a_gain():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {222.00 USD}",
        option_leg="Assets:Broker:Options   1 AAPL261016P00225000 {300.00 USD} @ 300.00 USD",
    )
    assert errors(body) == []


def test_share_count_is_hundred_per_contract():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   50 AAPL {225.00 USD}", cash="-11250.00"
    )
    (err,) = errors(body)
    assert err.message == "AAPL261016P00225000 assignment: 1 contract(s) but 50 AAPL; expected 100"


def test_two_lots_are_compared_as_a_total():
    def case(*stock_legs):
        return (
            sto("2026-09-03", PUT, "300.00")
            + sto("2026-09-10", PUT, "250.00")
            + txn(
                "2026-10-16",
                "assignment",
                f"Assets:Broker:Options   1 {PUT} {{300.00 USD}}",
                f"Assets:Broker:Options   1 {PUT} {{250.00 USD}}",
                *stock_legs,
                "Assets:Broker:Cash  -45000.00 USD",
            )
        )

    correct = case(
        "Assets:Broker:Stock   100 AAPL {222.00 USD}",
        "Assets:Broker:Stock   100 AAPL {222.50 USD}",
    )
    assert errors(correct) == []
    (err,) = errors(case("Assets:Broker:Stock   200 AAPL {225.00 USD}"))
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 45000.00 USD, expected 44450.00 USD "
        "(strike 225 × 200 − premium 550.00)"
    )


def test_two_strikes_are_checked_together():
    other = "AAPL261016P00230000"

    def case(basis):
        return (
            sto("2026-09-03", PUT, "300.00")
            + sto("2026-09-03", other, "400.00")
            + txn(
                "2026-10-16",
                "assignment",
                f"Assets:Broker:Options   1 {PUT} {{300.00 USD}}",
                f"Assets:Broker:Options   1 {other} {{400.00 USD}}",
                f"Assets:Broker:Stock   200 AAPL {{{basis} USD}}",
                "Assets:Broker:Cash  -45500.00 USD",
            )
        )

    assert errors(case("224.00")) == []
    (err,) = errors(case("227.50"))
    assert err.message == (
        "AAPL261016P00225000, AAPL261016P00230000 assignment: AAPL basis 45500.00 USD, "
        "expected 44800.00 USD (strike value 45500.00 − premium 700.00)"
    )


def test_tolerance_is_half_a_cent_per_share():
    def case(basis):
        return sto("2026-09-03", PUT, "237.50") + put_assignment(
            f"Assets:Broker:Stock   100 AAPL {{{basis} USD}}",
            option_leg=f"Assets:Broker:Options   1 {PUT} {{237.50 USD}}",
        )

    assert errors(case("222.63")) == []
    assert errors(case("222.62")) == []
    assert len(errors(case("222.64"))) == 1
    assert len(errors(case("222.61"))) == 1


def test_call_tolerance_is_half_a_cent_per_share():
    def case(total):
        return errors(call_assignment(f"{sold_aapl()} @@ {total} USD"))

    assert case("28220.50") == []
    assert case("28219.50") == []
    assert len(case("28220.51")) == 1
    assert len(case("28219.49")) == 1


def test_ordinary_close_is_not_an_assignment():
    body = sto("2026-09-03", PUT, "300.00") + txn(
        "2026-09-20",
        "buy to close",
        f"Assets:Broker:Options   1 {PUT} {{300.00 USD}} @ 100.00 USD",
        "Assets:Broker:Cash  -100.00 USD",
    )
    assert errors(body) == []


def test_protective_put_is_not_an_assignment():
    body = txn(
        "2026-09-03",
        "protective put",
        f"Assets:Broker:Options   1 {PUT} {{500.00 USD}}",
        "Assets:Broker:Stock   100 AAPL {230.00 USD}",
        "Assets:Broker:Cash  -23500.00 USD",
    )
    assert errors(body) == []


def test_exercising_a_long_put_is_not_checked():
    body = (
        txn(
            "2026-01-02",
            "buy stock",
            "Assets:Broker:Stock   100 AAPL {200.00 USD}",
            "Assets:Broker:Cash  -20000.00 USD",
        )
        + txn(
            "2026-09-03",
            "buy put",
            f"Assets:Broker:Options   1 {PUT} {{500.00 USD}}",
            "Assets:Broker:Cash  -500.00 USD",
        )
        + txn(
            "2026-10-16",
            "exercise",
            f"Assets:Broker:Options  -1 {PUT} {{500.00 USD}}",
            "Assets:Broker:Stock  -100 AAPL {200.00 USD} @ 999.00 USD",
            "Assets:Broker:Cash  22500.00 USD",
        )
    )
    assert errors(body) == []


def test_the_stock_leg_must_move_in_the_assignments_direction():
    body = (
        txn(
            "2026-01-02",
            "buy stock",
            "Assets:Broker:Stock   100 AAPL {200.00 USD}",
            "Assets:Broker:Cash  -20000.00 USD",
        )
        + sto("2026-09-03", PUT, "300.00")
        + txn(
            "2026-10-16",
            "stock sale beside the close",
            f"Assets:Broker:Options   1 {PUT} {{300.00 USD}} @ 100.00 USD",
            sold_aapl("230.00"),
            "Assets:Broker:Cash  22900.00 USD",
        )
    )
    assert errors(body) == []


def test_put_purchase_without_a_cost_is_reported():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL @ 225.00 USD"
    )
    (err,) = errors(body)
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL purchase has no cost; expected basis 22200.00 USD"
    )


def test_call_sale_without_a_price_is_reported():
    (err,) = errors(call_assignment(sold_aapl()))
    assert err.message == (
        "AAPL261016C00280000 assignment: AAPL sale has no price; expected proceeds 28220.00 USD"
    )


def test_an_option_held_without_cost_has_no_known_premium():
    body = txn(
        "2026-09-03",
        "sell to open",
        f"Assets:Broker:Options  -1 {PUT}",
        "Assets:Broker:Cash  300.00 USD",
    ) + txn(
        "2026-10-16",
        "assignment",
        f"Assets:Broker:Options   1 {PUT}",
        "Assets:Broker:Stock   100 AAPL {225.00 USD}",
        "Assets:Broker:Cash  -22500.00 USD",
    )
    assert errors(body) == []


def test_a_naked_call_opens_a_short_lot_and_gives_its_cost_as_proceeds():
    def case(cost):
        return sto("2026-09-04", CALL, "220.00") + txn(
            "2026-10-16",
            "assignment",
            f"Assets:Broker:Options   1 {CALL} {{220.00 USD}}",
            f"Assets:Broker:Stock  -100 AAPL {{{cost} USD}}",
            "Assets:Broker:Cash  28000.00 USD",
        )

    assert errors(case("282.20")) == []
    (err,) = errors(case("280.00"))
    assert err.message == (
        "AAPL261016C00280000 assignment: AAPL proceeds 28000.00 USD, expected 28220.00 USD "
        "(strike 280 × 100 + premium 220.00)"
    )

    # Shares held in another account are not the lot being sold here.
    body = (
        "2026-01-01 open Assets:Other:Stock\n"
        + txn(
            "2026-01-02",
            "buy stock elsewhere",
            "Assets:Other:Stock   100 AAPL {200.00 USD}",
            "Assets:Broker:Cash  -20000.00 USD",
        )
        + case("282.20")
    )
    assert errors(body) == []


def test_a_put_and_a_call_in_one_entry_are_separate_assignments():
    def case(basis):
        return (
            sto("2026-09-03", PUT, "300.00")
            + sto("2026-09-04", CALL, "220.00")
            + txn(
                "2026-10-16",
                "assignment",
                f"Assets:Broker:Options   1 {PUT} {{300.00 USD}}",
                f"Assets:Broker:Options   1 {CALL} {{220.00 USD}}",
                f"Assets:Broker:Stock   100 AAPL {{{basis} USD}}",
                "Assets:Broker:Cash  -22500.00 USD",
            )
        )

    assert errors(case("222.00")) == []
    (err,) = errors(case("225.00"))
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 22500.00 USD, expected 22200.00 USD "
        "(strike 225 × 100 − premium 300.00)"
    )


def test_a_total_cost_is_read_as_its_total():
    def case(total):
        stock = "Assets:Broker:Stock   100 AAPL {{" + total + " USD}}"
        return sto("2026-09-03", PUT, "300.00") + put_assignment(stock)

    assert errors(case("22200.00")) == []
    (err,) = errors(case("22500.00"))
    assert err.message.startswith("AAPL261016P00225000 assignment: AAPL basis 22500.00 USD")


def test_commission_expensed_and_folded_are_both_accepted():
    fee = "Expenses:Broker:Commission  5.00 USD"
    open_ = sto("2026-09-03", PUT, "300.00")
    assert (
        errors(
            open_
            + put_assignment("Assets:Broker:Stock   100 AAPL {222.00 USD}", fee, cash="-22505.00")
        )
        == []
    )
    assert (
        errors(
            open_ + put_assignment("Assets:Broker:Stock   100 AAPL {222.05 USD}", cash="-22505.00")
        )
        == []
    )
    assert (
        errors(
            open_
            + put_assignment(
                "Assets:Broker:Stock   100 AAPL {222.03 USD}",
                "Expenses:Broker:Commission  2.00 USD",
                cash="-22505.00",
            )
        )
        == []
    )
    assert errors(call_assignment(sold_aapl("282.20"), fee, cash="27995.00")) == []
    assert errors(call_assignment(sold_aapl("282.15"), cash="27995.00")) == []


def test_the_error_names_the_fee_window():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {225.00 USD}", cash="-22505.00"
    )
    (err,) = errors(body)
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 22500.00 USD, expected 22200.00 to "
        "22205.00 USD (strike 225 × 100 − premium 300.00, + up to 5.00 in fees)"
    )
    (err,) = errors(call_assignment(sold_aapl("283.00"), cash="27995.00"))
    assert err.message == (
        "AAPL261016C00280000 assignment: AAPL proceeds 28300.00 USD, "
        "expected 28215.00 to 28220.00 USD "
        "(strike 280 × 100 + premium 220.00, − up to 5.00 in fees)"
    )


def test_an_interpolated_cash_posting_falls_back_to_the_exact_rule():
    body = sto("2026-09-03", PUT, "300.00") + txn(
        "2026-10-16",
        "assignment",
        PUT_CLOSE,
        "Assets:Broker:Stock   100 AAPL {225.00 USD}",
        "Assets:Broker:Cash",
        balance=False,
    )
    (err,) = errors(body)
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 22500.00 USD, expected 22200.00 USD "
        "(strike 225 × 100 − premium 300.00)"
    )


def test_a_cost_outside_the_group_falls_back_to_the_exact_rule():
    open_ = sto("2026-09-03", PUT, "300.00")
    stock = "Assets:Broker:Stock   100 AAPL {222.05 USD}"
    # The folded fee is inside the window while the entry is one assignment
    # and its cash is an independent witness.
    assert errors(open_ + put_assignment(stock, cash="-22505.00")) == []
    # A sweep purchase moves the cash too: its cost is outside the group's
    # option and stock legs, so the window is off and the exact rule applies.
    (err,) = errors(
        open_ + put_assignment(stock, "Assets:Broker:Stock   10 QQQ {5.00 USD}", cash="-22555.00")
    )
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 22205.00 USD, expected 22200.00 USD "
        "(strike 225 × 100 − premium 300.00)"
    )


def test_two_closing_groups_fall_back_to_the_exact_rule():
    other = "MSFT261016C00500000"
    open_ = sto("2026-09-03", PUT, "300.00") + sto("2026-09-04", other, "220.00")
    stock = "Assets:Broker:Stock   100 AAPL {222.05 USD}"
    call_close = f"Assets:Broker:Options   1 {other} {{220.00 USD}}"
    # One closing group: the folded fee is accepted.
    assert errors(sto("2026-09-03", PUT, "300.00") + put_assignment(stock, cash="-22505.00")) == []
    # Two closing groups (a call bought back beside the put assigned) are not
    # one assignment, so the window is off and the exact rule applies.
    (err,) = errors(
        open_ + put_assignment(stock, call_close, cash="-22725.00"),
    )
    assert err.message == (
        "AAPL261016P00225000 assignment: AAPL basis 22205.00 USD, expected 22200.00 USD "
        "(strike 225 × 100 − premium 300.00)"
    )


def test_the_config_is_ignored_with_a_notice():
    def with_config(config, body):
        text = f'plugin "beancount_options.check_option_assignment" "{config}"\n\n{HEADER}{body}'
        _, errs, _ = loader.load_string(text)
        return errs

    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {222.00 USD}"
    )
    (notice,) = with_config("{'tolerance': 1}", body)
    assert notice.message == (
        "check_option_assignment: the config is ignored; this plugin has no options"
    )
    assert notice.details is None
    assert with_config(" \t", body) == []


def test_error_details_match_the_message_and_survive_pickling():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {225.00 USD}"
    )
    (err,) = errors(body)
    assert err.details == {
        "symbols": [PUT],
        "root": "AAPL",
        "right": "P",
        "basis": Decimal("22500.00"),
        "expected": Decimal("22200.00"),
        "premium": Decimal("300.00"),
        "currency": "USD",
        "fee": Decimal(0),
    }
    source, message, entry = err
    assert message.startswith(PUT)
    copy = pickle.loads(pickle.dumps(err))
    assert copy == err
    assert copy.details == err.details
    assert err._replace(message="x").details is None


def test_entries_returned_unchanged():
    body = sto("2026-09-03", PUT, "300.00") + put_assignment(
        "Assets:Broker:Stock   100 AAPL {222.00 USD}"
    )
    _, _, loaded = load("check_option_assignment", body)
    out, _ = plugin_module.check_option_assignment(loaded, {})
    assert out is loaded


def test_empty_ledger():
    assert plugin_module.check_option_assignment([], {}) == ([], [])
