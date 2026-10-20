# beancount-options

Beancount plugins that check option contracts written in a Beancount ledger:

- `check_option_expiry`: reports a contract that still has open units after it expires.
- `check_option_assignment`: checks an assigned short option (shares per contract, no gain on the option leg, and the stock's basis or proceeds of strike ∓ premium).

The plugins only check and report. They never generate entries.

These are the Python counterparts of native plugins in [rustledger](https://github.com/rustledger/rustledger). The same `plugin` line runs this package under `bean-check` and Fava, and rustledger's native implementation under `rledger`:

```beancount
plugin "beancount_options.check_option_expiry"
plugin "beancount_options.check_option_assignment"
```

rustledger's CI runs both implementations on the same ledgers and requires identical results, apart from the gaps noted below.

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
- **Assigned or exercised:** on the assignment date, close the option leg in the same transaction that moves the stock. `check_option_assignment` checks that transaction's basis.
- **Closed or rolled earlier:** add the missing closing trade on the date it happened.

A position created through a `pad` entry is not seen: beancount fills pads after plugins run, so only transactions count.

### Configuration

The plugin takes one optional setting, `as_of`, as a Python dict literal:

```beancount
plugin "beancount_options.check_option_expiry" "{'as_of': '2026-10-16'}"
```

`as_of` sets "today" instead of the last entry's date, written `YYYY-MM-DD`. Entries dated after it are ignored, so forecast or scheduled transactions neither move "today" forward nor close a position early. A contract that expired before `as_of` is reported even if the ledger has no later entry. A config that is not a dict literal, has any other key, or writes the date another way is reported as an invalid configuration, and the plugin then skips.

rledger's native `check_option_expiry` reads `as_of` by the same rules, with the same messages, once rustledger/rustledger#2711 lands. Until then it ignores the config, warns, and uses the last entry's date.

## check_option_assignment

When a short option is assigned the premium is not a separate gain: for a short put the stock's basis is strike × shares − premium, for a short call the sale proceeds are strike × shares + premium. The plugin checks that the stock leg of an assignment says so.

An **assignment** is a transaction that reduces a short position in an OCC option and also moves its root commodity: stock units rise for a put, fall for a call. Ordinary closes, long options, cash-settled index options, and assignments split across two transactions are not checked. Puts and calls on one root are separate assignments, so a put assigned beside a call bought back in the same entry is still checked.

The plugin runs after booking, so a closing `{}` leg carries the cost of the lot it closed: the premium. Each assignment gets at most one error, from the first rule that fails:

1. Stock units = 100 × contracts.
2. The option leg realizes no gain (no price, or a price equal to cost).
3. The stock basis (put) or proceeds (call) match within half a cent per share, compared as totals across every booked lot. A put's stock leg needs a cost, and a call's a price; a call that opens a short stock position may give its proceeds as the short lot's cost instead.

A commission settles the entry beyond the strike value, and rule 3 then accepts the range "fee expensed to fee folded", but only when the entry is plainly one assignment and its cash is an independent witness (rustledger#2732): exactly one closing group, every cash posting written rather than booking-interpolated, and no posting carrying a cost or price outside the group's option and stock legs. The fee the window allows is the one the entry's cash proves it moved. When a guard fails, the exact figure is required: at worst a fee-folded ledger gets flagged.

Each error also carries a `details` attribute, a dict with the contracts, the root and right, and the numbers the message compares, for programs that would otherwise parse the message.

```beancount
plugin "beancount_options.check_option_assignment"

2026-01-01 open Assets:Broker:Options
2026-01-01 open Assets:Broker:Stock
2026-01-01 open Assets:Broker:Cash
2026-01-01 open Income:Trading:Gains

2026-09-03 * "Sell to open AAPL 225 put"
  Assets:Broker:Options  -1 AAPL261016P00225000 {300.00 USD}
  Assets:Broker:Cash     300.00 USD

2026-10-16 * "AAPL 225 put assigned"
  Assets:Broker:Options   1 AAPL261016P00225000 {}
  Assets:Broker:Stock   100 AAPL {225.00 USD}
  Assets:Broker:Cash  -22500.00 USD
  Income:Trading:Gains  -300.00 USD
```

`bean-check` reports, at the assignment line:

```
ledger.beancount:12: AAPL261016P00225000 assignment: AAPL basis 22500.00 USD, expected 22200.00 USD (strike 225 × 100 − premium 300.00)
```

### Fixing the error

Book the premium as part of the stock leg, not as income. For the put above, the stock's cost is (225 × 100 − 300) / 100 = 222.00 USD a share:

```beancount
2026-10-16 * "AAPL 225 put assigned"
  Assets:Broker:Options   1 AAPL261016P00225000 {}
  Assets:Broker:Stock   100 AAPL {222.00 USD}
  Assets:Broker:Cash  -22500.00 USD
```

The other errors say what to do where they differ: give the stock leg a cost (a put) or a price (a call), close the option leg at cost with `{}` and no `@`, and move 100 shares per contract. With a commission, either expense it on its own leg or fold it into the stock's cost or price: both are inside the window rule 3 accepts.

### Configuration

The plugin has no options. A config string that is not blank is reported once as a notice; beancount has no warning severity, so it is listed among `bean-check`'s errors. That, and amounts whose arithmetic leaves `rust_decimal`'s range (where the native plugin reports `… assignment not checked: amounts exceed the supported decimal range`, and this package computes and checks them normally), are the two results that differ from the native plugin.

## Development

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

`tests/cases/` holds config strings and ledgers with the exact errors each must give. They are meant to be shared with rustledger's native plugin's tests, so a rule change goes there first.

`scripts/differential.py` generates random ledgers and checks that `bean-check` and `rledger check` report the same errors, line for line:

```sh
uv run --with 'beancount>=3,<4' --with-editable . python scripts/differential.py --rledger /path/to/rledger --runs 300 --out /tmp/diff
# --includes splits trades across an included file, with same-day trades in both
```

`--includes` needs an rledger that orders same-day entries the way beancount does (by line number, whatever the file), which rustledger#2661 does.

Status: `check_option_expiry` and `check_option_assignment` are available. Design: [rustledger#2555](https://github.com/rustledger/rustledger/issues/2555).

## License

GPL-2.0-or-later. See [LICENSE](LICENSE). This package imports Beancount, which is GPL-2.0-only, so it is not licensed under GPL-3.0-only like rustledger.
