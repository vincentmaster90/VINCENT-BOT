import os
import time
import json
import requests
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

# ─── BOT STATE (server-side) ──────────────────────────────────────────────────
server_bots = {}
bot_thread = None
bot_thread_running = False

def get_kraken_ohlc(interval=240):
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={interval}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get('error'):
            return []
        result = data['result']
        key = [k for k in result.keys() if k != 'last'][0]
        return [float(c[4]) for c in result[key]]
    except Exception as e:
        print(f"OHLC error: {e}")
        return []

def get_kraken_price():
    try:
        url = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
        r = requests.get(url, timeout=10)
        data = r.json()
        result = data['result']
        key = list(result.keys())[0]
        return float(result[key]['c'][0])
    except:
        return 0

def place_kraken_order(api_key, api_secret, pair, side, volume):
    """Place a real order on Kraken"""
    try:
        url = "https://api.kraken.com/0/private/AddOrder"
        nonce = str(int(time.time() * 1000))
        data = {
            'nonce': nonce,
            'ordertype': 'market',
            'type': side,
            'volume': str(round(volume, 8)),
            'pair': pair
        }
        post_data = urllib.parse.urlencode(data)
        encoded = (nonce + post_data).encode()
        message = '/0/private/AddOrder'.encode() + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(api_secret)
        sig = hmac.new(secret, message, hashlib.sha512)
        signature = base64.b64encode(sig.digest()).decode()
        headers = {
            'API-Key': api_key,
            'API-Sign': signature,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        r = requests.post(url, headers=headers, data=post_data, timeout=10)
        result = r.json()
        if result.get('error') and len(result['error']) > 0:
            return False, result['error'][0]
        return True, result.get('result', {})
    except Exception as e:
        return False, str(e)

def calc_ma(data, period):
    if len(data) < period:
        return []
    return [sum(data[i-period+1:i+1])/period for i in range(period-1, len(data))]

def get_signal(fast, slow):
    if len(fast) < 2 or len(slow) < 2:
        return 'HOLD'
    if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
        return 'BUY'
    if fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
        return 'SELL'
    return 'BULL' if fast[-1] > slow[-1] else 'BEAR'

def bot_tick(bot_id, bot):
    try:
        closes = get_kraken_ohlc(bot['interval'])
        if not closes or len(closes) < 21:
            return

        price = closes[-1]
        ma_fast = calc_ma(closes[-60:], 9)
        ma_slow = calc_ma(closes[-60:], 21)

        bot['price'] = price
        bot['ma_fast'] = round(ma_fast[-1], 2) if ma_fast else 0
        bot['ma_slow'] = round(ma_slow[-1], 2) if ma_slow else 0
        bot['price_history'] = closes[-60:]

        api_key = bot.get('api_key', '')
        api_secret = bot.get('api_secret', '')

        # Check SL/TP
        if bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            if pnl_pct <= -(bot['sl'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] = round(bot.get('pnl', 0) + pnl, 4)
                bot['trades'] = bot.get('trades', 0) + 1
                bot['open_position'] = None
                msg = f"⛔ STOP LOSS @ ${price:.2f} | {pnl_pct*100:.2f}% | P&L: ${pnl:.2f}"
                bot['log'].insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'type': 'sell'})
                if api_key and api_secret:
                    qty = bot['capital'] / entry
                    place_kraken_order(api_key, api_secret, 'XBTUSD', 'sell', qty)
            elif pnl_pct >= (bot['tp'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] = round(bot.get('pnl', 0) + pnl, 4)
                bot['trades'] = bot.get('trades', 0) + 1
                bot['wins'] = bot.get('wins', 0) + 1
                bot['open_position'] = None
                msg = f"✅ TAKE PROFIT @ ${price:.2f} | +{pnl_pct*100:.2f}% | P&L: +${pnl:.2f}"
                bot['log'].insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'type': 'buy'})
                if api_key and api_secret:
                    qty = bot['capital'] / entry
                    place_kraken_order(api_key, api_secret, 'XBTUSD', 'sell', qty)

        signal = get_signal(ma_fast, ma_slow)
        last_signal = bot.get('last_signal', 'HOLD')

        if signal == 'BUY' and last_signal != 'BUY' and not bot.get('open_position'):
            bot['open_position'] = {'price': price, 'time': datetime.now().isoformat()}
            msg = f"🟢 BUY @ ${price:.2f} — Fast MA crossed above Slow MA"
            bot['log'].insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'type': 'buy'})
            if api_key and api_secret:
                qty = bot['capital'] / price
                success, result = place_kraken_order(api_key, api_secret, 'XBTUSD', 'buy', qty)
                if not success:
                    bot['log'].insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'msg': f"⚠ Ordine fallito: {result}", 'type': 'warn'})
        elif signal == 'SELL' and last_signal != 'SELL' and bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            pnl = pnl_pct * bot['capital']
            bot['pnl'] = round(bot.get('pnl', 0) + pnl, 4)
            bot['trades'] = bot.get('trades', 0) + 1
            if pnl > 0:
                bot['wins'] = bot.get('wins', 0) + 1
            bot['open_position'] = None
            msg = f"🔴 SELL @ ${price:.2f} | {pnl_pct*100:.2f}% | P&L: ${pnl:.2f}"
            bot['log'].insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'type': 'sell'})
            if api_key and api_secret:
                qty = bot['capital'] / entry
                place_kraken_order(api_key, api_secret, 'XBTUSD', 'sell', qty)

        bot['signal'] = signal
        bot['last_signal'] = signal
        bot['log'] = bot['log'][:100]  # Keep last 100 entries
        bot['last_updated'] = datetime.now().strftime('%H:%M:%S')

    except Exception as e:
        print(f"Bot {bot_id} tick error: {e}")

def run_bots_forever():
    global bot_thread_running
    print("Bot runner thread started")
    while bot_thread_running:
        for bot_id, bot in list(server_bots.items()):
            if bot.get('running'):
                bot_tick(bot_id, bot)
        time.sleep(30)
    print("Bot runner thread stopped")

# ─── HTML (embedded) ──────────────────────────────────────────────────────────
html_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
try:
    with open(html_path, 'r') as f:
        HTML_CONTENT = f.read()
except:
    HTML_CONTENT = '<h1>Loading...</h1>'

# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return HTML_CONTENT

@app.route('/api/bots', methods=['GET'])
def get_bots():
    # Return bots without sensitive data
    safe_bots = {}
    for bid, bot in server_bots.items():
        safe_bot = {k: v for k, v in bot.items() if k not in ['api_key', 'api_secret']}
        safe_bots[bid] = safe_bot
    return jsonify(safe_bots)

@app.route('/api/bots', methods=['POST'])
def save_bots():
    global server_bots, bot_thread, bot_thread_running
    data = request.json
    for bid, bot in data.items():
        if bid in server_bots:
            # Preserve API keys and runtime data
            bot['api_key'] = server_bots[bid].get('api_key', '')
            bot['api_secret'] = server_bots[bid].get('api_secret', '')
            bot['log'] = server_bots[bid].get('log', [])
            bot['signal'] = server_bots[bid].get('signal', 'HOLD')
            bot['open_position'] = server_bots[bid].get('open_position', None)
        else:
            bot['log'] = []
            bot['signal'] = 'HOLD'
            bot['open_position'] = None
        server_bots[bid] = bot
    return jsonify({'status': 'ok'})

@app.route('/api/connect', methods=['POST'])
def connect_api():
    global bot_thread, bot_thread_running
    data = request.json
    api_key = data.get('api_key', '')
    api_secret = data.get('api_secret', '')
    # Store keys in all bots
    for bot in server_bots.values():
        bot['api_key'] = api_key
        bot['api_secret'] = api_secret
    # Start bot runner thread if not running
    if not bot_thread_running:
        bot_thread_running = True
        bot_thread = threading.Thread(target=run_bots_forever, daemon=True)
        bot_thread.start()
    return jsonify({'status': 'connected'})

@app.route('/api/bot/<bot_id>/toggle', methods=['POST'])
def toggle_bot(bot_id):
    if bot_id in server_bots:
        server_bots[bot_id]['running'] = not server_bots[bot_id].get('running', False)
        status = server_bots[bot_id]['running']
        t = datetime.now().strftime('%H:%M:%S')
        msg = f"▶ Bot avviato sul server" if status else "■ Bot fermato"
        server_bots[bot_id]['log'].insert(0, {'time': t, 'msg': msg, 'type': 'buy' if status else 'warn'})
        return jsonify({'running': status})
    return jsonify({'error': 'not found'}), 404

@app.route('/api/bot/<bot_id>/start_all', methods=['POST'])
def start_all(bot_id):
    for bot in server_bots.values():
        bot['running'] = True
    return jsonify({'status': 'all started'})

@app.route('/api/stop_all', methods=['POST'])
def stop_all():
    for bot in server_bots.values():
        bot['running'] = False
    return jsonify({'status': 'all stopped'})

@app.route('/api/price', methods=['GET'])
def get_price():
    price = get_kraken_price()
    return jsonify({'price': price})

@app.route('/api/ohlc', methods=['GET'])
def get_ohlc():
    interval = request.args.get('interval', 240, type=int)
    since = request.args.get('since', 0, type=int)
    closes = get_kraken_ohlc(interval)
    return jsonify({'closes': closes})

@app.route('/api/log', methods=['GET'])
def get_log():
    all_logs = []
    for bot_id, bot in server_bots.items():
        for entry in bot.get('log', [])[:20]:
            all_logs.append({**entry, 'bot': bot.get('name', bot_id)})
    all_logs.sort(key=lambda x: x.get('time', ''), reverse=True)
    return jsonify(all_logs[:50])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
