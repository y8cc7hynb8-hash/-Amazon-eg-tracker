import os
import time
import random
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم تريد تنبيهاً بها
MIN_DISCOUNT = 25

# إعداد جلسة اتصال مع ترويسات متصفح كاملة
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
})

alerted_links = set()

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
        f"🔥 صيد جديد من متجر {store} ({discount}% خصم)!\n\n"
        f"📦 {title[:80]}\n"
        f"💰 السعر الآن: {current_p:,} ج.م\n"
        f"🏷 السعر السابق: {old_p:,} ج.م\n"
        f"🔗 الرابط: {link}"
    )
    send_telegram(msg)
    time.sleep(2)

# --- 1. متجر 2B ---
def scan_2b():
    store_name = "2B Egypt"
    urls = ["https://2b.com.eg/ar/deals.html"]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = session.get(url, timeout=20)
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
        time.sleep(random.uniform(6, 10))

# --- 2. متجر دبي فون ---
def scan_dubai_phone():
    store_name = "دبي فون (Dubai Phone)"
    urls = ["https://dubaiphone.net/ar/deals"]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                print(f"[{store_name}] تخطي، كود: {r.status_code}", flush=True)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.find_all("div", class_=re.compile(r"product-layout|product-thumb|product-item"))
            for prod in products:
                link_elem = prod.find("a", href=True)
                if not link_elem or not link_elem.get("href"):
                    continue
                link = link_elem["href"]
                if link in alerted_links:
                    continue

                title = link_elem.get_text().strip() or "منتج دبي فون"
                price_new = prod.find(class_=re.compile(r"price-new|special-price"))
                price_old = prod.find(class_=re.compile(r"price-old|old-price"))

                curr_p = parse_price(price_new.get_text()) if price_new else 0
                old_p = parse_price(price_old.get_text()) if price_old else 0
                discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p and curr_p > 0) else 0

                if discount >= MIN_DISCOUNT:
                    alerted_links.add(link)
                    send_deal_alert(store_name, title, curr_p, old_p, discount, link)
        except Exception as e:
            print(f"[{store_name}] خطأ: {e}", flush=True)
        time.sleep(random.uniform(6, 10))

# --- 3. متجر رنين ---
def scan_raneen():
    store_name = "رنين (Raneen)"
    urls = ["https://www.raneen.com/ar/deals"]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = session.get(url, timeout=20)
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
        time.sleep(random.uniform(6, 10))

# --- 4. متجر راية شوب ---
def scan_rayashop():
    store_name = "راية شوب (Raya Shop)"
    urls = ["https://www.rayashop.com/ar/deals"]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = session.get(url, timeout=20)
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

                title = link_elem.get_text().strip() or "عرض راية شوب"
                curr_elem = prod.find("span", {"data-price-type": "finalPrice"}) or prod.find(class_=re.compile(r"special-price"))
                old_elem = prod.find("span", {"data-price-type": "oldPrice"}) or prod.find(class_=re.compile(r"old-price"))

                curr_p = parse_price(curr_elem.get_text()) if curr_elem else 0
                old_p = parse_price(old_elem.get_text()) if old_elem else 0
                discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p and curr_p > 0) else 0

                if discount >= MIN_DISCOUNT:
                    alerted_links.add(link)
                    send_deal_alert(store_name, title, curr_p, old_p, discount, link)
        except Exception as e:
            print(f"[{store_name}] خطأ: {e}", flush=True)
        time.sleep(random.uniform(6, 10))

def main():
    print("--- بدأ رادار الصيد الهادئ للمتاجر المصرية ---", flush=True)
    send_telegram(f"🚀 بدأ رادار الصيد بنمط التصفح البشري (خصومات {MIN_DISCOUNT}% فما فوق)...")

    # 6 جولات كل جولة بينها 10 دقائق (إجمالي تشغيل حوالي ساعة)
    for round_num in range(6):
        print(f"=== بداية الجولة رقم {round_num + 1} ===", flush=True)
        
        scan_2b()
        scan_dubai_phone()
        scan_raneen()
        scan_rayashop()

        if round_num < 5:
            wait_time = random.randint(540, 660)  # انتظار من 9 إلى 11 دقيقة
            print(f"انتظار {wait_time // 60} دقائق قبل الجولة التالية...", flush=True)
            time.sleep(wait_time)

    send_telegram("✅ انتهت جلسة الفحص اليومية.")

if __name__ == "__main__":
    main()
