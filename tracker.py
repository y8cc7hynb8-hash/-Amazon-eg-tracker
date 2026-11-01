import os
import sys
import subprocess
import time
import random
import re

# تثبيت مكتبات تخطي الحماية ومحاكاة بصمة المتصفح تلقائياً
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "curl_cffi", "cloudscraper", "beautifulsoup4", "requests"], check=False)

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

try:
    import cloudscraper
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
except ImportError:
    scraper = None

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# أقل نسبة خصم لإرسال تنبيه (15% فما فوق لرصد أي انخفاض أسعار)
MIN_DISCOUNT = 15

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
]

REFERERS = [
    "https://www.google.com.eg/",
    "https://www.google.com/",
    "https://www.bing.com/"
]

alerted_links = set()
total_deals_sent = 0

def get_headers(base_url=""):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": random.choice(REFERERS),
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1"
    }

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
        r = requests.post(url, json=payload, timeout=8)
        print(f"حالة إرسال تليجرام: {r.status_code}", flush=True)
    except Exception as e:
        print(f"خطأ تليجرام: {e}", flush=True)

def parse_price(text):
    if not text:
        return 0
    # إزالة الكسور العشرية إن وجدت ثم استخراج الأرقام
    clean_text = text.split(".")[0].split("٫")[0]
    digits = re.sub(r"[^\d]", "", clean_text)
    return int(digits) if digits else 0

def send_deal_alert(store, title, current_p, old_p, discount, link):
    global total_deals_sent
    total_deals_sent += 1
    price_info = f"💰 السعر الآن: {current_p:,} ج.م" if current_p > 0 else "💰 عرض خاص"
    old_info = f" (بدلاً من {old_p:,} ج.م)" if old_p > 0 else ""
    msg = (
        f"🚨 لقطة من {store} (خصم {discount}%)\n\n"
        f"📦 {title[:90]}\n"
        f"{price_info}{old_info}\n"
        f"🔗 الرابط: {link}"
    )
    send_telegram(msg)
    time.sleep(1.2)

# دالة متقدمة لجلب الصفحات بـ 3 محاولات ذكية لتخطي الحظر
def smart_fetch(url, store_name):
    print(f"🔍 جاري فحص [{store_name}]...", flush=True)
    headers = get_headers(url)

    # المحاولة 1: استخدام curl_cffi لمحاكاة بصمة متصفح Chrome الحقيقية (الأقوى ضد Cloudflare و Amazon)
    if HAS_CFFI:
        try:
            r = cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=15)
            if r.status_code == 200 and len(r.text) > 1000:
                print(f"✅ [{store_name}] تم فتح الموقع بنجاح (عبر محاكاة TLS).", flush=True)
                return r.text
        except Exception:
            pass

    # المحاولة 2: استخدام Cloudscraper لتجاوز التحديات
    if scraper:
        try:
            r = scraper.get(url, headers=headers, timeout=15)
            if r.status_code == 200 and len(r.text) > 1000:
                print(f"✅ [{store_name}] تم فتح الموقع بنجاح (عبر Cloudscraper).", flush=True)
                return r.text
        except Exception:
            pass

    # المحاولة 3: الطلب العادي
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            print(f"✅ [{store_name}] تم فتح الموقع بنجاح.", flush=True)
            return r.text
        else:
            print(f"⏭️ [{store_name}] محظور أو غير متاح (كود {r.status_code}) -> تخطي للمتجر التالي.", flush=True)
            return None
    except Exception as e:
        print(f"⏭️ [{store_name}] تعذر الاتصال -> تخطي للمتجر التالي.", flush=True)
        return None

# --- 1. أمازون مصر (Amazon Egypt) ---
def scan_amazon():
    store = "أمازون مصر (Amazon)"
    # رابط صفحة العروض والتخفيضات المباشرة القابلة للقراءة
    urls = [
        "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
        "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE"
    ]
    for url in urls:
        html = smart_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("div", {"data-component-type": "s-search-result"})
        for prod in products:
            link_elem = prod.find("a", class_="a-link-normal s-no-outline") or prod.find("a", href=True)
            if not link_elem or not link_elem.get("href"):
                continue
            href = link_elem["href"]
            if "/sspa/click" in href:
                continue
            link = href if href.startswith("http") else "https://www.amazon.eg" + href.split("?")[0]
            if link in alerted_links:
                continue

            title_elem = prod.find("h2")
            title = title_elem.get_text().strip() if title_elem else "منتج مخفض على أمازون"

            curr_elem = prod.find("span", class_="a-price-whole")
            old_elem = prod.find("span", {"data-a-strike": "true"})
            curr_p = parse_price(curr_elem.get_text()) if curr_elem else 0
            old_p = parse_price(old_elem.get_text()) if old_elem else 0

            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0
            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(2, 4))

# --- 2. نون مصر (Noon Egypt) ---
def scan_noon():
    store = "نون مصر (Noon)"
    urls = ["https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc"]
    for url in urls:
        html = smart_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("div", class_=re.compile(r"productContainer|grid", re.I)) or soup.find_all("a", href=re.compile(r"/egypt-ar/"))
        for prod in products:
            href = prod.get("href") if prod.name == "a" else (prod.find("a", href=True)["href"] if prod.find("a", href=True) else None)
            if not href or "/p/" not in href:
                continue
            link = href if href.startswith("http") else "https://www.noon.com" + href
            if link in alerted_links:
                continue

            text_content = prod.get_text(" ", strip=True)
            m_disc = re.search(r"(\d+)\s*%", text_content)
            discount = int(m_disc.group(1)) if m_disc else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                title = prod.get("title") or text_content[:70]
                send_deal_alert(store, title, 0, 0, discount, link)

# --- 3. جوميا مصر (Jumia Egypt) ---
def scan_jumia():
    store = "جوميا مصر (Jumia)"
    urls = [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount"
    ]
    for url in urls:
        html = smart_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        products = soup.find_all("article", class_="prd")
        for prod in products:
            link_elem = prod.find("a", class_="core")
            if not link_elem or not link_elem.get("href"):
                continue
            link = "https://www.jumia.com.eg" + link_elem["href"]
            if link in alerted_links:
                continue

            name_elem = prod.find("h3", class_="name")
            title = name_elem.get_text().strip() if name_elem else "عرض جوميا"
            discount_elem = prod.find("div", class_="bdg")
            discount = parse_price(discount_elem.get_text()) if discount_elem else 0

            curr_elem = prod.find("div", class_="prc")
            old_elem = prod.find("div", class_="old")
            curr_p = parse_price(curr_elem.get_text()) if curr_elem else 0
            old_p = parse_price(old_elem.get_text()) if old_elem else 0

            if discount >= MIN_DISCOUNT:
                alerted_links.add(link)
                send_deal_alert(store, title, curr_p, old_p, discount, link)
        time.sleep(random.uniform(2, 4))

# --- 4. متجر 2B Egypt ---
def scan_2b():
    store = "2B Egypt"
    urls = ["https://2b.com.eg/ar/deals.html"]
    for url in urls:
        html = smart_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for prod in soup.find_all("li", class_=re.compile(r"product-item")):
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

# --- 5. متجر رنين (Raneen) ---
def scan_raneen():
    store = "رنين (Raneen)"
    html = smart_fetch("https://www.raneen.com/ar/deals", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("div", class_=re.compile(r"product-item|productCard")):
        link_elem = prod.find("a", href=True)
        if not link_elem or not link_elem.get("href"):
            continue
        href = link_elem["href"]
        link = href if href.startswith("http") else "https://www.raneen.com" + href
        if link in alerted_links:
            continue

        title_el = prod.find(class_=re.compile(r"title|name"))
        title = title_el.get_text().strip() if title_el else "عرض رنين"
        prices = prod.find_all(class_=re.compile(r"price"))
        curr_p = parse_price(prices[0].get_text()) if prices else 0
        old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
        discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

        if discount >= MIN_DISCOUNT:
            alerted_links.add(link)
            send_deal_alert(store, title, curr_p, old_p, discount, link)

# --- 6. متجر كنز (Kenzz) ---
def scan_kenzz():
    store = "كنز (Kenzz)"
    html = smart_fetch("https://kenzz.com/offers", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("div", class_=re.compile(r"product|card|item", re.I)):
        link_elem = prod.find("a", href=True)
        if not link_elem or not link_elem.get("href"):
            continue
        href = link_elem["href"].strip()
        link = href if href.startswith("http") else "https://kenzz.com" + href
        if link in alerted_links:
            continue

        title_el = prod.find(class_=re.compile(r"title|name", re.I))
        title = title_el.get_text().strip() if title_el else "منتج كنز"
        if len(title) < 4:
            continue

        prices = prod.find_all(class_=re.compile(r"price", re.I))
        curr_p = parse_price(prices[0].get_text()) if prices else 0
        old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
        discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

        if discount >= MIN_DISCOUNT:
            alerted_links.add(link)
            send_deal_alert(store, title, curr_p, old_p, discount, link)

# --- 7. متجر دبي فون (Dubai Phone) ---
def scan_dubai_phone():
    store = "دبي فون (Dubai Phone)"
    html = smart_fetch("https://dubaiphone.net/ar/deals", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("div", class_=re.compile(r"product-layout|product-thumb|product-item")):
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

# --- 8. متجر راية شوب (RayaShop) ---
def scan_rayashop():
    store = "راية شوب (Raya Shop)"
    html = smart_fetch("https://www.rayashop.com/ar/deals", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("li", class_=re.compile(r"product-item")):
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

# --- 9. متجر تريد لاين (TradeLine) ---
def scan_tradeline():
    store = "تريد لاين (TradeLine)"
    html = smart_fetch("https://tradelinestores.com/collections/offers", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("div", class_=re.compile(r"product-card|grid-item|card", re.I)):
        link_elem = prod.find("a", href=True)
        if not link_elem or not link_elem.get("href"):
            continue
        href = link_elem["href"]
        link = href if href.startswith("http") else "https://tradelinestores.com" + href
        if link in alerted_links:
            continue

        title_el = prod.find(class_=re.compile(r"title|name", re.I))
        title = title_el.get_text().strip() if title_el else "عرض تريد لاين"
        prices = prod.find_all(class_=re.compile(r"price", re.I))
        curr_p = parse_price(prices[0].get_text()) if prices else 0
        old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
        discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

        if discount >= MIN_DISCOUNT:
            alerted_links.add(link)
            send_deal_alert(store, title, curr_p, old_p, discount, link)

# --- 10. متجر دريم 2000 (Dream 2000) ---
def scan_dream2000():
    store = "دريم 2000 (Dream 2000)"
    html = smart_fetch("https://dream2000.com/ar/offers", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("li", class_=re.compile(r"product-item")):
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

# --- 11. متجر سيليكت (Select) ---
def scan_select():
    store = "سيليكت (Select)"
    html = smart_fetch("https://select.eg/ar/collections/deals", store)
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    for prod in soup.find_all("div", class_=re.compile(r"product-item|grid-item", re.I)):
        link_elem = prod.find("a", href=True)
        if not link_elem or not link_elem.get("href"):
            continue
        href = link_elem["href"]
        link = href if href.startswith("http") else "https://select.eg" + href
        if link in alerted_links:
            continue

        title_el = prod.find(class_=re.compile(r"title|name", re.I))
        title = title_el.get_text().strip() if title_el else "عرض سيليكت"
        prices = prod.find_all(class_=re.compile(r"price", re.I))
        curr_p = parse_price(prices[0].get_text()) if prices else 0
        old_p = parse_price(prices[1].get_text()) if len(prices) > 1 else 0
        discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

        if discount >= MIN_DISCOUNT:
            alerted_links.add(link)
            send_deal_alert(store, title, curr_p, old_p, discount, link)

def main():
    print("--- بدء الفحص الشامل (جولة واحدة سريعة لـ 11 متجراً) ---", flush=True)
    send_telegram(f"🚀 بدأ الفحص الشامل لـ 11 متجراً مصرياً (جولة واحدة عند الطلب لرصد خصومات {MIN_DISCOUNT}% فأكثر)...")

    stores_scanners = [
        scan_amazon,
        scan_noon,
        scan_jumia,
        scan_2b,
        scan_raneen,
        scan_kenzz,
        scan_dubai_phone,
        scan_rayashop,
        scan_tradeline,
        scan_dream2000,
        scan_select
    ]

    for scanner in stores_scanners:
        try:
            scanner()
        except Exception as e:
            print(f"خطأ غير متوقع: {e}", flush=True)
        # فاصل قصير عشوائي بين كل متجر والآخر
        time.sleep(random.uniform(3, 6))

    print("--- انتهت الجولة بنجاح وتم إغلاق السكربت ---", flush=True)
    send_telegram(f"✅ انتهت جولة الفحص الشامل لـ 11 متجراً وأُغلق الرادار بنجاح!\n📊 إجمالي العروض المرسلة في هذه الجولة: {total_deals_sent}")

if __name__ == "__main__":
    main()
