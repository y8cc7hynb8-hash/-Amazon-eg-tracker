import os
import time
import random
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم مطلوبة للتنبيه (مثلاً 25% فما فوق)
MIN_DISCOUNT = 25

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
]

alerted_links = set()

def get_session():
    s = requests.Session()
    ua = random.choice(USER_AGENTS)
    s.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    })
    return s

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
        r = requests.post(url, json=payload, timeout=10)
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
        f"📦 {title[:85]}\n"
        f"💰 السعر بعد الخصم: {current_p:,} ج.م\n"
        f"🏷 السعر القديم: {old_p:,} ج.م\n"
        f"🔗 الرابط: {link}"
    )
    send_telegram(msg)
    time.sleep(2)

# --- 1. فحص موقع 2B Egypt ---
def scan_2b(session):
    store_name = "2B Egypt"
    urls = [
        "https://2b.com.eg/ar/offer.html",
        "https://2b.com.eg/ar/offers/last-best.html"
    ]
    for url in urls:
        print(f"[{store_name}] فحص صفحة: {url}", flush=True)
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                print(f"[{store_name}] تخطي، كود الرد: {r.status_code}", flush=True)
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.find_all("li", class_=re.compile(r"product-item"))
            if not products:
                products = soup.find_all("div", class_=re.compile(r"product-item-info"))

            print(f"[{store_name}] تم العثور على {len(products)} منتج.", flush=True)

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
            print(f"[{store_name}] استثناء: {e}", flush=True)

        # تأخير عشوائي بشري بين الصفحات
        human_delay = random.uniform(10, 16)
        time.sleep(human_delay)

# --- 2. فحص موقع رنين (Raneen) ---
def scan_raneen(session):
    store_name = "رنين (Raneen)"
    urls = [
        "https://www.raneen.com/ar/deals/daily-deals",
        "https://www.raneen.com/ar/raneen-offers/offers"
    ]
    for url in urls:
        print(f"[{store_name}] فحص صفحة: {url}", flush=True)
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                print(f"[{store_name}] تخطي، كود الرد: {r.status_code}", flush=True)
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            products = soup.find_all("div", class_=re.compile(r"product-item|productCard"))
            if not products:
                products = soup.find_all("li", class_=re.compile(r"product-item"))

            print(f"[{store_name}] تم العثور على {len(products)} منتج.", flush=True)

            for prod in products:
                link_elem = prod.find("a", href=True)
                if not link_elem or not link_elem.get("href"):
                    continue

                href = link_elem["href"]
                link = href if href.startswith("http") else "https://www.raneen.com" + href
                if link in alerted_links:
                    continue

                title_elem = prod.find(class_=re.compile(r"title|name|product-item-link"))
                title = title_elem.get_text().strip() if title_elem else "عرض رنين المميز"

                # استخراج نسبة الخصم إن وُجدت شارة خصم
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
            print(f"[{store_name}] استثناء: {e}", flush=True)

        human_delay = random.uniform(10, 16)
        time.sleep(human_delay)

def main():
    print("--- بدأ رادار رصد 2B ورنين (نمط التصفح الطبيعي) ---", flush=True)
    send_telegram(f"🚀 بدأ رادار صيد عروض 2B ورنين بنمط التصفح الهادئ (خصم {MIN_DISCOUNT}% فما فوق)...")

    # 4 جولات هادئة يفصل بين كل جولة وأخرى نحو 12 إلى 15 دقيقة
    for round_num in range(4):
        print(f"=== بداية الجولة {round_num + 1} ===", flush=True)
        session = get_session()

        scan_2b(session)
        time.sleep(random.uniform(12, 18))
        scan_raneen(session)

        if round_num < 3:
            pause = random.randint(720, 900)  # من 12 إلى 15 دقيقة راحة
            print(f"فترة انتظار طبيعية لمدة {pause // 60} دقيقة قبل الجولة التالية...", flush=True)
            time.sleep(pause)

    send_telegram("✅ انتهت جلسة فحص 2B ورنين بنجاح.")

if __name__ == "__main__":
    main()
