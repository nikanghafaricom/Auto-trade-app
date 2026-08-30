# ==============================================
# Hybrid Signal Bot - نسخه همروش (Hamravesh - Webhook Receiver & Exchange Auto-Sync)
# ==============================================
import os
import time
import logging
import gc
import json
import hmac
import hashlib
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import ccxt
import requests
from dotenv import load_dotenv

load_dotenv()

# ==================== لاگ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("trading_signals.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== تنظیمات ====================
class Config:
    TABDEAL_API_KEY = os.getenv("TABDEAL_API_KEY", "")
    TABDEAL_SECRET = os.getenv("TABDEAL_SECRET", "")
    RENDER_WEBHOOK_URL = os.getenv("RENDER_WEBHOOK_URL", "")
    SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")

    def validate(self):
        pass

# ==================== مدیریت معاملات با همگام‌سازی خودکار صرافی ====================
class TabdealTrader:
    def __init__(self, config: Config):
        self.config = config
        self.initial_capital = None
        self.last_capital_reset_time = None
        self.active_positions = {}
        
        try:
            self.exchange = ccxt.tabdeal({
                'apiKey': config.TABDEAL_API_KEY,
                'secret': config.TABDEAL_SECRET,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
            logger.info("اتصال به صرافی تبدیل با موفقیت راه‌اندازی شد.")
            # همگام‌سازی خودکار پوزیشن‌ها با صرافی هنگام استارت
            self.sync_positions_from_exchange()
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی اتصال به صرافی تبدیل: {e}")
            self.exchange = None

    def sync_positions_from_exchange(self):
        """بررسی خودکار موجودی صرافی و بازیابی پوزیشن‌های باز موقع روشن شدن ربات"""
        try:
            if not self.exchange:
                return
            
            logger.info("در حال بررسی موجودی صرافی برای بازیابی پوزیشن‌های باز...")
            balance = self.exchange.fetch_balance()
            free_balances = balance.get('free', {})
            
            for currency, amount in free_balances.items():
                if currency.upper() in ['USDT', 'IRT', 'IRR', 'TOMAN']:
                    continue
                
                # اگر مقداری از یک ارز دیجیتال در حساب موجود باشد
                if float(amount) > 0:
                    symbol = f"{currency.upper()}/USDT"
                    try:
                        # دریافت آخرین قیمت خرید از تاریخچه معاملات صرافی
                        trades = self.exchange.fetch_my_trades(symbol, limit=5)
                        entry_price = 0.0
                        if trades:
                            # آخرین معامله خرید را پیدا می‌کنیم
                            buy_trades = [t for t in trades if t['side'] == 'buy']
                            if buy_trades:
                                entry_price = float(buy_trades[-1]['price'])
                        
                        if entry_price == 0:
                            # اگر تاریخچه پیدا نشد، از قیمت لحظه‌ای فعلی استفاده کن
                            ticker = self.exchange.fetch_ticker(symbol)
                            entry_price = float(ticker['last'])

                        tp_price = entry_price * 1.025
                        sl_price = entry_price * 0.985

                        self.active_positions[symbol] = {
                            "entry_price": entry_price,
                            "tp_price": tp_price,
                            "sl_price": sl_price
                        }
                        logger.info(f"پوزیشن باز برای {symbol} از صرافی بازیابی شد! قیمت ورود: {entry_price} | TP: {tp_price} | SL: {sl_price}")
                    except Exception as ex:
                        logger.error(f"خطا در بازیابی اطلاعات پوزیشن {symbol}: {ex}")
                        
        except Exception as e:
            logger.error(f"خطا در همگام‌سازی پوزیشن‌ها با صرافی: {e}")

    def _generate_signature(self, query_string: str) -> str:
        secret_bytes = self.config.TABDEAL_SECRET.encode('utf-8')
        message_bytes = query_string.encode('utf-8')
        return hmac.new(secret_bytes, message_bytes, hashlib.sha256).hexdigest()

    def get_usdt_balance(self) -> Optional[float]:
        try:
            url = "https://api1.tabdeal.org/api/v1/account"
            timestamp = str(int(time.time() * 1000))
            query_string = f"timestamp={timestamp}"
            signature = self._generate_signature(query_string)
            
            headers = {
                "X-MBX-APIKEY": self.config.TABDEAL_API_KEY,
                "Content-Type": "application/json"
            }
            full_url = f"{url}?{query_string}&signature={signature}"

            res = requests.get(full_url, headers=headers, timeout=10)
            if res.status_code == 200:
                response = res.json()
                data = response.get('data', response.get('balances', response.get('assets', [])))
                if isinstance(data, list):
                    for asset in data:
                        if asset.get('currency', '').upper() == 'USDT' or asset.get('asset', '').upper() == 'USDT':
                            return float(asset.get('free', asset.get('balance', 0.0)))
                return 0.0
            return None
        except Exception as e:
            return None

    def check_and_update_capital(self, current_balance: float):
        now = datetime.now()
        if self.initial_capital is None or self.last_capital_reset_time is None:
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
        elif now - self.last_capital_reset_time >= timedelta(hours=3):
            self.initial_capital = current_balance
            self.last_capital_reset_time = now

    def check_tp_sl_and_update(self, symbol: str, current_price: float) -> Optional[dict]:
        if symbol not in self.active_positions:
            return None
        
        pos = self.active_positions[symbol]
        entry_price = pos["entry_price"]
        tp_price = pos["tp_price"]
        sl_price = pos["sl_price"]
        
        if current_price >= tp_price or current_price <= sl_price:
            logger.info(f"حد سود یا حد زیان برای {symbol} فعال شد! قیمت لحظه‌ای: {current_price} | قیمت ورود: {entry_price}")
            return self.execute_spot_order(symbol, "SELL", current_price)
            
        return None

    def execute_spot_order(self, symbol: str, side: str, price: float):
        if not self.exchange:
            return None

        try:
            usdt_balance = self.get_usdt_balance()
            if usdt_balance is None:
                return None

            self.check_and_update_capital(usdt_balance)

            if side == "BUY":
                if symbol in self.active_positions:
                    return None

                if usdt_balance < 1.0:
                    return None

                base_capital = self.initial_capital if self.initial_capital and self.initial_capital > 0 else usdt_balance
                allocated_budget = base_capital * 0.20
                if allocated_budget < 1.0:
                    allocated_budget = 1.0

                if usdt_balance < allocated_budget:
                    return None

                amount_to_buy = allocated_budget / price
                order = self.exchange.create_market_buy_order(symbol, amount_to_buy)
                
                tp_price = price * 1.025
                sl_price = price * 0.985
                
                self.active_positions[symbol] = {
                    "entry_price": price,
                    "tp_price": tp_price,
                    "sl_price": sl_price
                }
                logger.info(f"سفارش خرید اسپات ثبت شد. TP: {tp_price} | SL: {sl_price}")
                return None

            elif side == "SELL":
                base_currency = symbol.split('/')[0]
                base_free = 0.0
                try:
                    url = "https://api1.tabdeal.org/api/v1/account"
                    timestamp = str(int(time.time() * 1000))
                    query_string = f"timestamp={timestamp}"
                    signature = self._generate_signature(query_string)
                    headers = {"X-MBX-APIKEY": self.config.TABDEAL_API_KEY, "Content-Type": "application/json"}
                    full_url = f"{url}?{query_string}&signature={signature}"
                    res = requests.get(full_url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        response = res.json()
                        data = response.get('data', response.get('balances', response.get('assets', [])))
                        if isinstance(data, list):
                            for asset in data:
                                if asset.get('currency', '').upper() == base_currency.upper() or asset.get('asset', '').upper() == base_currency.upper():
                                    base_free = float(asset.get('free', asset.get('balance', 0.0)))
                                    break
                except Exception as e:
                    pass
                
                if base_free > 0:
                    order = self.exchange.create_market_sell_order(symbol, base_free)
                    pnl_percent = 0.0
                    if symbol in self.active_positions:
                        entry_price = self.active_positions[symbol]["entry_price"]
                        pnl_percent = ((price - entry_price) / entry_price) * 100
                        del self.active_positions[symbol]

                    return {
                        "action": "close_trade",
                        "symbol": symbol,
                        "side": "SELL",
                        "exit_price": price,
                        "pnl": round(pnl_percent, 2)
                    }
                else:
                    if symbol in self.active_positions:
                        del self.active_positions[symbol]
                    return None

        except Exception as e:
            logger.error(f"خطا در اجرای سفارش صرافی تبدیل برای {symbol}: {e}")
            return None

# ==================== ارتباط با رندر ====================
class RenderNotifier:
    def __init__(self, config: Config):
        self.config = config

    def send_to_render(self, payload: dict):
        if not self.config.RENDER_WEBHOOK_URL:
            return
        try:
            secret_token = self.config.SECRET_TOKEN
            headers = {"X-Secret-Token": secret_token}
            requests.post(self.config.RENDER_WEBHOOK_URL, json=payload, headers=headers, timeout=10)
        except Exception as e:
            logger.error(f"خطا در ارسال داده به رندر: {e}")

# ==================== وب‌سرور همروش ====================
class HamraveshWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Hamravesh Bot is alive and running!")

    def do_POST(self):
        try:
            auth_token = self.headers.get("X-Secret-Token")
            config = Config()
            
            if config.SECRET_TOKEN and auth_token != config.SECRET_TOKEN:
                self.send_response(403)
                self.end_headers()
                return

            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            action = data.get("action")

            if action == "ping":
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "pong"}).encode('utf-8'))
                return

            if action == "execute_trade":
                symbol = data.get("symbol")
                side = data.get("side")
                price = data.get("price")
                
                trader = TabdealTrader(config)
                trade_result = trader.execute_spot_order(symbol, side, price)

                if trade_result:
                    notifier = RenderNotifier(config)
                    notifier.send_to_render(trade_result)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": "Processed"}).encode('utf-8'))

        except Exception as e:
            logger.error(f"خطا در پردازش وب‌هوک همروش: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        return

def start_hamravesh_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HamraveshWebhookHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"خطا در اجرای وب‌سرور همروش: {e}")

threading.Thread(target=start_hamravesh_server, daemon=True).start()

if __name__ == "__main__":
    logger.info("بخش همروش فعال شد.")
    try:
        config = Config()
        trader = TabdealTrader(config)
        trader.get_usdt_balance()

        while True:
            if trader.active_positions and trader.exchange:
                for symbol in list(trader.active_positions.keys()):
                    try:
                        ticker = trader.exchange.fetch_ticker(symbol)
                        current_price = float(ticker['last'])
                        close_result = trader.check_tp_sl_and_update(symbol, current_price)
                        if close_result:
                            notifier = RenderNotifier(config)
                            notifier.send_to_render(close_result)
                    except Exception as e:
                        pass
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("بخش همروش متوقف شد.")
