# ==============================================
# Hybrid Signal Bot - نسخه نهایی همروش (Hamravesh - Webhook Receiver & Tabdeal Spot)
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
import pandas as pd
import ccxt
from dotenv import load_dotenv

load_dotenv()

# Global variable to store incoming data from Render
latest_market_data = {}
data_lock = threading.Lock()

# ==================== لاگ (اول تعریف می‌شود تا توابع دیگر به آن دسترسی داشته باشند) ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler("trading_signals.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== وب‌سرور و دریافت وب‌هوک از رندر ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Hamravesh Bot is alive and running!")

    def do_POST(self):
        global latest_market_data
        try:
            # بررسی رمز امنیتی مشترک با رندر
            auth_token = self.headers.get("X-Secret-Token")
            expected_token = os.getenv("SECRET_TOKEN", "")
            
            if expected_token and auth_token != expected_token:
                self.send_response(403)
                self.end_headers()
                return

            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            symbol = data.get("symbol")
            if symbol:
                with data_lock:
                    latest_market_data[symbol] = data.get("ohlcv")
                
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Data received"}).encode('utf-8'))
            else:
                self.send_response(400)
                self.end_headers()
        except Exception as e:
            logger.error(f"خطا در پردازش وب‌هوک دریافتی از رندر: {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        return

def start_health_check_server():
    # اصلاح پورت برای سازگاری کامل با همروش و رفع ارور Not Ready
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"وب‌سرور همروش روی پورت {port} آغاز به کار کرد.")
        server.serve_forever()
    except Exception as e:
        logger.error(f"خطا در اجرای وب‌سرور: {e}")

threading.Thread(target=start_health_check_server, daemon=True).start()

# ==================== تنظیمات ====================
class Config:
    TABDEAL_API_KEY = os.getenv("TABDEAL_API_KEY", "")
    TABDEAL_SECRET = os.getenv("TABDEAL_SECRET", "")

    RENDER_WEBHOOK_URL = os.getenv("RENDER_WEBHOOK_URL", "")

    SYMBOLS = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "BNB/USDT",
        "XRP/USDT",
        "AVAX/USDT",
        "NEAR/USDT",
        "ADA/USDT",
        "DOGE/USDT",
        "LINK/USDT",
    ]

    ENTRY_TIMEFRAME = "15m"
    TREND_TIMEFRAME = "4h"
    CHECK_INTERVAL = 300

    def validate(self):
        pass

# ==================== لایه تحلیل تکنیکال ====================
class AnalysisLayer:
    def __init__(self, config: Config):
        self.config = config

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        df['ema_fast'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_trend'] = df['close'].ewm(span=200, adjust=False).mean()

        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()

        df['vol_sma'] = df['volume'].rolling(window=20).mean()
        df['support'] = df['low'].rolling(window=15).min()
        df['resistance'] = df['high'].rolling(window=15).max()

        return df

    def get_major_trend(self, df_trend: pd.DataFrame) -> str:
        latest = df_trend.iloc[-1]
        if latest['close'] > latest['ema_trend'] and latest['ema_fast'] > latest['ema_slow']:
            return "BULLISH"
        elif latest['close'] < latest['ema_trend'] and latest['ema_fast'] < latest['ema_slow']:
            return "BEARISH"
        return "NEUTRAL"

# ==================== موتور سیگنال ====================
class SignalEngine:
    def __init__(self, config: Config):
        self.config = config

    def get_rule_signal(self, df_15m: pd.DataFrame, trend_4h: str) -> Optional[str]:
        latest = df_15m.iloc[-1]
        prev = df_15m.iloc[-2]
        
        if pd.isna(latest['rsi']) or pd.isna(latest['ema_fast']) or pd.isna(latest['atr']):
            return None

        if latest['atr'] < (latest['close'] * 0.0015):
            return None

        volume_confirmed = latest['volume'] > (latest['vol_sma'] * 0.50)

        if trend_4h in ["BULLISH", "NEUTRAL"]:
            ema_bull = latest['ema_fast'] > latest['ema_slow']
            rsi_buy = (latest['rsi'] > 42 and prev['rsi'] <= 42) or (48 <= latest['rsi'] <= 65 and latest['rsi'] > prev['rsi'])
            if ema_bull and rsi_buy and volume_confirmed:
                return "BUY"

        if trend_4h in ["BEARISH", "NEUTRAL"]:
            ema_bear = latest['ema_fast'] < latest['ema_slow']
            rsi_sell = (latest['rsi'] < 58 and prev['rsi'] >= 58) or (35 <= latest['rsi'] <= 52 and latest['rsi'] < prev['rsi'])
            if ema_bear and rsi_sell and volume_confirmed:
                return "SELL"

        return None

# ==================== مدیریت معاملات واقعی در صرافی تبدیل (Spot بدون اهرم) ====================
class TabdealTrader:
    def __init__(self, config: Config):
        self.config = config
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

    def get_usdt_balance(self) -> float:
        if not self.exchange:
            return 0.0
        try:
            # استفاده از متد سازگار با ccxt-ir یا درخواست مستقیم به پنل صرافی در صورت عدم پشتیبانی fetch_balance
            if hasattr(self.exchange, 'private_get_account_balances') or hasattr(self.exchange, 'fetch_balance'):
                try:
                    balance = self.exchange.fetch_balance()
                    usdt_free = balance.get('USDT', {}).get('free', 0.0)
                    return float(usdt_free)
                except Exception:
                    # روش جایگزین برای صرافی‌های ایرانی متصل از طریق ccxt-ir
                    response = self.exchange.private_get_account_balances() if hasattr(self.exchange, 'private_get_account_balances') else {}
                    for asset in response.get('data', []):
                        if asset.get('currency') == 'USDT' or asset.get('asset') == 'USDT':
                            return float(asset.get('free', asset.get('balance', 0.0)))
            return 0.0
        except Exception as e:
            logger.error(f"خطا در دریافت موجودی صرافی تبدیل: {e}")
            return 0.0

    def execute_spot_order(self, symbol: str, side: str, price: float, usdt_allocation_percent: float = 0.20):
        if not self.exchange:
            logger.error("صرافی تبدیل مقداردهی نشده است.")
            return None

        try:
            usdt_balance = self.get_usdt_balance()
            if usdt_balance < 10:
                logger.warning(f"موجودی تتر کافی نیست: {usdt_balance} USDT (توجه: اگر تست می‌کنید، این هشدار مانع اجرای سفارش نمی‌شود اما برای تست واقعی نیاز به تتر دارید)")

            # محاسبه بودجه بر اساس تخصیص یا پیش‌فرض در صورت صفر بودن موجودی تستی
            allocated_budget = (usdt_balance * usdt_allocation_percent) if usdt_balance >= 10 else 10.0
            amount_to_buy = allocated_budget / price

            logger.info(f"سرمایه تخصیص‌یافته برای {symbol}: {allocated_budget} USDT (اسپات / بدون اهرم)")

            if side == "BUY":
                order = self.exchange.create_market_buy_order(symbol, amount_to_buy)
                logger.info(f"سفارش خرید اسپات در تبدیل ثبت شد: {order}")
                return order
            elif side == "SELL":
                base_currency = symbol.split('/')[0]
                try:
                    balance = self.exchange.fetch_balance()
                    base_free = balance.get(base_currency, {}).get('free', 0.0)
                except Exception:
                    base_free = 1.0 # مقدار پیش‌فرض برای جلوگیری از توقف در صورت خطای ساختار موجودی
                
                if base_free > 0:
                    order = self.exchange.create_market_sell_order(symbol, base_free)
                    logger.info(f"سفارش فروش اسپات در تبدیل ثبت شد: {order}")
                    return order
                
        except Exception as e:
            logger.error(f"خطا در اجرای سفارش واقعی در صرافی تبدیل برای {symbol}: {e}")
            return None

# ==================== ارتباط با رندر (جهت ارسال به تلگرام) ====================
class RenderNotifier:
    def __init__(self, config: Config):
        self.config = config

    def send_to_render(self, payload: dict):
        if not self.config.RENDER_WEBHOOK_URL:
            return
        try:
            import requests
            secret_token = os.getenv("SECRET_TOKEN", "")
            headers = {"X-Secret-Token": secret_token}
            requests.post(self.config.RENDER_WEBHOOK_URL, json=payload, headers=headers, timeout=10)
        except Exception as e:
            logger.error(f"خطا در ارسال داده به رندر: {e}")

# ==================== سیستم اصلی همروش ====================
class HamraveshTradingSystem:
    def __init__(self):
        self.config = Config()
        self.config.validate()
        self.analysis = AnalysisLayer(self.config)
        self.signal_engine = SignalEngine(self.config)
        self.tabdeal = TabdealTrader(self.config)
        self.notifier = RenderNotifier(self.config)
        self.running = True
        self.last_signal_time: Dict[str, datetime] = {}

    def process_symbol_from_webhook(self, symbol: str, ohlcv_data: list):
        try:
            if not ohlcv_data or len(ohlcv_data) < 50:
                return

            df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            df_indicators = self.analysis.calculate_indicators(df)
            trend = self.analysis.get_major_trend(df_indicators)

            rule_signal = self.signal_engine.get_rule_signal(df_indicators, trend)
            if not rule_signal:
                return

            now = datetime.now()
            if symbol in self.last_signal_time:
                if now - self.last_signal_time[symbol] < timedelta(minutes=90):
                    return

            latest = df_indicators.iloc[-1]
            price = float(latest['close'])
            
            # اجرای سفارش اسپات در صرافی تبدیل
            order_result = self.tabdeal.execute_spot_order(symbol, rule_signal, price)

            if order_result:
                payload = {
                    "action": "new_trade",
                    "symbol": symbol,
                    "side": rule_signal,
                    "price": price,
                    "trend": trend
                }
                self.notifier.send_to_render(payload)

            self.last_signal_time[symbol] = now

        except Exception as e:
            logger.error(f"خطا در پردازش داده وب‌هوک برای {symbol}: {e}")

    def start(self):
        logger.info("بخش همروش بات فعال شد (منتظر دریافت داده از رندر - اسپات واقعی)")
        while self.running:
            with data_lock:
                current_data = dict(latest_market_data)
            
            for symbol, ohlcv in current_data.items():
                self.process_symbol_from_webhook(symbol, ohlcv)

            gc.collect()
            time.sleep(10)

    def stop(self):
        self.running = False
        logger.info("بخش همروش متوقف شد")

if __name__ == "__main__":
    bot = HamraveshTradingSystem()
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
