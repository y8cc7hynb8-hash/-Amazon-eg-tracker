import os
import sys
import subprocess
import time
import random
import re
import json

# تثبيت مكتبات تخطي الحماية تلقائياً
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

# أقل نسبة خصم لإرسال تنبيه (15% فما فوق)
MIN_DISCOUNT = 15
# أقصى عدد عروض يُرسل من كل متجر في الجولة الواحدة (يرسل الأقوى خصماً أولاً)
MAX_DEALS_PER_STORE = 8

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

alerted_links = set()
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

# قارئ أسعار ذكي يتعامل مع الأرقام العربية واختصار (ج.م.) والكسور بدقة
def parse_price(text):
    if not text:
        return 0
    text = str(text)
    # تحويل الأرقام العربية (٠-٩) إلى إنجليزية (0-9)
    arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
    text = text.translate(arabic_digits)
    # استخراج أول رقم سليم مع الفواصل الآلاف وتجاهل الكسور العشرية ونقاط (ج.م.)
    matches = re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text)
    if not matches:
        return 0
    # اختيار الرقم الأكبر منطقياً في حال وجود رقم فرعي صغير
    candidates = [int(m.replace(",", "")) for m in matches if int(m.replace(",", "")) > 0]
    return candidates[0] if candidates else 0

def dispatch_store_deals(store, deals_list):
    global total_deals_sent
    if not deals_list:
        return
    # ترتيب العروض تنازلياً حسب نسبة الخصم لإرسال أقوى العروض أولاً
    deals_list.sort(key=lambda x: x["discount"], reverse=True)
    sent_count = 0
    for d in deals_list:
        if sent_count >= MAX_DEALS_PER_STORE:
            break
        if d["link"] in alerted_links:
            continue
        alerted_links.add(d["link"])
        sent_count += 1
        total_deals_sent += 1

        price_str = f"💰 السعر الآن: {d['curr_p']:,} ج.م" if d["curr_p"] > 0 else "💰 عرض خاص"
        old_str = f" (بدلاً من {d['old_p']:,} ج.م)" if d["old_p"] > 0 else ""
        msg = (
            f"🚨 لقطة من {store} (خصم {d['discount']}%)\n\n"
            f"📦 {d['title'][:90]}\n"
            f"{price_str}{old_str}\n"
            f"🔗 الرابط: {d['link']}"
        )
        send_telegram(msg)
        time.sleep(1.3)

# جلب ذكي متعدد البصمات لتخطي 403 و 429
def smart_fetch(url, store_name):
    print(f"🔍 جاري فحص [{store_name}]: {url}", flush=True)
    headers = get_headers()

    if HAS_CFFI:
        # تجربة أكثر من بصمة متصفح (كروم ثم سفاري آيفون) لتجاوز الحظر
        for fp in ["chrome120", "safari17_0", "edge101"]:
            try:
                r = cffi_requests.get(url, headers=headers, impersonate=fp, timeout=15)
                if r.status_code == 200 and len(r.text) > 1500:
                    print(f"✅ [{store_name}] تم الفتح بنجاح (بصمة {fp}).", flush=True)
                    return r.text
            except Exception:
                continue

    if scraper:
        try:
            r = scraper.get(url, headers=headers, timeout=15)
            if r.status_code == 200 and len(r.text) > 1500:
                print(f"✅ [{store_name}] تم الفتح بنجاح (Cloudscraper).", flush=True)
                return r.text
        except Exception:
            pass

    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200 and len(r.text) > 1500:
            print(f"✅ [{store_name}] تم الفتح بنجاح.", flush=True)
            return r.text
        print(f"⏭️ [{store_name}] تخطي الرابط (كود {r.status_code}).", flush=True)
    except Exception:
        print(f"⏭️ [{store_name}] تعذر الاتصال بالرابط -> تخطي.", flush=True)
    return None

# محرك شامل يستخرج العروض من أي صفحة HTML + JSON مخفي
def universal_extract(html, base_domain):
    deals = []
    soup = BeautifulSoup(html, "html.parser")

    # 1. الفحص الشامل لبطاقات HTML (يعمل مع Magento, WooCommerce, OpenCart, Shopify)
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
        title = title_el.get_text(" ", strip=True)
        if not title or len(title) < 5:
            title = link_el.get("title") or ""
        if len(title) < 5:
            continue

        # البحث عن السعر الجديد والقديم
        special_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I)) or card.find(["del", "s"])

        curr_p = parse_price(special_el.get_text()) if special_el else 0
        old_p = parse_price(old_el.get_text()) if old_el else 0

        if curr_p == 0 or old_p == 0:
            price_tags = card.find_all(class_=re.compile(r"price", re.I))
            nums = []
            for pt in price_tags:
                val = parse_price(pt.get_text())
                if val > 20 and val not in nums:
                    nums.append(val)
            if len(nums) >= 2:
                curr_p, old_p = min(nums[0], nums[1]), max(nums[0], nums[1])

        discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0

        # فحص شارة الخصم المئوية إن وجدت
        if discount == 0:
            badge_m = re.search(r"(\d{1,2})\s*%", card.get_text(" ", strip=True))
            if badge_m:
                discount = int(badge_m.group(1))

        if MIN_DISCOUNT <= discount <= 90:
            deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": discount, "link": link})

    # 2. فحص JSON المخفي (لمواقع Next.js مثل نون وراية وكنز)
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        try:
            raw_json = next_data.string
            # البحث عن كائنات المنتجات في الـ JSON
            for m in re.finditer(r'"(?:name|title)"\s*:\s*"([^"]{6,120})".*?"(?:price|regular_price)"\s*:\s*(\d+(?:\.\d+)?).*?"(?:sale_price|final_price|special_price)"\s*:\s*(\d+(?:\.\d+)?)', raw_json):
                title = m.group(1)
                p1, p2 = int(float(m.group(2))), int(float(m.group(3)))
                old_p, curr_p = max(p1, p2), min(p1, p2)
                if old_p > curr_p > 0:
                    disc = round(((old_p - curr_p) / old_p) * 100)
                    if MIN_DISCOUNT <= disc <= 90:
                        deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": disc, "link": base_domain})
        except Exception:
            pass

    return deals

# --- 1. أمازون مصر (Amazon Egypt) ---
def scan_amazon():
    store = "أمازون مصر (Amazon)"
    urls = [
        "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
        "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
        "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE"
    ]
    deals = []
    for url in urls:
        html = smart_fetch(url, store)
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
            title = title_elem.get_text(strip=True) if title_elem else "عرض أمازون"
            curr_elem = prod.find("span", class_="a-price-whole")
            old_elem = prod.find("span", {"data-a-strike": "true"})
            curr_p = parse_price(curr_elem.get_text()) if curr_elem else 0
            old_p = parse_price(old_elem.get_text()) if old_elem else 0
            discount = round(((old_p - curr_p) / old_p) * 100) if (old_p > curr_p > 0) else 0
            if discount >= MIN_DISCOUNT:
                deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": discount, "link": link})
        time.sleep(2)
    dispatch_store_deals(store, deals)

# --- 2. نون مصر (Noon Egypt) ---
def scan_noon():
    store = "نون مصر (Noon)"
    urls = [
        "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
        "https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc"
    ]
    deals = []
    for url in urls:
        html = smart_fetch(url, store)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        # استخراج من روابط المنتجات مباشرة
        for a in soup.find_all("a", href=re.compile(r"/egypt-ar/.*/p/")):
            href = a["href"]
            link = href if href.startswith("http") else "https://www.noon.com" + href
            text = a.get_text(" ", strip=True)
            m_disc = re.search(r"(\d{1,2})\s*%\s*(?:خصم|off)?", text, re.I)
            discount = int(m_disc.group(1)) if m_disc else 0
            prices = [int(x.replace(",", "")) for x in re.findall(r"\d{1,3}(?:,\d{3})+|\d{3,6}", text)]
            curr_p = min(prices[:2]) if len(prices) >= 2 else (prices[0] if prices else 0)
            old_p = max(prices[:2]) if len(prices) >= 2 else 0
            if discount == 0 and old_p > curr_p > 0:
                discount = round(((old_p - curr_p) / old_p) * 100)
            if discount >= MIN_DISCOUNT:
                title = a.get("title") or text[:85]
                deals.append({"title": title, "curr_p": curr_p, "old_p": old_p, "discount": discount, "link": link})
        deals.extend(universal_extract(html, "https://www.noon.com"))
    dispatch_store_deals(store, deals)

# --- 3. فحص باقي المتاجر المصرية بالمحرك الشامل ---
STORES_CONFIG = [
    {
        "name": "2B Egypt",
        "domain": "https://2b.com.eg",
        "urls": [
            "https://2b.com.eg/ar/deals.html",
            "https://2b.com.eg/ar/computers.html",
            "https://2b.com.eg/ar/mobiles-tablets.html",
            "https://2b.com.eg/ar/home-appliances.html"
        ]
    },
    {
        "name": "رنين (Raneen)",
        "domain": "https://www.raneen.com",
        "urls": [
            "https://www.raneen.com/ar/deals",
            "https://www.raneen.com/ar/"
        ]
    },
    {
        "name": "راية شوب (Raya Shop)",
        "domain": "https://www.rayashop.com",
        "urls": [
            "https://www.rayashop.com/ar/deals",
            "https://www.rayashop.com/ar/mobiles-tablets",
            "https://www.rayashop.com/ar/home-appliances"
        ]
    },
    {
        "name": "بي تك (B.Tech)",
        "domain": "https://btech.com",
        "urls": [
            "https://btech.com/ar/daily-deals",
            "https://btech.com/ar/"
        ]
    },
    {
        "name": "دريم 2000 (Dream 2000)",
        "domain": "https://dream2000.com",
        "urls": [
            "https://dream2000.com/",
            "https://dream2000.com/mobiles.html",
            "https://dream2000.com/home-appliances.html"
        ]
    },
    {
        "name": "كنز (Kenzz)",
        "domain": "https://kenzz.com",
        "urls": [
            "https://kenzz.com/",
            "https://kenzz.com/ar"
        ]
    },
    {
        "name": "دبي فون (Dubai Phone)",
        "domain": "https://www.dubaiphone.net",
        "urls": [
            "https://www.dubaiphone.net/ar/",
            "https://www.dubaiphone.net/"
        ]
    },
    {
        "name": "جوميا مصر (Jumia)",
        "domain": "https://www.jumia.com.eg",
        "urls": [
            "https://www.jumia.com.eg/ar/flash-sales/",
            "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount"
        ]
    }
]

def main():
    print("--- بدء الفحص الذكي الشامل (الإصدار المطور) ---", flush=True)
    send_telegram(f"🚀 بدأ الفحص الشامل المطور (خصومات {MIN_DISCOUNT}% فما فوق)...")

    # 1. فحص أمازون ونون
    scan_amazon()
    time.sleep(random.uniform(2, 4))
    scan_noon()
    time.sleep(random.uniform(2, 4))

    # 2. فحص جميع المتاجر المصرية الأخرى
    for store_cfg in STORES_CONFIG:
        store_deals = []
        for u in store_cfg["urls"]:
            html = smart_fetch(u, store_cfg["name"])
            if html:
                extracted = universal_extract(html, store_cfg["domain"])
                store_deals.extend(extracted)
            time.sleep(random.uniform(2, 4))
        dispatch_store_deals(store_cfg["name"], store_deals)

    print(f"--- انتهت الجولة بنجاح (إجمالي العروض المرسلة: {total_deals_sent}) ---", flush=True)
    send_telegram(f"✅ انتهت جولة الفحص الشامل وأُغلق الرادار بنجاح!\n📊 إجمالي اللقطات المرسلة: {total_deals_sent}")

if __name__ == "__main__":
    main()
