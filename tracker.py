#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, random, html as htmllib, subprocess, traceback, urllib.parse
from collections import defaultdict

print("⚡ جاري بدء تشغيل ملف tracker.py (الإصدار V11)...", flush=True)

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

MIN_DISCOUNT = int(os.environ.get("MIN_DISCOUNT", 20))
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 72))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 250))
MAX_DEALS_PER_STORE = int(os.environ.get("MAX_DEALS_PER_STORE", 5))
REALERT_HOURS = 72

STATE_FILE = "sent_deals.json"
HISTORY_FILE = "history.json"

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
INSTALLMENT_WORDS = ("قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month", "تقسيط", "أقساط", "installment")
COUPON_WORDS = ("coupon", "كوبون", "استخدم", "كود", "promo")

_AR = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR, "0123456789" * 2 + ".,,")
_CUR = r"(?:ج\s?\.?\s?م\.?|جنيه|جنية|EGP|L\.?E\.?)"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRICE_RE = re.compile(r"(?<![\w.,])(" + _NUM + r")\s*" + _CUR + r"(?!\w)|(?<!\w)" + _CUR + r"\s*(" + _NUM + r")(?![\d])", re.I)
_BADGE_PCT_RE = re.compile(r"(?:خصم|وفر|off|save|-|\b)\s*(\d{1,2})\s*%", re.I)

def log(msg: str):
    print(msg, flush=True)

def send_telegram(message: str, use_html: bool = False) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log("⚠ BOT_TOKEN أو CHAT_ID غير موجود!")
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
        except Exception as e:
            log(f"خطأ تليجرام: {e}")
            time.sleep(2)
    return False

def parse_price(value) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value)) if value >= 50 else 0
    text = str(value)
    if any(w in text.lower() for w in INSTALLMENT_WORDS):
        return 0
    text = text.translate(_TRANS).replace("\u00a0", " ")
    m = _PRICE_RE.search(text)
    if m:
        v = int(float((m.group(1) or m.group(2)).replace(",", "")))
        if v >= 50:
            return v
    t2 = re.sub(r"(?<=\d)\.\d{1,2}(?!\d)", "", text)
    for n in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", t2):
        v = int(n.replace(",", ""))
        if v >= 50:
            return v
    return 0

def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, str):
        p = parse_price(v)
        return float(p) if p else None
    if isinstance(v, dict):
        for k in ("value", "amount", "default", "current", "price", "final", "regular", "raw"):
            if k in v:
                n = _num(v[k])
                if n:
                    return n
    return None

def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)
    return t[:200]

def norm_title(title: str) -> str:
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return " ".join(w for w in clean.split() if len(w) > 1)[:10]

def is_blocked(text: str) -> bool:
    if not text or len(text) < 800:
        return True
    head = text[:5000].lower()
    for b in ("just a moment", "attention required", "access denied", "security checkpoint", "cf_chl_opt", "captcha-delivery.com", "_incapsula_resource"):
        if b in head:
            return True
    return False

def smart_fetch(url: str, store_name: str, expect_json: bool = False) -> str:
    log(f"🔍 جاري فحص [{store_name}]: {url}")
    parts = urllib.parse.urlsplit(url)
    home = f"{parts.scheme}://{parts.netloc}/"
    headers = {
        "User-Agent": UA_CHROME,
        "Accept-Language": AR_LANG,
        "Accept": "application/json,*/*" if expect_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": home
    }

    # 1. محاولة الاتصال المباشر ببصمات متصفح حقيقية
    if HAS_CFFI:
        for fp in ("chrome120", "safari17_0"):
            try:
                sess = cffi_requests.Session(impersonate=fp)
                r = sess.get(url, headers=headers, timeout=20)
                if r.status_code == 200 and (expect_json or not is_blocked(r.text)):
                    log(f"   ✅ فتح مباشر ({fp}) - {len(r.text)//1024}KB")
                    return r.text
            except Exception:
                pass

    # 2. محاولة Cloudscraper
    if scraper and not expect_json:
        try:
            r = scraper.get(url, headers=headers, timeout=20)
            if r.status_code == 200 and not is_blocked(r.text):
                log(f"   ✅ فتح عبر Cloudscraper - {len(r.text)//1024}KB")
                return r.text
        except Exception:
            pass

    # 3. البوابات الوسيطة المجانية لتجاوز حظر IP جيتهاب (جوميا / دبي فون / بي تك)
    enc = urllib.parse.quote(url, safe="")
    proxies = [
        ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
        ("AllOrigins", f"https://api.allorigins.win/raw?url={enc}"),
        ("CorsProxy", f"https://corsproxy.io/?{enc}"),
        ("JinaReader", f"https://r.jina.ai/{url}")
    ]
    for pname, purl in proxies:
        try:
            h = dict(headers)
            if pname == "JinaReader":
                h["X-Return-Format"] = "html"
            r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=28) if HAS_CFFI else requests.get(purl, headers=h, timeout=28)
            if r.status_code == 200 and (expect_json or not is_blocked(r.text)):
                log(f"   🔓 تم تخطي الحظر عبر بوابة ({pname}) - {len(r.text)//1024}KB")
                return r.text
        except Exception:
            continue

    log(f"   ⛔ تعذر فتح الرابط في [{store_name}] بعد كل المحاولات.")
    return ""

# --- استخراج المنتجات من JSON المخفي في أي صفحة (نون، راية، بي تك، ووكومرس) ---
_NAME_KEYS = ("name", "title", "product_name", "productName", "name_ar", "title_ar")
_CUR_KEYS = ("sale_price", "salePrice", "special_price", "specialPrice", "final_price", "finalPrice", "offer_price", "discounted_price", "current_price", "selling_price")
_OLD_KEYS = ("was_price", "wasPrice", "old_price", "oldPrice", "regular_price", "regularPrice", "list_price", "original_price", "compare_at_price", "strike_price", "price_before")
_LINK_KEYS = ("permalink", "url", "link", "product_url", "url_path", "url_key", "slug", "handle", "sku")

def extract_json_tree(root, base_domain: str, is_noon: bool = False) -> list:
    deals = []
    seen = set()
    stack = [root]
    visited = 0
    while stack and visited < 250000:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            name = None
            for k in _NAME_KEYS:
                v = node.get(k)
                if isinstance(v, str) and len(v.strip()) >= 6 and not v.strip().isdigit():
                    name = clean_title(v)
                    break
            if name:
                cur = old = None
                # Magento GraphQL
                pr = node.get("price_range")
                if isinstance(pr, dict) and isinstance(pr.get("minimum_price"), dict):
                    mp = pr["minimum_price"]
                    cur, old = _num(mp.get("final_price")), _num(mp.get("regular_price"))
                # WooCommerce API
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
                            old = pv  # نظام نون: price هو القديم و sale_price هو الحالي
                if cur and old and old > cur >= MIN_PRODUCT_PRICE:
                    c_int, o_int = int(round(cur)), int(round(old))
                    disc = round((o_int - c_int) / o_int * 100)
                    if MIN_DISCOUNT <= disc <= MAX_REALISTIC_DISC:
                        link = base_domain
                        if is_noon and (node.get("sku") or node.get("catalog_sku")):
                            sku = node.get("sku") or node.get("catalog_sku")
                            slug = node.get("url") or "deal"
                            link = f"https://www.noon.com/egypt-ar/{slug.strip('/')}/{sku}/p/"
                        else:
                            for k in _LINK_KEYS:
                                lv = node.get(k)
                                if isinstance(lv, str) and lv.strip():
                                    lv = lv.strip()
                                    if k == "url_key":
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

# --- استخراج المنتجات من HTML لجميع المتاجر ---
def extract_html_deals(html_text: str, page_url: str, base_domain: str, is_amazon: bool = False, is_noon: bool = False) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")

    # 1. استخراج أمازون مصر بدقة
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

    # 2. فحص كل سكربتات JSON المخفية في الصفحة (Next.js / Nuxt / LD+JSON)
    for s in soup.find_all("script"):
        txt = (s.string or s.get_text() or "").strip()
        if len(txt) < 100:
            continue
        if s.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__") or "json" in (s.get("type") or "").lower():
            try:
                deals.extend(extract_json_tree(json.loads(txt), base_domain, is_noon=is_noon))
            except Exception:
                pass

    # 3. فحص كروت الـ HTML القياسية (2B, رنين، دريم 2000، جوميا، بي تك، كنز، راية)
    cards = soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|product-card|productCard|prd|grid-item|product-layout|plp-product-card|card|item", re.I))
    for card in cards:
        a = card.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        link = urllib.parse.urljoin(base_domain, href)

        t_el = card.find(["h2", "h3", "h4", "strong", "a"], class_=re.compile(r"name|title|link", re.I)) or a
        title = clean_title(t_el.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6 or any(w in title.lower() for w in INSTALLMENT_WORDS):
            continue

        sp_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I)) or card.find(["del", "s"])

        cur = parse_price(sp_el.get_text()) if sp_el else 0
        old = parse_price(old_el.get_text()) if old_el else 0

        if not cur or not old:
            nums = []
            for pt in card.find_all(class_=re.compile(r"price|prc", re.I)):
                v = parse_price(pt.get_text())
                if v >= MIN_PRODUCT_PRICE and v not in nums:
                    nums.append(v)
            if len(nums) >= 2:
                cur, old = min(nums), max(nums)
            elif len(nums) == 1:
                cur = nums[0]

        # حساب السعر القديم من شارة الخصم المئوية لو لم يكن مكتوباً
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
     "urls": ["https://www.jumia.com.eg/ar/flash-sales/", "https://www.jumia.com.eg/ar/electronics/?sort=discount"]},
    {"id": "raya", "name": "راية شوب (Raya Shop)", "domain": "https://www.rayashop.com",
     "urls": ["https://www.rayashop.com/ar/deals", "https://www.rayashop.com/ar/mobiles-tablets"]},
    {"id": "btech", "name": "بي تك (B.Tech)", "domain": "https://btech.com",
     "urls": ["https://btech.com/ar/mobyilat.html", "https://btech.com/ar/tv-home-theater.html", "https://btech.com/ar/"]},
    {"id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg",
     "urls": ["https://2b.com.eg/ar/deals.html", "https://2b.com.eg/ar/computers.html"]},
    {"id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com",
     "urls": ["https://www.raneen.com/ar/deals", "https://www.raneen.com/ar/"]},
    {"id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com",
     "urls": ["https://dream2000.com/mobiles.html", "https://dream2000.com/home-appliances.html"]},
    {"id": "kenzz", "name": "كنز (Kenzz)", "domain": "https://kenzz.com",
     "urls": ["https://kenzz.com/ar"]},
]

def load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")

def commit_files():
    try:
        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)
        for f in (STATE_FILE, HISTORY_FILE):
            if os.path.exists(f):
                subprocess.run(["git", "add", f], check=False)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
            subprocess.run(["git", "commit", "-m", "chore: update deals memory [skip ci]"], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            subprocess.run(["git", "push"], check=False)
            log("💾 تم حفظ الذاكرة في GitHub بنجاح.")
    except Exception as e:
        log(f"⚠ خطأ حفظ الذاكرة: {e}")

def main():
    t0 = time.time()
    now = int(t0)
    log("🚀 بدء الفحص الشامل للمتاجر المصرية (V11)...")
    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    summary_lines = ["📊 <b>ملخص جولة الرادار (V11)</b>", ""]
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
                found_deals.extend(extract_html_deals(
                    html_txt, u, st["domain"],
                    is_amazon=bool(st.get("amazon")),
                    is_noon=bool(st.get("noon"))
                ))
            time.sleep(random.uniform(1.0, 2.0))

        # إزالة التكرار وترتيب العروض من الأقوى خصماً للأقل
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
            pid = f"{sid}|{norm_title(d['title'])}"
            prev_sent = state.get(pid)
            if prev_sent:
                age_h = (now - prev_sent.get("ts", 0)) / 3600.0
                if age_h < REALERT_HOURS and d["price"] >= prev_sent.get("price", d["price"]) * 0.95:
                    continue

            # تسجيل السعر في الذاكرة التاريخية
            h_rec = history.get(pid, {"prices": []})
            h_prices = h_rec.get("prices", [])
            status_tag = "🔥 <b>عرض مرصود</b>"
            if h_prices:
                min_prev = min(h_prices)
                if d["price"] < min_prev:
                    status_tag = "🏆 <b>أقل سعر تاريخي مسجل!</b>"
                elif d["price"] <= sum(h_prices) / len(h_prices) * 0.90:
                    status_tag = "✅ <b>خصم حقيقي مؤكد تاريخياً</b>"
            h_prices.append(d["price"])
            history[pid] = {"prices": h_prices[-30:], "ts": now}

            saving = d["old"] - d["price"]
            msg = (
                f"{status_tag} — {htmllib.escape(sname)}\n"
                f"📦 {htmllib.escape(d['title'])}\n"
                f"💰 السعر الحالي: <b>{d['price']:,} ج.م</b>\n"
                f"🏷 السعر السابق: {d['old']:,} ج.م (خصم {d['discount']}% — توفير {saving:,} ج.م)\n"
                f"🔗 {d['link']}"
            )
            if send_telegram(msg, use_html=True):
                state[pid] = {"price": d["price"], "ts": now}
                sent_store += 1
                total_sent += 1
                time.sleep(1.2)

        if opened:
            summary_lines.append(f"✅ {sname}: وُجد {len(ordered)} عرض | أُرسل {sent_store}")
        else:
            summary_lines.append(f"❌ {sname}: محجوب من سيرفر GitHub")

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    commit_files()

    elapsed = (time.time() - t0) / 60.0
    summary_lines.append(f"\n⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي المرسل: <b>{total_sent}</b>")
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
