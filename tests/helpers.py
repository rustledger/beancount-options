# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers: load a ledger through beancount with a plugin enabled."""

import textwrap

from beancount import loader

HEADER = """\
2026-01-01 open Assets:Broker:Options
2026-01-01 open Assets:Broker:Cash
2026-01-01 open Assets:Broker:Stock
2026-01-01 open Assets:IRA:Options
2026-01-01 open Expenses:Fees
2026-01-01 open Expenses:Broker:Commission
2026-01-01 open Income:Options
2026-01-01 open Income:Trading:Gains
"""


def load(plugin, body):
    """Load ``body`` after the standard opens, with ``plugin`` enabled.

    Returns (errors from the plugin, every other error, entries).
    """
    text = f'plugin "beancount_options.{plugin}"\n\n{HEADER}\n{textwrap.dedent(body)}'
    entries, errors, _ = loader.load_string(text)
    ours = [e for e in errors if type(e).__module__.startswith("beancount_options")]
    others = [e for e in errors if e not in ours]
    return ours, others, entries


def leg(date, units, symbol, account="Assets:Broker:Options"):
    """One option leg against cash, at a price so the transaction balances."""
    cash = -int(units) * 100
    return f"""
{date} * "option leg"
  {account}  {units} {symbol} @ 100 USD
  Assets:Broker:Cash  {cash} USD
"""


def later(date):
    """A transaction with no option postings, moving the last date forward."""
    return f"""
{date} * "unrelated"
  Assets:Broker:Cash  -1.00 USD
  Expenses:Fees  1.00 USD
"""


def leg_at_cost(date, units, symbol, cost="300.00", account="Assets:Broker:Options"):
    """One option leg at cost, the rustledger#2555 convention.

    Opens with ``{cost USD}``; a close (``cost=None``) uses ``{}`` and books
    the difference to Income:Options.
    """
    if cost is None:
        return f"""
{date} * "close at cost"
  {account}  {units} {symbol} {{}} @ 100 USD
  Assets:Broker:Cash  {-int(units) * 100} USD
  Income:Options
"""
    total = -int(units) * float(cost)
    return f"""
{date} * "open at cost"
  {account}  {units} {symbol} {{{cost} USD}}
  Assets:Broker:Cash  {total:.2f} USD
"""
