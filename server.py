# ==============================================
# Hybrid Signal Bot - نسخه همروش (Hamravesh - Webhook Receiver & Tabdeal Spot Execution)
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

# ==================== مدیریت معاملات واقعی در صرافی تبدیل (Spot بدون اهرم) ====================
class TabdealTrader:
    def __init__(self, config: Config):
        self.config = config
        self.initial_capital = None
        self.last_capital_reset_time = None
        self.active_positions = {}  # ذخیره قیمت ورود برای محاسبه سود و زیان
        
        try:
            self.exchange = ccxt.tabdeal({
                'apiKey': config.TABDEAL_API_KEY,
                'secret': config.TABDEAL_SECRET,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی اتصال به صرافی تبدیل: {e}")
            self.exchange = None

    def _generate_signature(self, query_string: str) -> str:
        """تولید امضای امنیتی بر اساس مستندات رسمی تبدیل با HMAC-SHA256"""
        secret_bytes = self.config.TABDEAL_SECRET.encode('utf-8')
        message_bytes = query_string.encode('utf-8')
        return hmac.new(secret_bytes, message_bytes, hashlib.sha256).hexdigest()

    def get_usdt_balance(self) -> float:
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
            logger.info(f"پاسخ دیاگ لحظه‌ای API تبدیل - کد پاسخ: {res.status_code}")
            
            if res.status_code == 200:
                response = res.json()
                logger.info(f"محتوای پاسخ موجودی: {response}")
                data = response.get('data', response.get('balances', response.get('assets', [])))
                if isinstance(data, list):
                    for asset in data:
                        if asset.get('currency', '').upper() == 'USDT' or asset.get('asset', '').upper() == 'USDT':
                            usdt_val = float(asset.get('free', asset.get('balance', 0.0)))
                            logger.info(f"موجودی تتر شناسایی شده: {usdt_val}")
                            return usdt_val
            else:
                logger.error(f"خطا در دریافت موجودی صرافی تبدیل - متن پاسخ: {res.text}")
            return 0.0
        except Exception as e:
            logger.error(f"خطا در دریافت مستقیم موجودی صرافی تبدیل: {e}")
            return 0.0

    def check_and_update_capital(self, current_balance: float):
        """بررسی و به‌روزرسانی سرمایه پایه هر ۳ ساعت یک‌بار"""
        now = datetime.now()
        if self.initial_capital is None or self.last_capital_reset_time is None:
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
            logger.info(f"سرمایه پایه اولیه ثبت شد: {self.initial_capital} USDT")
        elif now - self.last_capital_reset_time >= timedelta(hours=3):
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
            logger.info(f"دوره‌ی ۳ ساعته تکمیل شد. سرمایه پایه بر اساس موجودی جدید به‌روز شد: {self.initial_capital} USDT")

    def execute_spot_order(self, symbol: str, side: str, price: float, usdt_allocation_percent: float = 0.50):
        if not self.exchange:
            logger.error("صرافی تبدیل مقداردهی نشده است.")
            return None

        try:
            usdt_balance = self.get_usdt_balance()
            self.check_and_update_capital(usdt_balance)

            base_capital = self.initial_capital if self.initial_capital and self.initial_capital > 0 else usdt_balance
            allocated_budget = base_capital * usdt_allocation_percent
            MIN_REQUIRED_USDT = 1.0  

            if allocated_budget < MIN_REQUIRED_USDT:
                if usdt_balance >= MIN_REQUIRED_USDT:
                    allocated_budget = MIN_REQUIRED_USDT
                else:
                    logger.warning(f"موجودی کیف پول کافی نیست. معامله رد شد.")
                    return None

            if usdt_balance < allocated_budget:
                logger.warning(f"موجودی کل کافی نیست. معامله رد شد.")
                return None

            amount_to_buy = allocated_budget / price
            logger.info(f"سرمایه نهایی تخصیص‌یافته برای {symbol}: {allocated_budget} USDT (اسپات / بدون اهرم)")

            formatted_symbol = symbol.replace('/', '').upper()
            if not formatted_symbol.endswith('USDT') and not formatted_symbol.endswith('IRT'):
                formatted_symbol += 'USDT'

            if side == "BUY":
                order = self.exchange.create_market_buy_order(symbol, amount_to_buy)
                self.active_positions[symbol] = {
                    "entry_price": price
                }
                logger.info(f"سفارش خرید اسپات در تبدیل ثبت شد: {order}")
                return None

            elif side == "SELL":
                base_currency = symbol.split('/')[0]
                base_free = 0.0
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
                    response = res.json()
                    data = response.get('data', response.get('balances', response.get('assets', [])))
                    if isinstance(data, list):
                        for asset in data:
                            if asset.get('currency', '').upper() == base_currency.upper() or asset.get('asset', '').upper() == base_currency.upper():
                                base_free = float(asset.get('free', asset.get('balance', 0.0)))
                                break
                except Exception:
                    base_free = 0.0
                
                if base_free > 0:
                    order = self.exchange.create_market_sell_order(symbol, base_free)
                    
                    pnl_percent = 0.0
                    if symbol in self.active_positions:
                        entry_price = self.active_positions[symbol]["entry_price"]
                        pnl_percent = ((price - entry_price) / entry_price) * 100
                        del self.active_positions[symbol]

                    logger.info(f"سفارش فروش اسپات در تبدیل ثبت شد: {order} | سود/زیان: {pnl_percent:.2f}%")
                    return {
                        "action": "close_trade",
                        "symbol": symbol,
                        "side": "SELL",
                        "exit_price": price,
                        "pnl": round(pnl_percent, 2)
                    }
                else:
                    logger.warning(f"دارایی کافی از ارز {base_currency} برای فروش موجود نیست.")
                    return None
                
        except Exception as e:
            logger.error(f"خطا در اجرای سفارش واقعی در صرافی تبدیل برای {symbol}: {e}")
            return None

# ==================== ارتباط با رندر (جهت ارسال نتیجه معامله) ====================
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

# ==================== وب‌سرور و دریافت دستور از رندر ====================
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

            # پاسخ به پینگ تستی رندر برای بررسی سلامت چرخه
            if action == "ping":
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "pong"}).encode('utf-8'))
                return

            # دریافت دستور معامله از رندر و اجرا در صرافی تبدیل
            if action == "execute_trade":
                symbol = data.get("symbol")
                side = data.get("side")
                price = data.get("price")
                
                trader = TabdealTrader(config)
                trade_result = trader.execute_spot_order(symbol, side, price)

                # اگر معامله بسته شد و نتیجه سود/زیان داشت، به رندر برگردانده شود
                if trade_result:
                    notifier = RenderNotifier(config)
                    notifier.send_to_render(trade_result)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": "Processed"}).encode('utf-8'))

        except Exception as e:
            logger.error(f"خطا در پردازش وب‌هوک دریافتی در همروش: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        return

def start_hamravesh_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HamraveshWebhookHandler)
        logger.info(f"وب‌سرور همروش روی پورت {port} آغاز به کار کرد.")
        server.serve_forever()
    except Exception as e:
        logger.error(f"خطا در اجرای وب‌سرور همروش: {e}")

threading.Thread(target=start_hamravesh_server, daemon=True).start()

if __name__ == "__main__":
    logger.info("بخش همروش بات فعال شد و آماده دریافت دستورات از رندر است.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("بخش همروش متوقف شد.")
