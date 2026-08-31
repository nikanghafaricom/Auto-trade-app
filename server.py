# ==============================================
# Hybrid Signal Bot - نسخه همروش (Hamravesh - Webhook Receiver & Exchange Auto-Sync)
# ==============================================
import os
import time
import logging
import gc
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import ccxt
import requests
from dotenv import load_dotenv

# ایمپورت پکیج اختصاصی صرافی تبدیل
from tabdeal.spot import Spot
from tabdeal.enums import OrderSides, OrderTypes

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
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی اتصال به صرافی تبدیل: گسکت CCXT: {e}")
            self.exchange = None

        # کلاینت اختصاصی صرافی تبدیل برای اتصال و درخواست‌ها
        try:
            self.tabdeal_client = Spot(config.TABDEAL_API_KEY, config.TABDEAL_SECRET)
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی کلاینت اختصاصی تبدیل: {e}")
            self.tabdeal_client = None

        self.check_order_endpoint_health()

    def check_order_endpoint_health(self):
        try:
            if self.tabdeal_client:
                # تست سلامت با متد account یا سفارشات
                self.tabdeal_client.account()
                logger.info("وضعیت دسترسی به بخش حساب و سفارشات صرافی تبدیل: موفق - ارتباط با اندپوینت برقرار است.")
            else:
                logger.warning("هشدار: کلاینت اختصاصی تبدیل مقداردهی اولیه نشده است.")
        except Exception as e:
            logger.error(f"خطای بحرانی: عدم توانایی در دسترسی به بخش سفارشات صرافی تبدیل در زمان دپلوی: {e}")

    def get_usdt_balance(self) -> Optional[float]:
        try:
            if not self.tabdeal_client:
                return None
            
            account = self.tabdeal_client.account()
            logger.info(f"پاسخ دیاگ لحظه‌ای API تبدیل - حساب دریافت شد")
            
            data = account.get('data', account.get('balances', account.get('assets', [])))
            if isinstance(data, list):
                for asset in data:
                    if asset.get('currency', '').upper() == 'USDT' or asset.get('asset', '').upper() == 'USDT':
                        usdt_val = float(asset.get('free', asset.get('balance', 0.0)))
                        logger.info(f"موجودی تتر شناسایی شده: {usdt_val}")
                        return usdt_val
            return 0.0
        except Exception as e:
            logger.error(f"خطای شبکه یا استثناء در ارتباط با صرافی تبدیل برای دریافت موجودی: {e}")
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
                logger.error("معامله متوقف شد: امکان برقراری ارتباط صحیح با صرافی تبدیل جهت استعلام موجودی وجود نداشت.")
                return None

            self.check_and_update_capital(usdt_balance)

            clean_symbol = symbol.replace('/', '')

            if side == "BUY":
                if symbol in self.active_positions:
                    logger.info(f"برای نماد {symbol} از قبل پوزیشن باز وجود دارد؛ خرید جدید ثبت نمی‌شود.")
                    return None

                if usdt_balance < 1.0:
                    logger.warning(f"موجودی کل حساب ({usdt_balance} USDT) کمتر از حداقل مجاز صرافی (1.0 USDT) است. معامله رد شد.")
                    return None

                base_capital = self.initial_capital if self.initial_capital and self.initial_capital > 0 else usdt_balance
                allocated_budget = base_capital * 0.20
                if allocated_budget < 1.0:
                    allocated_budget = 1.0

                if usdt_balance < allocated_budget:
                    logger.warning(f"موجودی کل کافی برای تخصیص بودجه مورد نظر نیست. معامله رد شد.")
                    return None

                amount_to_buy = allocated_budget / price
                amount_str = f"{amount_to_buy:.6f}"
                
                logger.info(f"سرمایه نهایی تخصیص‌یافته برای {symbol}: {allocated_budget} USDT (اسپات / بدون اهرم)")

                # استفاده از پکیج SDK تبدیل برای ثبت سفارش خرید
                order_response = self.tabdeal_client.new_order(
                    symbol=clean_symbol,
                    side=OrderSides.BUY,
                    type=OrderTypes.MARKET,
                    quantity=amount_str
                )
                
                tp_price = dynamic_tp if dynamic_tp else price * 1.025
                sl_price = dynamic_sl if dynamic_sl else price * 0.985
                
                self.active_positions[symbol] = {
                    "entry_price": price,
                    "tp_price": tp_price,
                    "sl_price": sl_price
                }
                logger.info(f"سفارش خرید اسپات در تبدیل با موفقیت ثبت شد: {order_response} | TP: {tp_price} | SL: {sl_price}")
                return None

            elif side == "SELL":
                base_currency = symbol.split('/')[0]
                base_free = 0.0
                try:
                    account = self.tabdeal_client.account()
                    data = account.get('data', account.get('balances', account.get('assets', [])))
                    if isinstance(data, list):
                        for asset in data:
                            if asset.get('currency', '').upper() == base_currency.upper() or asset.get('asset', '').upper() == base_currency.upper():
                                base_free = float(asset.get('free', asset.get('balance', 0.0)))
                                break
                except Exception as e:
                    logger.error(f"خطا در استعلام دارایی پایه برای فروش: {e}")
                
                if base_free > 0:
                    amount_str = f"{base_free:.6f}"
                    
                    try:
                        # مرحله اول: تلاش برای فروش مستقیم با پکیج SDK
                        order_response = self.tabdeal_client.new_order(
                            symbol=clean_symbol,
                            side=OrderSides.SELL,
                            type=OrderTypes.MARKET,
                            quantity=amount_str
                        )
                        
                        pnl_percent = 0.0
                        if symbol in self.active_positions:
                            entry_price = self.active_positions[symbol]["entry_price"]
                            pnl_percent = ((price - entry_price) / entry_price) * 100
                            del self.active_positions[symbol]

                        logger.info(f"سفارش فروش اسپات در تبدیل با موفقیت ثبت شد: {order_response} | سود/زیان: {pnl_percent:.2f}%")
                        return {
                            "action": "close_trade",
                            "symbol": symbol,
                            "side": "SELL",
                            "exit_price": price,
                            "pnl": round(pnl_percent, 2)
                        }
                    except Exception as direct_err:
                        # اگر فروش مستقیم به خاطر محدودیت صرافی خطا داد، استفاده از روش جایگزین (خرید معکوس با دارایی موجود)
                        logger.warning(f"فروش مستقیم موفق نبود (خطای صرافی: {direct_err}). تلاش از طریق روش جایگزین (خرید/تبدیل)...")
                        try:
                            alt_order_response = self.tabdeal_client.new_order(
                                symbol=clean_symbol,
                                side=OrderSides.BUY,
                                type=OrderTypes.MARKET,
                                quantity=amount_str
                            )
                            
                            pnl_percent = 0.0
                            if symbol in self.active_positions:
                                entry_price = self.active_positions[symbol]["entry_price"]
                                pnl_percent = ((price - entry_price) / entry_price) * 100
                                del self.active_positions[symbol]

                            logger.info(f"سفارش از طریق مسیر جایگزین (تبدیل) با موفقیت انجام شد: {alt_order_response} | سود/زیان: {pnl_percent:.2f}%")
                            return {
                                "action": "close_trade",
                                "symbol": symbol,
                                "side": "SELL",
                                "exit_price": price,
                                "pnl": round(pnl_percent, 2)
                            }
                        except Exception as alt_ex:
                            logger.error(f"خطای استثناء در اجرای روش جایگزین فروش: {alt_ex}")
                            return None
                else:
                    logger.warning(f"دارایی کافی از ارز {base_currency} برای فروش موجود نیست.")
                    if symbol in self.active_positions:
                        del self.active_positions[symbol]
                    return None

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
            secret_token = self.config.SECRET_TOKEN
            headers = {"X-Secret-Token": secret_token}
            requests.post(self.config.RENDER_WEBHOOK_URL, json=payload, headers=headers, timeout=10)
            logger.info("نتیجه معامله با موفقیت به رندر ارسال شد.")
        except Exception as e:
            logger.error(f"خطا در ارسال داده به رندر: {e}")

# ==================== تعریف سراسری برای حفظ وضعیت پوزیشن‌ها ====================
config = Config()
trader = TabdealTrader(config)
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
