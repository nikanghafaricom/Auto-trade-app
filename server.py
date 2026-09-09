# ==============================================
# نسخه تست موقت - این فایل جایگزین موقت hamravesh_bot.py است
# فقط یک کار می‌کند: به محض اجرا، کل موجودی BNB را می‌فروشد و نتیجه را چاپ می‌کند.
# اگر بعد از دیپلوی همین نسخه هم لاگ قدیمی دیدی، مشکل از پلتفرم دیپلوی است نه کد.
# ==============================================
import os
import sys
import math
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

WALLEX_API_KEY = os.getenv("WALLEX_API_KEY", "").strip()
BASE_URL = "https://api.wallex.ir/v1"
SYMBOL_BASE = "BNB"
WALLEX_SYMBOL = "BNBUSDT"


def log(msg):
    print(f"[TEST-SELL] {msg}", flush=True)
    sys.stdout.flush()


def get_market_limits(wallex_symbol: str):
    url = f"{BASE_URL}/markets"
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
        data = res.json()
        symbols = data.get('result', {}).get('symbols', {})
        symbol_data = symbols.get(wallex_symbol, {})
        min_qty = float(symbol_data.get('minQty', 0) or 0)
        min_notional = float(symbol_data.get('minNotional', 0) or 0)
        step_size = int(symbol_data.get('stepSize', 6))
        return min_qty, min_notional, step_size
    log(f"خطا در دریافت محدودیت‌های بازار {wallex_symbol} - کد: {res.status_code}")
    return 0.0, 0.0, 6


def get_wallex_price(wallex_symbol: str):
    url = f"{BASE_URL}/markets"
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
        data = res.json()
        symbols = data.get('result', {}).get('symbols', {})
        symbol_data = symbols.get(wallex_symbol, {})
        last_price = symbol_data.get('stats', {}).get('lastPrice')
        if last_price is not None:
            return float(last_price)
    log(f"خطا در دریافت قیمت لحظه‌ای {wallex_symbol}")
    return None


def get_base_free(base_symbol: str):
    url = f"{BASE_URL}/account/balances"
    headers = {"X-API-Key": WALLEX_API_KEY}
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        res_json = res.json()
        result_data = res_json.get('result', res_json)
        balances_dict = result_data.get('balances', result_data)

        if isinstance(balances_dict, dict):
            asset_info = balances_dict.get(base_symbol, {})
            return float(asset_info.get('value', asset_info.get('free', 0.0)))
        elif isinstance(balances_dict, list):
            for asset in balances_dict:
                if asset.get('asset', asset.get('symbol', '')).upper() == base_symbol.upper():
                    return float(asset.get('value', asset.get('free', 0.0)))
        return 0.0
    else:
        log(f"خطا در دریافت موجودی - کد: {res.status_code} | متن: {res.text}")
        return 0.0


def submit_order(wallex_symbol: str, side: str, quantity: float, price: float, order_type: str = "market"):
    url = f"{BASE_URL}/account/orders"
    headers = {
        "X-API-Key": WALLEX_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "symbol": wallex_symbol,
        "type": order_type,
        "side": side,
        "quantity": quantity,
        "price": str(price)
    }
    log(f"ارسال سفارش -> {json.dumps(payload, ensure_ascii=False)}")
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    return response


def place_sell_with_retries(wallex_symbol: str, quantity: float, price: float):
    limit_offsets = [0, 0.002, 0.01]

    response = submit_order(wallex_symbol, "sell", quantity, price, "market")
    log(f"پاسخ Market (sell) - کد: {response.status_code} | متن: {response.text}")
    if response.status_code in [200, 201]:
        return response

    if "امکان ثبت سفارش قیمت بازار" not in response.text:
        return response

    for offset in limit_offsets:
        limit_price = price * (1 - offset)
        response = submit_order(wallex_symbol, "sell", quantity, limit_price, "limit")
        log(f"پاسخ Limit آفست {offset * 100:.1f}٪ (sell) - کد: {response.status_code} | متن: {response.text}")
        if response.status_code in [200, 201]:
            return response

    return response


def run_test():
    log("############################################")
    log("### این نسخه تست موقت فروش BNB است ###")
    log("############################################")

    if not WALLEX_API_KEY:
        log("❌ WALLEX_API_KEY تنظیم نشده. تست متوقف شد.")
        return

    log(f"API KEY یافت شد (طول: {len(WALLEX_API_KEY)} کاراکتر)")

    base_free = get_base_free(SYMBOL_BASE)
    log(f"موجودی فعلی {SYMBOL_BASE}: {base_free}")

    if base_free <= 0:
        log(f"⚠️ موجودی {SYMBOL_BASE} صفر یا منفی است. چیزی برای فروش نیست.")
        return

    price = get_wallex_price(WALLEX_SYMBOL)
    if price is None:
        log("❌ دریافت قیمت لحظه‌ای ناموفق بود. تست متوقف شد.")
        return
    log(f"قیمت لحظه‌ای {WALLEX_SYMBOL}: {price}")

    min_qty, min_notional, step_size = get_market_limits(WALLEX_SYMBOL)
    log(f"محدودیت‌های بازار {WALLEX_SYMBOL} -> minQty: {min_qty} | minNotional: {min_notional} | stepSize: {step_size}")

    factor = 10 ** step_size
    quantity = math.floor(base_free * factor) / factor
    log(f"مقدار نهایی برای فروش پس از گرد کردن ({step_size} رقم اعشار): {quantity}")

    if quantity <= 0:
        log("⚠️ مقدار پس از گرد کردن صفر شد. فروش ممکن نیست.")
        return

    result = place_sell_with_retries(WALLEX_SYMBOL, quantity, price)

    if result.status_code in [200, 201]:
        log(f"✅✅✅ فروش {SYMBOL_BASE} موفق بود. ✅✅✅")
    else:
        log(f"❌❌❌ فروش {SYMBOL_BASE} ناموفق بود. کد نهایی: {result.status_code} | متن نهایی: {result.text} ❌❌❌")

    log("=== تست تمام شد ===")


# اجرای فوری در لحظه بارگذاری ماژول (نه فقط داخل __main__)
# تا حتی اگر پلتفرم دیپلوی به شکل متفاوتی فایل را صدا بزند، تست اجرا شود.
run_test()

# یک وب‌سرور ساده هم بالا می‌آوریم تا اگر پلتفرم منتظر باز بودن پورت است، health-check رد نشود.
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class SimpleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("تست فروش BNB اجرا و تمام شد. لاگ‌ها را بررسی کن.".encode('utf-8'))

        def log_message(self, format, *args):
            return

    port = int(os.environ.get("PORT", 8080))
    log(f"وب‌سرور موقت روی پورت {port} بالا آمد (فقط برای health-check).")
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()
except Exception as e:
    log(f"وب‌سرور موقت اجرا نشد (مهم نیست): {e}")
    while True:
        time.sleep(60)
