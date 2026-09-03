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
    ABANTETHER_API_KEY = os.getenv("ABANTETHER_API_KEY", "").strip()
    RENDER_WEBHOOK_URL = os.getenv("RENDER_WEBHOOK_URL", "")
    SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")

    def validate(self):
        pass

# ==================== مدیریت معاملات با همگام‌سازی خودکار صرافی (آبان‌تتر) ====================
class AbanTetherTrader:
    def __init__(self, config: Config):
        self.config = config
        self.initial_capital = None
        self.last_capital_reset_time = None
        self.positions_file = "active_positions.json"
        self.active_positions = self.load_positions()
        self.base_url = "https://api.abantether.com"
        
        try:
            self.exchange = ccxt.coinex({
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
            logger.info("اتصال به صرافی جهت دریافت قیمت‌های لحظه‌ای بازار با موفقیت راه‌اندازی شد.")
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی اتصال قیمت بازار: {e}")
            self.exchange = None

        self.check_order_endpoint_health()

    def load_positions(self) -> dict:
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"پوزیشن‌های باز قبلی با موفقیت از فایل بارگذاری شدند: {list(data.keys())}")
                    return data
            except Exception as e:
                logger.error(f"خطا در خواندن فایل پوزیشن‌ها: {e}")
        return {}

    def save_positions(self):
        try:
            with open(self.positions_file, 'w', encoding='utf-8') as f:
                json.dump(self.active_positions, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"خطا در ذخیره فایل پوزیشن‌ها: {e}")

    def check_order_endpoint_health(self):
        try:
            url = f"{self.base_url}/api/v1/accounting/balances?type=spot"
            headers = {"Authorization": f"Bearer {self.config.ABANTETHER_API_KEY}"}
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                logger.info("وضعیت دسترسی به بخش حساب و موجودی صرافی آبان‌تتر: موفق - ارتباط با اندپوینت برقرار است.")
            else:
                logger.warning(f"هشدار: پاسخ غیرمنتظره از آبان‌تتر در تست سلامت (کد {res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"خطای بحرانی: عدم توانایی در دسترسی به بخش سفارشات صرافی آبان‌تتر در زمان دپلوی: {e}")

    def get_usdt_balance(self) -> Optional[float]:
        try:
            url = f"{self.base_url}/api/v1/accounting/balances?type=spot"
            headers = {"Authorization": f"Bearer {self.config.ABANTETHER_API_KEY}"}
            res = requests.get(url, headers=headers, timeout=10)
            logger.info(f"پاسخ دیاگ لحظه‌ای API آبان‌تتر - کد پاسخ: {res.status_code}")
            
            if res.status_code == 200:
                response = res.json()
                logger.info(f"محتوای پاسخ موجودی: {response}")
                balances_list = response if isinstance(response, list) else response.get('data', [])
                for asset in balances_list:
                    if asset.get('symbol', '').upper() == 'USDT':
                        usdt_val = float(asset.get('available', asset.get('balance', 0.0)))
                        logger.info(f"موجودی تتر شناسایی شده: {usdt_val}")
                        return usdt_val
                return 0.0
            else:
                logger.error(f"خطای ارتباط با صرافی در دریافت موجودی (کد پاسخ {res.status_code}) - متن پاسخ: {res.text}")
                return None
        except Exception as e:
            logger.error(f"خطای شبکه یا استثناء در ارتباط با صرافی آبان‌تتر برای دریافت موجودی: {e}")
            return None

    def check_and_update_capital(self, current_balance: float):
        now = datetime.now()
        if self.initial_capital is None or self.last_capital_reset_time is None:
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
            logger.info(f"سرمایه پایه اولیه ثبت شد: {self.initial_capital} USDT")
        elif now - self.last_capital_reset_time >= timedelta(hours=3):
            self.initial_capital = current_balance
            self.last_capital_reset_time = now
            logger.info(f"دوره‌ی ۳ ساعته تکمیل شد. سرمایه پایه بر اساس موجودی جدید به‌روز شد: {self.initial_capital} USDT")

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

    def execute_spot_order(self, symbol: str, side: str, price: float, dynamic_tp: float = None, dynamic_sl: float = None):
        try:
            usdt_balance = self.get_usdt_balance()
            if usdt_balance is None:
                logger.error("معامله متوقف شد: امکان برقراری ارتباط صحیح با صرافی آبان‌تتر جهت استعلام موجودی وجود نداشت.")
                return None

            self.check_and_update_capital(usdt_balance)

            base_symbol = symbol.split('/')[0]

            if side == "BUY":
                if symbol in self.active_positions:
                    logger.info(f"برای نماد {symbol} از قبل پوزیشن باز وجود دارد؛ خرید جدید ثبت نمی‌شود.")
                    return None

                if usdt_balance < 1.0:
                    logger.warning(f"موجودی کل حساب ({usdt_balance} USDT) کمتر از حداقل مجاز صرافی است. معامله رد شد.")
                    return None

                base_capital = self.initial_capital if self.initial_capital and self.initial_capital > 0 else usdt_balance
                allocated_budget = base_capital * 0.20
                if allocated_budget < 1.0:
                    allocated_budget = 1.0

                if usdt_balance < allocated_budget:
                    logger.warning(f"موجودی کل کافی برای تخصیص بودجه مورد نظر نیست. معامله رد شد.")
                    return None
                
                logger.info(f"سرمایه نهایی تخصیص‌یافته برای {symbol}: {allocated_budget} USDT (اسپات / بدون اهرم)")

                url = f"{self.base_url}/api/v1/order_handler/orders/otc/market"
                headers = {
                    "Authorization": f"Bearer {self.config.ABANTETHER_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "side": "buy",
                    "base_symbol": base_symbol,
                    "quote_symbol": "USDT",
                    "volume": float(allocated_budget)
                }

                response = requests.post(url, headers=headers, json=payload, timeout=15)
                logger.info(f"پاسخ ثبت سفارش خرید آبان‌تتر - کد: {response.status_code} | متن: {response.text}")

                if response.status_code in [200, 201]:
                    tp_price = dynamic_tp if dynamic_tp else price * 1.025
                    sl_price = dynamic_sl if dynamic_sl else price * 0.985
                    
                    self.active_positions[symbol] = {
                        "entry_price": price,
                        "tp_price": tp_price,
                        "sl_price": sl_price
                    }
                    self.save_positions()
                    logger.info(f"سفارش خرید اسپات در آبان‌تتر با موفقیت ثبت شد | TP: {tp_price} | SL: {sl_price}")
                    return None
                else:
                    logger.error(f"خطا در ثبت سفارش خرید آبان‌تتر: {response.text}")
                    return None

            elif side == "SELL":
                base_free = 0.0
                try:
                    url = f"{self.base_url}/api/v1/accounting/balances?type=spot&symbols={base_symbol}"
                    headers = {"Authorization": f"Bearer {self.config.ABANTETHER_API_KEY}"}
                    res = requests.get(url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        res_json = res.json()
                        balances_list = res_json if isinstance(res_json, list) else res_json.get('data', [])
                        for asset in balances_list:
                            if asset.get('symbol', '').upper() == base_symbol.upper():
                                base_free = float(asset.get('available', asset.get('balance', 0.0)))
                                break
                except Exception as e:
                    logger.error(f"خطا در استعلام دارایی پایه برای فروش در آبان‌تتر: {e}")
                
                if base_free > 0:
                    url = f"{self.base_url}/api/v1/order_handler/orders/otc/market"
                    headers = {
                        "Authorization": f"Bearer {self.config.ABANTETHER_API_KEY}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "side": "sell",
                        "base_symbol": base_symbol,
                        "quote_symbol": "USDT",
                        "volume": float(base_free)
                    }

                    response = requests.post(url, headers=headers, json=payload, timeout=15)
                    logger.info(f"پاسخ ثبت سفارش فروش آبان‌تتر - کد: {response.status_code} | متن: {response.text}")

                    if response.status_code in [200, 201]:
                        pnl_percent = 0.0
                        if symbol in self.active_positions:
                            entry_price = self.active_positions[symbol]["entry_price"]
                            pnl_percent = ((price - entry_price) / entry_price) * 100
                            del self.active_positions[symbol]
                            self.save_positions()

                        logger.info(f"سفارش فروش اسپات در آبان‌تتر با موفقیت ثبت شد | سود/زیان: {pnl_percent:.2f}%")
                        return {
                            "action": "close_trade",
                            "symbol": symbol,
                            "side": "SELL",
                            "exit_price": price,
                            "pnl": round(pnl_percent, 2)
                        }
                    else:
                        logger.error(f"خطا در ثبت سفارش فروش آبان‌تتر: {response.text}")
                        return None
                else:
                    logger.warning(f"دارایی کافی از ارز {base_symbol} برای فروش در آبان‌تتر موجود نیست.")
                    if symbol in self.active_positions:
                        del self.active_positions[symbol]
                        self.save_positions()
                    return None

        except Exception as e:
            logger.error(f"خطا در اجرای سفارش واقعی در صرافی آبان‌تتر برای {symbol}: {e}")
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
            logger.info("نتیجه معامله با موفقیت به رندر ارسال شد.")
        except Exception as e:
            logger.error(f"خطا در ارسال داده به رندر: {e}")

# ==================== تعریف سراسری برای حفظ وضعیت پوزیشن‌ها ====================
config = Config()
trader = AbanTetherTrader(config)
notifier = RenderNotifier(config)

# ==================== وب‌سرور همروش ====================
class HamraveshWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Hamravesh Bot is alive and running!")

    def do_POST(self):
        global trader, notifier, config
        try:
            auth_token = self.headers.get("X-Secret-Token")
            
            if config.SECRET_TOKEN and auth_token != config.SECRET_TOKEN:
                logger.warning("تلاش برای دسترسی غیرمجاز به وب‌هوک همروش با توکن اشتباه.")
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
                dynamic_tp = data.get("tp1")
                dynamic_sl = data.get("sl")
                logger.info(f"دستور اجرای معامله از رندر دریافت شد: {symbol} | سمت: {side}")
                
                trade_result = trader.execute_spot_order(symbol, side, price, dynamic_tp, dynamic_sl)

                if trade_result:
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
        trader.get_usdt_balance()

        while True:
            if trader.active_positions and trader.exchange:
                for symbol in list(trader.active_positions.keys()):
                    try:
                        ticker = trader.exchange.fetch_ticker(symbol)
                        current_price = float(ticker['last'])
                        close_result = trader.check_tp_sl_and_update(symbol, current_price)
                        if close_result:
                            notifier.send_to_render(close_result)
                    except Exception as e:
                        logger.error(f"خطا در بررسی قیمت لحظه‌ای {symbol}: {e}")
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("بخش همروش متوقف شد.")
