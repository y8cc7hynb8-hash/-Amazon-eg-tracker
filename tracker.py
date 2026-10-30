import os
import time
import re
import requests
from bs4 import BeautifulSoup

# بيانات تليجرام بيسحبها الكود تلقائياً من الإعدادات الآمنة
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# قائمة المنتجات المطلوب مراقبتها: الكود، السعر المستهدف، واسم توضيحي ليك
WATCHLIST = [
    {"asin": "B08N5WRWNW", "target": 1500, "name": "منتج تجريبي 1"},
    {"asin": "B09V3KXJPB", "target": 3000, "name": "منتج تجريبي 2"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, json=payload)

def get_price(asin):
    url = f"https://www.amazon.eg/dp/{asin}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            price_span = soup.find("span", {"class": "a-price-whole"})
            if price_span:
                raw_text = price_span.get_text()
                digits = re.sub(r"[^\d]", "", raw_text)
                if digits:
                    return int(digits)
    except Exception as e:
        print(f"Error checking {asin}: {e}")
    return None

def main():
    send_telegram("🚀 بدأ فحص أسعار أمازون مصر لمدة ساعتين...")
    # 12 جولة × 10 دقائق = ساعتين (120 دقيقة)
    for round_num in range(12):
        print(f"--- جولة الفحص رقم {round_num + 1} ---")
        for item in WATCHLIST:
            price = get_price(item["asin"])
            if price:
                print(f"{item['name']}: {price} EGP")
                if price <= item["target"]:
                    msg = (
                        f"🔥 لقطة على أمازون مصر!\n"
                        f"المنتج: {item['name']}\n"
                        f"السعر الحالي: {price} ج.م (المستهدف: {item['target']} ج.م)\n"
                        f"الرابط: https://www.amazon.eg/dp/{item['asin']}"
                    )
                    send_telegram(msg)
            time.sleep(5)
        
        if round_num < 11:
            time.sleep(600) # انتظار 10 دقائق قبل الجولة التالية

    send_telegram("✅ انتهت جلسة المراقبة (ساعتين).")

if __name__ == "__main__":
    main()
