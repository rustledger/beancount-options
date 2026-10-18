# SPDX-License-Identifier: GPL-2.0-or-later
"""Error on option contracts still open after their expiry date.

Option contracts are recognized by their OCC symbol (see ``_occ``). Every
other commodity is ignored.

"Today" is the date of the last entry of any kind in the ledger, not the system clock,
so the result is the same on every run. A contract is reported when its net
units in an account are non-zero and the ledger contains an entry dated
after its expiry. A close dated on the expiry day itself is fine.

The optional config ``"{'as_of': 'YYYY-MM-DD'}"`` sets "today" instead.
Entries dated after it are ignored, so forecast or scheduled transactions
neither move "today" forward nor close a position early.

The plugin only reports: entries are returned unchanged, so a ledger stays
complete without it.

Python counterpart of rustledger's native ``check_option_expiry``; messages
and locations match it exactly without a config, except that two same-day
trades in different included files can be ordered differently. The native
plugin does not read ``as_of`` yet.
"""

import ast
import datetime
import re
from collections import namedtuple
from decimal import Decimal

from beancount.core import data

from beancount_options._format import plain
from beancount_options._occ import parse_occ

__plugins__ = ("check_option_expiry",)

_ErrorFields = namedtuple("OptionExpiryError", "source message entry")


class OptionExpiryError(_ErrorFields):
    """A beancount error: ``source``, ``message`` and ``entry``, as usual.

    ``details`` carries the same facts as the message for programs, so they
    need not parse it: a dict with ``symbol``, ``account``, ``expiry`` (a
    date) and ``net`` (a Decimal), or None for a configuration error. It is
    an attribute, not a fourth field, so code that unpacks
    ``source, message, entry`` keeps working. ``_replace`` and ``_make``
    give a copy whose ``details`` is None.
    """

    details = None

    def __new__(cls, source, message, entry, details=None):
        self = super().__new__(cls, source, message, entry)
        self.details = details
        return self


# Exactly YYYY-MM-DD: fromisoformat accepts more forms from Python 3.11 on.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


# Whitespace around the config, spelled out: str.strip() also strips
# \x1c-\x1f and non-ASCII spaces, which other parsers do not.
_BLANK = " \t\n\r\f\v"
# A value is quoted back in a message only when it is this simple, so the
# message is the same on every Python version and in rustledger.
_QUOTABLE = re.compile(r"[ !#-&(-\[\]-~]{1,40}")


def _quoted(text):
    """`` 'text'`` for a short, plain ASCII string; empty otherwise."""
    return f" '{text}'" if _QUOTABLE.fullmatch(text) else ""


def _parse_config(config):
    """Read the plugin config.

    Returns ``(as_of, problem)``: the ``as_of`` date or None, and a
    description of what is wrong with the config, or None. A problem never
    echoes a value's Python repr, which differs between Python versions and
    runs; only a plain string (see ``_QUOTABLE``) is quoted back.
    """
    if config is None:
        return None, None
    if not isinstance(config, str):
        return None, "expected a dict such as {'as_of': '2026-10-16'}"
    config = config.strip(_BLANK)
    if not config:
        return None, None
    try:
        value = ast.literal_eval(config)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        value = None
    if not isinstance(value, dict):
        return None, "expected a dict such as {'as_of': '2026-10-16'}"
    if not all(isinstance(key, str) for key in value):
        return None, "keys must be strings"
    # The first unknown key as written; dicts keep insertion order, and a
    # repeated key keeps its first position and its last value.
    unknown = [key for key in value if key != "as_of"]
    if unknown:
        quoted = _quoted(unknown[0])
        return None, f"unknown key{quoted}" if quoted else "an unknown key"
    if "as_of" not in value:
        return None, None
    text = value["as_of"]
    if not isinstance(text, str):
        return None, "as_of must be a string"
    if _ISO_DATE.fullmatch(text):
        try:
            return datetime.date.fromisoformat(text), None
        except ValueError:
            pass
    problem = "as_of must be a date written YYYY-MM-DD"
    return None, f"{problem}, not{_quoted(text)}" if _quoted(text) else problem


class _Position:
    __slots__ = ("expiry", "net", "opened_by")

    def __init__(self, expiry, opened_by):
        self.expiry = expiry
        self.net = Decimal(0)
        # The entry that opened the current position: the last one that
        # moved it away from flat or across zero.
        self.opened_by = opened_by


def _entry_deltas(entry):
    """Net option units per (account, symbol) in one transaction.

    Netting first makes the result independent of posting order, and makes
    an entry whose legs cancel a no-op.
    """
    deltas = {}
    expiries = {}
    for posting in entry.postings:
        units = posting.units
        if units is None or not isinstance(units.number, Decimal):
            continue
        occ = parse_occ(units.currency)
        if occ is None:
            continue
        key = (posting.account, units.currency)
        deltas[key] = deltas.get(key, Decimal(0)) + units.number
        expiries[key] = occ.expiry
    return [(key, delta, expiries[key]) for key, delta in deltas.items()]


def check_option_expiry(entries, options_map, config=None):
    """Report option positions left open past expiry.

    Args:
      entries: the ledger's directives.
      options_map: the ledger's options (unused).
      config: an optional dict literal; ``as_of`` sets "today".
    Returns:
      The entries, unchanged, and a list of errors.
    """
    del options_map
    as_of, problem = _parse_config(config)
    if problem is not None:
        source = data.new_metadata("<check_option_expiry>", 0)
        message = f"Invalid configuration for check_option_expiry plugin: {problem}; skipping."
        return entries, [OptionExpiryError(source, message, None)]
    considered = entries if as_of is None else [e for e in entries if e.date <= as_of]

    # sorted() is stable, so same-day entries keep their order.
    positions = {}
    for entry in sorted(considered, key=lambda e: e.date):
        if not isinstance(entry, data.Transaction):
            continue
        for key, delta, expiry in _entry_deltas(entry):
            pos = positions.get(key)
            if pos is None:
                pos = positions[key] = _Position(expiry, entry)
            new_net = pos.net + delta
            if pos.net == 0 or (pos.net < 0 < new_net) or (new_net < 0 < pos.net):
                pos.opened_by = entry
            pos.net = new_net

    errors = []
    if as_of is not None:
        today = as_of
    elif entries:
        today = max(entry.date for entry in entries)
    else:
        today = None
    if today is not None:
        # Sorted by (account, symbol), the order rustledger reports them in.
        for (account, symbol), pos in sorted(positions.items(), key=lambda kv: kv[0]):
            if pos.net == 0 or pos.expiry >= today:
                continue
            message = (
                f"{symbol} expired {pos.expiry.isoformat()} but {plain(pos.net)} "
                f"contract(s) remain open in {account}"
            )
            details = {"symbol": symbol, "account": account, "expiry": pos.expiry, "net": pos.net}
            errors.append(OptionExpiryError(pos.opened_by.meta, message, pos.opened_by, details))
    return entries, errors
