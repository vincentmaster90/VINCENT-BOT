import os
import time
import json
import requests
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

# ─── BOT STATE ────────────────────────────────────────────────────────────────
bots_state = {}
price_cache = {'price': 0, 'timestamp': 0}

def get_kraken_ohlc(interval=240):
    """Fetch OHLC data from Kraken public API"""
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={interval}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get('error'):
            return []
        result = data['result']
        key = [k for k in result.keys() if k != 'last'][0]
        closes = [float(c[4]) for c in result[key]]
        return closes
    except Exception as e:
        print(f"Error fetching OHLC: {e}")
        return []

def calc_ma(data, period):
    if len(data) < period:
        return []
    result = []
    for i in range(period - 1, len(data)):
        result.append(sum(data[i - period + 1:i + 1]) / period)
    return result

def get_signal(fast, slow):
    if len(fast) < 2 or len(slow) < 2:
        return 'HOLD'
    f_now, f_prev = fast[-1], fast[-2]
    s_now, s_prev = slow[-1], slow[-2]
    if f_prev <= s_prev and f_now > s_now:
        return 'BUY'
    if f_prev >= s_prev and f_now < s_now:
        return 'SELL'
    return 'BULL' if f_now > s_now else 'BEAR'

def bot_tick(bot_id, bot):
    """Run one tick of bot logic"""
    try:
        closes = get_kraken_ohlc(bot['interval'])
        if not closes or len(closes) < 21:
            return

        price = closes[-1]
        ma_fast = calc_ma(closes, 9)
        ma_slow = calc_ma(closes, 21)

        bot['price'] = price
        bot['ma_fast'] = ma_fast[-1] if ma_fast else 0
        bot['ma_slow'] = ma_slow[-1] if ma_slow else 0

        # Check SL/TP
        if bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            if pnl_pct <= -(bot['sl'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] += pnl
                bot['trades'] += 1
                bot['open_position'] = None
                bot['log'].append(f"{datetime.now().strftime('%H:%M:%S')} ⛔ STOP LOSS @ ${price:.2f} | {pnl_pct*100:.2f}%")
            elif pnl_pct >= (bot['tp'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] += pnl
                bot['trades'] += 1
                bot['wins'] += 1
                bot['open_position'] = None
                bot['log'].append(f"{datetime.now().strftime('%H:%M:%S')} ✅ TAKE PROFIT @ ${price:.2f} | +{pnl_pct*100:.2f}%")

        signal = get_signal(ma_fast, ma_slow)
        last_signal = bot.get('last_signal', 'HOLD')

        if signal == 'BUY' and last_signal != 'BUY' and not bot.get('open_position'):
            bot['open_position'] = {'price': price, 'time': datetime.now().isoformat()}
            bot['log'].append(f"{datetime.now().strftime('%H:%M:%S')} 🟢 BUY @ ${price:.2f}")
        elif signal == 'SELL' and last_signal != 'SELL' and bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            pnl = pnl_pct * bot['capital']
            bot['pnl'] += pnl
            bot['trades'] += 1
            if pnl > 0:
                bot['wins'] += 1
            bot['open_position'] = None
            bot['log'].append(f"{datetime.now().strftime('%H:%M:%S')} 🔴 SELL @ ${price:.2f} | {pnl_pct*100:.2f}%")

        bot['signal'] = signal
        bot['last_signal'] = signal
        bot['log'] = bot['log'][-50:]  # Keep last 50 log entries

    except Exception as e:
        print(f"Bot {bot_id} error: {e}")

def run_bots():
    """Background thread running all active bots"""
    while True:
        for bot_id, bot in bots_state.items():
            if bot.get('running'):
                bot_tick(bot_id, bot)
        time.sleep(30)

# ─── API ROUTES ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vincent Salvatore — Multi-Bot Command Center</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;500;600;700&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #07080c;
    --surface: #0c0e14;
    --surface2: #10121a;
    --border: #1a1f2e;
    --accent: #00d4aa;
    --accent-dim: rgba(0,212,170,0.1);
    --red: #ff4d6a;
    --red-dim: rgba(255,77,106,0.1);
    --yellow: #f5c842;
    --blue: #4f8eff;
    --text: #e2e8f0;
    --muted: #4a5568;
    --mono: 'Space Mono', monospace;
    --sans: 'Inter', sans-serif;
  }

  body { background: var(--bg); color: var(--text); font-family: var(--sans); min-height: 100vh; }

  /* HEADER */
  header {
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .header-left { display: flex; flex-direction: column; gap: 2px; }
  .logo { font-family: var(--mono); font-size: 13px; letter-spacing: 0.12em; color: var(--accent); text-transform: uppercase; }
  .logo span { color: var(--muted); }
  .logo-sub { font-size: 10px; color: var(--muted); font-family: var(--mono); letter-spacing: 0.08em; }
  .header-right { display: flex; align-items: center; gap: 12px; }
  .btc-price { font-family: var(--mono); font-size: 18px; font-weight: 700; color: var(--text); }
  .btc-change { font-family: var(--mono); font-size: 11px; padding: 3px 8px; border-radius: 4px; }
  .btc-change.up { background: var(--accent-dim); color: var(--accent); }
  .btc-change.down { background: var(--red-dim); color: var(--red); }

  /* API SETUP */
  .api-setup {
    margin: 20px 24px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    display: flex;
    gap: 12px;
    align-items: flex-end;
  }
  .api-setup.connected { border-color: rgba(0,212,170,0.3); background: rgba(0,212,170,0.03); }
  .api-field { flex: 1; }
  .api-field label { font-size: 10px; font-family: var(--mono); color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; display: block; margin-bottom: 6px; }
  .api-field input {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    padding: 9px 12px;
    border-radius: 6px;
    outline: none;
    transition: border-color 0.2s;
  }
  .api-field input:focus { border-color: var(--accent); }
  .api-field input[type="password"] { letter-spacing: 0.15em; }
  .connect-btn {
    padding: 9px 20px;
    background: var(--accent);
    color: #07080c;
    border: none;
    border-radius: 6px;
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    white-space: nowrap;
    transition: filter 0.2s;
  }
  .connect-btn:hover { filter: brightness(1.1); }
  .connected-badge {
    display: none;
    align-items: center;
    gap: 8px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--accent);
  }
  .connected-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

  /* SUMMARY BAR */
  .summary-bar {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 0;
    margin: 0 24px 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }
  .summary-stat { padding: 14px 18px; border-right: 1px solid var(--border); }
  .summary-stat:last-child { border-right: none; }
  .summary-label { font-size: 10px; font-family: var(--mono); color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 4px; }
  .summary-value { font-size: 20px; font-family: var(--mono); font-weight: 700; }
  .summary-value.up { color: var(--accent); }
  .summary-value.down { color: var(--red); }
  .summary-value.neutral { color: var(--text); }

  /* BOTS GRID */
  .bots-section { padding: 0 24px 24px; }
  .bots-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .section-title { font-family: var(--mono); font-size: 11px; letter-spacing: 0.15em; color: var(--muted); text-transform: uppercase; }
  .add-bot-btn {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 14px;
    background: var(--accent-dim);
    border: 1px solid rgba(0,212,170,0.3);
    color: var(--accent);
    border-radius: 6px;
    font-family: var(--mono);
    font-size: 11px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .add-bot-btn:hover { background: var(--accent); color: #07080c; }

  .bots-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; }

  /* BOT CARD */
  .bot-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    transition: border-color 0.2s;
    position: relative;
  }
  .bot-card.running { border-color: rgba(0,212,170,0.25); }
  .bot-card.running::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent), transparent);
  }
  .bot-card.in-trade { border-color: rgba(79,142,255,0.4); }
  .bot-card.in-trade::before { background: linear-gradient(90deg, var(--blue), transparent); }

  .bot-header {
    padding: 14px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--border);
  }
  .bot-title { font-family: var(--mono); font-size: 12px; font-weight: 700; color: var(--text); }
  .bot-subtitle { font-size: 10px; color: var(--muted); font-family: var(--mono); margin-top: 2px; }
  .bot-controls { display: flex; gap: 6px; align-items: center; }
  .bot-toggle {
    width: 36px; height: 20px;
    background: var(--border);
    border-radius: 10px;
    border: none;
    cursor: pointer;
    position: relative;
    transition: background 0.2s;
  }
  .bot-toggle::after {
    content: '';
    position: absolute;
    top: 3px; left: 3px;
    width: 14px; height: 14px;
    background: var(--muted);
    border-radius: 50%;
    transition: all 0.2s;
  }
  .bot-toggle.on { background: var(--accent-dim); border: 1px solid var(--accent); }
  .bot-toggle.on::after { left: 18px; background: var(--accent); }
  .delete-btn { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px; padding: 2px 4px; }
  .delete-btn:hover { color: var(--red); }

  .bot-stats { display: grid; grid-template-columns: repeat(3, 1fr); border-bottom: 1px solid var(--border); }
  .bot-stat { padding: 10px 14px; border-right: 1px solid var(--border); }
  .bot-stat:last-child { border-right: none; }
  .bot-stat-label { font-size: 9px; font-family: var(--mono); color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 3px; }
  .bot-stat-value { font-size: 14px; font-family: var(--mono); font-weight: 700; }
  .bot-stat-value.up { color: var(--accent); }
  .bot-stat-value.down { color: var(--red); }
  .bot-stat-value.neutral { color: var(--text); }
  .bot-stat-value.blue { color: var(--blue); }

  .bot-config { padding: 12px 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border-bottom: 1px solid var(--border); }
  .config-item { display: flex; flex-direction: column; gap: 3px; }
  .config-label { font-size: 9px; font-family: var(--mono); color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
  .config-input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    padding: 5px 8px;
    border-radius: 4px;
    outline: none;
    width: 100%;
  }
  .config-input:focus { border-color: var(--accent); }
  .config-select {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    padding: 5px 8px;
    border-radius: 4px;
    outline: none;
    width: 100%;
  }

  .bot-signal {
    padding: 10px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-family: var(--mono);
    font-size: 11px;
  }
  .signal-text { color: var(--muted); }
  .signal-badge { padding: 3px 10px; border-radius: 4px; font-size: 10px; font-weight: 700; letter-spacing: 0.08em; }
  .sig-buy { background: var(--accent-dim); color: var(--accent); border: 1px solid var(--accent); }
  .sig-sell { background: var(--red-dim); color: var(--red); border: 1px solid var(--red); }
  .sig-bull { background: rgba(0,212,170,0.07); color: var(--accent); border: 1px solid rgba(0,212,170,0.3); }
  .sig-bear { background: var(--red-dim); color: var(--red); border: 1px solid rgba(255,77,106,0.3); }
  .sig-hold { background: rgba(74,85,104,0.2); color: var(--muted); border: 1px solid var(--border); }

  /* ADD BOT MODAL */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    z-index: 200;
    align-items: center;
    justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    width: 400px;
    max-width: 90vw;
  }
  .modal-title { font-family: var(--mono); font-size: 13px; color: var(--accent); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 20px; }
  .modal-field { margin-bottom: 14px; }
  .modal-field label { font-size: 10px; font-family: var(--mono); color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; display: block; margin-bottom: 6px; }
  .modal-field input, .modal-field select {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 13px;
    padding: 9px 12px;
    border-radius: 6px;
    outline: none;
  }
  .modal-field input:focus, .modal-field select:focus { border-color: var(--accent); }
  .modal-buttons { display: flex; gap: 10px; margin-top: 20px; }
  .modal-btn { flex: 1; padding: 10px; border-radius: 6px; font-family: var(--mono); font-size: 12px; font-weight: 700; cursor: pointer; border: none; }
  .modal-btn-confirm { background: var(--accent); color: #07080c; }
  .modal-btn-cancel { background: var(--border); color: var(--muted); }

  /* LOG GLOBAL */
  .global-log { margin: 0 24px 24px; }
  .log-box {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    height: 160px;
    overflow-y: auto;
    padding: 12px 16px;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.8;
  }
  .log-box::-webkit-scrollbar { width: 3px; }
  .log-box::-webkit-scrollbar-thumb { background: var(--border); }
  .log-entry { display: flex; gap: 10px; }
  .log-time { color: var(--muted); min-width: 55px; }
  .log-info { color: var(--text); }
  .log-buy { color: var(--accent); }
  .log-sell { color: var(--red); }
  .log-warn { color: var(--yellow); }

  @media (max-width: 768px) {
    .summary-bar { grid-template-columns: repeat(2, 1fr); }
    .bots-grid { grid-template-columns: 1fr; }
    .api-setup { flex-direction: column; }
  }

  .control-bar { display: flex; gap: 8px; align-items: center; }
  .btn-start-all { padding: 7px 14px; background: var(--accent); color: #07080c; border: none; border-radius: 6px; font-family: var(--mono); font-size: 11px; font-weight: 700; cursor: pointer; transition: filter 0.2s; }
  .btn-start-all:hover { filter: brightness(1.1); }
  .btn-stop-all { padding: 7px 14px; background: var(--red-dim); color: var(--red); border: 1px solid var(--red); border-radius: 6px; font-family: var(--mono); font-size: 11px; font-weight: 700; cursor: pointer; transition: all 0.2s; }
  .btn-stop-all:hover { background: var(--red); color: white; }
  .capital-bar { display: flex; gap: 10px; align-items: center; margin: 0 24px 16px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; }
  .capital-bar label { font-size: 10px; font-family: var(--mono); color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; white-space: nowrap; }
  .capital-bar input { background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: var(--mono); font-size: 13px; padding: 7px 10px; border-radius: 6px; outline: none; width: 140px; }
  .capital-bar input:focus { border-color: var(--accent); }
  .capital-update-btn { padding: 7px 14px; background: var(--accent-dim); color: var(--accent); border: 1px solid rgba(0,212,170,0.3); border-radius: 6px; font-family: var(--mono); font-size: 11px; font-weight: 700; cursor: pointer; white-space: nowrap; }
  .capital-update-btn:hover { background: var(--accent); color: #07080c; }
  .capital-info { font-size: 11px; font-family: var(--mono); color: var(--muted); }

  /* LIGHT THEME */
  body.light {
    --bg: #f0f2f5;
    --surface: #ffffff;
    --surface2: #f8f9fb;
    --border: #e2e8f0;
    --text: #1a202c;
    --muted: #718096;
  }

  /* THEME & LANG TOGGLES */
  .header-toggles { display: flex; gap: 8px; align-items: center; }
  .toggle-btn {
    padding: 5px 12px;
    border-radius: 20px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--muted);
    font-family: var(--mono);
    font-size: 11px;
    cursor: pointer;
    transition: all 0.2s;
    white-space: nowrap;
  }
  .toggle-btn:hover { border-color: var(--accent); color: var(--accent); }
  .toggle-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }

  .bot-name-input {
    background: none;
    border: none;
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 700;
    outline: none;
    width: 100%;
    padding: 0;
    cursor: text;
    border-bottom: 1px solid transparent;
    transition: border-color 0.2s;
  }
  .bot-name-input:hover { border-bottom-color: var(--border); }
  .bot-name-input:focus { border-bottom-color: var(--accent); }
  .bot-config-row {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--surface2);
  }
  .bot-config-item { display: flex; flex-direction: column; gap: 4px; }
  .bot-config-label { font-size: 9px; font-family: var(--mono); color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
  .bot-config-input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    padding: 4px 6px;
    border-radius: 4px;
    outline: none;
    width: 100%;
    transition: border-color 0.2s;
  }
  .bot-config-input:focus { border-color: var(--accent); }
  .bot-config-select {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    padding: 4px 6px;
    border-radius: 4px;
    outline: none;
    width: 100%;
  }
  .bot-config-select:focus { border-color: var(--accent); }

  /* CHART ON BOT CARD */
  .bot-chart-section {
    display: none;
    padding: 10px 14px 14px;
    border-top: 1px solid var(--border);
    background: var(--bg);
  }
  .bot-chart-section.open { display: block; }
  .bot-chart-toggle {
    width: 100%;
    background: none;
    border: none;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-family: var(--mono);
    font-size: 10px;
    padding: 7px;
    cursor: pointer;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    transition: color 0.2s;
  }
  .bot-chart-toggle:hover { color: var(--accent); }
  .bot-chart-canvas { width: 100% !important; border-radius: 4px; }

  /* FULLSCREEN CHART MODAL */
  .chart-modal {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.92);
    z-index: 300;
    flex-direction: column;
    padding: 20px;
  }
  .chart-modal.open { display: flex; }
  .chart-modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }
  .chart-modal-title { font-family: var(--mono); font-size: 14px; color: var(--accent); letter-spacing: 0.1em; text-transform: uppercase; }
  .chart-modal-info { font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .chart-modal-close {
    background: var(--red-dim);
    border: 1px solid var(--red);
    color: var(--red);
    font-family: var(--mono);
    font-size: 12px;
    padding: 6px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 700;
  }
  .chart-modal-close:hover { background: var(--red); color: white; }
  .chart-modal-canvas { flex: 1; width: 100% !important; border-radius: 8px; background: var(--bg); }
  .chart-modal-stats {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 10px;
    margin-top: 14px;
  }
  .chart-modal-stat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
  }
  .chart-modal-stat-label { font-size: 9px; font-family: var(--mono); color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 4px; }
  .chart-modal-stat-value { font-size: 15px; font-family: var(--mono); font-weight: 700; }
  .expand-chart-btn {
    background: none;
    border: none;
    color: var(--accent);
    font-family: var(--mono);
    font-size: 10px;
    cursor: pointer;
    padding: 0 6px;
    opacity: 0.7;
    transition: opacity 0.2s;
  }
  .expand-chart-btn:hover { opacity: 1; }

  /* INTERACTIVE CHART */
  .chart-modal-canvas { cursor: crosshair; }
  .chart-period-bar {
    display: flex;
    gap: 6px;
    margin-bottom: 12px;
    align-items: center;
  }
  .period-btn {
    padding: 5px 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--muted);
    font-family: var(--mono);
    font-size: 11px;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .period-btn:hover { border-color: var(--accent); color: var(--accent); }
  .period-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
  .chart-tooltip {
    position: fixed;
    background: var(--surface);
    border: 1px solid var(--accent);
    border-radius: 6px;
    padding: 8px 12px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text);
    pointer-events: none;
    display: none;
    z-index: 400;
    min-width: 160px;
  }
  .chart-tooltip.visible { display: block; }
  .tooltip-price { color: var(--accent); font-size: 13px; font-weight: 700; }
  .tooltip-ma { font-size: 10px; margin-top: 4px; }
</style>
</head>
<body>

<!-- HEADER -->
<header>
  <div class="header-left">
    <div class="logo">Vincent Salvatore <span>//</span> Multi-Bot</div>
    <div class="logo-sub">BTC/USD · Kraken · Command Center</div>
  </div>
  <div class="header-right">
    <div class="header-toggles">
      <button class="toggle-btn" id="langBtn" onclick="toggleLang()">🇬🇧 EN</button>
      <button class="toggle-btn" id="themeBtn" onclick="toggleTheme()">☀️ Light</button>
    </div>
    <div>
      <div class="btc-price" id="globalPrice">—</div>
      <div style="text-align:right;margin-top:2px">
        <span class="btc-change" id="globalChange">—</span>
      </div>
    </div>
  </div>
</header>

<!-- API SETUP -->
<div class="api-setup" id="apiSetup">
  <div class="api-field">
    <label>API Key Kraken</label>
    <input type="password" id="apiKey" placeholder="Incolla la tua API Key">
  </div>
  <div class="api-field">
    <label>API Secret Kraken</label>
    <input type="password" id="apiSecret" placeholder="Incolla il tuo API Secret">
  </div>
  <button class="connect-btn" onclick="connectAPI()">CONNETTI</button>
  <div class="connected-badge" id="connectedBadge">
    <div class="connected-dot"></div>
    <span>API Connessa</span>
  </div>
</div>

<!-- SUMMARY -->
<div class="summary-bar">
  <div class="summary-stat">
    <div class="summary-label">Bot Attivi</div>
    <div class="summary-value neutral" id="sumActive">0 / 0</div>
  </div>
  <div class="summary-stat">
    <div class="summary-label">P&L Oggi</div>
    <div class="summary-value" id="sumToday">$0.00</div>
  </div>
  <div class="summary-stat">
    <div class="summary-label">P&L Totale</div>
    <div class="summary-value" id="sumTotal">$0.00</div>
  </div>
  <div class="summary-stat">
    <div class="summary-label">Trade Totali</div>
    <div class="summary-value neutral" id="sumTrades">0</div>
  </div>
  <div class="summary-stat">
    <div class="summary-label">Win Rate</div>
    <div class="summary-value neutral" id="sumWinRate">0%</div>
  </div>
</div>

<!-- CAPITAL BAR -->
<div class="capital-bar">
  <label>Capitale Totale</label>
  <input type="number" id="totalCapital" placeholder="Es. 1000" min="1">
  <button class="capital-update-btn" onclick="updateCapital()">↻ Aggiorna</button>
  <span class="capital-info" id="capitalInfo">Imposta il capitale totale per il compounding automatico</span>
</div>

<!-- BOTS -->
<div class="bots-section">
  <div class="bots-header">
    <div class="section-title">I tuoi Bot — BTC/USD</div>
    <div class="control-bar">
      <button class="btn-start-all" onclick="startAllBots()">▶ Start All</button>
      <button class="btn-stop-all" onclick="stopAllBots()">■ Stop All</button>
      <button class="add-bot-btn" onclick="openAddModal()">+ Aggiungi Bot</button>
    </div>
  </div>
  <div class="bots-grid" id="botsGrid"></div>
</div>

<!-- GLOBAL LOG -->
<div class="global-log">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <div style="font-family:var(--mono);font-size:10px;letter-spacing:0.15em;color:var(--muted);text-transform:uppercase">Log Globale</div>
    <button onclick="document.getElementById('globalLog').innerHTML=''" style="font-size:11px;font-family:var(--mono);color:var(--muted);border:none;background:none;cursor:pointer">Pulisci</button>
  </div>
  <div class="log-box" id="globalLog">
    <div class="log-entry"><span class="log-time">--:--:--</span><span class="log-info">Command Center pronto. Connetti le API keys e avvia i bot.</span></div>
  </div>
</div>

<!-- ADD BOT MODAL -->
<div class="modal-overlay" id="modalOverlay">
  <div class="modal">
    <div class="modal-title">+ Nuovo Bot</div>
    <div class="modal-field">
      <label>Nome Bot</label>
      <input type="text" id="newBotName" placeholder="Es. Scalper 4h">
    </div>
    <div class="modal-field">
      <label>Intervallo</label>
      <select id="newBotInterval">
        <option value="60">1 ora</option>
        <option value="240" selected>4 ore</option>
        <option value="360">6 ore</option>
        <option value="720">12 ore</option>
        <option value="1440">1 giorno</option>
        <option value="10080">1 settimana</option>
      </select>
    </div>
    <div class="modal-field">
      <label>Capitale per trade (USD)</label>
      <input type="number" id="newBotCapital" value="20" min="1">
    </div>
    <div class="modal-field">
      <label>Stop Loss (%)</label>
      <input type="number" id="newBotSL" value="3" min="0.5" max="20" step="0.5">
    </div>
    <div class="modal-field">
      <label>Take Profit (%)</label>
      <input type="number" id="newBotTP" value="6" min="1" max="50" step="0.5">
    </div>
    <div class="modal-buttons">
      <button class="modal-btn modal-btn-cancel" onclick="closeAddModal()">Annulla</button>
      <button class="modal-btn modal-btn-confirm" onclick="confirmAddBot()">Aggiungi</button>
    </div>
  </div>
</div>

<script>
// ─── STATE ────────────────────────────────────────────────────────────────────
let apiConnected = false;
let globalPrice = 0;
let bots = [];
let globalInterval = null;
let botIntervals = {};

const DEFAULT_BOTS = [
  { name: 'Bot 1',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 2',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 3',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 4',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 5',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 6',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 7',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 8',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 9',  interval: 240, capital: 20, sl: 3, tp: 6 },
  { name: 'Bot 10', interval: 240, capital: 20, sl: 3, tp: 6 },
];

// ─── INIT BOTS ────────────────────────────────────────────────────────────────
function initBots() {
  const saved = localStorage.getItem('vincent_bots');
  if (saved) {
    bots = JSON.parse(saved);
  } else {
    bots = DEFAULT_BOTS.map((b, i) => ({
      id: i + 1,
      name: b.name,
      interval: b.interval,
      capital: b.capital,
      sl: b.sl,
      tp: b.tp,
      running: false,
      signal: 'HOLD',
      pnl: 0,
      trades: 0,
      wins: 0,
      openPosition: null,
      priceHistory: [],
      lastSignal: 'HOLD'
    }));
    saveBots();
  }
  renderBots();
  updateSummary();
}

function saveBots() {
  localStorage.setItem('vincent_bots', JSON.stringify(bots));
}

// ─── RENDER ───────────────────────────────────────────────────────────────────
function renderBots() {
  const grid = document.getElementById('botsGrid');
  grid.innerHTML = '';
  bots.forEach(bot => {
    grid.appendChild(createBotCard(bot));
  });
  updateSummary();
}

function intervalLabel(v) {
  const map = { 60: '1h', 240: '4h', 360: '6h', 720: '12h', 1440: '1D', 10080: '1W' };
  return map[v] || v + 'min';
}

function createBotCard(bot) {
  const card = document.createElement('div');
  card.className = 'bot-card' + (bot.running ? ' running' : '') + (bot.openPosition ? ' in-trade' : '');
  card.id = 'bot-card-' + bot.id;

  const pnlClass = bot.pnl >= 0 ? 'up' : 'down';
  const pnlStr = (bot.pnl >= 0 ? '+$' : '-$') + Math.abs(bot.pnl).toFixed(2);
  const winRate = bot.trades > 0 ? Math.round(bot.wins / bot.trades * 100) : 0;

  card.innerHTML = `
    <div class="bot-header">
      <div style="flex:1">
        <input class="bot-name-input" id="name-${bot.id}" value="${bot.name}" onchange="updateBotField(${bot.id},'name',this.value)">
        <div class="bot-subtitle">BTC/USD · ${intervalLabel(bot.interval)}</div>
      </div>
      <div class="bot-controls">
        <button class="bot-toggle ${bot.running ? 'on' : ''}" id="toggle-${bot.id}" onclick="toggleBot(${bot.id})"></button>
        <button class="delete-btn" onclick="deleteBot(${bot.id})">✕</button>
      </div>
    </div>
    <div class="bot-config-row">
      <div class="bot-config-item">
        <span class="bot-config-label">Intervallo</span>
        <select class="bot-config-select" onchange="updateBotField(${bot.id},'interval',parseInt(this.value))">
          <option value="60" ${bot.interval===60?'selected':''}>1h</option>
          <option value="240" ${bot.interval===240?'selected':''}>4h</option>
          <option value="360" ${bot.interval===360?'selected':''}>6h</option>
          <option value="720" ${bot.interval===720?'selected':''}>12h</option>
          <option value="1440" ${bot.interval===1440?'selected':''}>1D</option>
          <option value="10080" ${bot.interval===10080?'selected':''}>1W</option>
        </select>
      </div>
      <div class="bot-config-item">
        <span class="bot-config-label">SL (Stop Loss) %</span>
        <input class="bot-config-input" type="number" value="${bot.sl}" min="0.5" max="20" step="0.5" onchange="updateBotField(${bot.id},'sl',parseFloat(this.value))">
      </div>
      <div class="bot-config-item">
        <span class="bot-config-label">TP (Take Profit) %</span>
        <input class="bot-config-input" type="number" value="${bot.tp}" min="1" max="50" step="0.5" onchange="updateBotField(${bot.id},'tp',parseFloat(this.value))">
      </div>
    </div>
    <div class="bot-stats">
      <div class="bot-stat">
        <div class="bot-stat-label">P&L</div>
        <div class="bot-stat-value ${pnlClass}" id="pnl-${bot.id}">${pnlStr}</div>
      </div>
      <div class="bot-stat">
        <div class="bot-stat-label">Trade</div>
        <div class="bot-stat-value neutral" id="trades-${bot.id}">${bot.trades}</div>
      </div>
      <div class="bot-stat">
        <div class="bot-stat-label">Win Rate</div>
        <div class="bot-stat-value ${winRate >= 50 ? 'up' : 'down'}" id="winrate-${bot.id}">${winRate}%</div>
      </div>
    </div>
    <div class="bot-signal">
      <span class="signal-text" id="signal-sub-${bot.id}">Fast: — | Slow: —</span>
      <span class="signal-badge ${getSignalClass(bot.signal)}" id="signal-${bot.id}">${bot.signal}</span>
    </div>
    <div style="display:flex;align-items:center;border-top:1px solid var(--border)">
      <button class="bot-chart-toggle" style="flex:1;border-top:none;border-right:1px solid var(--border)" id="chart-toggle-btn-${bot.id}" onclick="toggleChart(${bot.id})">▼ Apri Grafico</button>
      <button class="expand-chart-btn" style="padding:7px 12px;font-size:12px" onclick="openFullChart(${bot.id})" title="Schermo intero">⛶ Espandi</button>
    </div>
    <div class="bot-chart-section" id="chart-section-${bot.id}">
      <canvas class="bot-chart-canvas" id="chart-${bot.id}" height="120"></canvas>
    </div>
  `;
  return card;
}

function getSignalClass(signal) {
  const map = { 'BUY': 'sig-buy', 'SELL': 'sig-sell', 'BULL': 'sig-bull', 'BEAR': 'sig-bear', 'HOLD': 'sig-hold' };
  return map[signal] || 'sig-hold';
}

function updateBotCard(bot) {
  const pnlEl = document.getElementById('pnl-' + bot.id);
  const tradesEl = document.getElementById('trades-' + bot.id);
  const winrateEl = document.getElementById('winrate-' + bot.id);
  const signalEl = document.getElementById('signal-' + bot.id);
  const card = document.getElementById('bot-card-' + bot.id);

  if (!pnlEl) return;

  const pnlStr = (bot.pnl >= 0 ? '+$' : '-$') + Math.abs(bot.pnl).toFixed(2);
  pnlEl.textContent = pnlStr;
  pnlEl.className = 'bot-stat-value ' + (bot.pnl >= 0 ? 'up' : 'down');
  tradesEl.textContent = bot.trades;
  const wr = bot.trades > 0 ? Math.round(bot.wins / bot.trades * 100) : 0;
  winrateEl.textContent = wr + '%';
  winrateEl.className = 'bot-stat-value ' + (wr >= 50 ? 'up' : 'down');
  signalEl.textContent = bot.signal;
  signalEl.className = 'signal-badge ' + getSignalClass(bot.signal);
  if (card) {
    card.className = 'bot-card' + (bot.running ? ' running' : '') + (bot.openPosition ? ' in-trade' : '');
  }
}

// ─── API CONNECTION ───────────────────────────────────────────────────────────
function connectAPI() {
  const key = document.getElementById('apiKey').value.trim();
  const secret = document.getElementById('apiSecret').value.trim();
  if (!key || !secret) { glog('⚠ Inserisci API Key e Secret', 'warn'); return; }
  apiConnected = true;
  document.getElementById('apiSetup').classList.add('connected');
  document.querySelector('.connect-btn').style.display = 'none';
  document.getElementById('connectedBadge').style.display = 'flex';
  glog('✅ API Kraken connessa — puoi avviare i bot', 'buy');
  startGlobalPriceFeed();
}

// ─── PRICE FEED ───────────────────────────────────────────────────────────────
async function fetchKrakenOHLC(interval) {
  const url = `https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=${interval}`;
  const res = await fetch(url);
  const data = await res.json();
  if (data.error && data.error.length > 0) throw new Error(data.error[0]);
  const result = data.result;
  const key = Object.keys(result).find(k => k !== 'last');
  return result[key].map(c => parseFloat(c[4]));
}

async function startGlobalPriceFeed() {
  await updateGlobalPrice();
  globalInterval = setInterval(updateGlobalPrice, 30000);
}

async function updateGlobalPrice() {
  try {
    const closes = await fetchKrakenOHLC(240);
    globalPrice = closes[closes.length - 1];
    const prev = closes[closes.length - 2];
    const pct = ((globalPrice - prev) / prev * 100).toFixed(2);
    document.getElementById('globalPrice').textContent = '$' + globalPrice.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const chEl = document.getElementById('globalChange');
    chEl.textContent = (pct >= 0 ? '+' : '') + pct + '%';
    chEl.className = 'btc-change ' + (pct >= 0 ? 'up' : 'down');
    // Tick all running bots
    bots.forEach(bot => { if (bot.running) tickBot(bot); });
  } catch(e) {
    glog('Errore fetch prezzo: ' + e.message, 'warn');
  }
}

// ─── BOT LOGIC ────────────────────────────────────────────────────────────────
function calcMA(data, period) {
  if (data.length < period) return [];
  const result = [];
  for (let i = period - 1; i < data.length; i++) {
    result.push(data.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period);
  }
  return result;
}

function getSignal(fast, slow) {
  if (fast.length < 2 || slow.length < 2) return 'HOLD';
  const fNow = fast[fast.length-1], fPrev = fast[fast.length-2];
  const sNow = slow[slow.length-1], sPrev = slow[slow.length-2];
  if (fPrev <= sPrev && fNow > sNow) return 'BUY';
  if (fPrev >= sPrev && fNow < sNow) return 'SELL';
  return fNow > sNow ? 'BULL' : 'BEAR';
}

async function tickBot(bot) {
  try {
    const closes = await fetchKrakenOHLC(bot.interval);
    bot.priceHistory = closes.slice(-60);
    const price = closes[closes.length - 1];
    const maFast = calcMA(bot.priceHistory, 9);
    const maSlow = calcMA(bot.priceHistory, 21);

    const fNow = maFast[maFast.length-1];
    const sNow = maSlow[maSlow.length-1];

    const subEl = document.getElementById('signal-sub-' + bot.id);
    if (subEl) subEl.textContent = `Fast: $${fNow?.toFixed(0)||'—'} | Slow: $${sNow?.toFixed(0)||'—'}`;

    // Check SL/TP
    if (bot.openPosition) {
      const pnlPct = (price - bot.openPosition.price) / bot.openPosition.price;
      if (pnlPct <= -(bot.sl/100)) {
        const tradePnl = pnlPct * bot.capital;
        bot.pnl += tradePnl;
        bot.trades++;
        bot.openPosition = null;
        glog(`⛔ [${bot.name}] STOP LOSS @ $${price.toFixed(0)} | ${(pnlPct*100).toFixed(2)}%`, 'sell');
        saveBots();
        updateSummary();
      } else if (pnlPct >= (bot.tp/100)) {
        const tradePnl = pnlPct * bot.capital;
        bot.pnl += tradePnl;
        bot.trades++;
        bot.wins++;
        bot.openPosition = null;
        glog(`✅ [${bot.name}] TAKE PROFIT @ $${price.toFixed(0)} | +${(pnlPct*100).toFixed(2)}%`, 'buy');
        saveBots();
        updateSummary();
      }
    }

    const signal = getSignal(maFast, maSlow);
    bot.signal = signal;

    if (signal === 'BUY' && bot.lastSignal !== 'BUY' && !bot.openPosition) {
      bot.openPosition = { price, time: new Date().toISOString() };
      glog(`🟢 [${bot.name}] BUY @ $${price.toFixed(0)} — Fast MA incrociata sopra Slow MA`, 'buy');
      saveBots();
    } else if (signal === 'SELL' && bot.lastSignal !== 'SELL' && bot.openPosition) {
      const pnlPct = (price - bot.openPosition.price) / bot.openPosition.price;
      const tradePnl = pnlPct * bot.capital;
      bot.pnl += tradePnl;
      bot.trades++;
      if (tradePnl > 0) bot.wins++;
      bot.openPosition = null;
      glog(`🔴 [${bot.name}] SELL @ $${price.toFixed(0)} | ${(pnlPct*100).toFixed(2)}%`, tradePnl >= 0 ? 'buy' : 'sell');
      saveBots();
    }

    bot.lastSignal = signal;
    updateBotCard(bot);
    updateSummary();

    // Redraw chart if open
    const chartSection = document.getElementById('chart-section-' + bot.id);
    if (chartSection && chartSection.classList.contains('open')) drawBotChart(bot);

  } catch(e) {
    // Silent fail per bot individuali
  }
}

// ─── TOGGLE BOT ───────────────────────────────────────────────────────────────
function toggleBot(id) {
  if (!apiConnected) { glog('⚠ Connetti prima le API keys', 'warn'); return; }
  const bot = bots.find(b => b.id === id);
  if (!bot) return;
  bot.running = !bot.running;
  const toggle = document.getElementById('toggle-' + id);
  if (toggle) toggle.className = 'bot-toggle ' + (bot.running ? 'on' : '');
  if (bot.running) {
    glog(`▶ [${bot.name}] avviato — Intervallo: ${intervalLabel(bot.interval)} | SL: ${bot.sl}% | TP: ${bot.tp}%`, 'buy');
    tickBot(bot);
  } else {
    glog(`■ [${bot.name}] fermato`, 'warn');
    if (bot.openPosition) glog(`⚠ [${bot.name}] Posizione aperta rimasta — chiudi manualmente su Kraken`, 'warn');
  }
  saveBots();
  updateBotCard(bot);
  updateSummary();
}

function deleteBot(id) {
  const bot = bots.find(b => b.id === id);
  if (!bot) return;
  if (bot.running) { glog('⚠ Ferma il bot prima di eliminarlo', 'warn'); return; }
  if (!confirm(`Eliminare "${bot.name}"?`)) return;
  bots = bots.filter(b => b.id !== id);
  saveBots();
  renderBots();
  glog(`🗑 [${bot.name}] eliminato`, 'warn');
}

// ─── SUMMARY ─────────────────────────────────────────────────────────────────
function updateSummary() {
  const active = bots.filter(b => b.running).length;
  const total = bots.length;
  const totalPnL = bots.reduce((a, b) => a + b.pnl, 0);
  const totalTrades = bots.reduce((a, b) => a + b.trades, 0);
  const totalWins = bots.reduce((a, b) => a + b.wins, 0);
  const winRate = totalTrades > 0 ? Math.round(totalWins / totalTrades * 100) : 0;

  document.getElementById('sumActive').textContent = active + ' / ' + total;
  const totalEl = document.getElementById('sumTotal');
  totalEl.textContent = (totalPnL >= 0 ? '+$' : '-$') + Math.abs(totalPnL).toFixed(2);
  totalEl.className = 'summary-value ' + (totalPnL >= 0 ? 'up' : 'down');
  document.getElementById('sumToday').textContent = (totalPnL >= 0 ? '+$' : '-$') + Math.abs(totalPnL).toFixed(2);
  document.getElementById('sumToday').className = 'summary-value ' + (totalPnL >= 0 ? 'up' : 'down');
  document.getElementById('sumTrades').textContent = totalTrades;
  document.getElementById('sumWinRate').textContent = winRate + '%';
  document.getElementById('sumWinRate').className = 'summary-value ' + (winRate >= 50 ? 'up' : 'down');
}

// ─── ADD BOT MODAL ────────────────────────────────────────────────────────────
function openAddModal() {
  document.getElementById('modalOverlay').classList.add('open');
}
function closeAddModal() {
  document.getElementById('modalOverlay').classList.remove('open');
}
function confirmAddBot() {
  const name = document.getElementById('newBotName').value.trim() || 'Bot ' + (bots.length + 1);
  const interval = parseInt(document.getElementById('newBotInterval').value);
  const capital = parseFloat(document.getElementById('newBotCapital').value) || 20;
  const sl = parseFloat(document.getElementById('newBotSL').value) || 3;
  const tp = parseFloat(document.getElementById('newBotTP').value) || 6;
  const newBot = {
    id: Date.now(),
    name, interval, capital, sl, tp,
    running: false, signal: 'HOLD',
    pnl: 0, trades: 0, wins: 0,
    openPosition: null, priceHistory: [], lastSignal: 'HOLD'
  };
  bots.push(newBot);
  saveBots();
  renderBots();
  closeAddModal();
  glog(`➕ [${name}] aggiunto — ${intervalLabel(interval)} | SL: ${sl}% | TP: ${tp}%`, 'info');
}

// ─── LOG ─────────────────────────────────────────────────────────────────────
function glog(msg, type = 'info') {
  const box = document.getElementById('globalLog');
  const t = new Date().toTimeString().slice(0, 8);
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.innerHTML = `<span class="log-time">${t}</span><span class="log-${type}">${msg}</span>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// ─── START ALL / STOP ALL shortcuts ──────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeAddModal();
});


// ─── START ALL / STOP ALL ─────────────────────────────────────────────────────
function startAllBots() {
  if (!apiConnected) { glog('⚠ Connetti prima le API keys', 'warn'); return; }
  let count = 0;
  bots.forEach(bot => {
    if (!bot.running) {
      bot.running = true;
      count++;
      tickBot(bot);
    }
  });
  saveBots();
  renderBots();
  glog(`▶ START ALL — ${count} bot avviati contemporaneamente`, 'buy');
  updateSummary();
}

function stopAllBots() {
  let count = 0;
  let openPositions = 0;
  bots.forEach(bot => {
    if (bot.running) {
      bot.running = false;
      count++;
      if (bot.openPosition) openPositions++;
    }
  });
  saveBots();
  renderBots();
  glog(`■ STOP ALL — ${count} bot fermati`, 'warn');
  if (openPositions > 0) glog(`⚠ ${openPositions} posizioni aperte rimaste — chiudi manualmente su Kraken`, 'warn');
  updateSummary();
}

// ─── CAPITAL UPDATE WITH COMPOUNDING ─────────────────────────────────────────
function updateCapital() {
  const newCapital = parseFloat(document.getElementById('totalCapital').value);
  if (!newCapital || newCapital <= 0) { glog('⚠ Inserisci un capitale valido', 'warn'); return; }
  
  const perBot = newCapital * 0.10;        // 10% del totale per ogni bot
  const perTrade = perBot * 0.20;          // 20% del 10% per ogni trade
  const maxExposed = perTrade * bots.length; // massimo esposto se tutti aprono insieme
  const wasRunning = bots.filter(b => b.running).length;
  
  // Stop all running bots
  if (wasRunning > 0) stopAllBots();
  
  // Update capital for all bots
  bots.forEach(bot => {
    bot.capital = parseFloat(perTrade.toFixed(2));
  });
  
  saveBots();
  renderBots();
  
  document.getElementById('capitalInfo').textContent = 
    `$${newCapital.toFixed(0)} totale → $${perBot.toFixed(0)}/bot (10%) → $${perTrade.toFixed(2)}/trade (20%) · Max esposto: $${maxExposed.toFixed(2)}`;
  
  glog(`💰 Capitale: $${newCapital.toFixed(2)} | Per bot: $${perBot.toFixed(2)} | Per trade: $${perTrade.toFixed(2)} | Max esposto: $${maxExposed.toFixed(2)}`, 'buy');
  
  if (wasRunning > 0) {
    glog('ℹ Bot fermati automaticamente — riavviali con START ALL', 'warn');
  }
}



function updateBotField(id, field, value) {
  const bot = bots.find(b => b.id === id);
  if (!bot) return;
  if (bot.running) { glog(`⚠ [${bot.name}] Ferma il bot prima di modificare le impostazioni`, 'warn'); return; }
  bot[field] = value;
  if (field === 'interval') {
    const card = document.getElementById('bot-card-' + id);
    if (card) {
      const subtitle = card.querySelector('.bot-subtitle');
      if (subtitle) subtitle.textContent = 'BTC/USD · ' + intervalLabel(value);
    }
  }
  saveBots();
}



// ─── FULLSCREEN CHART ─────────────────────────────────────────────────────────
let fullChartBotId = null;
let fullChartInterval = null;


async function fetchAndDrawFullChart(bot, range) {
  try {
    const { interval, since } = rangeToKrakenParams(range);
    let url = `https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=${interval}`;
    if (since > 0) url += `&since=${since}`;
    const res = await fetch(url);
    const data = await res.json();
    if (data.error && data.error.length > 0) throw new Error(data.error[0]);
    const result = data.result;
    const key = Object.keys(result).find(k => k !== 'last');
    const candles = result[key];
    bot.fullChartPrices = candles.map(c => parseFloat(c[4]));
    bot.fullChartTimestamps = candles.map(c => c[0] * 1000);
    drawFullChartInteractive(bot);
  } catch(e) {
    // fallback to existing priceHistory
    if (bot.priceHistory && bot.priceHistory.length > 1) {
      bot.fullChartPrices = bot.priceHistory;
      bot.fullChartTimestamps = [];
      drawFullChartInteractive(bot);
    }
  }
}

function openFullChart(id) {
  fullChartBotId = id;
  const bot = bots.find(b => b.id === id);
  if (!bot) return;
  document.getElementById('chartModal').classList.add('open');
  document.getElementById('chartModalTitle').textContent = bot.name + ' — BTC/USD';
  document.getElementById('chartModalInfo').textContent = intervalLabel(bot.interval) + ' · Fast MA(9) · Slow MA(21) · SL ' + bot.sl + '% · TP ' + bot.tp + '%';
  chartRange = '1M';
  document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', b.textContent === '1M'));
  fetchAndDrawFullChart(bot, chartRange);
  fullChartInterval = setInterval(() => {
    const b = bots.find(b => b.id === fullChartBotId);
    if (b) fetchAndDrawFullChart(b, chartRange);
  }, 30000);
}

function closeFullChart() {
  document.getElementById('chartModal').classList.remove('open');
  clearInterval(fullChartInterval);
  fullChartBotId = null;
}

function drawFullChart(bot) {
  const canvas = document.getElementById('chartModalCanvas');
  if (!canvas || bot.priceHistory.length < 2) return;
  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight || 400;
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const prices = bot.priceHistory;
  const maFast = calcMA(prices, 9);
  const maSlow = calcMA(prices, 21);
  const price = prices[prices.length - 1];
  const fNow = maFast[maFast.length - 1];
  const sNow = maSlow[maSlow.length - 1];

  const minP = Math.min(...prices) * 0.998;
  const maxP = Math.max(...prices) * 1.002;
  const scaleX = W / (prices.length - 1);
  const scaleY = H / (maxP - minP);
  const toX = i => i * scaleX;
  const toY = v => H - (v - minP) * scaleY;

  // Grid lines
  ctx.strokeStyle = '#1a1f2e'; ctx.lineWidth = 1;
  for (let i = 0; i <= 6; i++) {
    const y = (H / 6) * i;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    const val = maxP - (maxP - minP) * (i / 6);
    ctx.fillStyle = '#4a5568'; ctx.font = '11px Space Mono';
    ctx.fillText('$' + Math.round(val).toLocaleString(), 8, y - 4);
  }
  // Vertical grid
  for (let i = 0; i <= 8; i++) {
    const x = (W / 8) * i;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }

  // Price area fill
  ctx.beginPath();
  ctx.moveTo(toX(0), H);
  prices.forEach((p, i) => ctx.lineTo(toX(i), toY(p)));
  ctx.lineTo(toX(prices.length - 1), H);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, 'rgba(0,212,170,0.15)');
  grad.addColorStop(1, 'rgba(0,212,170,0)');
  ctx.fillStyle = grad;
  ctx.fill();

  // Price line
  ctx.beginPath(); ctx.strokeStyle = '#00d4aa'; ctx.lineWidth = 2;
  prices.forEach((p, i) => i === 0 ? ctx.moveTo(toX(i), toY(p)) : ctx.lineTo(toX(i), toY(p)));
  ctx.stroke();

  // Fast MA
  if (maFast.length > 1) {
    const off = prices.length - maFast.length;
    ctx.beginPath(); ctx.strokeStyle = '#f5c842'; ctx.lineWidth = 1.5; ctx.setLineDash([5,5]);
    maFast.forEach((v, i) => { const x = toX(i+off); i===0 ? ctx.moveTo(x,toY(v)) : ctx.lineTo(x,toY(v)); });
    ctx.stroke(); ctx.setLineDash([]);
  }

  // Slow MA
  if (maSlow.length > 1) {
    const off = prices.length - maSlow.length;
    ctx.beginPath(); ctx.strokeStyle = '#ff4d6a'; ctx.lineWidth = 1.5; ctx.setLineDash([8,4]);
    maSlow.forEach((v, i) => { const x = toX(i+off); i===0 ? ctx.moveTo(x,toY(v)) : ctx.lineTo(x,toY(v)); });
    ctx.stroke(); ctx.setLineDash([]);
  }

  // Entry line
  if (bot.openPosition) {
    const entryY = toY(bot.openPosition.price);
    ctx.strokeStyle = '#4f8eff'; ctx.lineWidth = 1.5; ctx.setLineDash([6,4]);
    ctx.beginPath(); ctx.moveTo(0, entryY); ctx.lineTo(W, entryY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#4f8eff'; ctx.font = 'bold 11px Space Mono';
    ctx.fillText('📍 ENTRY $' + Math.round(bot.openPosition.price).toLocaleString(), 8, entryY - 6);
    // TP line
    const tpPrice = bot.openPosition.price * (1 + bot.tp/100);
    const tpY = toY(tpPrice);
    ctx.strokeStyle = '#00d4aa'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(0, tpY); ctx.lineTo(W, tpY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#00d4aa'; ctx.font = '10px Space Mono';
    ctx.fillText('TP +' + bot.tp + '% $' + Math.round(tpPrice).toLocaleString(), 8, tpY - 4);
    // SL line
    const slPrice = bot.openPosition.price * (1 - bot.sl/100);
    const slY = toY(slPrice);
    ctx.strokeStyle = '#ff4d6a'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(0, slY); ctx.lineTo(W, slY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ff4d6a'; ctx.font = '10px Space Mono';
    ctx.fillText('SL -' + bot.sl + '% $' + Math.round(slPrice).toLocaleString(), 8, slY + 12);
  }

  // Legend
  ctx.font = '11px Space Mono';
  ctx.fillStyle = '#00d4aa'; ctx.fillRect(W-160, 12, 12, 2); ctx.fillText('Prezzo', W-144, 16);
  ctx.fillStyle = '#f5c842'; ctx.fillRect(W-160, 28, 12, 2); ctx.fillText('Fast MA (9)', W-144, 32);
  ctx.fillStyle = '#ff4d6a'; ctx.fillRect(W-160, 44, 12, 2); ctx.fillText('Slow MA (21)', W-144, 48);

  // Update stats
  document.getElementById('cmPrice').textContent = '$' + price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  document.getElementById('cmFast').textContent = fNow ? '$' + Math.round(fNow).toLocaleString() : '—';
  document.getElementById('cmSlow').textContent = sNow ? '$' + Math.round(sNow).toLocaleString() : '—';
  document.getElementById('cmSignal').textContent = bot.signal;
  document.getElementById('cmSignal').style.color = ['BUY','BULL'].includes(bot.signal) ? 'var(--accent)' : ['SELL','BEAR'].includes(bot.signal) ? 'var(--red)' : 'var(--muted)';
  const pnlStr = (bot.pnl >= 0 ? '+$' : '-$') + Math.abs(bot.pnl).toFixed(2);
  document.getElementById('cmPnl').textContent = pnlStr;
  document.getElementById('cmPnl').style.color = bot.pnl >= 0 ? 'var(--accent)' : 'var(--red)';
  const wr = bot.trades > 0 ? Math.round(bot.wins / bot.trades * 100) : 0;
  document.getElementById('cmWinRate').textContent = wr + '%';
  document.getElementById('cmWinRate').style.color = wr >= 50 ? 'var(--accent)' : 'var(--red)';
}

// Close with ESC
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeFullChart(); closeAddModal(); }
});

// ─── BOT CHARTS ───────────────────────────────────────────────────────────────
function toggleChart(id) {
  const section = document.getElementById('chart-section-' + id);
  const btn = document.getElementById('chart-toggle-btn-' + id);
  if (!section) return;
  const isOpen = section.classList.toggle('open');
  if (btn) btn.textContent = isOpen ? '▲ Chiudi Grafico' : '▼ Apri Grafico';
  if (isOpen) {
    const bot = bots.find(b => b.id === id);
    if (bot && bot.priceHistory.length > 1) drawBotChart(bot);
  }
}

function drawBotChart(bot) {
  const canvas = document.getElementById('chart-' + bot.id);
  if (!canvas) return;
  const W = canvas.offsetWidth || 300;
  const H = 120;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const prices = bot.priceHistory;
  if (prices.length < 2) return;

  const maFast = calcMA(prices, 9);
  const maSlow = calcMA(prices, 21);

  const minP = Math.min(...prices) * 0.999;
  const maxP = Math.max(...prices) * 1.001;
  const scaleX = W / (prices.length - 1);
  const scaleY = H / (maxP - minP);
  const toX = i => i * scaleX;
  const toY = v => H - (v - minP) * scaleY;

  // Grid
  ctx.strokeStyle = '#1a1f2e'; ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = (H / 3) * i;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    const val = maxP - (maxP - minP) * (i / 3);
    ctx.fillStyle = '#4a5568'; ctx.font = '9px Space Mono';
    ctx.fillText('$' + Math.round(val).toLocaleString(), 4, y - 2);
  }

  // Price
  ctx.beginPath(); ctx.strokeStyle = '#00d4aa'; ctx.lineWidth = 1.5;
  prices.forEach((p, i) => i === 0 ? ctx.moveTo(toX(i), toY(p)) : ctx.lineTo(toX(i), toY(p)));
  ctx.stroke();

  // Fast MA
  if (maFast.length > 1) {
    const off = prices.length - maFast.length;
    ctx.beginPath(); ctx.strokeStyle = '#f5c842'; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
    maFast.forEach((v, i) => { const x = toX(i+off); i===0 ? ctx.moveTo(x,toY(v)) : ctx.lineTo(x,toY(v)); });
    ctx.stroke(); ctx.setLineDash([]);
  }

  // Slow MA
  if (maSlow.length > 1) {
    const off = prices.length - maSlow.length;
    ctx.beginPath(); ctx.strokeStyle = '#ff4d6a'; ctx.lineWidth = 1; ctx.setLineDash([5,3]);
    maSlow.forEach((v, i) => { const x = toX(i+off); i===0 ? ctx.moveTo(x,toY(v)) : ctx.lineTo(x,toY(v)); });
    ctx.stroke(); ctx.setLineDash([]);
  }

  // Legend
  ctx.font = '9px Space Mono';
  ctx.fillStyle = '#00d4aa'; ctx.fillRect(W-110, 6, 8, 2); ctx.fillText('Prezzo', W-98, 10);
  ctx.fillStyle = '#f5c842'; ctx.fillRect(W-110, 18, 8, 2); ctx.fillText('Fast MA', W-98, 22);
  ctx.fillStyle = '#ff4d6a'; ctx.fillRect(W-110, 30, 8, 2); ctx.fillText('Slow MA', W-98, 34);

  // Mark open position
  if (bot.openPosition) {
    const entryPrice = bot.openPosition.price;
    const entryY = toY(entryPrice);
    ctx.strokeStyle = '#4f8eff'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(0, entryY); ctx.lineTo(W, entryY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#4f8eff'; ctx.font = '9px Space Mono';
    ctx.fillText('ENTRY $' + Math.round(entryPrice).toLocaleString(), 4, entryY - 3);
  }
}

// ─── TRANSLATIONS ─────────────────────────────────────────────────────────────
let currentLang = 'it';

const TRANSLATIONS = {
  it: {
    logoSub: 'BTC/USD · Kraken · Command Center',
    apiKey: 'API Key Kraken',
    apiSecret: 'API Secret Kraken',
    connect: 'CONNETTI',
    connected: 'API Connessa',
    capitalLabel: 'Capitale Totale',
    capitalBtn: '↻ Aggiorna',
    capitalPlaceholder: 'Es. 1000',
    botsTitle: 'I tuoi Bot — BTC/USD',
    startAll: '▶ Start All',
    stopAll: '■ Stop All',
    addBot: '+ Aggiungi Bot',
    logTitle: 'Log Globale',
    logClear: 'Pulisci',
    sumActive: 'Bot Attivi',
    sumToday: 'P&L Oggi',
    sumTotal: 'P&L Totale',
    sumTrades: 'Trade Totali',
    sumWinRate: 'Win Rate',
    modalTitle: '+ Nuovo Bot',
    modalName: 'Nome Bot',
    modalInterval: 'Intervallo',
    modalCapital: 'Capitale per trade (USD)',
    modalSL: 'Stop Loss (%)',
    modalTP: 'Take Profit (%)',
    modalCancel: 'Annulla',
    modalConfirm: 'Aggiungi',
    botPnl: 'P&L',
    botTrades: 'Trade',
    botWinRate: 'Win Rate',
    langBtn: '🇬🇧 EN',
    themeLight: '☀️ Light',
    themeDark: '🌙 Dark',
  },
  en: {
    logoSub: 'BTC/USD · Kraken · Command Center',
    apiKey: 'Kraken API Key',
    apiSecret: 'Kraken API Secret',
    connect: 'CONNECT',
    connected: 'API Connected',
    capitalLabel: 'Total Capital',
    capitalBtn: '↻ Update',
    capitalPlaceholder: 'e.g. 1000',
    botsTitle: 'Your Bots — BTC/USD',
    startAll: '▶ Start All',
    stopAll: '■ Stop All',
    addBot: '+ Add Bot',
    logTitle: 'Global Log',
    logClear: 'Clear',
    sumActive: 'Active Bots',
    sumToday: 'P&L Today',
    sumTotal: 'Total P&L',
    sumTrades: 'Total Trades',
    sumWinRate: 'Win Rate',
    modalTitle: '+ New Bot',
    modalName: 'Bot Name',
    modalInterval: 'Interval',
    modalCapital: 'Capital per trade (USD)',
    modalSL: 'Stop Loss (%)',
    modalTP: 'Take Profit (%)',
    modalCancel: 'Cancel',
    modalConfirm: 'Add',
    botPnl: 'P&L',
    botTrades: 'Trades',
    botWinRate: 'Win Rate',
    langBtn: '🇮🇹 IT',
    themeLight: '☀️ Light',
    themeDark: '🌙 Dark',
  }
};

function toggleLang() {
  currentLang = currentLang === 'it' ? 'en' : 'it';
  applyTranslations();
  localStorage.setItem('vincent_lang', currentLang);
}

function applyTranslations() {
  const t = TRANSLATIONS[currentLang];
  document.getElementById('langBtn').textContent = t.langBtn;
  document.querySelector('.logo-sub').textContent = t.logoSub;
  // API section
  const apiLabels = document.querySelectorAll('.api-field label');
  if (apiLabels[0]) apiLabels[0].textContent = t.apiKey;
  if (apiLabels[1]) apiLabels[1].textContent = t.apiSecret;
  document.querySelector('.connect-btn').textContent = t.connect;
  document.querySelector('#connectedBadge span').textContent = t.connected;
  // Capital bar
  document.querySelector('.capital-bar label').textContent = t.capitalLabel;
  document.querySelector('.capital-update-btn').textContent = t.capitalBtn;
  document.getElementById('totalCapital').placeholder = t.capitalPlaceholder;
  // Bots section
  document.querySelector('.section-title').textContent = t.botsTitle;
  document.querySelector('.btn-start-all').textContent = t.startAll;
  document.querySelector('.btn-stop-all').textContent = t.stopAll;
  document.querySelector('.add-bot-btn').textContent = t.addBot;
  // Summary
  const sumLabels = document.querySelectorAll('.summary-label');
  if (sumLabels[0]) sumLabels[0].textContent = t.sumActive;
  if (sumLabels[1]) sumLabels[1].textContent = t.sumToday;
  if (sumLabels[2]) sumLabels[2].textContent = t.sumTotal;
  if (sumLabels[3]) sumLabels[3].textContent = t.sumTrades;
  if (sumLabels[4]) sumLabels[4].textContent = t.sumWinRate;
  // Log
  document.querySelector('.global-log div div').textContent = t.logTitle;
  document.querySelector('.global-log button').textContent = t.logClear;
  // Modal
  document.querySelector('.modal-title').textContent = t.modalTitle;
  const modalLabels = document.querySelectorAll('.modal-field label');
  if (modalLabels[0]) modalLabels[0].textContent = t.modalName;
  if (modalLabels[1]) modalLabels[1].textContent = t.modalInterval;
  if (modalLabels[2]) modalLabels[2].textContent = t.modalCapital;
  if (modalLabels[3]) modalLabels[3].textContent = t.modalSL;
  if (modalLabels[4]) modalLabels[4].textContent = t.modalTP;
  document.querySelector('.modal-btn-cancel').textContent = t.modalCancel;
  document.querySelector('.modal-btn-confirm').textContent = t.modalConfirm;
}

// ─── THEME TOGGLE ─────────────────────────────────────────────────────────────
let currentTheme = 'dark';

function toggleTheme() {
  currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
  const t = TRANSLATIONS[currentLang];
  if (currentTheme === 'light') {
    document.body.classList.add('light');
    document.getElementById('themeBtn').textContent = t.themeDark;
  } else {
    document.body.classList.remove('light');
    document.getElementById('themeBtn').textContent = t.themeLight;
  }
  localStorage.setItem('vincent_theme', currentTheme);
}

function loadPreferences() {
  const lang = localStorage.getItem('vincent_lang');
  const theme = localStorage.getItem('vincent_theme');
  if (lang) { currentLang = lang; applyTranslations(); }
  if (theme === 'light') { currentTheme = 'light'; document.body.classList.add('light'); document.getElementById('themeBtn').textContent = '🌙 Dark'; }
}

// ─── INIT ─────────────────────────────────────────────────────────────────────
initBots();
loadPreferences();
</script>

<!-- FULLSCREEN CHART MODAL -->
<div class="chart-modal" id="chartModal">
  <div class="chart-modal-header">
    <div>
      <div class="chart-modal-title" id="chartModalTitle">BOT 1 — BTC/USD</div>
      <div class="chart-modal-info" id="chartModalInfo">4h · Fast MA(9) · Slow MA(21)</div>
    </div>
    <button class="chart-modal-close" onclick="closeFullChart()">✕ CHIUDI</button>
  </div>
  <div class="chart-period-bar">
    <span style="font-family:var(--mono);font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em">Periodo:</span>
    <button class="period-btn" onclick="setChartRange('1D')">1G</button>
    <button class="period-btn" onclick="setChartRange('1W')">1S</button>
    <button class="period-btn active" onclick="setChartRange('1M')">1M</button>
    <button class="period-btn" onclick="setChartRange('3M')">3M</button>
    <button class="period-btn" onclick="setChartRange('6M')">6M</button>
    <button class="period-btn" onclick="setChartRange('1Y')">1A</button>
    <button class="period-btn" onclick="setChartRange('ALL')">MAX</button>
  </div>
  <canvas class="chart-modal-canvas" id="chartModalCanvas"></canvas>
  <div class="chart-modal-stats">
    <div class="chart-modal-stat">
      <div class="chart-modal-stat-label">Prezzo Live</div>
      <div class="chart-modal-stat-value" id="cmPrice" style="color:var(--accent)">—</div>
    </div>
    <div class="chart-modal-stat">
      <div class="chart-modal-stat-label">Fast MA</div>
      <div class="chart-modal-stat-value" id="cmFast" style="color:#f5c842">—</div>
    </div>
    <div class="chart-modal-stat">
      <div class="chart-modal-stat-label">Slow MA</div>
      <div class="chart-modal-stat-value" id="cmSlow" style="color:#ff4d6a">—</div>
    </div>
    <div class="chart-modal-stat">
      <div class="chart-modal-stat-label">Segnale</div>
      <div class="chart-modal-stat-value" id="cmSignal">—</div>
    </div>
    <div class="chart-modal-stat">
      <div class="chart-modal-stat-label">P&L</div>
      <div class="chart-modal-stat-value" id="cmPnl">$0.00</div>
    </div>
    <div class="chart-modal-stat">
      <div class="chart-modal-stat-label">Win Rate</div>
      <div class="chart-modal-stat-value" id="cmWinRate">0%</div>
    </div>
  </div>
</div>

<!-- CHART TOOLTIP -->
<div class="chart-tooltip" id="chartTooltip">
  <div class="tooltip-price" id="ttPrice">$0</div>
  <div class="tooltip-ma" style="color:#f5c842" id="ttFast">Fast MA: —</div>
  <div class="tooltip-ma" style="color:#ff4d6a" id="ttSlow">Slow MA: —</div>
  <div class="tooltip-ma" style="color:var(--muted)" id="ttDate">—</div>
</div>
</body>
</html>
"""  

@app.route('/api/bots', methods=['GET'])
def get_bots():
    return jsonify(bots_state)

@app.route('/api/bots', methods=['POST'])
def create_bots():
    global bots_state
    bots_state = request.json
    return jsonify({'status': 'ok'})

@app.route('/api/bot/<bot_id>/toggle', methods=['POST'])
def toggle_bot(bot_id):
    if bot_id in bots_state:
        bots_state[bot_id]['running'] = not bots_state[bot_id].get('running', False)
        return jsonify({'running': bots_state[bot_id]['running']})
    return jsonify({'error': 'Bot not found'}), 404

@app.route('/api/price', methods=['GET'])
def get_price():
    closes = get_kraken_ohlc(240)
    if closes:
        return jsonify({'price': closes[-1], 'history': closes[-60:]})
    return jsonify({'price': 0, 'history': []})

@app.route('/api/ohlc', methods=['GET'])
def get_ohlc():
    interval = request.args.get('interval', 240, type=int)
    closes = get_kraken_ohlc(interval)
    return jsonify({'closes': closes})

if __name__ == '__main__':
    # Start bot runner thread
    thread = threading.Thread(target=run_bots, daemon=True)
    thread.start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
