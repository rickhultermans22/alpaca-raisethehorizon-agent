"""
universe.py — the two tiers this agent scans, fixed for the hackathon window
(2026-08-28 -> 2026-09-04).

TIER_A: liquid names confirmed to report earnings inside the contest window (source:
  a TradingView earnings-calendar pull on 2026-08-27). This is a fixed snapshot, not
  a live feed — the window is 5 trading days, re-deriving it live isn't worth the
  extra moving part. Update by hand if the roster needs to change.

TIER_B: liquid sector ETFs + mega-caps with NO earnings this week. These are the
  control group: their front/back IV ratio should stay flat. If one of these lights
  up, that's a signal something else (macro, a rumor) is moving it — worth a look,
  but treated with more suspicion than a TIER_A hit.
"""
import datetime as dt

# ticker -> (earnings_date, 'BMO' | 'AMC')
TIER_A = {
    'MDT':  (dt.date(2026, 9, 1), 'BMO'),
    'DELL': (dt.date(2026, 9, 1), 'AMC'),
    'PANW': (dt.date(2026, 9, 1), 'AMC'),
    'MDB':  (dt.date(2026, 9, 1), 'AMC'),
    'AVGO': (dt.date(2026, 9, 2), 'AMC'),
    'SNOW': (dt.date(2026, 9, 2), 'AMC'),
    'GOLD': (dt.date(2026, 9, 2), 'AMC'),
    'HPE':  (dt.date(2026, 9, 2), 'AMC'),
    'NTAP': (dt.date(2026, 9, 2), 'AMC'),
    'RH':   (dt.date(2026, 9, 3), 'BMO'),
    'CIEN': (dt.date(2026, 9, 3), 'BMO'),
    'LULU': (dt.date(2026, 9, 3), 'AMC'),
    'ZS':   (dt.date(2026, 9, 3), 'AMC'),
    'DOCU': (dt.date(2026, 9, 3), 'AMC'),
    'IOT':  (dt.date(2026, 9, 3), 'AMC'),
}

TIER_B = [
    'SPY', 'QQQ',
    'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLB', 'XLU', 'XLRE', 'XLC',
    'AAPL', 'MSFT', 'NVDA', 'AMZN',
]


def earliest_valid_front_expiry(ticker: str, expiries: list[dt.date]) -> dt.date | None:
    """The first expiry that sits AFTER the earnings move has actually happened.

    BMO on day D: the move is in by the 9:30 open, so an expiry on D itself already
    reflects it -> D is valid.
    AMC on day D: the move lands after D's close, options already stopped trading
    -> the earliest valid expiry is the first trading day after D.
    """
    info = TIER_A.get(ticker)
    if info is None:
        return None
    edate, tod = info
    floor_date = edate if tod == 'BMO' else edate + dt.timedelta(days=1)
    candidates = sorted(d for d in expiries if d >= floor_date)
    return candidates[0] if candidates else None
