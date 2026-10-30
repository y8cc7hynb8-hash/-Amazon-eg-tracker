import os
import time
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم مطلوبة للتنبيه (مثلاً 25% فما فوق)
MIN_DISCOUNT = 25

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
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0

# --- 1. فحص موقع 2B Egypt ---
def scan_2b():
    store_name = "2B Egypt"
    urls = [
        "https://2b.com.eg/ar/deals.html",
        "https://2b.com.eg/ar/computers.html?price_drop=1"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[{store_name}] فشل، كود: {r.status_code}", flush=True)
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

# --- 2. فحص موقع رنين (Raneen) ---
def scan_raneen():
    store_name = "رنين (Raneen)"
    urls = [
        "https://www.raneen.com/ar/deals",
        "https://www.raneen.com/ar/electronics-offers"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[{store_name}] فشل، كود: {r.status_code}", flush=True)
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
                badge = prod.find(class_=re.compile(r"discount|badge|label"))
                discount = parse_price(badge.get_text()) if badge else 0

                prices = prod.find_all(class_=re.compile(r"price"))
                curr_p = parse_price(prices[0].get_text()) if prices else 0
                old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0

                if (old_p > curr_p and curr_p > 0) and discount == 0:
                    discount = round(((old_p - curr_p) / old_p) * 100)

                if discount >= MIN_DISCOUNT:
                    alerted_links.add(link)
                    send_deal_alert(store_name, title, curr_p, old_p, discount, link)
        except Exception as e:
            print(f"[{store_name}] خطأ: {e}", flush=True)

# --- 3. فحص موقع دبي فون (Dubai Phone) ---
def scan_dubai_phone():
    store_name = "دبي فون (Dubai Phone)"
    urls = [
        "https://dubaiphone.net/ar/deals",
        "https://dubaiphone.net/ar/flash-sale"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[{store_name}] فشل، كود: {r.status_code}", flush=True)
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

# --- 4. فحص موقع راية شوب (RayaShop) ---
def scan_rayashop():
    store_name = "راية شوب (Raya Shop)"
    urls = [
        "https://www.rayashop.com/ar/deals",
        "https://www.rayashop.com/ar/flash-deal"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[{store_name}] فشل، كود: {r.status_code}", flush=True)
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

# --- 5. فحص موقع بي تك (B.Tech) ---
def scan_btech():
    store_name = "بي تك (B.Tech)"
    urls = [
        "https://btech.com/ar/flash-deals",
        "https://btech.com/ar/hot-deals"
    ]
    for url in urls:
        print(f"[{store_name}] فحص: {url}", flush=True)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[{store_name}] فشل، كود: {r.status_code}", flush=True)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.find_all("div", class_=re.compile(r"product-item|plp-product-card"))
            for prod in products:
                link_elem = prod.find("a", href=True)
                if not link_elem or not link_elem.get("href"):
                    continue
                href = link_elem["href"]
                link = href if href.startswith("http") else "https://btech.com" + href
                if link in alerted_links:
                    continue

                title_elem = prod.find(class_=re.compile(r"title|name"))
                title = title_elem.get_text().strip() if title_elem else "عرض بي تك"

                curr_elem = prod.find(class_=re.compile(r"finalPrice|special-price|current"))
                old_elem = prod.find(class_=re.compile(r"oldPrice|old-price|was"))

                curr_p = parse_price(curr_elem.get_text()) if curr_elem else 0
                old_p = parse_price(old_elem.get_text()) if old_elem else 0

                discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p and curr_p > 0) else 0

                if discount >= MIN_DISCOUNT:
                    alerted_links.add(link)
                    send_deal_alert(store_name, title, curr_p, old_p, discount, link)
        except Exception as e:
            print(f"[{store_name}] خطأ: {e}", flush=True)

def send_deal_alert(store, title, current_p, old_p, discount, link):
    msg = (
        f"🔥 صيد جديد من متجر {store} ({discount}% خصم)!\n\n"
        f"📦 {title[:80]}\n"
        f"💰 السعر الآن: {current_p:,} ج.م\n"
        f"🏷 السعر السابق: {old_p:,} ج.م\n"
        f"🔗 الرابط: {link}"
    )
    send_telegram(msg)
    time.sleep(1)

def main():
    print("--- بدأ رادار الصيد الشامل للمتاجر المصرية ---", flush=True)
    send_telegram(f"🚀 بدأ رادار الصيد الشامل (2B - رنين - دبي فون - راية شوب - بي تك) برصد خصومات {MIN_DISCOUNT}% فأكثر...")

    # تشغيل دوري لمدة ساعة تقريباً (10 جولات، كل جولة تفحص المتاجر الخمسة وتنتظر 5 دقائق)
    for round_num in range(10):
        print(f"=== بداية الجولة رقم {round_num + 1} ===", flush=True)
        scan_2b()
        time.sleep(3)
        scan_raneen()
        time.sleep(3)
        scan_dubai_phone()
        time.sleep(3)
        scan_rayashop()
        time.sleep(3)
        scan_btech()
        time.sleep(3)

        if round_num < 9:
            print("انتظار 5 دقائق للجولة التالية...", flush=True)
            time.sleep(300)

    send_telegram("✅ انتهت جلسة فحص المتاجر المصرية.")

if __name__ == "__main__":
    main()
