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

The plugin tracks each contract's net units per account, netting each transaction's legs first. It reports a contract that still has units open when the ledger has an entry of any kind dated after its expiry, at the transaction that opened the current position (the last one that moved it off zero or across it). "Today" is the ledger's last entry date, not the clock, so every run gives the same answer. A close dated on the expiry day is fine. Each expiry error also carries a `details` attribute, a dict with `symbol`, `account`, `expiry` and `net`, for programs that would otherwise parse the message.

```beancount
plugin "beancount_options.check_option_expiry"

2026-09-01 open Assets:Broker:Options
2026-09-01 open Assets:Broker:Cash
2026-09-01 open Income:Interest
2026-09-01 open Income:Options

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

### Fixing the error

The error usually means the ledger never recorded what happened to the contract. Record it in the account that holds the contract:

- **Expired worthless:** on the expiry date, close the position at a price of zero. For the short put above, opened at cost:

  ```beancount
  2026-10-16 * "AAPL 230 put expired worthless"
    Assets:Broker:Options   1 AAPL261016P00230000 {} @ 0 USD
    Income:Options       -250.00 USD
  ```

  `{}` closes the lot the sale opened. The premium you kept, the lot's 250.00 USD, goes to income. For a long option the signs flip: `-1 … {} @ 0 USD`, and the premium you paid is a loss, booked to an expense or a negative income posting.
- **Assigned or exercised:** on the assignment date, close the option leg in the same transaction that moves the stock. `check_option_assignment`, coming next, will check that transaction's basis.
- **Closed or rolled earlier:** add the missing closing trade on the date it happened.

A position created through a `pad` entry is not seen: beancount fills pads after plugins run, so only transactions count.

### Configuration

The plugin takes one optional setting, `as_of`, as a Python dict literal:

```beancount
plugin "beancount_options.check_option_expiry" "{'as_of': '2026-10-16'}"
```

`as_of` sets "today" instead of the last entry's date, written `YYYY-MM-DD`. Entries dated after it are ignored, so forecast or scheduled transactions neither move "today" forward nor close a position early. A contract that expired before `as_of` is reported even if the ledger has no later entry. A config that is not a dict literal, has any other key, or writes the date another way is reported as an invalid configuration, and the plugin then skips.

rledger's native `check_option_expiry` does not read `as_of` yet: it ignores the config and always uses the last entry's date. Until it does, the same ledger can give different results under `rledger check` and `bean-check` when `as_of` is set.

`check_option_assignment` is coming in a later release.

## Development

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

`tests/cases/` holds config strings and ledgers with the exact errors each must give. They are meant to be shared with rustledger's native plugin's tests, so a rule change goes there first.

`scripts/differential.py` generates random ledgers and checks that `bean-check` and `rledger check` report the same errors, line for line:

```sh
uv run --with beancount --with-editable . python scripts/differential.py --rledger /path/to/rledger --runs 300 --out /tmp/diff
# --includes splits trades across an included file, with same-day trades in both
```

Status: `check_option_expiry` is available; `check_option_assignment` is next. Design: [rustledger#2555](https://github.com/rustledger/rustledger/issues/2555).

## License

GPL-2.0-or-later. See [LICENSE](LICENSE). This package imports Beancount, which is GPL-2.0-only, so it is not licensed under GPL-3.0-only like rustledger.
