# SPDX-License-Identifier: GPL-2.0-or-later
"""Differential test: random ledgers through bean-check and rledger.

Generates ledgers with option legs (sign changes, legs netted in one entry,
several accounts, same-day entries, non-transaction last entries, OCC
look-alikes), runs `bean-check` with this package and `rledger check` with
its native plugin, and compares the errors line for line.

    uv run --with beancount --with-editable . python scripts/differential.py \\
        --rledger /path/to/rledger --runs 300 --out /tmp/diff

Exits 1 and keeps each mismatching ledger in --out when the tools disagree.
"""

import argparse
import datetime
import pathlib
import random
import re
import subprocess
import sys

ACCOUNTS = ["Assets:Broker:Options", "Assets:IRA:Options"]
SYMBOLS = [
    "AAPL261016P00230000",
    "AAPL261016C00230000",
    "SPY261120C00600000",
    "BRK.B261016P00450000",
    "XYZ1261016C00010000",
    "F261218P00012000",
    "GOOGL1261016C00150000",
    "TSLA260930P00250000",
    "AAPL261316P00230000",  # month 13: not an option, ignored
    "AAPL261016P0023000",  # 7 strike digits: not an option
]
START = datetime.date(2026, 9, 1)
# `<file>:<line>: message` (bean-check) or `<file>:<line>:<col>: error[...]:
# message` (rledger). Only errors located in the generated file are compared;
# the exit status covers the rest.
ERROR_LINE = re.compile(r"(?P<name>[^\s:\[]+\.beancount):(?P<line>\d+):(?:\d+:)?\s+(?P<msg>.*)")


def ledger(rng, include=None):
    """A random ledger. With ``include`` (a file name), about half the trades
    go to a second text for that file, on eight shared dates so same-day
    trades land in both files. Returns (main text, included text or None).
    """
    # With an include, trades share a few dates spread over the whole range,
    # so same-day trades land in both files on either side of expiries.
    pool = rng.sample(range(1, 121), 8) if include else range(1, 121)
    other = []
    lines = [
        'plugin "beancount_options.check_option_expiry"',
        "",
        f"{START} open Assets:Broker:Options",
        f"{START} open Assets:IRA:Options",
        f"{START} open Assets:Broker:Cash",
        f"{START} open Income:Misc",
        "",
    ]
    symbols = rng.sample(SYMBOLS, rng.randint(1, 4))
    for _ in range(rng.randint(1, 12)):
        date = START + datetime.timedelta(days=rng.choice(pool))
        target = other if include and rng.random() < 0.5 else lines
        target.append(f'{date} * "trade"')
        for _ in range(rng.randint(1, 3)):
            units = rng.choice([-3, -2, -1, 1, 2, 3, 100, -100, "0.5", "-1.50"])
            target.append(f"  {rng.choice(ACCOUNTS)}  {units} {rng.choice(symbols)} @ 1.00 USD")
        target.append("  Assets:Broker:Cash")
        target.append("")
    if include:
        lines += [f'include "{include}"', ""]
    end = START + datetime.timedelta(days=rng.randint(30, 150))
    kind = rng.choice(["txn", "price", "note", "none"])
    if kind == "txn":
        lines += [f'{end} * "interest"', "  Assets:Broker:Cash  1.00 USD", "  Income:Misc", ""]
    elif kind == "price":
        lines += [f"{end} price AAPL 230.00 USD", ""]
    elif kind == "note":
        lines += [f'{end} note Assets:Broker:Cash "hello"', ""]
    return "\n".join(lines), ("\n".join(other) if include else None)


def errors(text, names):
    """``file:line: message`` for each error located in one of ``names``."""
    out = []
    for raw in text.splitlines():
        m = ERROR_LINE.search(raw)
        name = pathlib.Path(m["name"]).name if m else None
        if name in names:
            msg = re.sub(r"^error\[[A-Z0-9_]+\]:", "", m["msg"]).strip()
            out.append(f"{name}:{m['line']}: {msg}")
    return out


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, p.stdout + p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rledger", required=True)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--includes",
        action="store_true",
        help="split trades across an included file, with same-day trades in both",
    )
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        sys.exit(f"{out} is not empty; give a new or empty directory")
    # This interpreter's beancount, which has this package installed.
    bean_check = [sys.executable, "-m", "beancount.scripts.check"]
    mismatches = with_errors = 0
    for i in range(args.runs):
        seed = args.seed + i
        path = out / f"case{seed}.beancount"
        inc = path.with_name(f"case{seed}-inc.beancount")
        main_text, inc_text = ledger(random.Random(seed), inc.name if args.includes else None)
        path.write_text(main_text)
        if inc_text is not None:
            inc.write_text(inc_text)
        names = {path.name, inc.name}
        py_ok, py_text = run([*bean_check, str(path)])
        rs_ok, rs_text = run([args.rledger, "check", str(path)])
        py, rs = errors(py_text, names), errors(rs_text, names)
        with_errors += bool(py)
        if py != rs or py_ok != rs_ok:
            mismatches += 1
            path.with_suffix(".diff").write_text(
                f"bean-check ok={py_ok}\n"
                + "\n".join(py)
                + f"\n\nrledger ok={rs_ok}\n"
                + "\n".join(rs)
                + "\n"
            )
        else:
            path.unlink()
            inc.unlink(missing_ok=True)
            # bean-check's pickle cache, a dotfile next to the ledger.
            path.with_name(f".{path.name}.cache").unlink(missing_ok=True)
    print(f"{args.runs} ledgers, {with_errors} with errors, {mismatches} mismatches -> {out}")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
