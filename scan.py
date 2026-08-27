"""
scan.py — the daily term-structure scan.

For every ticker in universe.TIER_A / TIER_B: find the front (short) and back (long)
expiry for an ATM call calendar, read their implied vol, and compute the front/back
IV ratio. A flat name sits close to 1.0; a name pricing in a real event sits well
above it.

TIER_A front expiry is earnings-aware (see universe.earliest_valid_front_expiry) —
it must fall AFTER the earnings move has actually landed, otherwise the "front" leg
expires before the crush it's supposed to be capturing.

Liquidity guard: a contract with no two-sided quote, or a bid/ask spread wider than
MAX_SPREAD_PCT of the mid, is skipped rather than trusted — a wide/stale quote can
fake a big ratio.

Writes a timestamped run to log/scan_<UTC-ts>.json — that log is what the decision
log / dashboard reads. Places no orders. Run this, read the ranked table, decide.

Usage:
  python scan.py                    # full universe
  python scan.py --tickers AVGO,DELL
"""
import argparse
import datetime as dt
import json
import os

import universe
from alpaca_client import option_data_client, stock_data_client, trading_client

BACK_TARGET_DTE = 32
TIER_B_FRONT_TARGET_DTE = 7
MAX_SPREAD_PCT = 0.25          # reject a quote wider than 25% of its own mid
RATIO_THRESHOLD = 1.3          # starting value, not yet validated live — see SPEC
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')


def nearest_expiry(expiries, target_dte, today):
    best, best_d = None, 1e9
    for d in expiries:
        dte = (d - today).days
        if dte < 2:
            continue
        if abs(dte - target_dte) < best_d:
            best, best_d = d, abs(dte - target_dte)
    return best


def atm_contract(per_exp, exp, spot):
    best, best_d = None, 1e9
    for sym, snap in per_exp[exp]:
        strike = float(sym[-8:]) / 1000.0
        d = abs(strike - spot)
        if d < best_d:
            best, best_d = (sym, snap, strike), d
    return best


def quote_ok(snap):
    q = getattr(snap, 'latest_quote', None)
    if not q or not q.bid_price or not q.ask_price or q.bid_price <= 0:
        return False, None
    mid = 0.5 * (q.bid_price + q.ask_price)
    spread = q.ask_price - q.bid_price
    return (spread / mid) <= MAX_SPREAD_PCT, mid


def scan_ticker(odc, sdc, ticker, tier, today):
    from alpaca.data.requests import OptionChainRequest, StockLatestQuoteRequest
    from alpaca.trading.enums import ContractType

    row = {'ticker': ticker, 'tier': tier, 'ok': False}
    try:
        q = sdc.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=ticker))[ticker]
        spot = 0.5 * (q.bid_price + q.ask_price) if q.bid_price and q.ask_price else None
    except Exception as e:
        row['error'] = f'spot: {e}'
        return row
    if not spot:
        row['error'] = 'no spot quote'
        return row
    row['spot'] = spot

    try:
        chain = odc.get_option_chain(OptionChainRequest(
            underlying_symbol=ticker, type=ContractType.CALL,
            expiration_date_gte=today + dt.timedelta(days=2),
            expiration_date_lte=today + dt.timedelta(days=BACK_TARGET_DTE + 20),
            feed='indicative'))
    except Exception as e:
        row['error'] = f'chain: {e}'
        return row
    if not chain:
        row['error'] = 'empty chain'
        return row

    per_exp = {}
    for sym, snap in chain.items():
        try:
            rest = sym[len(ticker):]
            d = dt.date(2000 + int(rest[0:2]), int(rest[2:4]), int(rest[4:6]))
        except Exception:
            continue
        per_exp.setdefault(d, []).append((sym, snap))
    if not per_exp:
        row['error'] = 'unparseable chain'
        return row

    if tier == 'A':
        front_exp = universe.earliest_valid_front_expiry(ticker, sorted(per_exp))
    else:
        front_exp = nearest_expiry(sorted(per_exp), TIER_B_FRONT_TARGET_DTE, today)
    back_exp = nearest_expiry(sorted(per_exp), BACK_TARGET_DTE, today)

    if not front_exp or not back_exp or front_exp not in per_exp or back_exp not in per_exp:
        row['error'] = 'no usable front/back expiry'
        return row

    front = atm_contract(per_exp, front_exp, spot)
    back = atm_contract(per_exp, back_exp, spot)
    if not front or not back:
        row['error'] = 'no ATM contract'
        return row

    f_sym, f_snap, f_strike = front
    b_sym, b_snap, b_strike = back
    f_iv = getattr(f_snap, 'implied_volatility', None)
    b_iv = getattr(b_snap, 'implied_volatility', None)
    f_liq_ok, f_mid = quote_ok(f_snap)
    b_liq_ok, b_mid = quote_ok(b_snap)

    row.update({
        'front_expiry': front_exp.isoformat(), 'front_dte': (front_exp - today).days,
        'front_strike': f_strike, 'front_iv': f_iv, 'front_mid': f_mid, 'front_liquid': f_liq_ok,
        'back_expiry': back_exp.isoformat(), 'back_dte': (back_exp - today).days,
        'back_strike': b_strike, 'back_iv': b_iv, 'back_mid': b_mid, 'back_liquid': b_liq_ok,
    })

    if f_iv and b_iv and b_iv > 0 and f_liq_ok and b_liq_ok:
        row['ratio'] = round(f_iv / b_iv, 3)
        row['candidate'] = row['ratio'] >= RATIO_THRESHOLD and tier == 'A'
        row['ok'] = True
    else:
        row['error'] = 'missing IV or illiquid quote'
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tickers', default=None,
                     help='comma-separated override; default = full TIER_A + TIER_B universe')
    args = ap.parse_args()

    tc = trading_client()
    acc = tc.get_account()
    print(f'=== ALPACA PAPER === status {acc.status} · equity ${float(acc.equity):,.0f} '
          f'· options level {getattr(acc, "options_trading_level", "?")}\n')

    if args.tickers:
        picked = [t.strip().upper() for t in args.tickers.split(',') if t.strip()]
        jobs = [(t, 'A' if t in universe.TIER_A else 'B') for t in picked]
    else:
        jobs = [(t, 'A') for t in universe.TIER_A] + [(t, 'B') for t in universe.TIER_B]

    odc = option_data_client()
    sdc = stock_data_client()
    today = dt.date.today()

    rows = [scan_ticker(odc, sdc, t, tier, today) for t, tier in jobs]

    ok_rows = sorted((r for r in rows if r.get('ok')), key=lambda r: -r['ratio'])
    print(f'{"ticker":<6}{"tier":<5}{"front":>12}{"iv":>8}{"back":>12}{"iv":>8}{"ratio":>8}  candidate')
    for r in ok_rows:
        flag = 'YES' if r.get('candidate') else ''
        print(f'{r["ticker"]:<6}{r["tier"]:<5}{r["front_expiry"]:>12}{r["front_iv"]*100:>7.0f}%'
              f'{r["back_expiry"]:>12}{r["back_iv"]*100:>7.0f}%{r["ratio"]:>8.2f}  {flag}')

    skipped = [r for r in rows if not r.get('ok')]
    if skipped:
        print(f'\nskipped ({len(skipped)}): ' + ', '.join(f'{r["ticker"]}[{r.get("error")}]' for r in skipped))

    candidates = [r for r in ok_rows if r.get('candidate')]
    print(f'\n{len(candidates)} candidate(s) >= ratio {RATIO_THRESHOLD}: '
          + (', '.join(r['ticker'] for r in candidates) if candidates else '(none today)'))

    os.makedirs(LOG_DIR, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    outpath = os.path.join(LOG_DIR, f'scan_{ts}.json')
    with open(outpath, 'w') as f:
        json.dump({'run_utc': ts, 'threshold': RATIO_THRESHOLD, 'rows': rows}, f, indent=2, default=str)
    print(f'\nlogged: {outpath}')


if __name__ == '__main__':
    main()
