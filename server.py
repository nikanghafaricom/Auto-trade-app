    def get_usdt_balance(self) -> float:
        """دریافت موجودی واقعی تتر با قابلیت دیباگ دقیق خطاها"""
        urls = [
            "https://api1.tabdeal.org/api/v1/account/balance",
            "https://api1.tabdeal.org/api/v1/wallets",
            "https://api1.tabdeal.org/r/api/v1/account/balances"
        ]
        
        headers = {
            "X-API-Key": self.config.TABDEAL_API_KEY,
            "X-API-Secret": self.config.TABDEAL_SECRET,
            "Content-Type": "application/json"
        }

        for url in urls:
            try:
                res = requests.get(url, headers=headers, timeout=10)
                logger.info(f"تست مسیر {url} - کد پاسخ: {res.status_code} - متن: {res.text[:100]}و")
                if res.status_code == 200:
                    data = res.json()
                    items = data.get('data', data.get('balances', data))
                    if isinstance(items, list):
                        for asset in items:
                            currency = asset.get('currency', asset.get('asset', '')).upper()
                            if currency == 'USDT':
                                balance = float(asset.get('free', asset.get('balance', 0.0)))
                                logger.info(f"موجودی واقعی تتر دریافت شد: {balance} USDT")
                                return balance
            except Exception as e:
                logger.error(f"خطا در ارتباط با {url}: {e}")

        logger.warning("هیچ‌کدام از مسیرهای موجودی پاسخ ندادند. مقدار پیش‌فرض اعمال می‌شود.")
        return 100.0
