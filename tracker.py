import os
import time
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم تريد تلقي إشعار بها
MIN_DISCOUNT = 40

# روابط صفحات التخفيضات وأكبر العروض في جوميا مصر
JUMIA_URLS = [
    {"name": "عروض الفلاش والتخفيضات", "url": "https://www.jumia.com.eg/ar/flash-sales/"},
    {"name": "إلكترونيات وموبايلات", "url": "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount"},
    {"name": "أجهزة منزلية وتلفزيونات", "url": "https://www.jumia.com.eg/ar/home-office-appliances/?sort=discount"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

alerted_links = set()

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
        r = requests.post(url, json=payload, timeout=5)
        print("حالة إرسال تليجرام:", r.status_code, flush=True)
    except Exception as e:
        print("تعذر الاتصال بتليجرام:", e, flush=True)

def parse_number(text):
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def scan_jumia(section_name, url):
    print(f"جاري فحص: {section_name}...", flush=True)
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"استجابة سيرفر جوميا ({section_name}): {r.status_code}", flush=True)
        if r.status_code != 200:
            return

        soup = BeautifulSoup(r.text, "html.parser")
        products = soup.find_all("article", class_="prd")

        print(f"عدد المنتجات الموجودة في الصفحة: {len(products)}", flush=True)

        for prod in products:
            link_elem = prod.find("a", class_="core")
            if not link_elem or not link_elem.get("href"):
                continue

            product_link = "https://www.jumia.com.eg" + link_elem["href"]
            if product_link in alerted_links:
                continue

            name_elem = prod.find("h3", class_="name")
            title = name_elem.get_text().strip() if name_elem else "منتج مخفض على جوميا"

            # استخراج نسبة الخصم
            discount_elem = prod.find("div", class_="bdg")
            if not discount_elem:
                continue
            discount_text = discount_elem.get_text()
            discount_val = parse_number(discount_text)

            # استخراج الأسعار
            current_elem = prod.find("div", class_="prc")
            current_price = current_elem.get_text().strip() if current_elem else "غير محدد"

            old_elem = prod.find("div", class_="old")
            old_price = old_elem.get_text().strip() if old_elem else "غير متوفر"

            if discount_val and discount_val >= MIN_DISCOUNT:
                alerted_links.add(product_link)
                msg = (
                    f"🚨 صيد لقطة من جوميا مصر ({discount_val}% خصم)!\n\n"
                    f"📦 {title}\n"
                    f"💰 السعر الآن: {current_price} (قبل الخصم: {old_price})\n"
                    f"📁 القسم: {section_name}\n"
                    f"🔗 الرابط: {product_link}"
                )
                send_telegram(msg)
                time.sleep(1)

    except Exception as e:
        print(f"خطأ أثناء فحص جوميا: {e}", flush=True)

def main():
    print("--- بدأ تشغيل السكربت بنجاح ---", flush=True)
    send_telegram(f"🚀 بدأ رادار تخفيضات جوميا مصر (يرصد أي خصم من {MIN_DISCOUNT}% فما فوق)...")

    for round_num in range(12):
        print(f"--- جولة الفحص رقم {round_num + 1} ---", flush=True)
        for sec in JUMIA_URLS:
            scan_jumia(sec["name"], sec["url"])
            time.sleep(2)

        if round_num < 11:
            time.sleep(300)

    send_telegram("✅ انتهت جلسة فحص عروض جوميا مصر.")

if __name__ == "__main__":
    main()
