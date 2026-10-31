import os
import time
import random
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# يرصد أي انخفاض حقيقي في السعر من 15% فما فوق
MIN_DISCOUNT = 15

# مكتبة متصفحات حقيقية ومحدثة للتدوير العشوائي
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0"
]

session = requests.Session()
alerted_links = set()

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
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
        f"🚨 انهيار سعر / لقطة من {store} ({discount}% خصم)!\n\n"
        f"📦 {title[:80]}\n"
        f"💰 السعر الآن: {current_p:,} ج.م\n"
        f"🏷 السعر السابق: {old_p:,} ج.م\n"
        f"🔗 الرابط: {link}"
    )
    send_telegram(msg)
    time.sleep(1.5)

# دالة آمنة لجلب الصفحات مع تخطي أي متجر يظهر حظراً أو مشكلة فوراً
def safe_fetch(url, store_name):
    try:
        r = session.get(url, headers=get_headers(), timeout=12)
        if r.status_code == 200:
            return r.text
        elif r.status_code in [403, 429]:
            print(f"⚠️ [{store_name}] حظر أو قيود ({r.status_code}). سيتم عمل Skip تلقائياً.", flush=True)
            return None
        else:
            print(f"⚠️ [{store_name}] كود استجابة غير صالح ({r.status_code}). تخطي.", flush=True)
            return None
    except Exception as e:
        print(f"⚠️ [{store_name}] تعذر الاتصال: {e}. تخطي للمتجر التالي.", flush=True)
        return None

# --- 1. متجر 2B Egypt ---
def scan_2b():
    store = "2B Egypt"
    urls = ["https://2b.com.eg/ar/deals.html", "https://2b.com.eg/ar/computers.html?price_drop=1"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("li", class_=re.compile(r"product-item"))
        for prod in products:
            link_elem = prod.find("a", class_=re.compile(r"product-item-link|photo")) or prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            link = link_elem["href"].strip()
            if link in alerted_links:
                continue

            title = link_elem.get_text().strip()
            special = prod.find("span", {"data-price-type": "finalPrice"}) or prod.find("span", class_="special-price")
            old = prod.find("span", {"data-price-type": "oldPrice"}) or prod.find("span", class_="old-price")
            curr_p, old_p = parse_price(special.get_text()) if special else 0, parse_price(old.get_text()) if old else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 2. متجر رنين (Raneen) ---
def scan_raneen():
    store = "رنين (Raneen)"
    urls = ["https://www.raneen.com/ar/deals"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
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
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 3. متجر كنز (Kenzz) ---
def scan_kenzz():
    store = "كنز (Kenzz)"
    urls = ["https://kenzz.com/offers"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("div", class_=re.compile(r"product|card|item", re.I))
        for prod in products:
            link_elem = prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            href = link_elem["href"].strip()
            link = href if href.startswith("http") else "https://kenzz.com" + href
            if link in alerted_links:
                continue

            title = prod.find(class_=re.compile(r"title|name", re.I))
            title_text = title.get_text().strip() if title else "منتج كنز"
            if len(title_text) < 4:
                continue

            prices = prod.find_all(class_=re.compile(r"price", re.I))
            curr_p = parse_price(prices[0].get_text()) if prices else 0
            old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title_text, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 4. متجر دبي فون (Dubai Phone) ---
def scan_dubai_phone():
    store = "دبي فون (Dubai Phone)"
    urls = ["https://dubaiphone.net/ar/deals"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("div", class_=re.compile(r"product-layout|product-thumb|product-item"))
        for prod in products:
            link_elem = prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            link = link_elem["href"]
            if link in alerted_links:
                continue

            title = link_elem.get_text().strip() or "منتج دبي فون"
            p_new = prod.find(class_=re.compile(r"price-new|special-price"))
            p_old = prod.find(class_=re.compile(r"price-old|old-price"))
            curr_p = parse_price(p_new.get_text()) if p_new else 0
            old_p = parse_price(p_old.get_text()) if p_old else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 5. متجر راية شوب (RayaShop) ---
def scan_rayashop():
    store = "راية شوب (Raya Shop)"
    urls = ["https://www.rayashop.com/ar/deals"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("li", class_=re.compile(r"product-item"))
        for prod in products:
            link_elem = prod.find("a", class_=re.compile(r"product-item-link|photo")) or prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            link = link_elem["href"].strip()
            if link in alerted_links:
                continue

            title = link_elem.get_text().strip() or "عرض راية شوب"
            curr_e = prod.find("span", {"data-price-type": "finalPrice"}) or prod.find(class_=re.compile(r"special-price"))
            old_e = prod.find("span", {"data-price-type": "oldPrice"}) or prod.find(class_=re.compile(r"old-price"))
            curr_p = parse_price(curr_e.get_text()) if curr_e else 0
            old_p = parse_price(old_e.get_text()) if old_e else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 6. متجر تريد لاين (TradeLine Egypt) ---
def scan_tradeline():
    store = "تريد لاين (TradeLine)"
    urls = ["https://tradelinestores.com/collections/offers"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("div", class_=re.compile(r"product-card|grid-item|card", re.I))
        for prod in products:
            link_elem = prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            href = link_elem["href"]
            link = href if href.startswith("http") else "https://tradelinestores.com" + href
            if link in alerted_links:
                continue

            title = prod.find(class_=re.compile(r"title|name", re.I))
            title_text = title.get_text().strip() if title else "عرض تريد لاين"

            prices = prod.find_all(class_=re.compile(r"price", re.I))
            curr_p = parse_price(prices[0].get_text()) if prices else 0
            old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title_text, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 7. متجر دريم 2000 (Dream 2000) ---
def scan_dream2000():
    store = "دريم 2000 (Dream 2000)"
    urls = ["https://dream2000.com/ar/offers"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("li", class_=re.compile(r"product-item"))
        for prod in products:
            link_elem = prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            link = link_elem["href"].strip()
            if link in alerted_links:
                continue

            title = link_elem.get_text().strip() or "عرض دريم 2000"
            special = prod.find("span", {"data-price-type": "finalPrice"})
            old = prod.find("span", {"data-price-type": "oldPrice"})
            curr_p = parse_price(special.get_text()) if special else 0
            old_p = parse_price(old.get_text()) if old else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

# --- 8. متجر Select Mobile (سيليكت) ---
def scan_select():
    store = "سيليكت (Select)"
    urls = ["https://select.eg/ar/collections/deals"]
    for url in urls:
        html = safe_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("div", class_=re.compile(r"product-item|grid-item", re.I))
        for prod in products:
            link_elem = prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            href = link_elem["href"]
            link = href if href.startswith("http") else "https://select.eg" + href
            if link in alerted_links:
                continue

            title = prod.find(class_=re.compile(r"title|name", re.I))
            title_text = title.get_text().strip() if title else "عرض سيليكت"

            prices = prod.find_all(class_=re.compile(r"price", re.I))
            curr_p = parse_price(prices[0].get_text()) if prices else 0
            old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title_text, curr_p, old_p, discount, link)
        time.sleep(random.uniform(5, 9))

def main():
    print("--- بدأ رادار المتاجر المصرية الشامل (الرصد الذكي مع التخطي السريع) ---", flush=True)
    send_telegram(f"🚀 بدأ رادار الصيد الشامل (8 متاجر مصرية) لرصد أي انخفاض أسعار من {MIN_DISCOUNT}% فما فوق...")

    # فحص دوري بهدوء
    for round_num in range(5):
        print(f"=== بداية دورة الفحص رقم {round_num + 1} ===", flush=True)
        
        scan_2b()
        time.sleep(random.uniform(8, 14))
        scan_raneen()
        time.sleep(random.uniform(8, 14))
        scan_kenzz()
        time.sleep(random.uniform(8, 14))
        scan_dubai_phone()
        time.sleep(random.uniform(8, 14))
        scan_rayashop()
        time.sleep(random.uniform(8, 14))
        scan_tradeline()
        time.sleep(random.uniform(8, 14))
        scan_dream2000()
        time.sleep(random.uniform(8, 14))
        scan_select()

        if round_num < 4:
            wait_time = random.randint(480, 600)  # انتظار من 8 إلى 10 دقائق بين الدورات
            print(f"انتظار {wait_time // 60} دقائق قبل الدورة التالية لمحاكاة التصفح الطبيعي...", flush=True)
            time.sleep(wait_time)

    send_telegram("✅ انتهت جلسة الفحص الحالية بنجاح.")

if __name__ == "__main__":
    main()
