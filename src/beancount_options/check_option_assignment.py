# SPDX-License-Identifier: GPL-2.0-or-later
"""Check that assigned short options fold the premium into the stock leg.

When a short option is assigned, the premium is not a separate gain. For
a short put, the stock's basis is strike x shares - premium. For a short
call, the sale proceeds are strike x shares + premium. This plugin checks
that the stock leg of an assignment says so.

An **assignment** is a transaction that reduces a short position in an
OCC option (see ``_occ``) and also moves the option's root commodity:
stock units rise for a put, fall for a call. Ordinary closes, long
options, cash-settled index options, and assignments split across two
transactions are not checked.

The plugin runs after booking, so a closing ``{}`` posting carries the
cost of the lot it closed: the premium. Each assignment gets at most one
error, from the first rule that fails:

1. Stock units = 100 x contracts.
2. The option leg realizes no gain (no price, or a price equal to cost).
3. The stock basis (put) or proceeds (call) match within half a cent per
   share, compared as totals across every booked lot. A put's stock leg
   needs a cost, and a call's a price; a call that opens a short stock
   position (no stock held in that account before) may give its proceeds
   as the short lot's cost instead.

With a commission the entry settles beyond the strike value, and rule 3
then accepts the range "fee expensed to fee folded" - but only when the
entry is plainly one assignment and its cash is an independent witness
(rustledger#2732): exactly one closing group, every cash posting written
rather than booking-interpolated, and no posting carrying a cost or
price outside the group's option and stock legs. The fee the window
allows is the one the entry's cash proves it moved. When a guard fails,
or there is no cash to measure a fee from, the exact figure is required:
the safe direction, since at worst a fee-folded ledger gets flagged.

Puts and calls on one root are separate assignments, so a put assigned
beside a call bought back in the same entry is still checked. Same-day
entries are taken in beancount's ``entry_sortkey`` order (date, type,
line number). The plugin reads no config: a non-blank config string gives
one notice.

Python counterpart of rustledger's native ``check_option_assignment``
(design: rustledger#2555). Messages and locations match it exactly for
amounts inside the native plugin's decimal range; outside it the native
plugin reports "assignment not checked", which this package does not
reproduce (documented gap). The other gap: beancount errors have no
severity, so the ignored-config notice is listed among ``bean-check``'s
errors.

The plugin only reports: every directive is kept and nothing is inserted.
"""

from collections import namedtuple
from decimal import Decimal
from typing import Optional

from beancount.core import data
from beancount.core.interpolate import AUTOMATIC_META

from beancount_options._format import plain
from beancount_options._occ import parse_occ

__plugins__ = ("check_option_assignment",)

# Shares delivered per standard equity option contract.
SHARES_PER_CONTRACT = Decimal(100)
# Half a cent per share: the rounding tolerance on rule 3.
_TOLERANCE_PER_SHARE = Decimal("0.005")

_ErrorFields = namedtuple("OptionAssignmentError", "source message entry")


class OptionAssignmentError(_ErrorFields):
    """A beancount error: ``source``, ``message`` and ``entry``, as usual.

    ``details`` carries the same facts as the message for programs, so
    they need not parse it: a dict naming the contracts and rule, or None
    for a configuration notice. It is an attribute, not a fourth field,
    so code that unpacks ``source, message, entry`` keeps working.
    ``_replace`` and ``_make`` give a copy whose ``details`` is None.
    """

    details = None

    def __new__(cls, source, message, entry, details=None):
        self = super().__new__(cls, source, message, entry)
        self.details = details
        return self


# Whitespace around a config, spelled out: str.strip() also strips
# \x1c-\x1f and non-ASCII spaces, which other parsers do not.
_BLANK = " \t\n\r\f\v"


def _config_is_set(config) -> bool:
    """Whether ``config`` is worth noticing: anything but ASCII blanks."""
    return isinstance(config, str) and config.strip(_BLANK) != ""


def _units_of(posting) -> Optional[Decimal]:
    """A posting's units as a number, or None."""
    units = posting.units
    return units.number if units is not None else None


def _cost_total(posting, units: Decimal) -> Optional[Decimal]:
    """Total cost of a posting's units, as a positive number, or None
    when it has no cost.

    A booked cost is per unit; a cost that left the parser as a total
    (``{{...}}``) was divided at booking.
    """
    cost = posting.cost
    number = getattr(cost, "number", None)
    if cost is None or not isinstance(number, Decimal):
        return None
    return abs(number * units)


def _price_total(posting, units: Decimal) -> Optional[Decimal]:
    """Total price of a posting's units, as a positive number, or None
    when it has no price.

    A price annotation is per unit: beancount divides a total price
    (``@@``) at parse time.
    """
    price = posting.price
    number = getattr(price, "number", None)
    if price is None or not isinstance(number, Decimal):
        return None
    return abs(number * units)


def _sum_totals(totals):
    """Sum of totals, or None when one of them is missing."""
    parts = list(totals)
    if any(part is None for part in parts):
        return None
    return sum(parts, Decimal(0))


def _is_cash_posting(posting, currency: str, roots) -> bool:
    """Whether ``posting`` is one of the entry's cash postings for the
    fee window: no cost, no price, in ``currency``, on an Assets or
    Liabilities account (rustledger#2732)."""
    units = posting.units
    return (
        posting.cost is None
        and posting.price is None
        and units is not None
        and units.currency == currency
        and posting.account.split(":")[0] in roots
    )


def _commission(postings, root: str, right, currency, strike_value, one_assignment, roots):
    """The commission the entry's cash proves it moved, floored at 0.

    Widens rule 3 from the exact figure to the range "fee expensed to fee
    folded", but only when the cash is an independent witness and the
    entry is one assignment: exactly one closing group; every cash
    posting written (a booking-interpolated figure is filled from the
    very residual this would measure); and no posting carrying a cost or
    price outside the group's option and stock legs (a sweep purchase, a
    foreign-currency settlement priced with ``@``, or a second trade
    would all move the cash). Any guard failing gives 0 - today's exact
    rule, the safe direction: at worst a fee-folded ledger gets flagged.
    """
    if not one_assignment or currency is None:
        return Decimal(0)
    for posting in postings:
        if posting.cost is None and posting.price is None:
            continue
        units = posting.units
        occ = parse_occ(units.currency) if units is not None else None
        group_leg = units is not None and (
            units.currency == root or (occ is not None and occ.root == root and occ.right == right)
        )
        if not group_leg:
            return Decimal(0)
    cash = Decimal(0)
    for posting in postings:
        if not _is_cash_posting(posting, currency, roots):
            continue
        if posting.meta and posting.meta.get(AUTOMATIC_META):
            return Decimal(0)
        number = getattr(posting.units, "number", None)
        if not isinstance(number, Decimal):
            return Decimal(0)
        cash += number
    fee = -cash - strike_value if right == "P" else strike_value - cash
    return max(fee, Decimal(0))


class _Closing:
    """Option legs of one transaction that close short contracts of one
    right on one underlying."""

    __slots__ = (
        "symbols",
        "contracts",
        "strike_value",
        "premium",
        "priced",
        "priced_cost",
        "premium_realized",
        "strikes",
        "currency",
    )

    def __init__(self):
        self.symbols = []
        self.contracts = Decimal(0)
        self.strike_value = Decimal(0)
        self.premium = Decimal(0)
        self.priced = Decimal(0)
        self.priced_cost = Decimal(0)
        self.premium_realized = False
        self.strikes = []
        self.currency = None

    def add(self, units, strike, premium, price):
        """Add one option leg closing ``units`` contracts at ``strike``.

        ``premium`` is the leg's total cost; ``price`` its total price,
        or None when it has no price.
        """
        self.contracts += units
        self.strike_value += strike * units * SHARES_PER_CONTRACT
        self.premium += premium
        if price is None:
            return
        self.priced += price
        self.priced_cost += premium
        if premium != price:
            self.premium_realized = True


def _money(value: Decimal) -> str:
    """``value`` with two decimals, as rustledger prints amounts."""
    return f"{value:.2f}"


def _check(root, right, closing, postings, held, one_assignment, roots):
    """What one closing group gives: the message and details of the first
    rule that fails, or None when it is not an assignment or passes every
    rule."""
    stock = [
        (posting, _units_of(posting))
        for posting in postings
        if posting.units is not None
        and posting.units.currency == root
        and _units_of(posting) is not None
    ]
    stock_units = sum((units for _, units in stock), Decimal(0))
    assigned = stock_units > 0 if right == "P" else stock_units < 0
    if not assigned:
        return None

    label = ", ".join(closing.symbols)
    base = {"symbols": list(closing.symbols), "root": root, "right": right}
    currency = closing.currency or ""
    shares = abs(stock_units)
    expected_shares = closing.contracts * SHARES_PER_CONTRACT
    if shares != expected_shares:
        return (
            f"{label} assignment: {plain(closing.contracts)} contract(s) but "
            f"{plain(shares)} {root}; expected {plain(expected_shares)}",
            dict(base, contracts=closing.contracts, shares=shares, expected_shares=expected_shares),
        )
    if closing.premium_realized:
        return (
            f"{label} assignment: option leg priced at {_money(closing.priced)} {currency}, "
            f"not its cost {_money(closing.priced_cost)} {currency}; close it at cost and "
            f"fold the premium into the {root} leg",
            dict(base, priced=closing.priced, priced_cost=closing.priced_cost, currency=currency),
        )
    strike_value = closing.strike_value
    premium = closing.premium
    if len(closing.strikes) == 1:
        breakdown = f"strike {plain(closing.strikes[0])} × {plain(expected_shares)}"
    else:
        breakdown = f"strike value {_money(strike_value)}"
    tolerance = _TOLERANCE_PER_SHARE * shares
    # Rule 3's acceptance band. ``fee`` widens it to "fee expensed to fee
    # folded" when the entry's cash proves a commission (rustledger#2732);
    # 0 keeps the exact rule.
    fee = _commission(postings, root, right, closing.currency, strike_value, one_assignment, roots)

    def outside(actual, expected):
        """Whether ``actual`` falls outside the band."""
        if right == "P":
            lo = expected - tolerance
            hi = expected + fee + tolerance
        else:
            lo = expected - fee - tolerance
            hi = expected + tolerance
        return actual < lo or actual > hi

    if right == "P":
        expected = strike_value - premium
        basis = _sum_totals(_cost_total(posting, units) for posting, units in stock)
        if basis is None:
            return (
                f"{label} assignment: {root} purchase has no cost; "
                f"expected basis {_money(expected)} {currency}",
                dict(base, expected=expected, currency=currency),
            )
        if not outside(basis, expected):
            return None
        if fee > 0:
            window_hi = expected + fee
            message = (
                f"{label} assignment: {root} basis {_money(basis)} {currency}, expected "
                f"{_money(expected)} to {_money(window_hi)} {currency} ({breakdown} "
                f"\N{MINUS SIGN} premium {_money(premium)}, + up to {_money(fee)} in fees)"
            )
        else:
            message = (
                f"{label} assignment: {root} basis {_money(basis)} {currency}, expected "
                f"{_money(expected)} {currency} ({breakdown} "
                f"\N{MINUS SIGN} premium {_money(premium)})"
            )
        return message, dict(
            base, basis=basis, expected=expected, premium=premium, currency=currency, fee=fee
        )

    expected = strike_value + premium

    def proceeds(posting, units):
        price = _price_total(posting, units)
        # A sale with no price that opens (or extends) a short position
        # gives its proceeds as the short lot's cost.
        if price is None and posting.cost is not None:
            position = held(posting.account, root)
            if position <= 0:
                return _cost_total(posting, units)
        return price

    total = _sum_totals(proceeds(posting, units) for posting, units in stock)
    if total is None:
        return (
            f"{label} assignment: {root} sale has no price; "
            f"expected proceeds {_money(expected)} {currency}",
            dict(base, expected=expected, currency=currency),
        )
    if not outside(total, expected):
        return None
    if fee > 0:
        window_lo = expected - fee
        message = (
            f"{label} assignment: {root} proceeds {_money(total)} {currency}, expected "
            f"{_money(window_lo)} to {_money(expected)} {currency} ({breakdown} + premium "
            f"{_money(premium)}, \N{MINUS SIGN} up to {_money(fee)} in fees)"
        )
    else:
        message = (
            f"{label} assignment: {root} proceeds {_money(total)} {currency}, expected "
            f"{_money(expected)} {currency} ({breakdown} + premium {_money(premium)})"
        )
    return message, dict(
        base, proceeds=total, expected=expected, premium=premium, currency=currency, fee=fee
    )


def check_option_assignment(entries, options_map, config=None):
    """Report assignments that do not fold the premium into the stock leg.

    Args:
      entries: the ledger's directives, booked.
      options_map: the ledger's options; the account type names say
        which accounts hold settlement cash.
      config: optional; this plugin has no options. Anything but blanks
        gets one notice.
    Returns:
      The entries, unchanged, and a list of errors.
    """
    errors = []
    if _config_is_set(config):
        source = data.new_metadata("<check_option_assignment>", 0)
        message = "check_option_assignment: the config is ignored; this plugin has no options"
        errors.append(OptionAssignmentError(source, message, None))
    roots = (
        options_map.get("name_assets", "Assets"),
        options_map.get("name_liabilities", "Liabilities"),
    )

    # The option symbols and their roots: the only commodities tracked.
    tracked = set()
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            units = posting.units
            if units is None:
                continue
            occ = parse_occ(units.currency)
            if occ is None:
                continue
            tracked.add(units.currency)
            for other in entry.postings:
                if other.units is not None and other.units.currency == occ.root:
                    tracked.add(occ.root)
                    break
    if not tracked:
        return entries, errors

    # Walk directives in beancount's entry_sortkey order (date, type,
    # line number). The loader keeps file order for same-day entries;
    # beancount does not.
    net = {}

    def held(account, commodity, _net=net):
        return _net.get((account, commodity), Decimal(0))

    for entry in sorted(entries, key=data.entry_sortkey):
        if not isinstance(entry, data.Transaction):
            continue

        # Option legs that close short contracts, by underlying and right.
        closings = {}
        for posting in entry.postings:
            units = _units_of(posting)
            if units is None:
                continue
            symbol = posting.units.currency
            occ = parse_occ(symbol)
            if occ is None:
                continue
            position = net.get((posting.account, symbol), Decimal(0))
            if position >= 0 or units <= 0:
                continue
            premium = _cost_total(posting, units)
            if premium is None:
                # No known premium: not a closing the rules can read.
                continue
            price = _price_total(posting, units)
            closing = closings.setdefault((occ.root, occ.right), _Closing())
            if symbol not in closing.symbols:
                closing.symbols.append(symbol)
            if occ.strike not in closing.strikes:
                closing.strikes.append(occ.strike)
            if closing.currency is None and posting.cost is not None:
                closing.currency = posting.cost.currency
            closing.add(units, occ.strike, premium, price)

        for (root, right), closing in sorted(closings.items()):
            found = _check(
                root,
                right,
                closing,
                entry.postings,
                held,
                len(closings) == 1,
                roots,
            )
            if found is None:
                continue
            message, details = found
            errors.append(OptionAssignmentError(entry.meta, message, entry, details))

        # Apply this transaction's tracked legs to the running positions.
        for posting in entry.postings:
            units = _units_of(posting)
            if units is None:
                continue
            commodity = posting.units.currency
            if commodity in tracked:
                key = (posting.account, commodity)
                net[key] = net.get(key, Decimal(0)) + units
    return entries, errors
