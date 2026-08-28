"""
agent.py — the autonomous piece. Runs the scan, hands the results + strategy rules to
Claude with live tool access to Alpaca's MCP server, and lets it reason about which
calendar spreads to open or roll and actually call the tools to do it.

Safety default: DRY RUN. Every mutating tool (place/cancel/close/replace order,
exercise, watchlist writes) is intercepted and logged instead of executed, unless
--live is passed explicitly. Read-only tools (get_*, list_*, search_*) always run for
real, because the decision needs real data. This is the code version of "Claude
proposes a batch, Rick reviews, then it goes live" — not a hardcoded rule bolted on
after the fact, the default IS off.

Every run's full tool-call transcript is written to log/agent_run_<ts>.json — that's
the decision log the dashboard and the write-up read from.

Usage:
  python agent.py                 # dry run (default)
  python agent.py --live          # places real (paper) orders
  python agent.py --tickers AVGO,DELL --live
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from dotenv import load_dotenv

load_dotenv()

import scan
import universe
from alpaca_client import trading_client

MODEL = 'claude-sonnet-5'
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

MAX_POSITIONS = 8  # hard ceiling — upper bound of the 6-8 target band, enforced in code,
                    # not just in the prompt. The model can propose whatever it wants;
                    # this is what actually blocks it from exceeding the cap or
                    # duplicating a ticker, in both dry-run and --live.

MUTATING_TOOLS = {
    'place_stock_order', 'place_option_order', 'place_crypto_order',
    'cancel_order_by_id', 'cancel_all_orders', 'replace_order_by_id',
    'close_position', 'close_all_positions',
    'exercise_options_position', 'do_not_exercise_options_position',
    'create_watchlist', 'update_watchlist_by_id', 'delete_watchlist_by_id',
    'add_asset_to_watchlist_by_id', 'remove_asset_from_watchlist_by_id',
    'update_account_config', 'create_locate',
}

SYSTEM_PROMPT = """\
You are the trading agent for a solo entry in Alpaca's "Options Alpha Agents" hackathon.
Paper account only, real market data.

STRATEGY — earnings term-structure calendar spreads
A daily scan measures, per liquid ticker, the ratio of implied vol on a near-dated
("front") ATM call versus a longer-dated ("back") ATM call at the same strike. A flat
name sits near 1.0. A name with a real event priced in (earnings) sits well above it.
Where the ratio clears the threshold, express that view as a calendar spread: SELL the
front-month ATM call, BUY the back-month ATM call at the same strike, net debit. Max
loss is capped at the debit paid.

The scan already picked front/back EXPIRY DATES per candidate using an earnings-aware
rule (never an expiry that settles before the earnings print has actually landed) — use
those dates, don't re-derive them. You still need to look up the live, exact ATM
contract (OCC symbol) at those expiries yourself via the option-chain tools, since the
scan doesn't hand you tradeable symbols, only dates and tickers.

PROCESS — work in small batches
Don't fetch option chains for every candidate at once. Pick 2-3 at a time, look them up,
decide, then move to the next batch. Fetching everything in one giant tool-call turn risks
hitting the response token limit mid-batch — smaller batches are more reliable and let you
stop early once you've filled the position cap.

RISK RULES — do not deviate
- Debit per position: cap at 2.5-3% of current portfolio equity. This is a deliberate
  middle ground for a one-shot week-long contest — sized up from a standard 1% because
  the calendar structure is defined-risk (max loss = debit paid, no margin/tail
  exposure), so going bigger scales the dollar loss but not the risk shape — but not
  pushed to the max, to leave room if the term-structure read turns out wrong on a
  given name. Don't self-moderate back down to a smaller size; the cap is 2.5-3%, use it.
- Max concurrent positions: 6-8 total. Check get_all_positions / get_orders first —
  never exceed the cap, and never duplicate a calendar already open on the same ticker.
- Only open NEW positions on TIER A (earnings) candidates that the scan flagged
  candidate=true. TIER B tickers are a control group — do not trade them even if their
  ratio looks high; flag it in your summary instead, don't act on it.
- Rolling: for an EXISTING position, roll the front leg (close it, sell a new front
  leg) when it is within 2 days of its own expiry, or immediately after its earnings
  event has passed and the crush looks captured (front IV has dropped toward the back
  leg's level) — whichever comes first.
- If liquidity looks thin (wide bid/ask, no quote) on the contract you'd actually need,
  skip that name and say why. Don't force a trade into a bad price.
- This strategy is UNVALIDATED — you are not chasing conviction, you are executing a
  fixed, conservative rule set. If nothing qualifies today, doing nothing is the
  correct output.

At the end, write a short plain-language summary: what you looked at, what you did (or
would have done, in dry run), and why. There is no human gate before a real batch goes
live in production, this summary is the public audit trail people read afterward, not a
pre-execution checkpoint. So the reasoning has to stand on its own.
"""


def occ_underlying(occ_symbol: str) -> str:
    """AVGO260904C00370000 -> AVGO. OCC symbols end in a fixed 15 chars
    (YYMMDD + C/P + 8-digit strike); everything before that is the root ticker."""
    return occ_symbol[:-15]


def to_anthropic_tools(mcp_tools):
    out = []
    for t in mcp_tools:
        out.append({
            'name': t.name,
            'description': t.description or '',
            'input_schema': t.inputSchema,
        })
    return out


async def run(tickers_override, live: bool):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from anthropic import AsyncAnthropic

    tc = trading_client()
    acc = tc.get_account()
    print(f'=== ALPACA PAPER === equity ${float(acc.equity):,.0f} · '
          f'{"LIVE (real paper orders)" if live else "DRY RUN (no orders placed)"}\n')

    # Hard, code-level guard — NOT just a prompt instruction. Seeded from real positions
    # so it's correct even resuming into a run where positions already exist. Option
    # position symbols come back as the OCC symbol (e.g. AVGO260904C00370000); normalize
    # to the underlying ticker the same way occ_underlying() does for new orders.
    existing_positions = tc.get_all_positions()
    known_tickers = {occ_underlying(p.symbol) if len(p.symbol) > 15 else p.symbol
                      for p in existing_positions}
    position_count = len(existing_positions)
    print(f'  guard: {position_count} existing position(s) on {known_tickers or "(none)"} '
          f'— cap is {MAX_POSITIONS}\n')

    print('--- running scan ---')
    from alpaca_client import option_data_client, stock_data_client
    odc = option_data_client()
    sdc = stock_data_client()
    today = dt.date.today()
    if tickers_override:
        jobs = [(t, 'A' if t in universe.TIER_A else 'B') for t in tickers_override]
    else:
        jobs = [(t, 'A') for t in universe.TIER_A] + [(t, 'B') for t in universe.TIER_B]
    rows = [scan.scan_ticker(odc, sdc, t, tier, today) for t, tier in jobs]
    candidates = [r for r in rows if r.get('candidate')]
    print(f'{len(candidates)} candidate(s): ' + (', '.join(r['ticker'] for r in candidates) or '(none)'))

    from alpaca_client import mcp_server_command_args
    mcp_cmd, mcp_args = mcp_server_command_args(ENV_FILE)
    params = StdioServerParameters(command=mcp_cmd, args=mcp_args)
    transcript = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            tools = to_anthropic_tools(mcp_tools)

            client = AsyncAnthropic()
            user_content = (
                f"Today's scan ({today.isoformat()}), candidates only (full ranked list "
                f"in scan.py's own log if you need the flat ones for context):\n"
                f"{json.dumps(candidates, indent=2, default=str)}\n\n"
                f"Mode: {'LIVE — orders you place are real paper trades.' if live else 'DRY RUN — place_option_order calls will be intercepted and logged, not executed. Reason and act as if they were real; the interception is invisible to you.'}\n"
                f"Start by checking current positions and orders, then decide."
            )
            messages = [{'role': 'user', 'content': user_content}]

            for _ in range(20):  # hard cap on tool-use turns, avoid runaway loops
                resp = await client.messages.create(
                    model=MODEL, max_tokens=8192, system=SYSTEM_PROMPT,
                    tools=tools, messages=messages,
                )
                print(f'  (stop_reason={resp.stop_reason})')
                messages.append({'role': 'assistant', 'content': resp.content})
                transcript.append({'role': 'assistant', 'stop_reason': resp.stop_reason, 'content': [
                    b.model_dump() if hasattr(b, 'model_dump') else str(b) for b in resp.content
                ]})

                pending_tool_calls = [b for b in resp.content if b.type == 'tool_use']

                # Judge on WHETHER there's a tool call to run, not on stop_reason alone —
                # a response can be cut short by max_tokens after emitting several complete,
                # valid tool_use blocks. Treating that as "no more tool use" silently drops
                # real tool calls the model already committed to.
                if not pending_tool_calls:
                    final_text = ''.join(b.text for b in resp.content if b.type == 'text')
                    print('\n=== AGENT SUMMARY ===')
                    print(final_text)
                    transcript.append({'final_summary': final_text})
                    break

                if resp.stop_reason == 'max_tokens':
                    print(f'  [max_tokens hit after {len(pending_tool_calls)} tool call(s) — '
                          f'running them, then continuing]')

                tool_results = []
                for block in pending_tool_calls:
                    name, args = block.name, block.input

                    if name == 'place_option_order':
                        legs = args.get('legs', [])
                        tickers_in_order = {occ_underlying(leg['symbol']) for leg in legs
                                             if len(leg.get('symbol', '')) > 15}
                        dupes = tickers_in_order & known_tickers
                        if dupes:
                            result_text = json.dumps({
                                'rejected': True,
                                'reason': f'Already have a position on {sorted(dupes)} — '
                                          f'code-level guard blocks duplicates regardless of mode.',
                            })
                            print(f'  [BLOCKED] {name}: duplicate ticker {sorted(dupes)}')
                            tool_results.append({'type': 'tool_result', 'tool_use_id': block.id,
                                                  'content': result_text})
                            continue
                        if position_count >= MAX_POSITIONS:
                            result_text = json.dumps({
                                'rejected': True,
                                'reason': f'Position cap ({MAX_POSITIONS}) reached — '
                                          f'code-level guard, not a suggestion.',
                            })
                            print(f'  [BLOCKED] {name}: cap ({MAX_POSITIONS}) reached')
                            tool_results.append({'type': 'tool_result', 'tool_use_id': block.id,
                                                  'content': result_text})
                            continue
                        # Passed the guard — reserve the slot now, before dry-run/live branch,
                        # so a batch of proposals in the same turn can't jointly blow the cap.
                        known_tickers |= tickers_in_order
                        position_count += 1

                    if name in MUTATING_TOOLS and not live:
                        result_text = json.dumps({
                            'dry_run': True,
                            'would_have_called': name,
                            'args': args,
                            'note': 'Intercepted — pass --live to actually place this.',
                        })
                        print(f'  [DRY RUN] {name}({json.dumps(args, default=str)})')
                    else:
                        try:
                            call = await session.call_tool(name, args)
                            result_text = ''.join(
                                b.text for b in call.content if hasattr(b, 'text'))
                            tag = '[LIVE]' if name in MUTATING_TOOLS else ''
                            print(f'  {tag} {name}({json.dumps(args, default=str)[:200]})')
                        except Exception as e:
                            result_text = json.dumps({'error': str(e)})
                    tool_results.append({
                        'type': 'tool_result', 'tool_use_id': block.id, 'content': result_text,
                    })
                messages.append({'role': 'user', 'content': tool_results})
                transcript.append({'tool_results': tool_results})
            else:
                print('\n[stopped: hit the 20-turn cap]')

    os.makedirs(LOG_DIR, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    outpath = os.path.join(LOG_DIR, f'agent_run_{ts}.json')
    with open(outpath, 'w') as f:
        json.dump({'run_utc': ts, 'live': live, 'candidates': candidates, 'transcript': transcript},
                   f, indent=2, default=str)
    print(f'\nlogged: {outpath}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tickers', default=None, help='comma-separated override; default = full universe')
    ap.add_argument('--live', action='store_true', help='place real paper orders (default: dry run)')
    args = ap.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(',')] if args.tickers else None
    asyncio.run(run(tickers, args.live))


if __name__ == '__main__':
    main()
