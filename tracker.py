#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live Best Price Radar — Egyptian Shopping Deals Bot
==================================================
الإصلاحات المطبقة:
  1. إضافة import traceback (كان بيسبب NameError عند أي خطأ)
  2. product_fingerprint() بدل norm_title عشان ما نخلطش موديلات مختلفة (128GB ≠ 256GB)
  3. prune_state() لتنظيف الملف بعد 30 يوم (كان بينمو للأبد)
  4. احترام retry_after عند 429 من Telegram
  5. Session reuse لأداء أسرع
  6. Cooldown للمتاجر المحجوبة (6 ساعات)
  7. Git commit آمن مع autostash + return code checks
  8. Extractors محسّنة (خصوصاً أمازون)
  9. validate_deal() مركزية قبل الإرسال
  10. Logging بدل print
"""
import os, sys, time, json, re, random, html as htmllib, subprocess, urllib.parse, traceback, logging
from collections import defaultdict

# ============ Logging ============
log = logging.getLogger("radar")
log.setLevel(logging.INFO)
if not log.handlers:
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(_ch)

print("⚡ بدء تشغيل رادار العروض اللحظية (Live Best Price Radar)...", flush=True)

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

# ================= الإعدادات =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MIN_DISCOUNT = int(os.environ.get("MIN_DISCOUNT", 15))
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 150))
MAX_DEALS_PER_STORE = int(os.environ.get("MAX_DEALS_PER_STORE", 8))
REALERT_HOURS = int(os.environ.get("REALERT_HOURS", 72))
PRICE_DROP_THRESHOLD = 0.92          # أعد الإرسال فقط لو السعر نزل 8%+
STATE_MAX_AGE_DAYS = 30              # امسح السجلات الأقدم من كده
BLOCKED_COOLDOWN_HOURS = 6           # فترة تبريد للمتاجر المحجوبة

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
BLOCKED_FILE = os.path.join(DATA_DIR, "blocked_stores.json")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

INSTALLMENT_WORDS = (
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month",
    "تقسيط", "أقساط", "installment", "/mo", "فائدة", "بدون فوائد", "دفعة"
)

_AR = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR, "0123456789" * 2 + ".,,")
_BADGE_PCT_RE = re.compile(r"(?:خصم|وفر|off|save|-|\b)\s*(\d{1,2})\s*%", re.I)

# ================= أدوات مساعدة =================

def parse_price(value) -> int:
    """استخراج السعر النقدي الصافي واستبعاد نصوص الأقساط."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value)) if 50 <= value <= 500000 else 0
    text = str(value)
    if any(w in text.lower() for w in INSTALLMENT_WORDS):
        return 0
    text = text.translate(_TRANS).replace("\u00a0", " ")
    text = re.sub(r"[.,]\d{1,2}(?!\d)", " ", text)
    text = re.sub(r"ج\s*\.?\s*م\s*\.?", " ", text)
    candidates = []
    for m in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text):
        v = int(m.replace(",", ""))
        if 50 <= v <= 500000:
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

_SPEC_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(gb|tb|ml|mm|cm|inch|بوصة|لتر|كيلو|جرام|كجم|kg)", re.I
)

def product_fingerprint(title: str) -> str:
    """
    بصمة دقيقة: بتربط الاسم بالـ specs المهمة (128gb, 6.5inch, 500ml...)
    عشان "A55 128GB" ما يتحسبش نفس "A55 256GB".
    """
    t = (title or "").lower().translate(_TRANS)
    specs = []
    for m in _SPEC_RE.finditer(t):
        num = m.group(1)
        unit = m.group(2).lower()
        unit = {"كيلو": "kg", "جرام": "g", "كجم": "kg", "لتر": "l", "بوصة": "inch"}.get(unit, unit)
        specs.append(f"{num}{unit}")
    base = norm_title(title)
    return f"{base}||{'|'.join(sorted(specs))}" if specs else base

def validate_deal(d: dict) -> bool:
    """تحقق نهائي قبل الإرسال."""
    if not d.get("title") or not d.get("link"):
        return False
    if not isinstance(d.get("price"), int) or not isinstance(d.get("old"), int):
        return False
    if d["price"] < MIN_PRODUCT_PRICE or d["old"] <= d["price"]:
        return False
    # أعد حساب الخصم من الأرقام الفعلية
    computed = round((d["old"] - d["price"]) / d["old"] * 100)
    if not (MIN_DISCOUNT <= computed <= MAX_REALISTIC_DISC):
        return False
    d["discount"] = computed
    return True

# ================= Telegram =================

def send_telegram(message: str, use_html: bool = False, max_retries: int = 3) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("BOT_TOKEN أو CHAT_ID غير موجودين — لا يمكن الإرسال")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message[:4000], "disable_web_page_preview": False}
    if use_html:
        payload["parse_mode"] = "HTML"

    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                retry_after = 5
                try:
                    retry_after = r.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    pass
                log.warning(f"⏳ Telegram rate limit — انتظار {retry_after}s")
                time.sleep(retry_after + 1)
                continue
            if r.status_code == 400 and use_html:
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", message)[:4000]
                use_html = False
                continue
            log.warning(f"⚠ Telegram send فشل: {r.status_code} — {r.text[:150]}")
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            log.warning(f"⚠ Telegram exception: {e}")
            time.sleep(2 * (attempt + 1))
    return False

# ================= Fetching =================

_SESSIONS = {}
def _get_session(fp="chrome120"):
    if not HAS_CFFI:
        return None
    if fp not in _SESSIONS:
        _SESSIONS[fp] = cffi_requests.Session(impersonate=fp)
    return _SESSIONS[fp]

def is_blocked_page(text: str, status: int) -> bool:
    if not text or len(text) < 800:
        return True
    if status in (403, 429, 503) and len(text) < 25000:
        return True
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:4000], re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
    block_titles = ("just a moment", "attention required", "access denied", "403 forbidden",
                    "security checkpoint", "robot check", "one more step")
    return any(b in title for b in block_titles)

def smart_fetch(url: str, store_name: str, expect_json: bool = False) -> str:
    log.info(f"🔍 [{store_name}]: {url}")
    parts = urllib.parse.urlsplit(url)
    home = f"{parts.scheme}://{parts.netloc}/"
    headers = {
        "User-Agent": UA_CHROME,
        "Accept-Language": AR_LANG,
        "Accept": "application/json,text/plain,*/*" if expect_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": home,
    }

    # 1) مباشر عبر curl_cffi
    if HAS_CFFI:
        for fp in ("chrome120", "safari17_0"):
            try:
                sess = _get_session(fp)
                r = sess.get(url, headers=headers, timeout=20)
                if expect_json:
                    if r.status_code == 200 and r.text.lstrip()[:1] in ("{", "["):
                        log.info(f"   ✅ JSON ({fp})")
                        return r.text
                elif not is_blocked_page(r.text, r.status_code):
                    log.info(f"   ✅ مباشر ({fp}) — {len(r.text)//1024}KB")
                    return r.text
            except Exception as e:
                log.debug(f"   {fp} فشل: {e}")

    # 2) Cloudscraper
    if scraper and not expect_json:
        try:
            r = scraper.get(url, headers=headers, timeout=20)
            if not is_blocked_page(r.text, r.status_code):
                log.info(f"   ✅ Cloudscraper — {len(r.text)//1024}KB")
                return r.text
        except Exception as e:
            log.debug(f"   Cloudscraper فشل: {e}")

    # 3) Proxies مع retry مرة واحدة لكل واحد
    enc = urllib.parse.quote(url, safe="")
    proxies = [
        ("AllOrigins", f"https://api.allorigins.win/get?url={enc}"),
        ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
        ("JinaReader", f"https://r.jina.ai/{url}"),
    ]
    for pname, purl in proxies:
        if expect_json and pname == "JinaReader":
            continue
        for attempt in range(2):
            try:
                h = {"User-Agent": UA_CHROME, "Accept-Language": AR_LANG}
                if pname == "JinaReader":
                    h["X-Return-Format"] = "html"
                if HAS_CFFI:
                    r = _get_session().get(purl, headers=h, timeout=28)
                else:
                    r = requests.get(purl, headers=h, timeout=28)
                if r.status_code != 200 or not r.text:
                    break
                body = r.text
                if pname == "AllOrigins":
                    try:
                        body = json.loads(body).get("contents") or ""
                    except Exception:
                        break
                if expect_json:
                    if body.lstrip()[:1] in ("{", "["):
                        log.info(f"   🔓 JSON عبر {pname}")
                        return body
                elif not is_blocked_page(body, 200):
                    log.info(f"   🔓 تجاوز عبر {pname} — {len(body)//1024}KB")
                    return body
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(2)
                else:
                    log.debug(f"   {pname} فشل: {e}")
                    break

    log.warning(f"   ⛔ تعذر فتح [{store_name}]")
    return ""

# ================= Extractors =================

def extract_amazon_deals(html_text: str) -> list:
    """أمازون — نسخة محسّنة تتجنب التقاط السعر القديم كسعر حالي."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for div in soup.select('div[data-component-type="s-search-result"]'):
        asin = (div.get("data-asin") or "").strip()
        if not asin:
            continue
        h2 = div.find("h2")
        title = clean_title(h2.get_text(" ", strip=True)) if h2 else ""
        if not title:
            continue

        cur = old = 0
        for p in div.select("span.a-price"):
            is_old = (
                "a-text-price" in (p.get("class") or [])
                or p.get("data-a-strike") == "true"
            )
            off = p.select_one("span.a-offscreen")
            if not off:
                continue
            val = parse_price(off.get_text())
            if val <= 0:
                continue
            if is_old and not old:
                old = val
            elif not is_old and not cur:
                cur = val

        # fallback: span.a-price-whole لو offscreen مش موجود
        if not cur:
            whole = div.select_one("span.a-price-whole")
            cur = parse_price(whole.get_text(strip=True)) if whole else 0

        if title and old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                deals.append({
                    "title": title, "price": cur, "old": old,
                    "discount": disc, "link": f"https://www.amazon.eg/dp/{asin}",
                })
    return deals

def extract_noon_deals(html_text: str) -> list:
    """نون — يستبعد أرقام الواط والتقييمات."""
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

        title_el = (
            a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)})
            or a.find(["h2", "h3"])
        )
        title = clean_title(title_el.get_text(" ", strip=True) if title_el else a.get("title") or "")
        if len(title) < 6:
            continue

        p_now = (
            a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)})
            or a.find(class_=re.compile(r"priceNow|amount", re.I))
        )
        p_was = (
            a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)})
            or a.find(class_=re.compile(r"priceWas|oldPrice", re.I))
            or a.find("span", class_=re.compile(r"strike", re.I))
        )

        cur = parse_price(p_now.get_text()) if p_now else 0
        old = parse_price(p_was.get_text()) if p_was else 0

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if MIN_DISCOUNT <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        if old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                deals.append({
                    "title": title, "price": cur, "old": old,
                    "discount": disc, "link": link,
                })
    return deals

def extract_dream2000_deals(html_text: str) -> list:
    """دريم 2000 — يحذف أقسام التقسيط قبل قراءة السعر."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for card in soup.find_all("li", class_=re.compile(r"product-item")):
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
                deals.append({
                    "title": title, "price": cur, "old": old,
                    "discount": disc, "link": link,
                })
    return deals

def extract_general_deals(html_text: str, base_domain: str) -> list:
    """بقية المتاجر — محددات أضيق لمنع false positives."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.find_all(
        ["li", "div", "article"],
        class_=re.compile(r"product-item|product-card|productCard|prd|product-layout|plp-product", re.I),
    )
    for card in cards:
        for inst in card.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara", re.I)):
            inst.decompose()

        a = card.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        link = urllib.parse.urljoin(base_domain, href)

        t_el = (
            card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I))
            or a
        )
        title = clean_title(t_el.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6:
            continue

        # محددات أضيق — بنبحث بس في عناصر معروفة كسعر
        sp_el = (
            card.find(attrs={"data-price-type": "finalPrice"})
            or card.select_one(
                "[class*='special-price' i], [class*='finalPrice' i], "
                "[class*='price-new' i], [class*='sale-price' i], [class*='price-current' i]"
            )
            or card.find("ins")
        )
        old_el = (
            card.find(attrs={"data-price-type": "oldPrice"})
            or card.select_one(
                "[class*='old-price' i], [class*='price-old' i], "
                "[class*='regular-price' i], [class*='was-price' i]"
            )
            or card.find(["del", "s", "strike"])
        )

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
                deals.append({
                    "title": title, "price": cur, "old": old,
                    "discount": disc, "link": link,
                })
    return deals

# ================= المتاجر =================
STORES = [
    {"id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg", "type": "amazon",
     "urls": [
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE&page=2",
         "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE&page=2",
         "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE",
     ]},
    {"id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com", "type": "noon",
     "urls": [
         "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/large-appliances/?sort[by]=discount&sort[dir]=desc",
     ]},
    {"id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com", "type": "dream2000",
     "urls": [
         "https://dream2000.com/offers.html",
         "https://dream2000.com/mobiles.html",
         "https://dream2000.com/home-appliances.html",
     ]},
    {"id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg", "type": "general",
     "urls": [
         "https://2b.com.eg/ar/deals.html",
         "https://2b.com.eg/ar/computers.html",
         "https://2b.com.eg/ar/mobiles-tablets.html",
     ]},
    {"id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com", "type": "general",
     "urls": [
         "https://www.raneen.com/ar/deals",
         "https://www.raneen.com/ar/deals/appliances-electronics-offers",
     ]},
    {"id": "kenzz", "name": "كنز (Kenzz)", "domain": "https://kenzz.com", "type": "general",
     "urls": [
         "https://kenzz.com/ar",
         "https://kenzz.com/categories/electronics",
     ]},
    {"id": "raya", "name": "راية شوب (Raya Shop)", "domain": "https://www.rayashop.com", "type": "general",
     "urls": [
         "https://www.rayashop.com/ar/deals",
         "https://www.rayashop.com/ar/mobiles-tablets",
     ]},
    {"id": "btech", "name": "بي تك (B.Tech)", "domain": "https://btech.com", "type": "general",
     "urls": [
         "https://btech.com/ar/daily-deals",
         "https://btech.com/ar/mobyilat.html",
     ]},
    {"id": "jumia", "name": "جوميا مصر (Jumia)", "domain": "https://www.jumia.com.eg", "type": "general",
     "urls": [
         "https://www.jumia.com.eg/ar/flash-sales/",
         "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount",
     ]},
    {"id": "dubaiphone", "name": "دبي فون (Dubai Phone)", "domain": "https://www.dubaiphone.net", "type": "general",
     "urls": [
         "https://www.dubaiphone.net/ar/deals",
         "https://www.dubaiphone.net/ar/",
     ]},
]

# ================= State & Git =================

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
        log.warning(f"⚠ تعذر حفظ {path}: {e}")

def prune_state(state: dict, max_age_days: int = STATE_MAX_AGE_DAYS) -> dict:
    """يمنع ملف الـ state من النمو للأبد."""
    cutoff = time.time() - max_age_days * 86400
    return {k: v for k, v in state.items() if isinstance(v, dict) and v.get("ts", 0) > cutoff}

def load_blocked() -> dict:
    return load_json(BLOCKED_FILE)

def mark_blocked(sid: str):
    b = load_blocked()
    b[sid] = int(time.time())
    save_json(BLOCKED_FILE, b)

def is_blocked(sid: str) -> bool:
    b = load_blocked()
    ts = b.get(sid, 0)
    return (time.time() - ts) / 3600.0 < BLOCKED_COOLDOWN_HOURS

def _git(*args, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=False, capture_output=True, timeout=timeout)

def commit_files():
    """Git commit آمن مع autostash + return code checks."""
    try:
        _git("config", "user.name", "deals-radar[bot]")
        _git("config", "user.email", "deals-radar@users.noreply.github.com")

        files = [f for f in (STATE_FILE, BLOCKED_FILE, "sent_deals.json", "blocked_stores.json") if os.path.exists(f)]
        if not files:
            return
        for f in files:
            _git("add", f)

        diff = _git("diff", "--cached", "--quiet")
        if diff.returncode == 0:
            log.info("💾 لا توجد تغييرات للحفظ")
            return

        _git("commit", "-m", "chore: update radar state [skip ci]")

        pull = _git("pull", "--rebase", "--autostash", "origin", "HEAD")
        if pull.returncode != 0:
            log.warning(f"⚠ git pull فشل — إلغاء rebase: {pull.stderr.decode(errors='ignore')[:150]}")
            _git("rebase", "--abort")

        push = _git("push")
        if push.returncode == 0:
            log.info("💾 تم حفظ الذاكرة في GitHub")
        else:
            log.warning(f"⚠ git push فشل: {push.stderr.decode(errors='ignore')[:150]}")
    except subprocess.TimeoutExpired:
        log.warning("⚠ عملية Git انتهت مهلتها")
    except Exception as e:
        log.warning(f"⚠ خطأ Git: {e}")

def was_recently_sent(state: dict, sid: str, title: str, link: str, price: int, now: int) -> bool:
    """
    فحص مزدوج بالبصمة والرابط. أعد الإرسال فقط لو السعر نزل 8%+.
    """
    k_title = f"{sid}|{product_fingerprint(title)}"
    k_link = link.split("?")[0].rstrip("/").lower()
    for k in (k_title, k_link):
        rec = state.get(k)
        if isinstance(rec, dict):
            age_h = (now - rec.get("ts", 0)) / 3600.0
            if age_h < REALERT_HOURS and price > rec.get("price", price) * PRICE_DROP_THRESHOLD:
                return True
    return False

# ================= التشغيل =================

def main():
    t0 = time.time()
    now = int(t0)
    log.info("🚀 بدأ رادار الصفقات اللحظية...")
    state = load_json(STATE_FILE)
    summary_lines = ["📊 <b>ملخص جولة الصفقات اللحظية</b>", ""]
    total_sent = 0
    all_round_deals = []

    # 1. اجلب من كل المتاجر
    for st in STORES:
        sname, sid, stype = st["name"], st["id"], st.get("type", "general")

        if is_blocked(sid):
            summary_lines.append(f"⏸ {sname}: في فترة تبريد")
            log.info(f"⏸ [{sname}] في فترة تبريد، تخطي")
            continue

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

            # أمازون يحتاج تأخير أكبر
            delay = random.uniform(2.0, 3.5) if stype == "amazon" else random.uniform(0.6, 1.2)
            time.sleep(delay)

        # إزالة التكرار الداخلي بالبصمة (نفس الموديل)
        unique_deals = {}
        for d in found_deals:
            k = product_fingerprint(d["title"])
            if k and (k not in unique_deals or d["discount"] > unique_deals[k]["discount"]):
                unique_deals[k] = d

        all_round_deals.extend(unique_deals.values())

        if opened:
            summary_lines.append(f"✅ {sname}: {len(unique_deals)} عرض")
        else:
            mark_blocked(sid)
            summary_lines.append(f"❌ {sname}: محجوب (تبريد {BLOCKED_COOLDOWN_HOURS}س)")

    # 2. فهرس أسعار السوق بالبصمة الدقيقة
    price_by_fp = defaultdict(list)
    for d in all_round_deals:
        fp = product_fingerprint(d["title"])
        if fp and len(fp) >= 3:
            price_by_fp[fp].append((d["price"], d["store"]))

    # 3. إرسال أفضل سعر لكل موديل
    deals_by_store = defaultdict(list)
    for d in all_round_deals:
        deals_by_store[d["sid"]].append(d)

    for sid, d_list in deals_by_store.items():
        ordered = sorted(d_list, key=lambda x: (x["discount"], x["old"] - x["price"]), reverse=True)
        sent_store = 0

        for d in ordered:
            if sent_store >= MAX_DEALS_PER_STORE:
                break
            if not validate_deal(d):
                continue
            if was_recently_sent(state, sid, d["title"], d["link"], d["price"], now):
                continue

            # مقارنة السوق بالبصمة الدقيقة (مش norm_title)
            fp = product_fingerprint(d["title"])
            market = price_by_fp.get(fp, [])
            cheaper = [c for c in market if c[0] < d["price"] * 0.95 and c[1] != d["store"]]
            if cheaper:
                log.info(
                    f"   ❌ استبعاد [{d['title'][:35]}] في {d['store']} بـ {d['price']:,} — "
                    f"أرخص في {cheaper[0][1]} بـ {cheaper[0][0]:,}"
                )
                continue

            saving = d["old"] - d["price"]
            msg = (
                f"🔥 <b>أفضل سعر لحظي في السوق المصري</b> — {htmllib.escape(d['store'])}\n\n"
                f"📦 {htmllib.escape(d['title'])}\n"
                f"💰 السعر الحالي: <b>{d['price']:,} ج.م</b>\n"
                f"🏷 السعر السابق: {d['old']:,} ج.م (خصم {d['discount']}% — توفير {saving:,} ج.م)\n"
                f"🔗 {d['link']}"
            )

            if send_telegram(msg, use_html=True):
                pid_title = f"{sid}|{fp}"
                pid_link = d["link"].split("?")[0].rstrip("/").lower()
                state[pid_title] = {"price": d["price"], "ts": now}
                state[pid_link] = {"price": d["price"], "ts": now}
                sent_store += 1
                total_sent += 1
                time.sleep(1.2)

    # 4. تنظيف الـ state ثم الحفظ
    state = prune_state(state)
    save_json(STATE_FILE, state)
    commit_files()

    elapsed = (time.time() - t0) / 60.0
    summary_lines.append(f"\n⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي العروض المرسلة الآن: <b>{total_sent}</b>")
    summary_text = "\n".join(summary_lines)
    log.info("--- انتهت الجولة بنجاح ---\n" + summary_text)
    send_telegram(summary_text, use_html=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log.error(tb)
        send_telegram("❌ خطأ في تشغيل الرادار:\n" + tb[-1500:])
        sys.exit(1)
