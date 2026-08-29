# ==============================================
# Hybrid Signal Bot - نسخه همروش (Hamravesh - Webhook Receiver & Tabdeal Spot Execution)
# ==============================================
import os
import time
import logging
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
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

# ==================== مدیریت معاملات واقعی در صرافی تبدیل ====================
class TabdealTrader:
    def __init__(self, config: Config):
        self.config = config
        self.initial_capital = None
        self.last_capital_reset_time = None
        self.active_positions = {}  

    def get_usdt_balance(self) -> float:
        """گرفتن موجودی واقعی تتر از مسیر استاندارد تبدیل"""
        # آدرس رسمی موجودی بر اساس مستندات تبدیل
        url = "https://api1.tabdeal.org/r/api/v1/account/balances"
        
        headers = {
            "X-API-Key": self.config.TABDEAL_API_KEY,
            "X-API-Secret": self.config.TABDEAL_SECRET,
            "Content-Type": "application/json"
        }

        try:
            res = requests.get(url, headers=headers, timeout=10)
            logger.info(f"درخواست موجودی به تبدیل - کد پاسخ: {res.status_code}")
            
            if res.status_code == 200:
                response_data = res.json()
                items = response_data.get('data', response_data.get('balances', response_data))
                if isinstance(items, list):
                    for item in items:
                        currency = item.get('currency', item.get('asset', '')).upper()
                        if currency == 'USDT':
                            balance = float(item.get('free', item.get('balance', 0.0)))
                            logger.info(f"موجودی واقعی تتر با موفقیت اخذ شد: {balance} USDT")
                            return balance
            else:
                logger.error(f"خطای صرافی در دریافت موجودی: {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"خطای ارتباطی در دریافت موجودی تتر: {e}")

        return 0.0

    def check_and_update_capital(self, current_balance: float):
        now = datetime.now()
        if self.initial_capital is None or self.last_capital_reset_time is None:
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
            logger.info(f"سرمایه پایه اولیه ثبت شد: {self.initial_capital} USDT")
        elif now - self.last_capital_reset_time >= timedelta(hours=3):
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
            logger.info(f"سرمایه پایه به‌روز شد: {self.initial_capital} USDT")

    def execute_spot_order(self, symbol: str, side: str, price: float, usdt_allocation_percent: float = 0.50):
        try:
            usdt_balance = self.get_usdt_balance()
            if usdt_balance <= 0:
                logger.error("خطا: موجودی تتر صرافی صفر یا قابل خواندن نیست. معامله متوقف شد.")
                return None

            self.check_and_update_capital(usdt_balance)

            base_capital = self.initial_capital if self.initial_capital and self.initial_capital > 0 else usdt_balance
            allocated_budget = base_capital * usdt_allocation_percent
            
            if usdt_balance < allocated_budget:
                logger.warning(f"موجودی کافی نیست. موجودی: {usdt_balance}، بودجه مورد نیاز: {allocated_budget}")
                return None

            amount_to_buy = allocated_budget / price
            logger.info(f"تخصیص بودجه برای {symbol}: {allocated_budget} USDT (حجم: {amount_to_buy})")

            headers = {
                "X-API-Key": self.config.TABDEAL_API_KEY,
                "X-API-Secret": self.config.TABDEAL_SECRET,
                "Content-Type": "application/json"
            }

            if side == "BUY":
                order_url = "https://api1.tabdeal.org/r/api/v1/order"
                payload = {
                    "symbol": symbol.replace('/', '').lower(),
                    "side": "buy",
                    "type": "market",
                    "amount": amount_to_buy
                }
                res = requests.post(order_url, json=payload, headers=headers, timeout=10)
                logger.info(f"پاسخ ثبت سفارش خرید از صرافی: {res.status_code} - {res.text}")
                
                self.active_positions[symbol] = {"entry_price": price}
                return None

            elif side == "SELL":
                order_url = "https://api1.tabdeal.org/r/api/v1/order"
                payload = {
                    "symbol": symbol.replace('/', '').lower(),
                    "side": "sell",
                    "type": "market"
                }
                res = requests.post(order_url, json=payload, headers=headers, timeout=10)
                logger.info(f"پاسخ ثبت سفارش فروش از صرافی: {res.status_code} - {res.text}")

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
                
        except Exception as e:
            logger.error(f"خطا در اجرای سفارش واقعی در صرافی تبدیل برای {symbol}: {e}")
            return None

# ==================== ارتباط با رندر ====================
class RenderNotifier:
    def __init__(self, config: Config):
        self.config = config

    def send_to_render(self, payload: dict):
        if not self.config.RENDER_WEBHOOK_URL:
            return
        try:
            headers = {"X-Secret-Token": self.config.SECRET_TOKEN}
            requests.post(self.config.RENDER_WEBHOOK_URL, json=payload, headers=headers, timeout=10)
        except Exception as e:
            logger.error(f"خطا در ارسال داده به رندر: {e}")

# ==================== وب‌سرور همروش ====================
class HamraveshWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Hamravesh Bot is active with Real Tabdeal Trading!")

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
            
            if data.get("action") == "ping":
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "pong"}).encode('utf-8'))
                return

            if data.get("action") == "execute_trade":
                trader = TabdealTrader(config)
                trade_result = trader.execute_spot_order(
                    data.get("symbol"), 
                    data.get("side"), 
                    data.get("price")
                )

                if trade_result:
                    notifier = RenderNotifier(config)
                    notifier.send_to_render(trade_result)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))

        except Exception as e:
            logger.error(f"خطا در وب‌هوک همروش: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        return

def start_hamravesh_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HamraveshWebhookHandler)
    server.serve_forever()

threading.Thread(target=start_hamravesh_server, daemon=True).start()

if __name__ == "__main__":
    logger.info("سرویس همروش و اتصال به صرافی تبدیل استارت شد.")
    try:
        config = Config()
        # تست اولیه گرفتن موجودی به محض استارت
        trader = TabdealTrader(config)
        trader.get_usdt_balance()
    except Exception as e:
        logger.error(f"خطا: {e}")

    while True:
        time.sleep(60)
