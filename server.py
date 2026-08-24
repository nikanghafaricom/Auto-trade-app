# ==============================================
# Hamravesh Server - Analysis & Tabdeal Trading
# ==============================================
import os
import time
import logging
import requests
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Dict, Optional
import pandas as pd
import ccxt
from dotenv import load_dotenv

load_dotenv()

# ==================== تنظیمات ====================
class Config:
    # اطلاعات صرافی تبدیل (Tabdeal) - فرض بر این است که ccxt یا API اختصاصی دارد
    TABDEAL_API_KEY = os.getenv("TABDEAL_API_KEY", "")
    TABDEAL_SECRET = os.getenv("TABDEAL_SECRET", "")
    
    RENDER_CALLBACK_URL = os.getenv("RENDER_CALLBACK_URL", "https://your-render-app.onrender.com/webhook")
    
    # اطلاعات بله برای پیام شخصی (یا ربات بله)
    # اگر از بله استفاده می‌کنید توکن و چت‌آیدی آن را اینجا قرار دهید
    BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "")
    BALE_CHAT_ID = os.getenv("BALE_CHAT_ID", "")

# ==================== لاگ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== لایه صرافی تبدیل (Spot) ====================
class TabdealExchange:
    def __init__(self):
        # توجه: اگر صرافی تبدیل در ccxt پشتیبانی شود با نام خودش یا ساختار سفارشی متصل می‌شود
        # در اینجا ساختار استاندارد CCXT برای صرافی ایرانی یا اتصال مستقیم نوشته شده است
        try:
            self.exchange = ccxt.tabdeal({
                'apiKey': Config.TABDEAL_API_KEY,
                'secret': Config.TABDEAL_SECRET,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
        except Exception:
            self.exchange = None

    def get_usdt_balance(self) -> float:
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['free'].get('USDT', 0.0))
        except Exception as e:
            logger.error(f"خطا در دریافت موجودی تبدیل: {e}")
            return 0.0

    def execute_spot_order(self, symbol: str, side: str, amount_usdt: float, price: float):
        try:
            # معامله اسپات بدون اهرم
            amount = amount_usdt / price
            if side == "BUY":
                order = self.exchange.create_market_buy_order(symbol, amount)
            else:
                order = self.exchange.create_market_sell_order(symbol, amount)
            return order
        except Exception as e:
            logger.error(f"خطا در اجرای معامله واقعی در تبدیل: {e}")
            return None

# ==================== لایه تحلیل تکنیکال (دقیقاً مطابق کد شما) ====================
class AnalysisLayer:
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

# ==================== وب‌سرور همروش (دریافت داده از رندر) ====================
analysis_layer = AnalysisLayer()
tabdeal = TabdealExchange()
last_signal_time: Dict[str, datetime] = {}

class HamraveshWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            payload = json.loads(post_data.decode('utf-8'))
            symbol = payload.get("symbol")
            ohlcv_15m_raw = payload.get("ohlcv_15m")
            ohlcv_4h_raw = payload.get("ohlcv_4h")

            df_15m = pd.DataFrame(ohlcv_15m_raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_4h = pd.DataFrame(ohlcv_4h_raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            df_15m = analysis_layer.calculate_indicators(df_15m)
            df_4h = analysis_layer.calculate_indicators(df_4h)

            trend_4h = analysis_layer.get_major_trend(df_4h)
            rule_signal = analysis_layer.get_rule_signal(df_15m, trend_4h)

            if rule_signal:
                now = datetime.now()
                if symbol not in last_signal_time or (now - last_signal_time[symbol]) > timedelta(minutes=90):
                    last_signal_time[symbol] = now
                    latest = df_15m.iloc[-1]
                    price = float(latest['close'])

                    # ۱. بررسی موجودی صرافی تبدیل و مدیریت سرمایه اسپات
                    usdt_balance = tabdeal.get_usdt_balance()
                    allocated_budget = usdt_balance * 0.10  # مثلا ۱۰ درصد موجودی برای هر معامله اسپات

                    if allocated_budget > 10:  # حداقل مقدار معتبر
                        order_result = tabdeal.execute_spot_order(symbol, rule_signal, allocated_budget, price)
                        
                        if order_result:
                            # ساخت متن سیگنال دقیقا مثل کد قبلی شما
                            emoji = "🟢" if rule_signal == "BUY" else "🔴"
                            msg = f"""
{emoji} **ULTRA SIGNAL (Real Trade): {rule_signal}**
📍 **Symbol:** {symbol}
💵 **Entry Price:** {price:,}
⚙️ **Spot Execution:** Success ({allocated_budget:.2f} USDT)
⏰ {now.strftime('%Y-%m-%d %H:%M')}
"""
                            # ۲. ارسال سیگنال به رندر برای انتشار در کانال تلگرام
                            requests.post(Config.RENDER_CALLBACK_URL, json={
                                "action": "send_channel_signal",
                                "message": msg
                            }, timeout=10)

                            # ۳. ارسال گزارش معامله واقعی در پی‌وی (بله یا تلگرام)
                            pv_msg = f"✅ معامله واقعی اسپات در تبدیل انجام شد:\nارز: {symbol}\nجهت: {rule_signal}\nقیمت: {price}"
                            requests.post(Config.RENDER_CALLBACK_URL, json={
                                "action": "send_pv_report",
                                "message": pv_msg
                            }, timeout=10)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Processed successfully")
        except Exception as e:
            logger.error(f"خطا در پردازش داده در همروش: {e}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hamravesh Webhook Server is running!")

    def log_message(self, format, *args):
        return

def run_hamravesh_server():
    server = HTTPServer(("0.0.0.0", 80), HamraveshWebhookHandler)
    logger.info("پروژه همروش روی پورت 80 آماده دریافت داده است...")
    server.serve_forever()

if __name__ == "__main__":
    run_hamravesh_server()
