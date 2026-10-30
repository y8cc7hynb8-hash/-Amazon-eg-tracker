import os
import time
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MIN_DISCOUNT = 40

CATEGORY_URLS = [
    {"name": "إلكترونيات", "url": "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901100031"},
    {"name": "أجهزة منزلية ومطبخ", "url": "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901100031"},
    {"name": "كمبيوتر وإكسسواراته", "url": "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901100031"},
    {"name": "موبايلات وملحقاتها", "url": "https://www.amazon.eg/s?i=mobile&rh=p_n_deal_type%3A26901100031"},
    {"name": "ألعاب الفيديو", "url": "https://www.amazon.eg/s?i=videogames&rh=p_n_deal_type%3A26901100031"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

alerted_asins = set()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram response status:", r.status_code, flush=True)
    except Exception as e:
        print("Telegram error:", e, flush=True)

def parse_number(text):
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def scan_category(cat_name, url):
    print(f"جاري فحص قسم: {cat_name}...", flush=True)
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        print(f"حالة استجابة القسم {cat_name}: {r.status_code}", flush=True)
        if r.status_code != 200:
            return
        
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.find_all("div", {"data-component-type": "s-search-result"})
        print(f"عدد المنتجات المكتشفة في {cat_name}: {len(items)}", flush=True)
        
        for item in items:
            asin = item.get("data-asin")
            if not asin or asin in alerted_asins:
                continue

            title_elem = item.find("h2")
            title = title_elem.get_text().strip() if title_elem else "منتج على أمازون"

            price_whole = item.find("span", {"class": "a-price-whole"})
            if not price_whole:
                continue
            current_price = parse_number(price_whole.get_text())

            old_price_span = item.find("span", {"class": "a-text-price"})
            if not old_price_span:
                continue
            old_price_offscreen = old_price_span.find("span", {"class": "a-offscreen"})
            old_price_text = old_price_offscreen.get_text() if old_price_offscreen else old_price_span.get_text()
            old_price = parse_number(old_price_text)

            if current_price and old_price and old_price > current_price:
                discount = int(((old_price - current_price) / old_price) * 100)
                
                if discount >= MIN_DISCOUNT:
                    alerted_asins.add(asin)
                    msg = (
                        f"🚨 خصم {discount}% في قسم ({cat_name})!\n\n"
                        f"📦 {title[:90]}...\n"
                        f"💰 السعر الآن: {current_price} ج.م (بدلاً من {old_price} ج.م)\n"
                        f"🔗 الرابط: https://www.amazon.eg/dp/{asin}"
                    )
                    send_telegram(msg)
                    time.sleep(1)
    except Exception as e:
        print(f"Error scanning {cat_name}: {e}", flush=True)

def main():
    print("بدء تنفيذ السكريبت وإرسال تنبيه البداية...", flush=True)
    send_telegram(f"🚀 بدأ الرادار الشامل لأمازون مصر (يرصد أي خصم من {MIN_DISCOUNT}% فما فوق)...")
    
    for cat in CATEGORY_URLS:
        scan_category(cat["name"], cat["url"])
        time.sleep(2)
        
    print("انتهت الجولة الأولى بنجاح.", flush=True)
    send_telegram("✅ تمت الجولة الأولى بنجاح والسيستم يعمل بكفاءة.")

if __name__ == "__main__":
    main()
