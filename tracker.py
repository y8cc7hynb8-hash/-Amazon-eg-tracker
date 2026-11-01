import os
import sys
import subprocess
import time
import random
import re
import json
import urllib.parse

# تثبيت المكتبات اللازمة تلقائياً
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

MIN_DISCOUNT = 20          
MAX_REALISTIC_DISC = 75    
MIN_PRODUCT_PRICE = 200    
MAX_DEALS_PER_STORE = 5    

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

INSTALLMENT_KEYWORDS = ["قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month", "تقسيط", "أقساط"]

alerted_links = set()
seen_titles = set()
total_deals_sent = 0

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com.eg/",
        "Upgrade-Insecure-Requests": "1"
    }

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
    text = str(text)
    if any(k in text.lower() for k in INSTALLMENT_KEYWORDS):
        return 0
    arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
    text = text.translate(arabic_digits)
    text = re.sub(r"\.\d{1,2}(?!\d)", "", text)
    matches = re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text)
    if not matches:
        return 0
    candidates = [int(m.replace(",", "")) for m in matches if int(m.replace(",", "")) > 20]
    return candidates[0] if candidates else 0

def normalize_title(title):
    clean = re.sub(r"[^\w\s]", " ", title.lower())
    words = [w for w in clean.split() if len(w) > 2 and w not in ["عرض", "خصم", "جديد", "أصلي", "ضمان", "مصر"]]
    return " ".join(words[:6])

def smart_fetch(url, store_name, allow_proxy=True):
    print(f"🔍 جاري فحص [{store_name}]: {url}", flush=True)
    headers = get_headers()

    if HAS_CFFI:
        for fp in ["chrome120", "safari17_0", "edge101"]:
            try:
                r = cffi_requests.get(url, headers=headers, impersonate=fp, timeout=15)
                if r.status_code == 200 and len(r.text) > 800:
                    print(f"✅ [{store_name}] تم الفتح مباشرة (بصمة {fp}).", flush=True)
                    return r.text
            except Exception:
                continue

    if scraper:
        try:
            r = scraper.get(url, headers=headers, timeout=15)
            if r.status_code == 200 and len(r.text) > 800:
                print(f"✅ [{store_name}] تم الفتح عبر Cloudscraper.", flush=True)
                return r.text
        except Exception:
            pass

    if allow_proxy:
        encoded_url = urllib.parse.quote(url, safe="")
        proxy_gateways = [
            ("CodeTabs Proxy", f"https://api.codetabs.com/v1/proxy?quest={encoded_url}"),
            ("AllOrigins Proxy", f"https://api.allorigins.win/raw?url={encoded_url}"),
        ]
        for p_name, p_url in proxy_gateways:
            try:
                r = requests.get(p_url, headers=headers, timeout=18)
                if r.status_code == 200 and len(r.text) > 1000 and "403 Forbidden" not in r.text[:400]:
                    print(f"🔓 [{store_name}] تم تخطي الحظر بنجاح عبر بوابة ({p_name})!", flush=True)
                    return r.text
            except Exception:
                continue

    print(f"⏭️ [{store_name}] تعذر جلب الرابط بعد كل المحاولات -> تخطي.", flush=True)
    return None

def verify_market_price(title, store_price, old_price, store_name):
    if store_price < MIN_PRODUCT_PRICE or old_price <= store_price:
        return False, 0, "سعر غير منطقي"

    raw_disc = round(((old_price - store_price) / old_price) * 100)
    if raw_disc < MIN_DISCOUNT or raw_disc > MAX_REALISTIC_DISC:
        return False, 0, "نسبة خصم غير صالحة"

    return True, old_price, "خصم حقيقي مؤكد"

def universal_extract(html, base_domain):
    deals = []
    soup = BeautifulSoup(html, "html.parser")

    cards = soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|product-card|productCard|prd|grid-item|product-layout|item", re.I))
    for card in cards:
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        href = link_el["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        link = href if href.startswith("http") else base_domain.rstrip("/") + "/" + href.lstrip("/")

        title_el = card.find(["h2", "h3", "h4", "strong", "a"], class_=re.compile(r"name|title|link", re.I)) or link_el
        title = title_el.get_text(" ", strip=True) or link_el.get("title") or ""
        if len(title) < 6 or any(k in title.lower() for k in INSTALLMENT_KEYWORDS):
            continue

        special_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I)) or card.find(["del", "s"])

        curr_p = parse_price(special_el.get_text()) if special_el else 0
        old_p = parse_price(old_el.get_text()) if old_el else 0

        if curr_p == 0 or old_p == 0:
            price_tags = card.find_all(class_=re.compile(r"price", re.I))
            nums = []
            for pt in price_tags:
                val = parse_price(pt.get_text())
                if val >= MIN_PRODUCT_PRICE and val not in nums:
                    nums.append(val)
            if len(nums) >= 2:
                curr_p, old_p = min(nums[0], nums[1]), max(nums[0], nums[1])

        if old_p > curr_p >= MIN_PRODUCT_PRICE:
            discount = round(((old_p - curr_p) / old_p) * 100)
            if MIN_DISCOUNT <= discount <= MAX_REALISTIC_DISC:
                deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": discount, "link": link})

    return deals

def dispatch_verified_deals(store, deals_list):
    global total_deals_sent
    if not deals_list:
        return
    deals_list.sort(key=lambda x: (x["discount"], x["old_p"] - x["curr_p"]), reverse=True)
    sent_count = 0

    for d in deals_list:
        if sent_count >= MAX_DEALS_PER_STORE:
            break
        norm_t = normalize_title(d["title"])
        if d["link"] in alerted_links or (norm_t and norm_t in seen_titles):
            continue

        is_real, ref_price, market_note = verify_market_price(d["title"], d["curr_p"], d["old_p"], store)
        if not is_real:
            continue

        alerted_links.add(d["link"])
        if norm_t:
            seen_titles.add(norm_t)
        sent_count += 1
        total_deals_sent += 1

        saving_val = d["old_p"] - d["curr_p"]
        msg = (
            f"🔥 صفقـة حقيقية مؤكدة من {store} (خصم {d['discount']}%)\n\n"
            f"📦 {d['title'][:95]}\n"
            f"💰 السعر الحالي: {d['curr_p']:,} ج.م\n"
            f"🏷 السعر السابق: {d['old_p']:,} ج.م (توفير {saving_val:,} ج.م)\n"
            f"📊 حالة العرض: {market_note}\n"
            f"🔗 الرابط: {d['link']}"
        )
        send_telegram(msg)
        time.sleep(1.3)

# --- 1. فحص أمازون مصر ---
def scan_amazon():
    store = "أمازون مصر (Amazon)"
    urls = [
        "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
        "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE"
    ]
    deals = []
    for url in urls:
        html = smart_fetch(url, store, allow_proxy=False)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for prod in soup.find_all("div", {"data-component-type": "s-search-result"}):
            link_elem = prod.find("a", class_="a-link-normal s-no-outline") or prod.find("a", href=True)
            if not link_elem or not link_elem.get("href") or "/sspa/click" in link_elem["href"]:
                continue
            href = link_elem["href"]
            link = href if href.startswith("http") else "https://www.amazon.eg" + href.split("?")[0]
            title_elem = prod.find("h2")
            title = title_elem.get_text(strip=True) if title_elem else ""
            curr_elem = prod.find("span", class_="a-price-whole")
            old_elem = prod.find("span", {"data-a-strike": "true"})
            curr_p = parse_price(curr_elem.get_text()) if curr_elem else 0
            old_p = parse_price(old_elem.get_text()) if old_elem else 0
            if old_p > curr_p >= MIN_PRODUCT_PRICE:
                discount = round(((old_p - curr_p) / old_p) * 100)
                if MIN_DISCOUNT <= discount <= MAX_REALISTIC_DISC:
                    deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": discount, "link": link})
        time.sleep(2)
    dispatch_verified_deals(store, deals)

# --- 2. فحص نون مصر (معالجة دقيقة للأسعار الحقيقية) ---
def scan_noon():
    store = "نون مصر (Noon)"
    urls = [
        "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
        "https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc"
    ]
    deals = []
    for url in urls:
        html = smart_fetch(url, store, allow_proxy=False)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"/egypt-ar/.*/p/")):
            href = a["href"]
            link = href if href.startswith("http") else "https://www.noon.com" + href
            text = a.get_text(" ", strip=True)
            if any(k in text.lower() for k in INSTALLMENT_KEYWORDS):
                continue
            prices = [int(x.replace(",", "")) for x in re.findall(r"\d{1,3}(?:,\d{3})+|\d{3,6}", text) if int(x.replace(",", "")) >= MIN_PRODUCT_PRICE]
            if len(prices) >= 2:
                curr_p, old_p = min(prices[:2]), max(prices[:2])
                if old_p > curr_p:
                    discount = round(((old_p - curr_p) / old_p) * 100)
                    if MIN_DISCOUNT <= discount <= MAX_REALISTIC_DISC:
                        title = a.get("title") or text[:90]
                        deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": discount, "link": link})
        deals.extend(universal_extract(html, "https://www.noon.com"))
    dispatch_verified_deals(store, deals)

# --- 3. المتاجر المصرية ---
STORES_CONFIG = [
    {
        "name": "جوميا مصر (Jumia)",
        "domain": "https://www.jumia.com.eg",
        "urls": ["https://www.jumia.com.eg/ar/flash-sales/"]
    },
    {
        "name": "راية شوب (Raya Shop)",
        "domain": "https://www.rayashop.com",
        "urls": ["https://www.rayashop.com/ar/deals", "https://www.rayashop.com/ar/mobiles-tablets"]
    },
    {
        "name": "بي تك (B.Tech)",
        "domain": "https://btech.com",
        "urls": ["https://btech.com/ar/", "https://btech.com/ar/mobyilat.html"]
    },
    {
        "name": "2B Egypt",
        "domain": "https://2b.com.eg",
        "urls": ["https://2b.com.eg/ar/deals.html", "https://2b.com.eg/ar/computers.html"]
    },
    {
        "name": "رنين (Raneen)",
        "domain": "https://www.raneen.com",
        "urls": ["https://www.raneen.com/ar/deals"]
    },
    {
        "name": "دريم 2000 (Dream 2000)",
        "domain": "https://dream2000.com",
        "urls": ["https://dream2000.com/mobiles.html"]
    },
    {
        "name": "كنز (Kenzz)",
        "domain": "https://kenzz.com",
        "urls": ["https://kenzz.com/ar"]
    }
]

def main():
    print("--- بدء الرادار الذكي الشامل (V5) ---", flush=True)
    send_telegram(f"🚀 بدأ الفحص التلقائي لكل المتاجر المصرية (خصومات {MIN_DISCOUNT}% فأكثر)...")

    scan_amazon()
    time.sleep(random.uniform(2, 4))
    scan_noon()
    time.sleep(random.uniform(2, 4))

    for store_cfg in STORES_CONFIG:
        store_deals = []
        for u in store_cfg["urls"]:
            html = smart_fetch(u, store_cfg["name"], allow_proxy=True)
            if html:
                store_deals.extend(universal_extract(html, store_cfg["domain"]))
            time.sleep(random.uniform(2, 4))
        dispatch_verified_deals(store_cfg["name"], store_deals)

    print(f"--- انتهت الجولة بنجاح (إجمالي الصفقات المرسلة: {total_deals_sent}) ---", flush=True)
    send_telegram(f"✅ انتهت جولة الفحص التلقائي وأُغلق الرادار!\n📊 إجمالي الصفقات المرسلة: {total_deals_sent}")

if __name__ == "__main__":
    main()
