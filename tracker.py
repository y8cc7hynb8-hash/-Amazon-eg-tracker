#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, random, html as htmllib, subprocess, traceback, urllib.parse
from collections import defaultdict

print("⚡ بدء تشغيل رادار الصفقات الذكي (الإصدار V12.1)...", flush=True)

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
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 200))
MAX_DEALS_PER_STORE = int(os.environ.get("MAX_DEALS_PER_STORE", 5))
REALERT_HOURS = 96

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
INSTALLMENT_WORDS = ("قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month", "تقسيط", "أقساط", "installment", "/mo")

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
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value)) if 50 <= value <= 600000 else 0
    text = str(value)
    if any(w in text.lower() for w in INSTALLMENT_WORDS):
        return 0
    text = text.translate(_TRANS).replace("\u00a0", " ")
    text = re.sub(r"[.,]\d{1,2}(?!\d)", " ", text)
    text = re.sub(r"ج\s*\.?\s*م\s*\.?", " ", text)
    for m in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text):
        v = int(m.replace(",", ""))
        if 50 <= v <= 600000:
            return v
    return 0

def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if 50 <= v <= 600000 else None
    if isinstance(v, str):
        p = parse_price(v)
        return float(p) if p else None
    if isinstance(v, dict):
        for k in ("value", "amount", "default", "current", "price", "final", "regular", "raw", "sale_price"):
            if k in v:
                n = _num(v[k])
                if n:
                    return n
    return None

def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)
    return t[:190]

def norm_title(title: str) -> str:
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    stop = {"عرض", "خصم", "جديد", "أصلي", "ضمان", "مصر", "سنة", "new", "egypt", "with"}
    return " ".join(w for w in clean.split() if len(w) > 1 and w not in stop)[:8]

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
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:5000], re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
    block_titles = ("just a moment", "attention required", "access denied", "403 forbidden",
                    "security checkpoint", "robot check", "pardon our interruption", "one more step")
    if any(b in title for b in block_titles):
        return True
    if len(text) < 12000 and ("vercel security checkpoint" in text.lower() or "cf-browser-verification" in text.lower()):
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

    enc = urllib.parse.quote(url, safe="")
    proxies = [
        ("AllOrigins-JSON", f"https://api.allorigins.win/get?url={enc}"),
        ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
        ("AllOrigins-Raw", f"https://api.allorigins.win/raw?url={enc}"),
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

_NAME_KEYS = ("name", "title", "product_name", "productName", "name_ar", "title_ar", "label")
_CUR_KEYS = ("sale_price", "salePrice", "special_price", "specialPrice", "final_price", "finalPrice", "offer_price", "discounted_price", "current_price", "selling_price")
_OLD_KEYS = ("was_price", "wasPrice", "old_price", "oldPrice", "regular_price", "regularPrice", "list_price", "original_price", "compare_at_price", "strike_price", "price_before")
_LINK_KEYS = ("permalink", "url", "link", "product_url", "url_path", "url_key", "slug", "handle", "sku")

def extract_json_tree(root, base_domain: str, is_noon: bool = False) -> list:
    deals = []
    seen = set()
    stack = [root]
    visited = 0
    while stack and visited < 300000:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            name = None
            for k in _NAME_KEYS:
                v = node.get(k)
                if isinstance(v, str) and len(v.strip()) >= 6 and not v.strip().isdigit():
                    name = clean_title(v)
                    break
            if name and not any(w in name.lower() for w in INSTALLMENT_WORDS):
                cur = old = None
                pr = node.get("price_range")
                if isinstance(pr, dict) and isinstance(pr.get("minimum_price"), dict):
                    mp = pr["minimum_price"]
                    cur, old = _num(mp.get("final_price")), _num(mp.get("regular_price"))
                pz = node.get("prices")
                if cur is None and isinstance(pz, dict):
                    unit = int(pz.get("currency_minor_unit", 2) or 2)
                    div = float(10 ** unit)
                    c_raw = _num(pz.get("sale_price") or pz.get("price"))
                    o_raw = _num(pz.get("regular_price"))
                    if c_raw:
                        cur = c_raw / div
                        old = (o_raw / div) if o_raw else None
                if cur is None:
                    for k in _CUR_KEYS:
                        v = _num(node.get(k))
                        if v:
                            cur = v
                            break
                    for k in _OLD_KEYS:
                        v = _num(node.get(k))
                        if v:
                            old = v
                            break
                    pv = _num(node.get("price"))
                    if pv:
                        if cur is None:
                            cur = pv
                        elif old is None and pv > cur:
                            old = pv
                if cur and old and old > cur >= MIN_PRODUCT_PRICE:
                    c_int, o_int = int(round(cur)), int(round(old))
                    disc = round((o_int - c_int) / o_int * 100)
                    if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                        link = base_domain
                        if is_noon and (node.get("sku") or node.get("catalog_sku")):
                            sku = node.get("sku") or node.get("catalog_sku")
                            slug = node.get("url") or "deal"
                            link = f"https://www.noon.com/egypt-ar/{str(slug).strip('/')}/{sku}/p/"
                        else:
                            for k in _LINK_KEYS:
                                lv = node.get(k)
                                if isinstance(lv, str) and lv.strip():
                                    lv = lv.strip()
                                    if k == "url_key" and not lv.endswith(".html"):
                                        lv = "/" + lv.strip("/") + ".html"
                                    link = urllib.parse.urljoin(base_domain, lv)
                                    break
                        key = (norm_title(name), c_int)
                        if key not in seen:
                            seen.add(key)
                            deals.append({"title": name, "price": c_int, "old": o_int, "discount": disc, "link": link})
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return deals

def extract_universal_dom(soup, base_domain: str) -> list:
    deals = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        if any(bad in href.lower() for bad in ("/cart", "/login", "/wishlist", "/account", "/checkout", "mailto:", "tel:")):
            continue
        link = urllib.parse.urljoin(base_domain, href)

        card = a
        for _ in range(5):
            if len(card.get_text(" ", strip=True)) >= 25 or card.parent is None:
                break
            card = card.parent

        if card.parent and card.parent.name in ("li", "article", "div") and len(card.parent.get_text(" ", strip=True)) < 500:
            card = card.parent

        card_text = card.get_text(" ", strip=True)
        if len(card_text) < 15 or len(card_text) > 700:
            continue

        t_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I)) or card.find(["h2", "h3", "h4"])
        title = clean_title(t_el.get_text(" ", strip=True)) if t_el else clean_title(a.get("title") or "")
        if len(title) < 6:
            img = card.find("img", alt=True)
            if img and len(img["alt"].strip()) >= 6:
                title = clean_title(img["alt"])
        if len(title) < 6:
            raw_t = re.sub(r"\d+(?:[.,]\d+)?\s*(?:ج\.?م\.?|جنيه|EGP|%)", "", a.get_text(" ", strip=True), flags=re.I)
            title = clean_title(raw_t)
        if len(title) < 6 or any(w in title.lower() for w in INSTALLMENT_WORDS):
            continue

        sp_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current|amount|sellingPrice", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular|strike", re.I)) or card.find(["del", "s", "strike"])

        cur = parse_price(sp_el.get_text()) if sp_el else 0
        old = parse_price(old_el.get_text()) if old_el else 0

        if not cur or not old:
            clean_card_txt = card_text.translate(_TRANS)
            clean_card_txt = re.sub(r"[.,]\d{1,2}(?!\d)", " ", clean_card_txt)
            price_matches = re.findall(r"(?:(\d{1,3}(?:,\d{3})+|\d{3,6})\s*(?:ج\.?م|جنيه|جنية|EGP)|(?:ج\.?م|جنيه|جنية|EGP)\s*(\d{1,3}(?:,\d{3})+|\d{3,6}))", clean_card_txt, re.I)
            nums = []
            for m1, m2 in price_matches:
                val = int((m1 or m2).replace(",", ""))
                if MIN_PRODUCT_PRICE <= val <= 400000 and val not in nums:
                    nums.append(val)
            if len(nums) >= 2:
                cur, old = min(nums[:2]), max(nums[:2])
            elif len(nums) == 1 and not cur:
                cur = nums[0]

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if MIN_DISCOUNT <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        if old > cur >= MIN_PRODUCT_PRICE:
            disc = round((old - cur) / old * 100)
            if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                k = (norm_title(title), cur)
                if k not in seen:
                    seen.add(k)
                    deals.append({"title": title, "price": cur, "old": old, "discount": disc, "link": link})

    return deals

def extract_all_deals(html_text: str, page_url: str, base_domain: str, is_amazon: bool = False, is_noon: bool = False) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")

    if is_amazon:
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

    for s in soup.find_all("script"):
        txt = (s.string or s.get_text() or "").strip()
        if len(txt) < 80:
            continue
        if s.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__") or "json" in (s.get("type") or "").lower():
            try:
                deals.extend(extract_json_tree(json.loads(txt), base_domain, is_noon=is_noon))
            except Exception:
                pass
        elif "__next_f.push" in txt or "final_price" in txt or "special_price" in txt or "sale_price" in txt:
            unescaped = txt.replace('\\"', '"').replace("\\\\", "\\")
            for m in re.finditer(r'\{[^{}]{20,1200}\}', unescaped):
                chunk = m.group(0)
                if any(k in chunk for k in ("price", "Price", "sku")):
                    try:
                        deals.extend(extract_json_tree(json.loads(chunk), base_domain, is_noon=is_noon))
                    except Exception:
                        pass

    deals.extend(extract_universal_dom(soup, base_domain))
    return deals

STORES = [
    {"id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg", "amazon": True,
     "urls": [
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE",
     ]},
    {"id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com", "noon": True,
     "urls": [
         "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc",
     ]},
    {"id": "dubaiphone", "name": "دبي فون (Dubai Phone)", "domain": "https://www.dubaiphone.net",
     "api": "https://www.dubaiphone.net/wp-json/wc/store/v1/products?on_sale=true&per_page=80",
     "urls": ["https://www.dubaiphone.net/ar/"]},
    {"id": "jumia", "name": "جوميا مصر (Jumia)", "domain": "https://www.jumia.com.eg",
     "urls": [
         "https://www.jumia.com.eg/ar/flash-sales/",
         "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount",
         "https://www.jumia.com.eg/ar/electronics/?sort=discount",
     ]},
    {"id": "raya", "name": "راية شوب (Raya Shop)", "domain": "https://www.rayashop.com",
     "urls": [
         "https://www.rayashop.com/ar/deals",
         "https://www.rayashop.com/ar/mobiles-tablets",
         "https://www.rayashop.com/ar/home-appliances",
     ]},
    {"id": "btech", "name": "بي تك (B.Tech)", "domain": "https://btech.com",
     "urls": [
         "https://btech.com/ar/daily-deals",
         "https://btech.com/ar/mobyilat.html",
         "https://btech.com/ar/tv-home-theater.html",
         "https://btech.com/ar/",
     ]},
    {"id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg",
     "urls": ["https://2b.com.eg/ar/deals.html", "https://2b.com.eg/ar/computers.html"]},
    {"id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com",
     "urls": ["https://www.raneen.com/ar/deals", "https://www.raneen.com/ar/"]},
    {"id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com",
     "urls": [
         "https://dream2000.com/offers.html",
         "https://dream2000.com/home-appliances.html",
         "https://dream2000.com/mobiles.html",
         "https://dream2000.com/",
     ]},
    {"id": "kenzz", "name": "كنز (Kenzz)", "domain": "https://kenzz.com",
     "urls": ["https://kenzz.com/ar"]},
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
    log("🚀 بدء الفحص الشامل للمتاجر المصرية (V12.1)...")
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    summary_lines = ["📊 <b>ملخص جولة الرادار (V12.1)</b>", ""]
    total_sent = 0

    for st in STORES:
        sname = st["name"]
        sid = st["id"]
        found_deals = []
        opened = False

        if st.get("api"):
            raw = smart_fetch(st["api"], sname, expect_json=True)
            if raw:
                try:
                    found_deals.extend(extract_json_tree(json.loads(raw), st["domain"]))
                    opened = True
                except Exception:
                    pass

        for u in st["urls"]:
            html_txt = smart_fetch(u, sname)
            if html_txt:
                opened = True
                extracted = extract_all_deals(
                    html_txt, u, st["domain"],
                    is_amazon=bool(st.get("amazon")),
                    is_noon=bool(st.get("noon"))
                )
                found_deals.extend(extracted)
            time.sleep(random.uniform(0.8, 1.5))

        unique_deals = {}
        for d in found_deals:
            k = norm_title(d["title"])
            if k and (k not in unique_deals or d["discount"] > unique_deals[k]["discount"]):
                unique_deals[k] = d

        ordered = sorted(unique_deals.values(), key=lambda x: (x["discount"], x["old"] - x["price"]), reverse=True)
        sent_store = 0

        for d in ordered:
            if sent_store >= MAX_DEALS_PER_STORE:
                break
            if was_recently_sent(state, sid, d["title"], d["link"], d["price"], now):
                continue

            pid_title = f"{sid}|{norm_title(d['title'])}"
            pid_path = f"{sid}|{clean_path_key(d['link'])}"

            h_rec = history.get(pid_title) or history.get(pid_path) or {"prices": []}
            h_prices = h_rec.get("prices", []) if isinstance(h_rec, dict) else []
            status_tag = "🔥 <b>صفقة مرصودة</b>"
            if h_prices:
                if d["price"] < min(h_prices):
                    status_tag = "🏆 <b>أقل سعر تاريخي مسجل!</b>"
                elif d["price"] <= sum(h_prices) / len(h_prices) * 0.90:
                    status_tag = "✅ <b>خصم حقيقي مؤكد تاريخياً</b>"
            h_prices.append(d["price"])
            history[pid_title] = {"prices": h_prices[-30:], "ts": now}

            saving = d["old"] - d["price"]
            msg = (
                f"{status_tag} — {htmllib.escape(sname)}\n"
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

        if opened:
            summary_lines.append(f"✅ {sname}: وُجد {len(ordered)} عرض | أُرسل {sent_store}")
        else:
            summary_lines.append(f"❌ {sname}: محجوب")

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    commit_files()

    elapsed = (time.time() - t0) / 60.0
    summary_lines.append(f"\n⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي المرسل الجديد: <b>{total_sent}</b>")
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
