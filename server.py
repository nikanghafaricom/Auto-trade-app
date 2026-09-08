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
    WALLEX_API_KEY = os.getenv("WALLEX_API_KEY", "").strip()
    RENDER_WEBHOOK_URL = os.getenv("RENDER_WEBHOOK_URL", "")
    SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")

    def validate(self):
        pass

# ==================== مدیریت معاملات با همگام‌سازی خودکار صرافی (والکس) ====================
class WallexTrader:
    def __init__(self, config: Config):
        self.config = config
        self.initial_capital = None
        self.last_capital_reset_time = None
        self.positions_file = "active_positions.json"
        self.active_positions = self.load_positions()
        self.base_url = "https://api.wallex.ir/v1"

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
            url = f"{self.base_url}/account/balances"
            headers = {"X-API-Key": self.config.WALLEX_API_KEY}
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                logger.info("وضعیت دسترسی به بخش حساب و موجودی صرافی والکس: موفق - ارتباط با اندپوینت برقرار است.")
            else:
                logger.warning(f"هشدار: پاسخ غیرمنتظره از والکس در تست سلامت (کد {res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"خطای بحرانی: عدم توانایی در دسترسی به بخش سفارشات صرافی والکس در زمان دپلوی: {e}")

    def get_usdt_balance(self) -> Optional[float]:
        try:
            url = f"{self.base_url}/account/balances"
            headers = {"X-API-Key": self.config.WALLEX_API_KEY}
            res = requests.get(url, headers=headers, timeout=10)
            logger.info(f"پاسخ دیاگ لحظه‌ای API والکس - کد پاسخ: {res.status_code}")

            if res.status_code == 200:
                response = res.json()
                logger.info(f"محتوای پاسخ موجودی: {response}")

                result_data = response.get('result', response)
                balances_dict = result_data.get('balances', result_data)

                if isinstance(balances_dict, dict):
                    usdt_info = balances_dict.get('USDT', {})
                    usdt_val = float(usdt_info.get('value', usdt_info.get('free', 0.0)))
                    logger.info(f"موجودی تتر شناسایی شده: {usdt_val}")
                    return usdt_val
                elif isinstance(balances_dict, list):
                    for asset in balances_dict:
                        if asset.get('asset', asset.get('symbol', '')).upper() == 'USDT':
                            usdt_val = float(asset.get('value', asset.get('free', 0.0)))
                            logger.info(f"موجودی تتر شناسایی شده: {usdt_val}")
                            return usdt_val
                return 0.0
            else:
                logger.error(f"خطای ارتباط با صرافی در دریافت موجودی (کد پاسخ {res.status_code}) - متن پاسخ: {res.text}")
                return None
        except Exception as e:
            logger.error(f"خطای شبکه یا استثناء در ارتباط با صرافی والکس برای دریافت موجودی: {e}")
            return None

    def get_market_limits(self, wallex_symbol: str):
        """دریافت حداقل مقدار (minQty) و حداقل ارزش سفارش (minNotional) یک بازار از والکس."""
        try:
            url = f"{self.base_url}/markets"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                symbols = data.get('result', {}).get('symbols', {})
                symbol_data = symbols.get(wallex_symbol, {})
                min_qty = float(symbol_data.get('minQty', 0) or 0)
                min_notional = float(symbol_data.get('minNotional', 0) or 0)
                return min_qty, min_notional
            logger.error(f"خطا در دریافت محدودیت‌های بازار {wallex_symbol} - کد: {res.status_code}")
            return 0.0, 0.0
        except Exception as e:
            logger.error(f"خطای شبکه در دریافت محدودیت‌های بازار {wallex_symbol}: {e}")
            return 0.0, 0.0

    def get_wallex_price(self, wallex_symbol: str) -> Optional[float]:
        """دریافت قیمت لحظه‌ای (lastPrice) مستقیم از اندپوینت عمومی بازارهای والکس."""
        try:
            url = f"{self.base_url}/markets"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                symbols = data.get('result', {}).get('symbols', {})
                symbol_data = symbols.get(wallex_symbol, {})
                last_price = symbol_data.get('stats', {}).get('lastPrice')
                if last_price is not None:
                    return float(last_price)
                logger.error(f"نماد {wallex_symbol} در پاسخ بازارهای والکس یافت نشد.")
                return None
            else:
                logger.error(f"خطا در دریافت قیمت لحظه‌ای {wallex_symbol} از والکس - کد: {res.status_code}")
                return None
        except Exception as e:
            logger.error(f"خطای شبکه در دریافت قیمت لحظه‌ای {wallex_symbol} از والکس: {e}")
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

    def _submit_order(self, wallex_symbol: str, side: str, quantity: float, price: float, order_type: str = "market"):
        """ارسال سفارش به والکس. اگر order_type=limit باشد، فیلد price هم ارسال می‌شود."""
        url = f"{self.base_url}/account/orders"
        headers = {
            "X-API-Key": self.config.WALLEX_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "symbol": wallex_symbol,
            "type": order_type,
            "side": side,
            "quantity": round(quantity, 5),
            "price": str(price)
        }

        response = requests.post(url, headers=headers, json=payload, timeout=15)
        return response

    def _place_order_with_retries(self, wallex_symbol: str, side: str, quantity: float, price: float, limit_offsets: list = None):
        """
        اول Market امتحان می‌شود.
        اگر نشد و limit_offsets داده شده باشد، به ترتیب هر آفست به‌صورت سفارش Limit امتحان می‌شود
        (آفست 0 یعنی Limit دقیقاً روی همان قیمت، بدون بالا/پایین بردن).
        خرید: قیمت با (1 + آفست) ضرب می‌شود (بالاتر).
        فروش: قیمت با (1 - آفست) ضرب می‌شود (پایین‌تر).
        side: "buy" یا "sell" (حروف کوچک، مطابق پارامتر ورودی به _submit_order)
        """
        # مرحله ۱: Market
        response = self._submit_order(wallex_symbol, side, quantity, price, "market")
        logger.info(f"پاسخ سفارش Market ({side}) والکس - کد: {response.status_code} | متن: {response.text}")
        if response.status_code in [200, 201]:
            return response

        if not limit_offsets:
            logger.warning(f"سفارش Market ({side}) برای {wallex_symbol} ناموفق بود؛ طبق تنظیم، سراغ Limit نمی‌رویم و معامله رد می‌شود.")
            return response

        if "امکان ثبت سفارش قیمت بازار" not in response.text:
            # خطا ربطی به عدم پشتیبانی Market ندارد؛ رفتن به مراحل بعد فایده‌ای ندارد
            return response

        for offset in limit_offsets:
            limit_price = price * (1 + offset) if side == "buy" else price * (1 - offset)
            response = self._submit_order(wallex_symbol, side, quantity, limit_price, "limit")
            logger.info(f"پاسخ سفارش Limit آفست {offset * 100:.1f}٪ ({side}) - کد: {response.status_code} | متن: {response.text}")
            if response.status_code in [200, 201]:
                return response

        return response

    def execute_spot_order(self, symbol: str, side: str, price: float, dynamic_tp: float = None, dynamic_sl: float = None):
        try:
            usdt_balance = self.get_usdt_balance()
            if usdt_balance is None:
                logger.error("معامله متوقف شد: امکان برقراری ارتباط صحیح با صرافی والکس جهت استعلام موجودی وجود نداشت.")
                return None

            self.check_and_update_capital(usdt_balance)

            base_symbol = symbol.split('/')[0]
            # بازگشت به فرمت بدون خط تیره (مثل DOGEUSDT) چون صرافی در ارور قبل این نماد را شناخت
            wallex_symbol = f"{base_symbol}USDT"

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

                min_qty, min_notional = self.get_market_limits(wallex_symbol)
                if min_notional > 0:
                    safe_budget = min_notional * 1.05  # حاشیه امن ۵٪ برای جلوگیری از افتادن زیر حداقل هنگام فروش با افت جزئی قیمت
                    if allocated_budget < safe_budget:
                        allocated_budget = safe_budget
                        logger.info(f"بودجه {symbol} برای رعایت حداقل ارزش مجاز بازار ({min_notional} USDT) به {allocated_budget:.4f} USDT افزایش یافت.")

                if usdt_balance < allocated_budget:
                    logger.warning(f"موجودی کل کافی برای تخصیص بودجه مورد نظر نیست. معامله رد شد.")
                    return None

                logger.info(f"سرمایه نهایی تخصیص‌یافته برای {symbol}: {allocated_budget} USDT (اسپات / بدون اهرم)")

                amount = allocated_budget / price if price > 0 else 0

                response = self._place_order_with_retries(wallex_symbol, "buy", amount, price, limit_offsets=[0, 0.001, 0.002, 0.005])

                if response.status_code in [200, 201]:
                    tp_price = dynamic_tp if dynamic_tp else price * 1.025
                    sl_price = dynamic_sl if dynamic_sl else price * 0.985

                    self.active_positions[symbol] = {
                        "entry_price": price,
                        "tp_price": tp_price,
                        "sl_price": sl_price
                    }
                    self.save_positions()
                    logger.info(f"سفارش خرید اسپات در والکس با موفقیت ثبت شد | TP: {tp_price} | SL: {sl_price}")
                    return None
                else:
                    logger.error(f"خطا در ثبت سفارش خرید والکس: {response.text}")
                    return None

            elif side == "SELL":
                base_free = 0.0
                try:
                    url = f"{self.base_url}/account/balances"
                    headers = {"X-API-Key": self.config.WALLEX_API_KEY}
                    res = requests.get(url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        res_json = res.json()
                        result_data = res_json.get('result', res_json)
                        balances_dict = result_data.get('balances', result_data)

                        if isinstance(balances_dict, dict):
                            asset_info = balances_dict.get(base_symbol, {})
                            base_free = float(asset_info.get('value', asset_info.get('free', 0.0)))
                        elif isinstance(balances_dict, list):
                            for asset in balances_dict:
                                if asset.get('asset', asset.get('symbol', '')).upper() == base_symbol.upper():
                                    base_free = float(asset.get('value', asset.get('free', 0.0)))
                                    break
                except Exception as e:
                    logger.error(f"خطا در استعلام دارایی پایه برای فروش در والکس: {e}")

                if base_free > 0:
                    response = self._place_order_with_retries(wallex_symbol, "sell", base_free, price, limit_offsets=[0, 0.002, 0.01])

                    if response.status_code in [200, 201]:
                        pnl_percent = 0.0
                        if symbol in self.active_positions:
                            entry_price = self.active_positions[symbol]["entry_price"]
                            pnl_percent = ((price - entry_price) / entry_price) * 100
                            del self.active_positions[symbol]
                            self.save_positions()

                        logger.info(f"سفارش فروش اسپات در والکس با موفقیت ثبت شد | سود/زیان: {pnl_percent:.2f}%")
                        return {
                            "action": "close_trade",
                            "symbol": symbol,
                            "side": "SELL",
                            "exit_price": price,
                            "pnl": round(pnl_percent, 2)
                        }
                    else:
                        logger.error(f"خطا در ثبت سفارش فروش والکس: {response.text}")
                        return None
                else:
                    logger.warning(f"دارایی کافی از ارز {base_symbol} برای فروش در والکس موجود نیست.")
                    if symbol in self.active_positions:
                        del self.active_positions[symbol]
                        self.save_positions()
                    return None

        except Exception as e:
            logger.error(f"خطا در اجرای سفارش واقعی در صرافی والکس برای {symbol}: {e}")
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
trader = WallexTrader(config)
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
            if trader.active_positions:
                for symbol in list(trader.active_positions.keys()):
                    try:
                        base_symbol = symbol.split('/')[0]
                        wallex_symbol = f"{base_symbol}USDT"
                        current_price = trader.get_wallex_price(wallex_symbol)
                        if current_price is None:
                            continue
                        close_result = trader.check_tp_sl_and_update(symbol, current_price)
                        if close_result:
                            notifier.send_to_render(close_result)
                    except Exception as e:
                        logger.error(f"خطا در بررسی قیمت لحظه‌ای {symbol}: {e}")
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("بخش همروش متوقف شد.")
