# ==============================================
# Hybrid Signal Bot - نسخه جامع همروش (Hamravesh - Tabdeal Spot)
# ==============================================
import os
import time
import logging
import requests
import gc
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import pandas as pd
import ccxt
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ==================== وب‌سرور استاندارد ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Hamravesh Bot is alive and running!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return

def start_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"خطا در اجرای وب‌سرور: {e}")

threading.Thread(target=start_health_check_server, daemon=True).start()

# ==================== تنظیمات ====================
class Config:
    # صرافی تحلیل‌گر (دریافت داده از کوین ایکس)
    EXCHANGE_ID = "coinex"
    API_KEY = os.getenv("EXCHANGE_API_KEY", "")
    SECRET = os.getenv("EXCHANGE_SECRET", "")
    PASSWORD = os.getenv("EXCHANGE_PASSWORD", "")

    # صرافی اجرایی (صرافی تبدیل - واقعی)
    TABDEAL_API_KEY = os.getenv("TABDEAL_API_KEY", "")
    TABDEAL_SECRET = os.getenv("TABDEAL_SECRET", "")

    AI_API_KEY = os.getenv("AI_API_KEY")
    AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.x.ai/v1")
    AI_MODEL = os.getenv("AI_MODEL", "grok-3")

    # رندر جهت هماهنگی ارسال به تلگرام
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
    MIN_CONFIDENCE_AI = 0.80

    def validate(self):
        required = {
            "AI_API_KEY": self.AI_API_KEY,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"این متغیرهای محیطی تنظیم نشدن: {', '.join(missing)}")

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

# ==================== لایه داده ====================
class DataLayer:
    def __init__(self, config: Config):
        self.config = config
        exchange_class = getattr(ccxt, config.EXCHANGE_ID)
        self.exchange = exchange_class({
            'apiKey': config.API_KEY,
            'secret': config.SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

# ==================== لایه تحلیل ====================
class AnalysisLayer:
    def __init__(self, config: Config):
        self.config = config
        self.client = OpenAI(
            api_key=config.AI_API_KEY,
            base_url=config.AI_BASE_URL
        )

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

    def get_major_trend(self, df_4h: pd.DataFrame) -> str:
        latest = df_4h.iloc[-1]
        if latest['close'] > latest['ema_trend'] and latest['ema_fast'] > latest['ema_slow']:
            return "BULLISH"
        elif latest['close'] < latest['ema_trend'] and latest['ema_fast'] < latest['ema_slow']:
            return "BEARISH"
        return "NEUTRAL"

    def get_ai_confirmation(self, symbol: str, side: str, df: pd.DataFrame, trend: str) -> Dict:
        latest = df.iloc[-1]
        
        prompt = f"""
You are an elite quantitative crypto trader.
Context:
- Symbol: {symbol}
- Trade Side: {side}
- Higher Timeframe (4H) Trend: {trend}
- 15m Close: {latest['close']}
- RSI: {latest['rsi']:.1f}
- EMA20/50: {latest['ema_fast']:.2f} / {latest['ema_slow']:.2f}
- ATR Volatility: {latest['atr']:.4f}
- Volume ratio: {latest['volume']/latest['vol_sma']:.2f}x

Assign a final score (60 to 95) evaluating if this signal matches high-probability criteria.
Output ONLY the integer score.
"""
        try:
            response = self.client.chat.completions.create(
                model=self.config.AI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=6
            )
            answer = response.choices[0].message.content.strip()
            score = float(''.join(filter(str.isdigit, answer))) / 100.0
            if score < 0.60 or score > 0.98:
                score = 0.82
            return {"confidence": score}
        except Exception as e:
            logger.error(f"خطای AI برای {symbol}: {e}")
            return {"confidence": 0.80}

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
            # اتصال به صرافی تبدیل از طریق CCXT (یا پکیج سازگار)
            self.exchange = ccxt.tabdeal({
                'apiKey': config.TABDEAL_API_KEY,
                'secret': config.TABDEAL_SECRET,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'} # تاکید بر اسپات بودن
            })
        except Exception as e:
            logger.error(f"خطا در راه‌اندازی اتصال به صرافی تبدیل: {e}")
            self.exchange = None

    def get_usdt_balance(self) -> float:
        """بررسی موجودی تتر (USDT) در صرافی تبدیل جهت مدیریت سرمایه"""
        if not self.exchange:
            return 0.0
        try:
            balance = self.exchange.fetch_balance()
            usdt_free = balance.get('USDT', {}).get('free', 0.0)
            return float(usdt_free)
        except Exception as e:
            logger.error(f"خطا در دریافت موجودی صرافی تبدیل: {e}")
            return 0.0

    def execute_spot_order(self, symbol: str, side: str, price: float, usdt_allocation_percent: float = 0.20):
        """باز کردن معامله اسپات واقعی در تبدیل با مدیریت خودکار موجودی (بدون اهرم)"""
        if not self.exchange:
            logger.error("صرافی تبدیل مقداردهی نشده است.")
            return None

        try:
            # دریافت موجودی آزاد تتر
            usdt_balance = self.get_usdt_balance()
            if usdt_balance < 10:
                logger.warning(f"موجودی تتر کافی نیست: {usdt_balance} USDT")
                return None

            # تقسیم سرمایه (مثلا ۲۰ درصد از موجودی کل برای هر معامله اسپات)
            allocated_budget = usdt_balance * usdt_allocation_percent
            amount_to_buy = allocated_budget / price

            logger.info(f"سرمایه تخصیص‌یافته برای {symbol}: {allocated_budget} USDT (اسپات / بدون اهرم)")

            # ارسال سفارش خرید اسپات به صرافی تبدیل
            if side == "BUY":
                order = self.exchange.create_market_buy_order(symbol, amount_to_buy)
                logger.info(f"سفارش خرید اسپات در تبدیل ثبت شد: {order}")
                return order
            elif side == "SELL":
                # در بازار اسپات، فروش به معنای تبدیل ارز پایه به تتر است
                base_currency = symbol.split('/')[0]
                balance = self.exchange.fetch_balance()
                base_free = balance.get(base_currency, {}).get('free', 0.0)
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
            requests.post(self.config.RENDER_WEBHOOK_URL, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"خطا در ارسال داده به رندر: {e}")

# ==================== سیستم اصلی همروش ====================
class HamraveshTradingSystem:
    def __init__(self):
        self.config = Config()
        self.config.validate()
        self.data = DataLayer(self.config)
        self.analysis = AnalysisLayer(self.config)
        self.signal_engine = SignalEngine(self.config)
        self.tabdeal = TabdealTrader(self.config)
        self.notifier = RenderNotifier(self.config)
        self.running = True
        self.last_signal_time: Dict[str, datetime] = {}

    def process_symbol(self, symbol: str):
        try:
            df_15m = self.data.fetch_ohlcv(symbol, timeframe=self.config.ENTRY_TIMEFRAME)
            df_15m = self.analysis.calculate_indicators(df_15m)

            df_4h = self.data.fetch_ohlcv(symbol, timeframe=self.config.TREND_TIMEFRAME)
            df_4h = self.analysis.calculate_indicators(df_4h)

            trend_4h = self.analysis.get_major_trend(df_4h)

            rule_signal = self.signal_engine.get_rule_signal(df_15m, trend_4h)
            if not rule_signal:
                return

            now = datetime.now()
            if symbol in self.last_signal_time:
                if now - self.last_signal_time[symbol] < timedelta(minutes=90):
                    return

            latest = df_15m.iloc[-1]
            ai_result = self.analysis.get_ai_confirmation(symbol, rule_signal, df_15m, trend_4h)

            if ai_result["confidence"] >= self.config.MIN_CONFIDENCE_AI:
                price = float(latest['close'])
                
                # ۱. بررسی موجودی و باز کردن معامله واقعی اسپات در صرافی تبدیل
                order_result = self.tabdeal.execute_spot_order(symbol, rule_signal, price)

                if order_result:
                    # ۲. ارسال سیگنال و نتیجه به رندر برای پوشش تلگرام
                    payload = {
                        "action": "new_trade",
                        "symbol": symbol,
                        "side": rule_signal,
                        "price": price,
                        "confidence": ai_result["confidence"],
                        "trend_4h": trend_4h
                    }
                    self.notifier.send_to_render(payload)

                self.last_signal_time[symbol] = now

        except Exception as e:
            logger.error(f"خطا در پردازش همروش برای {symbol}: {e}")

    def run_once(self):
        logger.info("----- شروع آنالیز و اجرای همروش -----")
        for symbol in self.config.SYMBOLS:
            self.process_symbol(symbol)
            time.sleep(1.5)

    def start(self):
        logger.info("بخش همروش بات فعال شد (حالت واقعی - اسپات)")
        while self.running:
            self.run_once()
            gc.collect()
            time.sleep(self.config.CHECK_INTERVAL)

    def stop(self):
        self.running = False
        logger.info("بخش همروش متوقف شد")

if __name__ == "__main__":
    bot = HamraveshTradingSystem()
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
