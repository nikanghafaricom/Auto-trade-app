# ==============================================
# Hybrid Signal Bot - نسخه همروش (Hamravesh - Webhook Receiver & Tabdeal Spot Execution)
# ==============================================
import os
import time
import logging
import json
import hmac
import hashlib
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
        logger.info("ماژول صرافی تبدیل با موفقیت فعال شد.")

    def _generate_signature(self, query_string: str) -> str:
        """تولید امضا بر اساس مستندات رسمی تبدیل با HMAC-SHA256"""
        secret_bytes = self.config.TABDEAL_SECRET.encode('utf-8')
        message_bytes = query_string.encode('utf-8')
        return hmac.new(secret_bytes, message_bytes, hashlib.sha256).hexdigest()

    def get_usdt_balance(self) -> float:
        """دریافت موجودی واقعی تتر مطابق با مستندات رسمی صرافی تبدیل"""
        url = "https://api1.tabdeal.org/api/v1/account"
        
        timestamp = str(int(time.time() * 1000))
        query_string = f"timestamp={timestamp}"
        signature = self._generate_signature(query_string)
        
        # هدر دقیق مطابق مستندات (X-MBX-APIKEY)
        headers = {
            "X-MBX-APIKEY": self.config.TABDEAL_API_KEY,
            "Content-Type": "application/json"
        }
        
        full_url = f"{url}?{query_string}&signature={signature}"

        try:
            res = requests.get(full_url, headers=headers, timeout=10)
            logger.info(f"پاسخ دریافت موجودی - کد پاسخ: {res.status_code} - متن: {res.text[:200]}")
            
            if res.status_code == 200:
                data = res.json()
                items = data.get('data', data.get('balances', data.get('assets', data)))
                if isinstance(items, list):
                    for asset in items:
                        currency = asset.get('currency', asset.get('asset', '')).upper()
                        if currency == 'USDT':
                            balance = float(asset.get('free', asset.get('balance', 0.0)))
                            logger.info(f"موجودی واقعی تتر دریافت شد: {balance} USDT")
                            return balance
            else:
                logger.error(f"خطای صرافی در دریافت موجودی: {res.text}")
        except Exception as e:
            logger.error(f"خطا در ارتباط با سرور تبدیل: {e}")

        logger.warning("استفاده از مقدار پیش‌فرض موجودی برای جلوگیری از توقف ربات.")
        return 100.0

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
            self.check_and_update_capital(usdt_balance)

            base_capital = self.initial_capital if self.initial_capital and self.initial_capital > 0 else usdt_balance
            allocated_budget = base_capital * usdt_allocation_percent
            amount_to_buy = allocated_budget / price
            
            formatted_symbol = symbol.replace('/', '').upper()
            if not formatted_symbol.endswith('USDT') and not formatted_symbol.endswith('IRT'):
                formatted_symbol += 'USDT'

            logger.info(f"ارسال سفارش به صرافی برای {formatted_symbol} | حجم: {amount_to_buy}")

            order_url = "https://api1.tabdeal.org/api/v1/order"
            timestamp = str(int(time.time() * 1000))
            
            if side.upper() == "BUY":
                quantity_str = f"{round(amount_to_buy, 6):.6f}".rstrip('0').rstrip('.')
                query_string = f"quantity={quantity_str}&side=buy&symbol={formatted_symbol}&timestamp={timestamp}&type=market"
                signature = self._generate_signature(query_string)

                payload_data = {
                    "symbol": formatted_symbol,
                    "side": "buy",
                    "type": "market",
                    "quantity": round(amount_to_buy, 6),
                    "timestamp": timestamp
                }
            else:
                query_string = f"side=sell&symbol={formatted_symbol}&timestamp={timestamp}&type=market"
                signature = self._generate_signature(query_string)

                payload_data = {
                    "symbol": formatted_symbol,
                    "side": "sell",
                    "type": "market",
                    "timestamp": timestamp
                }

            headers = {
                "X-MBX-APIKEY": self.config.TABDEAL_API_KEY,
                "Content-Type": "application/json"
            }

            final_order_url = f"{order_url}?{query_string}&signature={signature}"
            res = requests.post(final_order_url, json=payload_data, headers=headers, timeout=10)
            logger.info(f"پاسخ ثبت سفارش از صرافی تبدیل: {res.status_code} - {res.text}")

            if side.upper() == "BUY":
                self.active_positions[symbol] = {"entry_price": price}
                return None
            elif side.upper() == "SELL":
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
            logger.error(f"خطا در اجرای سفارش در صرافی تبدیل: {e}")
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
        self.wfile.write(b"Hamravesh Bot is active with Official Tabdeal Docs!")

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
    logger.info("سرویس همروش طبق مستندات رسمی تبدیل استارت شد.")
    try:
        config = Config()
        trader = TabdealTrader(config)
        trader.get_usdt_balance()
    except Exception as e:
        logger.error(f"خطا: {e}")

    while True:
        time.sleep(60)
