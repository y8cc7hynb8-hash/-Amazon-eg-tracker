import os
import time
import random
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# نسبة الخصم المطلوبة للتنبيه (40% فما فوق)
MIN_DISCOUNT = 40

# قائمة User-Agents عشوائية لمتصفحات حقيقية متنوعة
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

session = requests.Session()
alerted_links = set()

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1"
    }

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
        r = session.post(url, json=payload, timeout=8)
        print(f"حالة إرسال تليجرام: {r.status_code}", flush=True)
    except Exception as e:
        print(f"خطأ تليجرام: {e}", flush=True)

def parse_price(text):
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0

def send_deal_alert(store, title, current_p, old_p, discount, link):
    msg = (
        f"🚨 صيد لقطة نادرة من {store} ({discount}% خصم)!\n\n"
        f"📦 {title[:80]}\n"
        f"💰 السعر الآن: {current_p:,} ج.م\n"
        f"🏷 السعر السابق: {old_p:,} ج.م\n"
        f"🔗 الرابط: {link}"
    )
    send_telegram(msg)
    time.sleep(2)

# --- فحص متجر 2B Egypt ---
def scan_2b():
    store_name = "2B Egypt"
    urls = [
        "https://2b.com.eg/ar/deals.html",
        "https://2b.com.eg/ar/computers.html?price_drop=1"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = session.get(url, headers=get_headers(), timeout=20)
            if r.status_code != 200:
                print(f"[{store_name}] تخطي، كود: {r.status_code}", flush=True)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.find_all("li", class_=re.compile(r"product-item"))
            for prod in products:
                link_elem = prod.find("a", class_=re.compile(r"product-item-link|photo")) or prod.find("a", href=True)
                if not link_elem or not link_elem.get("href"):
                    continue
                link = link_elem["href"].strip()
                if link in alerted_links:
                    continue

                title = link_elem.get_text().strip()
                special_elem = prod.find("span", {"data-price-type": "finalPrice"}) or prod.find("span", class_="special-price")
                old_elem = prod.find("span", {"data-price-type": "oldPrice"}) or prod.find("span", class_="old-price")

                curr_p = parse_price(special_elem.get_text()) if special_elem else 0
                old_p = parse_price(old_elem.get_text()) if old_elem else 0
                discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p and curr_p > 0) else 0

                if discount >= MIN_DISCOUNT:
                    alerted_links.add(link)
                    send_deal_alert(store_name, title, curr_p, old_p, discount, link)
        except Exception as e:
            print(f"[{store_name}] خطأ: {e}", flush=True)
        # محاكاة إنسانية: انتظار عشوائي بين الصفحات
        time.sleep(random.uniform(10, 18))

# --- فحص متجر رنين (Raneen) ---
def scan_raneen():
    store_name = "رنين (Raneen)"
    urls = [
        "https://www.raneen.com/ar/deals",
        "https://www.raneen.com/ar/deals/appliances-electronics-offers"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = session.get(url, headers=get_headers(), timeout=20)
            if r.status_code != 200:
                print(f"[{store_name}] تخطي، كود: {r.status_code}", flush=True)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.find_all("div", class_=re.compile(r"product-item|productCard"))
            for prod in products:
                link_elem = prod.find("a", href=True)
                if not link_elem or not link_elem.get("href"):
                    continue
                href = link_elem["href"]
                link = href if href.startswith("http") else "https://www.raneen.com" + href
                if link in alerted_links:
                    continue

                title = prod.find(class_=re.compile(r"title|name")).get_text().strip() if prod.find(class_=re.compile(r"title|name")) else "عرض رنين"
                prices = prod.find_all(class_=re.compile(r"price"))
                curr_p = parse_price(prices[0].get_text()) if prices else 0
                old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
                discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p and curr_p > 0) else 0

                if discount >= MIN_DISCOUNT:
                    alerted_links.add(link)
                    send_deal_alert(store_name, title, curr_p, old_p, discount, link)
        except Exception as e:
            print(f"[{store_name}] خطأ: {e}", flush=True)
        # محاكاة إنسانية: انتظار عشوائي بين الصفحات
        time.sleep(random.uniform(10, 18))

def main():
    print("--- بدأ رادار الصيد (2B ورنين) بخصومات 40%+ ---", flush=True)
    send_telegram(f"🚀 بدأ رادار الصيد لمتجري (2B ورنين) يرصد أي خصم من {MIN_DISCOUNT}% فما فوق بنمط تصفح هادئ...")

    # 4 جولات فحص فقط (لتفادي أي حظر IP ناتج عن الجلسات الطويلة)
    for round_num in range(4):
        print(f"=== بداية الجولة رقم {round_num + 1} ===", flush=True)
        
        scan_2b()
        time.sleep(random.uniform(15, 25))
        scan_raneen()

        if round_num < 3:
            # انتظار بين 12 إلى 15 دقيقة بين الجولات
            wait_seconds = random.randint(720, 900)
            print(f"انتظار {wait_seconds // 60} دقيقة قبل الجولة التالية محاكاة للتصفح الطبيعي...", flush=True)
            time.sleep(wait_seconds)

    send_telegram("✅ انتهت جلسة الفحص الحالية.")

if __name__ == "__main__":
    main()
