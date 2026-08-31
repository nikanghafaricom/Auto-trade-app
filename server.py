# ==============================================
# Hybrid Signal Bot - نسخه ۱: فقط خرید (BUY)
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
from typing import Optional
import ccxt
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler("trading_signals.log", encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class Config:
    TABDEAL_API_KEY = os.getenv("TABDEAL_API_KEY", "")
    TABDEAL_SECRET = os.getenv("TABDEAL_SECRET", "")
    RENDER_WEBHOOK_URL = os.getenv("RENDER_WEBHOOK_URL", "")
    SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")

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
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی اتصال به صرافی تبدیل: {e}")
            self.exchange = None

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
            headers = {"X-MBX-APIKEY": self.config.TABDEAL_API_KEY, "Content-Type": "application/json"}
            full_url = f"{url}?{query_string}&signature={signature}"

            res = requests.get(full_url, headers=headers, timeout=10)
            if res.status_code == 200:
                response = res.json()
                data = response.get('data', response.get('balances', response.get('assets', [])))
                if isinstance(data, list):
                    for asset in data:
                        if asset.get('currency', '').upper() == 'USDT' or asset.get('asset', '').upper() == 'USDT':
                            usdt_val = float(asset.get('free', asset.get('balance', 0.0)))
                            logger.info(f"موجودی تتر شناسایی شده: {usdt_val}")
                            return usdt_val
                return 0.0
            else:
                logger.error(f"خطا در دریافت موجودی (کد {res.status_code}): {res.text}")
                return None
        except Exception as e:
            logger.error(f"خطا در ارتباط با صرافی برای دریافت موجودی: {e}")
            return None

    def execute_spot_order(self, symbol: str, side: str, price: float):
        try:
            usdt_balance = self.get_usdt_balance()
            if usdt_balance is None or usdt_balance < 1.0:
                logger.error("موجودی تتر کافی نیست یا خطا در دریافت موجودی.")
                return None

            clean_symbol = symbol.replace('/', '')
            tabdeal_symbol = symbol.replace('/', '_')
            url = "https://api1.tabdeal.org/api/v1/order"

            allocated_budget = usdt_balance * 0.20
            if allocated_budget < 1.0:
                allocated_budget = 1.0

            amount_to_buy = allocated_budget / price
            amount_str = f"{amount_to_buy:.6f}"
            timestamp = str(int(time.time() * 1000))
            
            params = {
                "symbol": clean_symbol,
                "tabdealSymbol": tabdeal_symbol,
                "side": "BUY",
                "type": "MARKET",
                "quantity": amount_str,
                "timestamp": timestamp
            }
            
            query_string = f"quantity={amount_str}&side=BUY&symbol={clean_symbol}&tabdealSymbol={tabdeal_symbol}&timestamp={timestamp}&type=MARKET"
            signature = self._generate_signature(query_string)
            headers = {"X-MBX-APIKEY": self.config.TABDEAL_API_KEY, "Content-Type": "application/json"}
            full_url = f"{url}?signature={signature}"
            
            res = requests.post(full_url, json=params, headers=headers, timeout=10)
            if res.status_code in [200, 201]:
                logger.info(f"سفارش خرید بیت‌کوین با موفقیت ثبت شد: {res.json()}")
            else:
                logger.error(f"خطا در ثبت سفارش خرید (کد {res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"خطا در اجرای خرید: {e}")

config = Config()
trader = TabdealTrader(config)

class HamraveshWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        return

def start_server():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), HamraveshWebhookHandler).serve_forever()

threading.Thread(target=start_server, daemon=True).start()

if __name__ == "__main__":
    logger.info("--- تست خرید خودکار بیت‌کوین آغاز شد ---")
    price = 60000.0
    if trader.exchange:
        try:
            price = float(trader.exchange.fetch_ticker("BTC/USDT")['last'])
        except:
            pass
    trader.execute_spot_order("BTC/USDT", "BUY", price)
    while True:
        time.sleep(30)
