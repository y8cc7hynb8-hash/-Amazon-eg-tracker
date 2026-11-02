#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, random, html as htmllib, subprocess, traceback, urllib.parse
from collections import defaultdict

print("⚡ بدء تشغيل رادار الصفقات ومقارنة الأسعار (الإصدار V13)...", flush=True)

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

MIN_DISCOUNT = int(os.environ.get("MIN_DISCOUNT", 15))
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 72))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 250))
MAX_DEALS_PER_STORE = int(os.environ.get("MAX_DEALS_PER_STORE", 5))
REALERT_HOURS = 96

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

# قائمة الكلمات المحظورة لمنع التقاط الأقساط الشهرية في دريم 2000 وبي تك وراية
INSTALLMENT_WORDS = [
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month",
    "تقسيط", "أقساط", "installment", "/mo", "فائدة", "بدون فوائد", "دفعة"
]

_AR = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR, "0123456789" * 2 + ".,,")
_BADGE_PCT_RE = re.compile(r"(?:خصم|وفر|off|save|-|\b)\s*(\d{1,2})\s*%", re.I)

def log(msg: str):
    print(msg, flush=True)

def send_telegram(message: str, use_html: bool = False) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message[:4000], "disable_web_page_preview": False}
    if use_html:
        payload["parse_mode"] = "HTML"
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                return True
            if r.status_code == 400 and use_html:
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", message)[:4000]
                use_html = False
                continue
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return False

def parse_price(value) -> int:
    """قارئ أسعار مخصص يرفض الأقساط تماماً ويعالج الفواصل العشرية."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value)) if 100 <= value <= 500000 else 0
    text = str(value)
    if any(w in text.lower() for w in INSTALLMENT_WORDS):
        return 0
    text = text.translate(_TRANS).replace("\u00a0", " ")
    # حذف الكسور العشرية
    text = re.sub(r"[.,]\d{1,2}(?!\d)", " ", text)
    text = re.sub(r"ج\s*\.?\s*م\s*\.?", " ", text)
    candidates = []
    for m in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text):
        v = int(m.replace(",", ""))
        if 100 <= v <= 500000:
            candidates.append(v)
    return candidates[0] if candidates else 0

def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)
    return t[:180]

def norm_title(title: str) -> str:
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    stop = {"عرض", "خصم", "جديد", "أصلي", "ضمان", "مصر", "سنة", "new", "egypt", "with", "and"}
    words = [w for w in clean.split() if len(w) > 1 and w not in stop]
    return " ".join(words[:8])

def clean_path_key(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url)
        return p.path.rstrip("/").lower()
    except Exception:
        return url

def is_real_block(text: str, status: int) -> bool:
    if not text or len(text) < 900:
        return True
    if status in (403, 429, 503) and len(text) < 25000:
        return True
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:4000], re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
    block_titles = ("just a moment", "attention required", "access denied", "403 forbidden",
                    "security checkpoint", "robot check", "one more step")
    if any(b in title for b in block_titles):
        return True
    return False

def smart_fetch(url: str, store_name: str, expect_json: bool = False) -> str:
    log(f"🔍 فحص [{store_name}]: {url}")
    parts = urllib.parse.urlsplit(url)
    home = f"{parts.scheme}://{parts.netloc}/"
    headers = {
        "User-Agent": UA_CHROME,
        "Accept-Language": AR_LANG,
        "Accept": "application/json,text/plain,*/*" if expect_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": home
    }

    # 1. الاتصال المباشر
    if HAS_CFFI:
        for fp in ("chrome120", "safari17_0"):
            try:
                sess = cffi_requests.Session(impersonate=fp)
                r = sess.get(url, headers=headers, timeout=20)
                if expect_json:
                    if r.status_code == 200 and r.text.lstrip()[:1] in ("{", "["):
                        log(f"   ✅ JSON مباشر ({fp})")
                        return r.text
                elif not is_real_block(r.text, r.status_code):
                    log(f"   ✅ فتح مباشر ({fp}) - {len(r.text)//1024}KB")
                    return r.text
            except Exception:
                pass

    if scraper and not expect_json:
        try:
            r = scraper.get(url, headers=headers, timeout=20)
            if not is_real_block(r.text, r.status_code):
                log(f"   ✅ فتح Cloudscraper - {len(r.text)//1024}KB")
                return r.text
        except Exception:
            pass

    # 2. بوابات التخطي لتجاوز حظر IP جيتهاب
    enc = urllib.parse.quote(url, safe="")
    proxies = [
        ("AllOrigins-JSON", f"https://api.allorigins.win/get?url={enc}"),
        ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
        ("JinaReader", f"https://r.jina.ai/{url}")
    ]
    for pname, purl in proxies:
        if expect_json and pname == "JinaReader":
            continue
        try:
            h = {"User-Agent": UA_CHROME, "Accept-Language": AR_LANG}
            if pname == "JinaReader":
                h["X-Return-Format"] = "html"
            r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=30) if HAS_CFFI else requests.get(purl, headers=h, timeout=30)
            if r.status_code != 200 or not r.text:
                continue
            body = r.text
            if pname == "AllOrigins-JSON":
                try:
                    body = json.loads(body).get("contents") or ""
                except Exception:
                    continue
            if expect_json:
                if body.lstrip()[:1] in ("{", "["):
                    log(f"   🔓 JSON عبر ({pname})")
                    return body
            elif not is_real_block(body, 200):
                log(f"   🔓 تم تخطي الحظر عبر ({pname}) - {len(body)//1024}KB")
                return body
        except Exception:
            continue

    log(f"   ⛔ تعذر فتح الرابط في [{store_name}]")
    return ""

# --- 1. فحص أمازون مصر بدقة بالغة ---
def extract_amazon_deals(html_text: str) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for div in soup.select('div[data-component-type="s-search-result"]'):
        asin = (div.get("data-asin") or "").strip()
        if not asin:
            continue
        h2 = div.find("h2")
        title = clean_title(h2.get_text(" ", strip=True)) if h2 else ""
        cur = old = 0
        for p in div.select("span.a-price"):
            off = p.select_one("span.a-offscreen")
            val = parse_price((off or p).get_text(" ", strip=True))
            if p.get("data-a-strike") == "true" or "a-text-price" in (p.get("class") or []):
                old = old or val
            else:
                cur = cur or val
        if not cur:
            whole = div.select_one("span.a-price-whole")
            cur = parse_price(whole.get_text(" ", strip=True)) if whole else 0
        if title and old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                deals.append({"title": title, "price": cur, "old": old, "discount": disc, "link": f"https://www.amazon.eg/dp/{asin}"})
    return deals

# --- 2. فحص نون مصر (معالجة دقيقة تمنع التقاط أرقام الواط والتقييمات كـ أسعار) ---
def extract_noon_deals(html_text: str) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/p/" not in href or "/egypt-ar/" not in href:
            continue
        link = urllib.parse.urljoin("https://www.noon.com", href)
        card_text = a.get_text(" ", strip=True)
        if any(w in card_text.lower() for w in INSTALLMENT_WORDS):
            continue

        title_el = a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)}) or a.find(["h2", "h3"])
        title = clean_title(title_el.get_text(" ", strip=True) if title_el else a.get("title") or "")
        if len(title) < 6:
            continue

        cur = old = 0
        # استخراج السعر من عناصر السعر الحصرية بنون
        p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
        p_was = a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)}) or a.find(class_=re.compile(r"priceWas|oldPrice", re.I)) or a.find("span", class_=re.compile(r"strike", re.I))

        if p_now:
            cur = parse_price(p_now.get_text())
        if p_was:
            old = parse_price(p_was.get_text())

        # حساب السعر من الخصم المئوي إذا لم يوجد وسم سعر مشطوب
        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if MIN_DISCOUNT <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        if old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                deals.append({"title": title, "price": cur, "old": old, "discount": disc, "link": link})
    return deals

# --- 3. فحص دريم 2000 (معالجة حاسمة تمنع التقاط قسط دريم 2000 كـ سعر) ---
def extract_dream2000_deals(html_text: str) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for card in soup.find_all("li", class_=re.compile(r"product-item")):
        # استبعاد خانة التقسيط داخل دريم 2000 كلياً من القراءة
        for inst_tag in card.find_all(class_=re.compile(r"installment|tabby|tamara|monthly", re.I)):
            inst_tag.decompose()

        a = card.find("a", class_=re.compile(r"product-item-link|photo")) or card.find("a", href=True)
        if not a:
            continue
        link = a["href"].strip()
        title = clean_title(a.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6:
            continue

        sp_el = card.find("span", {"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price"))
        old_el = card.find("span", {"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price"))

        cur = parse_price(sp_el.get_text()) if sp_el else 0
        old = parse_price(old_el.get_text()) if old_el else 0

        if old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                deals.append({"title": title, "price": cur, "old": old, "discount": disc, "link": link})
    return deals

# --- 4. فحص باقي المتاجر (2B، رنين، كنز، راية، بي تك، جوميا) ---
def extract_general_deals(html_text: str, base_domain: str) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")

    # فحص الكروت الكلاسيكية
    cards = soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|productCard|prd|product-layout|plp-product-card", re.I))
    for card in cards:
        # حذف أقسام الأقساط
        for inst in card.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara", re.I)):
            inst.decompose()

        a = card.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        link = urllib.parse.urljoin(base_domain, href)

        t_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I)) or a
        title = clean_title(t_el.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6:
            continue

        sp_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I)) or card.find(["del", "s", "strike"])

        cur = parse_price(sp_el.get_text()) if sp_el else 0
        old = parse_price(old_el.get_text()) if old_el else 0

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card.get_text(" ", strip=True).translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if MIN_DISCOUNT <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        if old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                deals.append({"title": title, "price": cur, "old": old, "discount": disc, "link": link})
    return deals

STORES = [
    {"id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg", "type": "amazon",
     "urls": [
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE",
     ]},
    {"id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com", "type": "noon",
     "urls": [
         "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc"
     ]},
    {"id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com", "type": "dream2000",
     "urls": [
         "https://dream2000.com/offers.html",
         "https://dream2000.com/mobiles.html",
         "https://dream2000.com/home-appliances.html"
     ]},
    {"id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg", "type": "general",
     "urls": [
         "https://2b.com.eg/ar/deals.html",
         "https://2b.com.eg/ar/computers.html",
         "https://2b.com.eg/ar/mobiles-tablets.html"
     ]},
    {"id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com", "type": "general",
     "urls": [
         "https://www.raneen.com/ar/deals",
         "https://www.raneen.com/ar/"
     ]},
    {"id": "kenzz", "name": "كنز (Kenzz)", "domain": "https://kenzz.com", "type": "general",
     "urls": [
         "https://kenzz.com/ar",
         "https://kenzz.com/categories/electronics"
     ]},
    {"id": "raya", "name": "راية شوب (Raya Shop)", "domain": "https://www.rayashop.com", "type": "general",
     "urls": [
         "https://www.rayashop.com/ar/deals",
         "https://www.rayashop.com/ar/mobiles-tablets"
     ]},
    {"id": "btech", "name": "بي تك (B.Tech)", "domain": "https://btech.com", "type": "general",
     "urls": [
         "https://btech.com/ar/daily-deals",
         "https://btech.com/ar/mobyilat.html"
     ]},
    {"id": "jumia", "name": "جوميا مصر (Jumia)", "domain": "https://www.jumia.com.eg", "type": "general",
     "urls": [
         "https://www.jumia.com.eg/ar/flash-sales/",
         "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount"
     ]},
    {"id": "dubaiphone", "name": "دبي فون (Dubai Phone)", "domain": "https://www.dubaiphone.net", "type": "general",
     "urls": [
         "https://www.dubaiphone.net/ar/deals",
         "https://www.dubaiphone.net/ar/"
     ]}
]

def load_json(path: str) -> dict:
    for p in (path, os.path.basename(path), os.path.join("data", os.path.basename(path))):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}

def save_json(path: str, data: dict):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")

def commit_files():
    try:
        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)
        for f in (STATE_FILE, HISTORY_FILE, "sent_deals.json", "history.json"):
            if os.path.exists(f):
                subprocess.run(["git", "add", f], check=False)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
            subprocess.run(["git", "commit", "-m", "chore: update deals memory [skip ci]"], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            subprocess.run(["git", "push"], check=False)
            log("💾 تم حفظ الذاكرة في GitHub بنجاح.")
    except Exception as e:
        log(f"⚠ خطأ حفظ الذاكرة: {e}")

def was_recently_sent(state: dict, sid: str, title: str, link: str, price: int, now: int) -> bool:
    k_title = f"{sid}|{norm_title(title)}"
    k_path = f"{sid}|{clean_path_key(link)}"
    for k in (k_title, k_path, clean_path_key(link)):
        rec = state.get(k)
        if isinstance(rec, dict):
            age_h = (now - rec.get("ts", 0)) / 3600.0
            if age_h < REALERT_HOURS and price >= rec.get("price", price) * 0.95:
                return True
    return False

def main():
    t0 = time.time()
    now = int(t0)
    log("🚀 بدء الفحص الشامل والمقارنة اللحظية (V13)...")
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    summary_lines = ["📊 <b>ملخص جولة الرادار (V13)</b>", ""]
    total_sent = 0
    all_round_deals = []

    # 1. جمع العروض من جميع المتاجر
    for st in STORES:
        sname = st["name"]
        sid = st["id"]
        stype = st.get("type", "general")
        found_deals = []
        opened = False

        for u in st["urls"]:
            html_txt = smart_fetch(u, sname)
            if html_txt:
                opened = True
                if stype == "amazon":
                    extracted = extract_amazon_deals(html_txt)
                elif stype == "noon":
                    extracted = extract_noon_deals(html_txt)
                elif stype == "dream2000":
                    extracted = extract_dream2000_deals(html_txt)
                else:
                    extracted = extract_general_deals(html_txt, st["domain"])

                for item in extracted:
                    item["store"] = sname
                    item["sid"] = sid
                found_deals.extend(extracted)
            time.sleep(random.uniform(0.6, 1.2))

        # إزالة التكرار الداخلي لكل متجر
        unique_deals = {}
        for d in found_deals:
            k = norm_title(d["title"])
            if k and (k not in unique_deals or d["discount"] > unique_deals[k]["discount"]):
                unique_deals[k] = d

        all_round_deals.extend(unique_deals.values())

        if opened:
            summary_lines.append(f"✅ {sname}: وُجد {len(unique_deals)} عرض")
        else:
            summary_lines.append(f"❌ {sname}: محجوب")

    # 2. بناء فهرس المقارنة بالسوق المصري لنفس الموديل في هذه الجولة (Cross-Store Verification)
    price_by_model = defaultdict(list)
    for d in all_round_deals:
        k = norm_title(d["title"])
        if len(k) >= 4:
            price_by_model[k].append((d["price"], d["store"]))

    # 3. إرسال أفضل الصفقات الحقيقية فقط (التي لا يوجد أرخص منها بالسوق)
    deals_by_store = defaultdict(list)
    for d in all_round_deals:
        deals_by_store[d["sid"]].append(d)

    for sid, d_list in deals_by_store.items():
        ordered = sorted(d_list, key=lambda x: (x["discount"], x["old"] - x["price"]), reverse=True)
        sent_store = 0

        for d in ordered:
            if sent_store >= MAX_DEALS_PER_STORE:
                break
            if was_recently_sent(state, sid, d["title"], d["link"], d["price"], now):
                continue

            # فحص المقارنة: هل هذا المنتج معروض في متجر آخر بسعر أرخص؟
            k = norm_title(d["title"])
            market_cands = price_by_model.get(k, [])
            cheaper_elsewhere = [item for item in market_cands if item[0] < d["price"] * 0.95 and item[1] != d["store"]]
            if cheaper_elsewhere:
                log(f"   ❌ استبعاد عرض وهمي: [{d['title'][:35]}] في {d['store']} بـ {d['price']:,} لأن له بديل في {cheaper_elsewhere[0][1]} بـ {cheaper_elsewhere[0][0]:,}")
                continue

            pid_title = f"{sid}|{norm_title(d['title'])}"
            pid_path = f"{sid}|{clean_path_key(d['link'])}"

            h_rec = history.get(pid_title) or history.get(pid_path) or {"prices": []}
            h_prices = h_rec.get("prices", []) if isinstance(h_rec, dict) else []
            status_tag = "🔥 <b>أفضل سعر في السوق المصري</b>"
            if h_prices:
                if d["price"] < min(h_prices):
                    status_tag = "🏆 <b>أقل سعر تاريخي مسجل!</b>"
                elif d["price"] <= sum(h_prices) / len(h_prices) * 0.90:
                    status_tag = "✅ <b>خصم حقيقي مؤكد تاريخياً</b>"
            h_prices.append(d["price"])
            history[pid_title] = {"prices": h_prices[-30:], "ts": now}

            saving = d["old"] - d["price"]
            msg = (
                f"{status_tag} — {htmllib.escape(d['store'])}\n"
                f"📦 {htmllib.escape(d['title'])}\n"
                f"💰 السعر الحالي: <b>{d['price']:,} ج.م</b>\n"
                f"🏷 السعر السابق: {d['old']:,} ج.م (خصم {d['discount']}% — توفير {saving:,} ج.م)\n"
                f"🔗 {d['link']}"
            )
            if send_telegram(msg, use_html=True):
                state[pid_title] = {"price": d["price"], "ts": now}
                state[pid_path] = {"price": d["price"], "ts": now}
                sent_store += 1
                total_sent += 1
                time.sleep(1.1)

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    commit_files()

    elapsed = (time.time() - t0) / 60.0
    summary_lines.append(f"\n⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي الصفقات الحقيقية المرسلة: <b>{total_sent}</b>")
    summary_text = "\n".join(summary_lines)
    log("--- انتهت الجولة بنجاح ---\n" + summary_text)
    send_telegram(summary_text, use_html=True)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        send_telegram("❌ خطأ في الرادار:\n" + tb[-1500:])
        sys.exit(1)
