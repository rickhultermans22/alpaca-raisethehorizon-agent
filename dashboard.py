"""
dashboard.py — read-only view of the paper account + the agent's own decision log.

Shows: equity/status, open positions, the equity curve, the latest scan (ranked,
candidates highlighted), and a feed of past agent runs with the plain-language
summary the agent wrote for itself. Places no orders, changes nothing — this is the
public URL a judge opens to see what happened, not a control panel.

Run:  streamlit run dashboard.py
"""
import glob
import json
import os

import streamlit as st

from alpaca_client import trading_client

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'log')

st.set_page_config(page_title='RaiseTheHorizon — Options Alpha Agent', page_icon='🟣', layout='wide')

RTH_CSS = """
<style>
:root{
  --bg:#EBEEFA; --card:#fff; --ink:#0C0F1E; --muted:#6E749A; --line:#E1E5F4;
  --energy:#FF6A00; --top:#F0169B; --up:#00C853; --down:#FF2D55;
}
.stApp{background:var(--bg); color:var(--ink);}
[data-testid="stHeader"]{background:transparent;}
.rth-hero{
  background:linear-gradient(90deg, var(--top), var(--energy));
  border-radius:16px; padding:22px 26px; margin-bottom:18px; color:#fff;
}
.rth-hero h1{margin:0; font-size:26px; font-weight:800;}
.rth-hero p{margin:4px 0 0; opacity:.92; font-size:14px;}
.badge{display:inline-block; padding:3px 12px; border-radius:20px; font-weight:800;
  font-size:11px; letter-spacing:.03em; margin-left:10px; vertical-align:middle;}
.badge.dry{background:#fff; color:var(--top);}
.badge.live{background:#0C0F1E; color:#fff;}
.tile{background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:14px 16px; text-align:left;}
.tile .lab{color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;}
.tile .val{font-size:24px; font-weight:800; margin-top:4px;}
.card{background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 18px; margin-bottom:12px;}
.tag{font-size:10.5px; font-weight:800; padding:2px 9px; border-radius:20px;}
.tag.cand{background:#FFE0F0; color:var(--top);}
.tag.tierA{background:#FFE7D5; color:#C24E00;}
.tag.tierB{background:#F0F1F8; color:var(--muted);}
</style>
"""
st.markdown(RTH_CSS, unsafe_allow_html=True)


@st.cache_data(ttl=60)
def load_account_and_positions():
    tc = trading_client()
    acc = tc.get_account()
    positions = tc.get_all_positions()
    clock = tc.get_clock()
    return acc, positions, clock


@st.cache_data(ttl=60)
def load_equity_curve():
    tc = trading_client()
    from alpaca.trading.requests import GetPortfolioHistoryRequest
    hist = tc.get_portfolio_history(GetPortfolioHistoryRequest(period='1M', timeframe='1D'))
    return hist


def load_logs(pattern):
    files = sorted(glob.glob(os.path.join(LOG_DIR, pattern)), reverse=True)
    out = []
    for f in files:
        try:
            out.append(json.load(open(f)))
        except Exception:
            continue
    return out


try:
    acc, positions, clock = load_account_and_positions()
except SystemExit as e:
    st.error(str(e))
    st.stop()

live_mode = os.environ.get('AGENT_LIVE_MODE', 'false').lower() == 'true'
badge = f'<span class="badge {"live" if live_mode else "dry"}">{"LIVE" if live_mode else "DRY RUN"}</span>'

st.markdown(f"""
<div class="rth-hero">
  <h1>RaiseTheHorizon — Options Alpha Agent {badge}</h1>
  <p>Earnings term-structure calendar spreads · Alpaca paper account {acc.account_number} ·
     Alpaca AI Trading Agents Hackathon</p>
</div>
""", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
for col, lab, val in [
    (c1, 'Equity', f'${float(acc.equity):,.0f}'),
    (c2, 'Buying power', f'${float(acc.buying_power):,.0f}'),
    (c3, 'Open positions', f'{len(positions)}'),
    (c4, 'Market', 'OPEN' if clock.is_open else 'CLOSED'),
]:
    col.markdown(f'<div class="tile"><div class="lab">{lab}</div><div class="val">{val}</div></div>',
                 unsafe_allow_html=True)

st.write('')
left, right = st.columns([3, 2])

with left:
    st.subheader('Equity curve')
    try:
        hist = load_equity_curve()
        import pandas as pd
        import plotly.graph_objects as go
        df = pd.DataFrame({'t': pd.to_datetime(hist.timestamp, unit='s'), 'equity': hist.equity})
        fig = go.Figure(go.Scatter(x=df['t'], y=df['equity'], mode='lines',
                                    line=dict(color='#F0169B', width=2.5),
                                    fill='tozeroy', fillcolor='rgba(240,22,155,0.08)'))
        fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10),
                           plot_bgcolor='white', paper_bgcolor='white',
                           yaxis=dict(gridcolor='#E1E5F4'), xaxis=dict(gridcolor='#E1E5F4'))
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.info(f'No portfolio history yet ({e}).')

    st.subheader('Open positions')
    if not positions:
        st.caption('None yet.')
    else:
        rows = [{
            'symbol': p.symbol, 'qty': float(p.qty), 'avg entry': float(p.avg_entry_price),
            'current': float(p.current_price), 'P&L': float(p.unrealized_pl),
            'P&L %': f'{100*float(p.unrealized_plpc):+.1f}%',
        } for p in positions]
        st.dataframe(rows, use_container_width=True, hide_index=True)

with right:
    st.subheader('Latest scan')
    scans = load_logs('scan_*.json')
    if not scans:
        st.caption('No scan logged yet — run scan.py.')
    else:
        s = scans[0]
        st.caption(f"run {s['run_utc']} · threshold {s['threshold']}")
        ok_rows = sorted((r for r in s['rows'] if r.get('ok')), key=lambda r: -r['ratio'])
        for r in ok_rows[:12]:
            tag = 'cand' if r.get('candidate') else ('tierA' if r['tier'] == 'A' else 'tierB')
            label = 'CANDIDATE' if r.get('candidate') else f"Tier {r['tier']}"
            st.markdown(
                f'<div class="card"><b>{r["ticker"]}</b> '
                f'<span class="tag {tag}">{label}</span><br>'
                f'<span style="color:var(--muted);font-size:12px">'
                f'ratio {r["ratio"]:.2f} · front {r["front_iv"]*100:.0f}% ({r["front_expiry"]}) '
                f'· back {r["back_iv"]*100:.0f}% ({r["back_expiry"]})</span></div>',
                unsafe_allow_html=True)

st.subheader('Decision log')
runs = load_logs('agent_run_*.json')
if not runs:
    st.caption('No agent runs logged yet — run agent.py.')
else:
    for run in runs[:10]:
        mode = 'LIVE' if run.get('live') else 'DRY RUN'
        summary = ''
        for item in run.get('transcript', []):
            if 'final_summary' in item:
                summary = item['final_summary']
        with st.expander(f"{run['run_utc']} · {mode} · {len(run.get('candidates', []))} candidate(s)"):
            st.markdown(summary or '_(no summary text)_')
