# beancount-options

Beancount plugins that check option contracts written in a Beancount ledger:

- `check_option_expiry`: reports a contract that still has open units after it expires.
- `check_option_assignment` (planned, not yet in the package): checks an assigned short option (shares per contract, no gain on the option leg, and the stock's basis or proceeds of strike ∓ premium).

The plugins only check and report. They never generate entries.

These are the Python counterparts of native plugins in [rustledger](https://github.com/rustledger/rustledger). The same `plugin` line runs this package under `bean-check` and Fava, and rustledger's native implementation under `rledger`:

```beancount
plugin "beancount_options.check_option_expiry"
```

Add the `check_option_assignment` line once that plugin ships. rustledger's CI runs both implementations on the same ledgers and requires identical results.

## Install

```sh
pip install git+https://github.com/rustledger/beancount-options
```

Requires Python 3.9+ and beancount 3.

## check_option_expiry

A posting is an option when its commodity is an OCC symbol with the spaces removed: a root of 1–6 letters, digits or dots starting with a letter (adjusted roots such as `XYZ1` are checked too), the expiry as `YYMMDD`, `C` or `P`, and the strike × 1000 as 8 digits. `AAPL261016P00230000` is the AAPL 2026-10-16 230 put. Other commodities are ignored.

The plugin tracks each contract's net units per account, netting each transaction's legs first. It reports a contract that still has units open when the ledger has an entry of any kind dated after its expiry, at the transaction that opened the current position (the last one that moved it off zero or across it). "Today" is the ledger's last entry date, not the clock, so every run gives the same answer. A close dated on the expiry day is fine.

```beancount
plugin "beancount_options.check_option_expiry"

2026-09-01 open Assets:Broker:Options
2026-09-01 open Assets:Broker:Cash
2026-09-01 open Income:Interest

2026-09-02 * "Sell to open"
  Assets:Broker:Options  -1 AAPL261016P00230000 {250.00 USD}
  Assets:Broker:Cash      250.00 USD

2026-10-20 * "Interest"
  Assets:Broker:Cash        1.00 USD
  Income:Interest          -1.00 USD
```

`bean-check` reports, at the sell-to-open line:

```
ledger.beancount:7: AAPL261016P00230000 expired 2026-10-16 but -1 contract(s) remain open in Assets:Broker:Options
```

### Configuration

The plugin takes one optional setting, `as_of`, as a Python dict literal:

```beancount
plugin "beancount_options.check_option_expiry" "{'as_of': '2026-10-16'}"
```

`as_of` sets "today" instead of the last entry's date, written `YYYY-MM-DD`. Entries dated after it are ignored, so forecast or scheduled transactions neither move "today" forward nor close a position early. A contract that expired before `as_of` is reported even if the ledger has no later entry. A config that is not a dict literal, has any other key, or writes the date another way is reported as an invalid configuration, and the plugin then skips.

`check_option_assignment` is coming in a later release.

## Development

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Status: `check_option_expiry` is available; `check_option_assignment` is next. Design: [rustledger#2555](https://github.com/rustledger/rustledger/issues/2555).

## License

GPL-2.0-or-later. See [LICENSE](LICENSE). This package imports Beancount, which is GPL-2.0-only, so it is not licensed under GPL-3.0-only like rustledger.
