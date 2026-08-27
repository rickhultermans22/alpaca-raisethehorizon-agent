# Alpaca RaiseTheHorizon Agent

An options-alpha agent built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai, Aug 28 – Sep 4 2026, "Options Alpha Agents" track).

## The idea

Options pricing in the U.S. embeds a term structure: how expensive an option is depends on
how far out it expires. Normally that term structure is close to flat. Around a scheduled
event — an earnings release — the option expiring shortly after the event gets priced with
extra implied volatility, because the market knows a large move is possible. Once the event
passes, that extra premium tends to collapse ("IV crush"), whether or not the stock actually
moved much.

This agent measures that skew directly: for a basket of liquid, optionable names, it compares
the implied volatility of the nearest ("front") expiry against a longer-dated ("back") expiry
on the same strike. A flat name sits close to a 1.0 ratio. A name with a real event priced in
sits well above it — in a live scan on 2026-08-27, ahead of earnings, AVGO measured 1.51,
DELL 1.39, PANW 1.37, against two earnings-free control ETFs (XLK, XLF) sitting at 1.00 and
1.07.

Where the skew clears a threshold, the agent expresses that view with a **calendar spread**:
sell the front (short-dated) option, buy the back (long-dated) option at the same strike.
Net debit, so the maximum loss is capped at what was paid — unlike selling premium naked.
The front leg is rolled forward as expiries near, harvesting the term-structure edge again
each cycle.

One deliberate design detail: for a name reporting *after market close*, the front leg must
be the first expiry *after* the report — an expiry that closes trading the same day as an
AMC release settles before the number is even out, and would miss the crush it's meant to
capture. See `universe.earliest_valid_front_expiry`.

This is genuinely new, unvalidated territory — there is no backtest behind the threshold
(1.3, currently a starting value) because historical options-chain data for this isn't
available cheaply. The agent is deliberately sized small per position while it proves itself
live, on paper, during the contest week.

## What's here

- `alpaca_client.py` — thin wrapper around [alpaca-py](https://github.com/alpacahq/alpaca-py), credentials from environment variables.
- `universe.py` — the two ticker tiers: `TIER_A` (liquid names confirmed reporting earnings inside the contest window) and `TIER_B` (liquid sector ETFs / mega-caps with no earnings this week, used as a flat control group).
- `scan.py` — the daily scan: front/back IV ratio per ticker, a liquidity guard (rejects wide/stale quotes), ranks candidates, logs every run to `log/`.

Order placement and rolling run through Alpaca's [MCP server](https://github.com/alpacahq/alpaca-mcp-server) as required by the hackathon rules (Trading API + MCP server/CLI), not directly through the SDK — `scan.py` only reads market data and never places an order.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your Alpaca PAPER key/secret
python scan.py
```

Paper trading only. `alpaca_client.py` refuses to run if `ALPACA_PAPER_TRADE` is set to `false`.

## Status

Built solo for the hackathon, evenings only.
