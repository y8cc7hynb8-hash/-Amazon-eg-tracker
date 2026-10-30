import os
import time
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم تريد تنبيهاً بها (مثلاً 25% فما فوق)
MIN_DISCOUNT = 25

# صفحات العروض والأجهزة المخفضة في 2B مصر
URLS_TO_SCAN = [
    {"name": "عروض وتخفيضات 2B الرئيسية", "url": "https://2b.com.eg/ar/deals.html"},
    {"name": "عروض أجهزة اللابتوب والكمبيوتر", "url": "https://2b.com.eg/ar/computers.html?price_drop=1"},
    {"name": "عروض الموبايلات والإلكترونيات", "url": "https://2b.com.eg/ar/mobiles-tablets.html"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

alerted_links = set()

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
        r = requests.post(url, json=payload, timeout=8)
        print("حالة إرسال تليجرام:", r.status_code, flush=True)
    except Exception as e:
        print("خطأ تليجرام:", e, flush=True)

def parse_price(text):
    if not text:
        return 0
    # تنظيف النص وإبقاء الأرقام فقط
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0

def scan_2b(section_name, target_url):
    print(f"جاري فحص: {section_name}...", flush=True)
    try:
        r = requests.get(target_url, headers=HEADERS, timeout=20)
        print(f"استجابة سيرفر 2B ({section_name}): {r.status_code}", flush=True)
        
        if r.status_code != 200:
            return

        soup = BeautifulSoup(r.text, "html.parser")
        # فحص بطاقات المنتجات وفق هيكلية متجر 2B
        products = soup.find_all("li", class_=re.compile(r"product-item"))
        if not products:
            products = soup.find_all("div", class_=re.compile(r"product-item-info"))

        print(f"تم العثور على {len(products)} منتج في الصفحة.", flush=True)

        for prod in products:
            link_elem = prod.find("a", class_=re.compile(r"product-item-link|photo")) or prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue

            link = link_elem["href"].strip()
            if link in alerted_links:
                continue

            name_elem = prod.find("strong", class_="product-item-name") or link_elem
            title = name_elem.get_text().strip() if name_elem else "منتج مخفض على 2B"

            # محاولة قراءة السعر قبل وبعد الخصم
            special_elem = prod.find("span", {"data-price-type": "finalPrice"}) or prod.find("span", class_="special-price")
            old_elem = prod.find("span", {"data-price-type": "oldPrice"}) or prod.find("span", class_="old-price")

            # لو كان نظام السعر بسيط
            if not special_elem:
                prices = prod.find_all("span", class_="price")
                if len(prices) >= 2:
                    old_elem = prices[0]
                    special_elem = prices[1]
                elif len(prices) == 1:
                    special_elem = prices[0]

            curr_price_val = parse_price(special_elem.get_text()) if special_elem else 0
            old_price_val = parse_price(old_elem.get_text()) if old_elem else 0

            # حساب نسبة الخصم
            discount_percent = 0
            if old_price_val > curr_price_val and curr_price_val > 0:
                discount_percent = round(((old_price_val - curr_price_val) / old_price_val) * 100)

            # فحص وجود شارة الخصم بالنص في حال لم تكن الأسعار واضحة
            badge_elem = prod.find(class_=re.compile(r"discount|badge|label", re.I))
            if badge_elem and discount_percent == 0:
                m = re.search(r"(\d+)%", badge_elem.get_text())
                if m:
                    discount_percent = int(m.group(1))

            if discount_percent >= MIN_DISCOUNT:
                alerted_links.add(link)
                msg = (
                    f"🔥 لقطة جديدة من موقع 2B Egypt ({discount_percent}% خصم)!\n\n"
                    f"📦 {title[:90]}\n"
                    f"💰 السعر بعد الخصم: {curr_price_val:,} ج.م\n"
                    f"🏷 السعر السابق: {old_price_val:,} ج.م\n"
                    f"🔗 الرابط المباشر: {link}"
                )
                send_telegram(msg)
                time.sleep(1)

    except Exception as e:
        print(f"خطأ أثناء فحص 2B: {e}", flush=True)

def main():
    print("--- بدأ تشغيل رادار متجر 2B مصر ---", flush=True)
    send_telegram(f"🚀 بدأ رادار تخفيضات 2B مصر (رصد خصم من {MIN_DISCOUNT}% فما فوق)...")

    # فحص دوري لمدة ساعة (12 جولة × 5 دقائق)
    for round_num in range(12):
        print(f"--- جولة الفحص رقم {round_num + 1} ---", flush=True)
        for sec in URLS_TO_SCAN:
            scan_2b(sec["name"], sec["url"])
            time.sleep(2)

        if round_num < 11:
            time.sleep(300)

    send_telegram("✅ انتهت جلسة رصد عروض 2B مصر.")

if __name__ == "__main__":
    main()
