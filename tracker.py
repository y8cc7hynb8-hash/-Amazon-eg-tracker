import os
import time
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم عايز يجيلك بيها إشعار (تقدر تغيرها لـ 50 أو 60 لو عايز اللقطات القوية فقط)
MIN_DISCOUNT = 40

# روابط البحث لأهم الأقسام مرتبة بأحدث المنتجات والعروض (تقدر تضيف أي رابط بحث من أمازون هنا)
CATEGORY_URLS = [
    {"name": "إلكترونيات", "url": "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901100031"},
    {"name": "أجهزة منزلية ومطبخ", "url": "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901100031"},
    {"name": "كمبيوتر وإكسسواراته", "url": "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901100031"},
    {"name": "موبايلات وملحقاتها", "url": "https://www.amazon.eg/s?i=mobile&rh=p_n_deal_type%3A26901100031"},
    {"name": "ألعاب الفيديو", "url": "https://www.amazon.eg/s?i=videogames&rh=p_n_deal_type%3A26901100031"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
}

# ذاكرة مؤقتة لمنع تكرار إرسال نفس العرض خلال الساعتين
alerted_asins = set()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
    requests.post(url, json=payload)

def parse_number(text):
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def scan_category(cat_name, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return
        
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.find_all("div", {"data-component-type": "s-search-result"})
        
        for item in items:
            asin = item.get("data-asin")
            if not asin or asin in alerted_asins:
                continue

            # استخراج اسم المنتج
            title_elem = item.find("h2")
            title = title_elem.get_text().strip() if title_elem else "منتج على أمازون"

            # استخراج السعر الحالي
            price_whole = item.find("span", {"class": "a-price-whole"})
            if not price_whole:
                continue
            current_price = parse_number(price_whole.get_text())

            # استخراج السعر القديم قبل الخصم لحساب النسبة الحقيقية
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
        print(f"Error scanning {cat_name}: {e}")

def main():
    send_telegram(f"🚀 بدأ الرادار الشامل لأمازون مصر (يرصد أي خصم من {MIN_DISCOUNT}% فما فوق)...")
    
    # فحص مستمر لمدة ساعتين (24 جولة × 5 دقائق)
    for round_num in range(24):
        for cat in CATEGORY_URLS:
            scan_category(cat["name"], cat["url"])
            time.sleep(5)
        
        if round_num < 23:
            time.sleep(300) # انتظار 5 دقائق قبل المسح التالي

    send_telegram("✅ انتهت جلسة الفحص الشامل (ساعتين).")

if __name__ == "__main__":
    main()
