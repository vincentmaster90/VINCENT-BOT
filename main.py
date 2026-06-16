import os
import time
import json
import requests
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
from flask import Flask, render_template, jsonify, request
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
    return render_template('index.html')

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
