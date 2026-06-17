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

# ─── LOAD API KEYS FROM ENVIRONMENT ──────────────────────────────────────────
KRAKEN_API_KEY = os.environ.get('KRAKEN_API_KEY', '')
KRAKEN_API_SECRET = os.environ.get('KRAKEN_API_SECRET', '')

# ─── BOT STATE ────────────────────────────────────────────────────────────────
server_bots = {
    str(i): {
        'id': i,
        'name': f'Bot {i}',
        'interval': 240,
        'capital': 1.38,
        'sl': 3,
        'tp': 6,
        'running': True if i == 1 else False,
        'signal': 'HOLD',
        'pnl': 0,
        'trades': 0,
        'wins': 0,
        'open_position': None,
        'last_signal': 'HOLD',
        'log': [],
        'price': 0,
        'ma_fast': 0,
        'ma_slow': 0,
    } for i in range(1, 11)
}

bot_thread_running = False

def get_kraken_ohlc(interval=240):
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={interval}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get('error') and data['error']:
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

def place_kraken_order(side, volume):
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        return False, "No API keys configured"
    try:
        nonce = str(int(time.time() * 1000))
        data = {
            'nonce': nonce,
            'ordertype': 'market',
            'type': side,
            'volume': str(round(volume, 8)),
            'pair': 'XBTUSD'
        }
        post_data = urllib.parse.urlencode(data)
        encoded = (nonce + post_data).encode()
        message = '/0/private/AddOrder'.encode() + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(KRAKEN_API_SECRET)
        sig = hmac.new(secret, message, hashlib.sha512)
        signature = base64.b64encode(sig.digest()).decode()
        headers = {
            'API-Key': KRAKEN_API_KEY,
            'API-Sign': signature,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        r = requests.post('https://api.kraken.com/0/private/AddOrder',
                         headers=headers, data=post_data, timeout=10)
        result = r.json()
        if result.get('error') and result['error']:
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

def add_log(bot, msg, log_type='info'):
    entry = {'time': datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'type': log_type}
    bot['log'].insert(0, entry)
    bot['log'] = bot['log'][:100]
    print(f"[{bot['name']}] {msg}")

def bot_tick(bot):
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

        # Check SL/TP
        if bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            if pnl_pct <= -(bot['sl'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] = round(bot['pnl'] + pnl, 4)
                bot['trades'] += 1
                bot['open_position'] = None
                add_log(bot, f"⛔ STOP LOSS @ ${price:.2f} | {pnl_pct*100:.2f}% | ${pnl:.2f}", 'sell')
                qty = bot['capital'] / entry
                place_kraken_order('sell', qty)
            elif pnl_pct >= (bot['tp'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] = round(bot['pnl'] + pnl, 4)
                bot['trades'] += 1
                bot['wins'] += 1
                bot['open_position'] = None
                add_log(bot, f"✅ TAKE PROFIT @ ${price:.2f} | +{pnl_pct*100:.2f}% | +${pnl:.2f}", 'buy')
                qty = bot['capital'] / entry
                place_kraken_order('sell', qty)

        signal = get_signal(ma_fast, ma_slow)
        last = bot.get('last_signal', 'HOLD')

        if signal == 'BUY' and last != 'BUY' and not bot.get('open_position'):
            bot['open_position'] = {'price': price, 'time': datetime.now().isoformat()}
            add_log(bot, f"🟢 BUY @ ${price:.2f}", 'buy')
            qty = bot['capital'] / price
            success, result = place_kraken_order('buy', qty)
            if not success:
                add_log(bot, f"⚠ Ordine fallito: {result}", 'warn')
        elif signal == 'SELL' and last != 'SELL' and bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            pnl = pnl_pct * bot['capital']
            bot['pnl'] = round(bot['pnl'] + pnl, 4)
            bot['trades'] += 1
            if pnl > 0:
                bot['wins'] += 1
            bot['open_position'] = None
            add_log(bot, f"🔴 SELL @ ${price:.2f} | {pnl_pct*100:.2f}% | ${pnl:.2f}", 'sell')
            qty = bot['capital'] / entry
            place_kraken_order('sell', qty)

        bot['signal'] = signal
        bot['last_signal'] = signal

    except Exception as e:
        print(f"Tick error [{bot['name']}]: {e}")

def run_bots_forever():
    global bot_thread_running
    print(f"🚀 Bot runner started — API key: {'SET' if KRAKEN_API_KEY else 'NOT SET'}")
    while bot_thread_running:
        active = [b for b in server_bots.values() if b.get('running')]
        for bot in active:
            bot_tick(bot)
        if active:
            print(f"Tick complete — {len(active)} bots active")
        time.sleep(30)

# ─── AUTO-START BOT THREAD ────────────────────────────────────────────────────
def start_bot_thread()

# Auto-activate Bot 1 on server start
server_bots['1']['running'] = True
server_bots['1']['interval'] = 60  # 1 hour for faster signals
server_bots['1']['sl'] = 0.5
server_bots['1']['tp'] = 1.0
server_bots['1']['capital'] = 13.80  # 20% of $69
print("✅ Bot 1 auto-activated on server start"):
    global bot_thread_running
    if not bot_thread_running:
        bot_thread_running = True
        t = threading.Thread(target=run_bots_forever, daemon=True)
        t.start()
        print("Bot thread started")

start_bot_thread()

# Auto-activate Bot 1 on server start
server_bots['1']['running'] = True
server_bots['1']['interval'] = 60  # 1 hour for faster signals
server_bots['1']['sl'] = 0.5
server_bots['1']['tp'] = 1.0
server_bots['1']['capital'] = 13.80  # 20% of $69
print("✅ Bot 1 auto-activated on server start")

# ─── HTML ─────────────────────────────────────────────────────────────────────
html_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
try:
    with open(html_path, 'r') as f:
        HTML_CONTENT = f.read()
except:
    HTML_CONTENT = '<h1>Bot Server Running</h1>'

# ─── AUTO START BOTS ON FIRST REQUEST ────────────────────────────────────────
_started = False

@app.before_request
def auto_start():
    global _started
    if not _started:
        _started = True
        start_bot_thread()

# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return HTML_CONTENT

@app.route('/api/status', methods=['GET'])
def status():
    active = sum(1 for b in server_bots.values() if b.get('running'))
    return jsonify({
        'status': 'running',
        'api_key_set': bool(KRAKEN_API_KEY),
        'active_bots': active,
        'total_bots': len(server_bots)
    })

@app.route('/api/bots', methods=['GET'])
def get_bots():
    safe = {}
    for bid, bot in server_bots.items():
        safe[bid] = {k: v for k, v in bot.items() if k not in ['api_key', 'api_secret']}
    return jsonify(safe)

@app.route('/api/bots', methods=['POST'])
def save_bots():
    data = request.json
    for bid, bot_data in data.items():
        if bid in server_bots:
            for k in ['interval', 'capital', 'sl', 'tp', 'name']:
                if k in bot_data:
                    server_bots[bid][k] = bot_data[k]
    return jsonify({'status': 'ok'})

@app.route('/api/bot/<bot_id>/toggle', methods=['POST'])
def toggle_bot(bot_id):
    if bot_id in server_bots:
        server_bots[bot_id]['running'] = not server_bots[bot_id].get('running', False)
        status = server_bots[bot_id]['running']
        add_log(server_bots[bot_id], 
                '▶ Bot avviato sul server 24/7' if status else '■ Bot fermato',
                'buy' if status else 'warn')
        return jsonify({'running': status})
    return jsonify({'error': 'not found'}), 404

@app.route('/api/stop_all', methods=['POST'])
def stop_all():
    for bot in server_bots.values():
        bot['running'] = False
    return jsonify({'status': 'stopped'})

@app.route('/api/start_all', methods=['POST'])
def start_all():
    for bot in server_bots.values():
        bot['running'] = True
    return jsonify({'status': 'started'})

@app.route('/api/log', methods=['GET'])
def get_log():
    all_logs = []
    for bot in server_bots.values():
        for entry in bot.get('log', [])[:10]:
            all_logs.append({**entry, 'bot_name': bot['name']})
    all_logs.sort(key=lambda x: x.get('time', ''), reverse=True)
    return jsonify(all_logs[:50])

@app.route('/api/price', methods=['GET'])
def get_price():
    return jsonify({'price': get_kraken_price()})

@app.route('/api/ohlc', methods=['GET'])
def get_ohlc():
    interval = request.args.get('interval', 240, type=int)
    return jsonify({'closes': get_kraken_ohlc(interval)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
