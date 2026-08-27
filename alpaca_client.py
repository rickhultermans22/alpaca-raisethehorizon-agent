"""
alpaca_client.py — thin wrapper around alpaca-py, credentials from environment.

Reads ALPACA_API_KEY / ALPACA_SECRET_KEY / ALPACA_PAPER_TRADE from the environment
(via .env locally, or real env vars when deployed). Never hardcode keys here — this
repo is public.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _keys():
    key = os.environ.get('ALPACA_API_KEY')
    secret = os.environ.get('ALPACA_SECRET_KEY')
    if not key or not secret:
        raise SystemExit(
            'ALPACA_API_KEY / ALPACA_SECRET_KEY not set. Copy .env.example to .env '
            'and fill in your Alpaca PAPER trading keys (alpaca.markets -> Paper '
            'Trading -> API Keys).'
        )
    return key, secret


def is_paper() -> bool:
    return os.environ.get('ALPACA_PAPER_TRADE', 'true').lower() != 'false'


def trading_client():
    from alpaca.trading.client import TradingClient
    key, secret = _keys()
    return TradingClient(key, secret, paper=is_paper())


def option_data_client():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    key, secret = _keys()
    return OptionHistoricalDataClient(key, secret)


def stock_data_client():
    from alpaca.data.historical.stock import StockHistoricalDataClient
    key, secret = _keys()
    return StockHistoricalDataClient(key, secret)


def main():
    if not is_paper():
        raise SystemExit('ALPACA_PAPER_TRADE=false — this project is paper-only. Refusing to run.')
    tc = trading_client()
    acc = tc.get_account()
    print('=== ALPACA PAPER ===')
    print(f'  account       {acc.account_number}  ({acc.status})')
    print(f'  created       {acc.created_at}')
    print(f'  equity        ${float(acc.equity):,.2f}')
    print(f'  options level {getattr(acc, "options_trading_level", "?")}')
    clock = tc.get_clock()
    print(f'  market        {"OPEN" if clock.is_open else "closed"} '
          f'(next open {clock.next_open:%Y-%m-%d %H:%M})')


if __name__ == '__main__':
    main()
