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

KRAKEN_API_KEY = os.environ.get('KRAKEN_API_KEY', '')
KRAKEN_API_SECRET = os.environ.get('KRAKEN_API_SECRET', '')

TRADES_FILE = '/app/trade_history.json'

server_bots = {}
for i in range(1, 11):
    server_bots[str(i)] = {
        'id': i,
        'name': 'Bot ' + str(i),
        'interval': 240,
        'capital': 13.80,
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
    }

trade_history = []
bot_thread_running = False


def load_trade_history():
    global trade_history
    try:
        with open(TRADES_FILE, 'r') as f:
            trade_history = json.load(f)
        print('Loaded ' + str(len(trade_history)) + ' trades from history')
    except:
        trade_history = []


def save_trade_history():
    try:
        with open(TRADES_FILE, 'w') as f:
            json.dump(trade_history, f)
    except Exception as e:
        print('Save error: ' + str(e))


def record_trade(bot, trade_type, price, pnl=None, reason='Signal'):
    trade = {
        'id': len(trade_history) + 1,
        'bot': bot['name'],
        'type': trade_type,
        'price': round(price, 2),
        'capital': bot['capital'],
        'pnl': round(pnl, 4) if pnl is not None else None,
        'pnl_pct': round((pnl / bot['capital']) * 100, 2) if pnl is not None else None,
        'reason': reason,
        'sl': bot['sl'],
        'tp': bot['tp'],
        'interval': bot['interval'],
        'date': datetime.now().strftime('%Y-%m-%d'),
        'time': datetime.now().strftime('%H:%M:%S'),
        'timestamp': datetime.now().isoformat(),
        'signal': bot['signal'],
        'ma_fast': bot['ma_fast'],
        'ma_slow': bot['ma_slow'],
    }
    trade_history.append(trade)
    save_trade_history()
    return trade


def get_kraken_ohlc(interval=240):
    try:
        url = 'https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=' + str(interval)
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get('error') and data['error']:
            return []
        result = data['result']
        key = [k for k in result.keys() if k != 'last'][0]
        return [float(c[4]) for c in result[key]]
    except Exception as e:
        print('OHLC error: ' + str(e))
        return []


def get_kraken_price():
    try:
        url = 'https://api.kraken.com/0/public/Ticker?pair=XBTUSD'
        r = requests.get(url, timeout=10)
        data = r.json()
        result = data['result']
        key = list(result.keys())[0]
        return float(result[key]['c'][0])
    except:
        return 0


def place_kraken_order(side, volume):
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        return False, 'No API keys'
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
    t = datetime.now().strftime('%H:%M:%S')
    bot['log'].insert(0, {'time': t, 'msg': msg, 'type': log_type})
    bot['log'] = bot['log'][:100]
    print('[' + bot['name'] + '] ' + msg)


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

        if bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            if pnl_pct <= -(bot['sl'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] = round(bot['pnl'] + pnl, 4)
                bot['trades'] += 1
                bot['open_position'] = None
                add_log(bot, 'STOP LOSS @ $' + str(round(price, 2)) + ' | ' + str(round(pnl_pct*100, 2)) + '% | $' + str(round(pnl, 2)), 'sell')
                record_trade(bot, 'SELL', price, pnl, 'Stop Loss')
                qty = bot['capital'] / entry
                place_kraken_order('sell', qty)
            elif pnl_pct >= (bot['tp'] / 100):
                pnl = pnl_pct * bot['capital']
                bot['pnl'] = round(bot['pnl'] + pnl, 4)
                bot['trades'] += 1
                bot['wins'] += 1
                bot['open_position'] = None
                add_log(bot, 'TAKE PROFIT @ $' + str(round(price, 2)) + ' | +' + str(round(pnl_pct*100, 2)) + '% | +$' + str(round(pnl, 2)), 'buy')
                record_trade(bot, 'SELL', price, pnl, 'Take Profit')
                qty = bot['capital'] / entry
                place_kraken_order('sell', qty)

        signal = get_signal(ma_fast, ma_slow)
        last = bot.get('last_signal', 'HOLD')

        if signal == 'BUY' and last != 'BUY' and not bot.get('open_position'):
            bot['open_position'] = {'price': price, 'time': datetime.now().isoformat()}
            add_log(bot, 'BUY @ $' + str(round(price, 2)), 'buy')
            record_trade(bot, 'BUY', price, None, 'MA Crossover')
            qty = bot['capital'] / price
            success, result = place_kraken_order('buy', qty)
            if not success:
                add_log(bot, 'Order failed: ' + str(result), 'warn')
        elif signal == 'SELL' and last != 'SELL' and bot.get('open_position'):
            entry = bot['open_position']['price']
            pnl_pct = (price - entry) / entry
            pnl = pnl_pct * bot['capital']
            bot['pnl'] = round(bot['pnl'] + pnl, 4)
            bot['trades'] += 1
            if pnl > 0:
                bot['wins'] += 1
            bot['open_position'] = None
            add_log(bot, 'SELL @ $' + str(round(price, 2)) + ' | ' + str(round(pnl_pct*100, 2)) + '% | $' + str(round(pnl, 2)), 'sell')
            record_trade(bot, 'SELL', price, pnl, 'MA Crossover')
            qty = bot['capital'] / entry
            place_kraken_order('sell', qty)

        bot['signal'] = signal
        bot['last_signal'] = signal

    except Exception as e:
        print('Tick error: ' + str(e))


def run_bots_forever():
    global bot_thread_running
    print('Bot runner started - API key: ' + ('SET' if KRAKEN_API_KEY else 'NOT SET'))
    while bot_thread_running:
        active = [b for b in server_bots.values() if b.get('running')]
        for bot in active:
            bot_tick(bot)
        if active:
            print('Tick done - ' + str(len(active)) + ' bots active at ' + datetime.now().strftime('%H:%M:%S'))
        time.sleep(30)


def start_bot_thread():
    global bot_thread_running
    if not bot_thread_running:
        bot_thread_running = True
        t = threading.Thread(target=run_bots_forever, daemon=True)
        t.start()
        print('Bot thread started')


load_trade_history()

html_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
try:
    with open(html_path, 'r') as f:
        HTML_CONTENT = f.read()
except:
    HTML_CONTENT = '<h1>Vincent Bot Server Running</h1><p>Bot 1 is active.</p>'


_started = False


@app.before_request
def auto_start():
    global _started
    if not _started:
        _started = True
        start_bot_thread()


@app.route('/')
def index():
    return HTML_CONTENT


@app.route('/api/status')
def status():
    active = sum(1 for b in server_bots.values() if b.get('running'))
    return jsonify({
        'status': 'running',
        'api_key_set': bool(KRAKEN_API_KEY),
        'active_bots': active,
        'total_bots': len(server_bots),
        'total_trades': len(trade_history)
    })


@app.route('/api/bots', methods=['GET'])
def get_bots():
    return jsonify(server_bots)


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
        st = server_bots[bot_id]['running']
        add_log(server_bots[bot_id], 'Bot started on server' if st else 'Bot stopped', 'buy' if st else 'warn')
        return jsonify({'running': st})
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


@app.route('/api/log')
def get_log():
    all_logs = []
    for bot in server_bots.values():
        for entry in bot.get('log', [])[:10]:
            e = dict(entry)
            e['bot_name'] = bot['name']
            all_logs.append(e)
    all_logs.sort(key=lambda x: x.get('time', ''), reverse=True)
    return jsonify(all_logs[:50])


@app.route('/api/trades')
def get_trades():
    return jsonify(trade_history)


@app.route('/api/trades/summary')
def trades_summary():
    total = len(trade_history)
    buys = [t for t in trade_history if t['type'] == 'BUY']
    sells = [t for t in trade_history if t['type'] == 'SELL']
    wins = [t for t in sells if t.get('pnl') and t['pnl'] > 0]
    losses = [t for t in sells if t.get('pnl') and t['pnl'] <= 0]
    total_pnl = sum(t.get('pnl', 0) for t in sells if t.get('pnl'))
    best = max((t.get('pnl', 0) for t in sells), default=0)
    worst = min((t.get('pnl', 0) for t in sells), default=0)
    win_rate = round(len(wins) / len(sells) * 100, 1) if sells else 0
    return jsonify({
        'total_trades': total,
        'buys': len(buys),
        'sells': len(sells),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'total_pnl': round(total_pnl, 4),
        'best_trade': round(best, 4),
        'worst_trade': round(worst, 4),
    })


@app.route('/api/price')
def get_price():
    return jsonify({'price': get_kraken_price()})


@app.route('/api/ohlc')
def get_ohlc():
    interval = request.args.get('interval', 240, type=int)
    return jsonify({'closes': get_kraken_ohlc(interval)})


if __name__ == '__main__':
    start_bot_thread()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
