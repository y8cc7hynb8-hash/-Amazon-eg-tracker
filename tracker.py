#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, random, html as htmllib, subprocess, traceback, urllib.parse
from collections import defaultdict

print("⚡ بدء تشغيل رادار الصفقات الاحترافي (V18.1 - تشغيل تلقائي منتظم)...", flush=True)

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

MIN_DISCOUNT_REGULAR = int(os.environ.get("MIN_DISCOUNT", 14))    # أقل خصم للسلع العادية
MIN_DISCOUNT_HIGH = 8                                             # أقل خصم للسلع الغالية (> 2000 ج.م)
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 100))
MAX_DEALS_AMAZON = 12                                             # حصة مضاعفة لأمازون مصر
MAX_DEALS_OTHER = int(os.environ.get("MAX_DEALS_PER_STORE", 6))
REALERT_HOURS = 48

STATE_FILE = "sent_deals.json"
HISTORY_FILE = "history.json"

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

INSTALLMENT_WORDS = (
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month",
    "تقسيط", "أقساط", "installment", "/mo", "فائدة", "بدون فوائد", "دفعة"
)

_AR = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR, "0123456789" * 2 + ".,,")
_BADGE_PCT_RE = re.compile(r"(?:خصم|وفر|off|save|-|\b)\s*(\d{1,2})\s*%", re.I)
_COUPON_RE = re.compile(r"(وفر\s+\d+[^<\n]{0,25}|خصم\s+\d+%?\s*مع\s*الكوبون|save\s+\d+[^<\n]{0,20}|كود\s*[:\s]\s*[A-Za-z0-9]+)", re.I)

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
            if r.status_code == 429:
                wait = int(r.json().get("parameters", {}).get("retry_after", 4))
                time.sleep(wait + 1)
                continue
            if r.status_code == 400 and use_html:
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", message)[:4000]
                use_html = False
                continue
            time.sleep(1.5)
        except Exception:
            time.sleep(1.5)
    return False

def parse_price(value) -> int:
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

def is_good_deal(cur: int, old: int, has_coupon: bool = False) -> bool:
    if cur < MIN_PRODUCT_PRICE or old <= cur:
        return False
    disc = round((old - cur) / old * 100)
    saving = old - cur
    if disc > MAX_REALISTIC_DISC:
        return False
    if has_coupon and disc >= 8:
        return True
    if cur >= 2000 and (disc >= MIN_DISCOUNT_HIGH or saving >= 250):
        return True
    return disc >= MIN_DISCOUNT_REGULAR

def is_blocked_page(text: str, status: int, is_amazon: bool = False) -> bool:
    if not text:
        return True
    min_len = 25000 if is_amazon else 12000
    if len(text) < min_len:
        if len(text) >= 3500 and ("/dp/" in text or "ج.م" in text or "جنيه" in text):
            pass
        else:
            return True

    if status in (403, 429, 503) and len(text) < 40000:
        return True

    head = text[:8000].lower()
    captcha_markers = (
        "validatecaptcha", "api-services-support@amazon.com", "enter the characters you see below",
        "أدخل الأحرف التي تراها", "لست روبوت", "cf-browser-verification", "vercel security checkpoint"
    )
    if any(c in head for c in captcha_markers):
        return True

    if is_amazon and ("data-asin" not in text and "/dp/" not in text):
        return True

    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    block_titles = ("just a moment", "attention required", "access denied", "403 forbidden",
                    "security checkpoint", "robot check", "one more step", "sorry! something went wrong")
    return any(b in title for b in block_titles)

def smart_fetch(url: str, store_name: str, is_amazon: bool = False) -> str:
    log(f"🔍 فحص [{store_name}]: {url}")
    base_headers = {
        "Accept-Language": AR_LANG,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.google.com.eg/",
        "Upgrade-Insecure-Requests": "1"
    }
    if is_amazon:
        base_headers["Cookie"] = "i18n-prefs=EGP; lc-acbamo=ar_AE"

    if HAS_CFFI:
        fps = ("safari17_0", "chrome120", "chrome116", "edge101") if is_amazon else ("chrome120", "safari17_0")
        for fp in fps:
            try:
                sess = cffi_requests.Session(impersonate=fp)
                r = sess.get(url, headers=base_headers, timeout=20)
                if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                    log(f"   ✅ فتح مباشر ({fp}) - {len(r.text)//1024}KB")
                    return r.text
                else:
                    log(f"   ⚠ تخطي استجابة محجوبة/قصيرة ({fp}: {len(r.text)//1024}KB)")
            except Exception:
                pass

    if scraper:
        try:
            r = scraper.get(url, headers=base_headers, timeout=20)
            if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                log(f"   ✅ فتح Cloudscraper - {len(r.text)//1024}KB")
                return r.text
        except Exception:
            pass

    enc = urllib.parse.quote(url, safe="")
    proxies = [
        ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
        ("AllOrigins-JSON", f"https://api.allorigins.win/get?url={enc}"),
        ("AllOrigins-Raw", f"https://api.allorigins.win/raw?url={enc}"),
        ("JinaReader-HTML", f"https://r.jina.ai/{url}"),
        ("JinaReader-Text", f"https://r.jina.ai/{url}")
    ]
    for pname, purl in proxies:
        try:
            h = {"User-Agent": UA_CHROME, "Accept-Language": AR_LANG}
            if pname == "JinaReader-HTML":
                h["X-Return-Format"] = "html"
                h["X-With-Links-Summary"] = "true"
            r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=32) if HAS_CFFI else requests.get(purl, headers=h, timeout=32)
            if r.status_code != 200 or not r.text:
                continue
            body = r.text
            if pname == "AllOrigins-JSON":
                try:
                    body = json.loads(body).get("contents") or ""
                except Exception:
                    continue
            if not is_blocked_page(body, 200, is_amazon=is_amazon):
                log(f"   🔓 تم التخطي بنجاح عبر ({pname}) - {len(body)//1024}KB")
                return body
        except Exception:
            continue

    log(f"   ⛔ تعذر فتح الرابط في [{store_name}] بعد كل المحاولات")
    return ""

# ================= محركات الاستخراج =================

def extract_amazon_deals(html_text: str, history: dict) -> list:
    deals = []
    seen_asins = set()
    soup = BeautifulSoup(html_text, "html.parser")

    cards = soup.select('div[data-component-type="s-search-result"], div[data-asin]')
    for div in cards:
        asin = (div.get("data-asin") or "").strip()
        if len(asin) != 10 or asin in seen_asins:
            continue
        h2 = div.find(["h2", "h3"]) or div.find("span", class_=re.compile(r"a-text-normal", re.I))
        title = clean_title(h2.get_text(" ", strip=True)) if h2 else ""
        if len(title) < 6:
            img = div.find("img", alt=True)
            if img:
                title = clean_title(img["alt"])
        if len(title) < 6:
            continue

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

        card_text = div.get_text(" ", strip=True)
        if not cur or not old:
            clean_txt = re.sub(r"[.,]\d{1,2}(?!\d)", " ", card_text.translate(_TRANS))
            p_matches = re.findall(r"(?:(\d{1,3}(?:,\d{3})+|\d{3,6})\s*(?:ج\.?م|جنيه|EGP)|(?:ج\.?م|جنيه|EGP)\s*(\d{1,3}(?:,\d{3})+|\d{3,6}))", clean_txt, re.I)
            nums = []
            for m1, m2 in p_matches:
                v = int((m1 or m2).replace(",", ""))
                if MIN_PRODUCT_PRICE <= v <= 400000 and v not in nums:
                    nums.append(v)
            if len(nums) >= 2:
                cur, old = min(nums[:2]), max(nums[:2])
            elif len(nums) == 1 and not cur:
                cur = nums[0]

        if cur < MIN_PRODUCT_PRICE:
            continue

        if not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if 10 <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        m_coup = _COUPON_RE.search(card_text)
        coupon_txt = m_coup.group(1).strip() if m_coup else ""

        link = f"https://www.amazon.eg/dp/{asin}"
        prev_price = history.get(link, 0)
        if not old and prev_price > cur:
            old = prev_price
        history[link] = cur

        if is_good_deal(cur, old, bool(coupon_txt)):
            seen_asins.add(asin)
            disc = round((old - cur) / old * 100)
            deals.append({
                "title": title, "price": cur, "old": old,
                "discount": disc, "link": link, "coupon": coupon_txt
            })

    if not deals:
        for a in soup.find_all("a", href=re.compile(r"/dp/[A-Z0-9]{10}")):
            m_asin = re.search(r"/dp/([A-Z0-9]{10})", a["href"])
            if not m_asin:
                continue
            asin = m_asin.group(1)
            if asin in seen_asins:
                continue
            card = a
            for _ in range(4):
                if card.parent and len(card.get_text(" ", strip=True)) < 60:
                    card = card.parent
                else:
                    break
            c_txt = card.get_text(" ", strip=True)
            title = clean_title(a.get_text(" ", strip=True) or a.get("title") or "")
            if len(title) < 8:
                continue
            clean_txt = re.sub(r"[.,]\d{1,2}(?!\d)", " ", c_txt.translate(_TRANS))
            p_matches = re.findall(r"(?:(\d{1,3}(?:,\d{3})+|\d{3,6})\s*(?:ج\.?م|جنيه|EGP)|(?:ج\.?م|جنيه|EGP)\s*(\d{1,3}(?:,\d{3})+|\d{3,6}))", clean_txt, re.I)
            nums = []
            for m1, m2 in p_matches:
                v = int((m1 or m2).replace(",", ""))
                if MIN_PRODUCT_PRICE <= v <= 400000 and v not in nums:
                    nums.append(v)
            if len(nums) >= 2:
                cur, old = min(nums[:2]), max(nums[:2])
                if is_good_deal(cur, old):
                    seen_asins.add(asin)
                    disc = round((old - cur) / old * 100)
                    deals.append({
                        "title": title, "price": cur, "old": old,
                        "discount": disc, "link": f"https://www.amazon.eg/dp/{asin}", "coupon": ""
                    })

    return deals

def extract_noon_deals(html_text: str, history: dict) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            data = json.loads(next_script.string)
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    sku = node.get("sku") or node.get("catalog_sku")
                    name = node.get("name") or node.get("title")
                    if sku and isinstance(name, str) and len(name) >= 6:
                        p_sale = parse_price(node.get("sale_price"))
                        p_reg = parse_price(node.get("price"))
                        cur = p_sale if p_sale > 0 else p_reg
                        old = p_reg if (p_sale > 0 and p_reg > p_sale) else 0
                        slug = node.get("url") or "deal"
                        link = f"https://www.noon.com/egypt-ar/{str(slug).strip('/')}/{sku}/p/"
                        if not old and history.get(link, 0) > cur:
                            old = history[link]
                        if cur >= MIN_PRODUCT_PRICE:
                            history[link] = cur
                        if is_good_deal(cur, old):
                            disc = round((old - cur) / old * 100)
                            deals.append({
                                "title": clean_title(name), "price": cur, "old": old,
                                "discount": disc, "link": link, "coupon": ""
                            })
                    for v in node.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(node, list):
                    for v in node:
                        if isinstance(v, (dict, list)):
                            stack.append(v)
        except Exception:
            pass

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/p/" not in href:
            continue
        link = urllib.parse.urljoin("https://www.noon.com", href).split("?")[0]
        card_text = a.get_text(" ", strip=True)
        if any(w in card_text.lower() for w in INSTALLMENT_WORDS):
            continue

        title_el = a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)}) or a.find(["h2", "h3"])
        title = clean_title(title_el.get_text(" ", strip=True) if title_el else a.get("title") or "")
        if len(title) < 6:
            img = a.find("img", alt=True)
            if img and len(img["alt"].strip()) >= 6:
                title = clean_title(img["alt"])
        if len(title) < 6:
            continue

        cur = old = 0
        p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
        p_was = a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)}) or a.find(class_=re.compile(r"priceWas|oldPrice|strike", re.I))

        if p_now:
            cur = parse_price(p_now.get_text())
        if p_was:
            old = parse_price(p_was.get_text())

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if 10 <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        if not old and cur >= MIN_PRODUCT_PRICE and history.get(link, 0) > cur:
            old = history[link]
        if cur >= MIN_PRODUCT_PRICE:
            history[link] = cur

        if is_good_deal(cur, old):
            disc = round((old - cur) / old * 100)
            deals.append({
                "title": title, "price": cur, "old": old,
                "discount": disc, "link": link, "coupon": ""
            })
    return deals

def extract_general_deals(html_text: str, base_domain: str, history: dict) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|productCard|prd|product-layout|plp-product-card|product-card|grid-item", re.I))
    for card in cards:
        for inst in card.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara", re.I)):
            inst.decompose()

        a = card.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        link = urllib.parse.urljoin(base_domain, href).split("?")[0]

        t_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I)) or a
        title = clean_title(t_el.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6 or any(w in title.lower() for w in INSTALLMENT_WORDS):
            continue

        sp_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I)) or card.find(["del", "s", "strike"])

        cur = parse_price(sp_el.get_text()) if sp_el else 0
        old = parse_price(old_el.get_text()) if old_el else 0

        if not cur:
            for pt in card.find_all(class_=re.compile(r"price", re.I)):
                v = parse_price(pt.get_text())
                if v >= MIN_PRODUCT_PRICE:
                    cur = v
                    break

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card.get_text(" ", strip=True).translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if 10 <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))

        if not old and cur >= MIN_PRODUCT_PRICE and history.get(link, 0) > cur:
            old = history[link]
        if cur >= MIN_PRODUCT_PRICE:
            history[link] = cur

        if is_good_deal(cur, old):
            disc = round((old - cur) / old * 100)
            deals.append({
                "title": title, "price": cur, "old": old,
                "discount": disc, "link": link, "coupon": ""
            })
    return deals

# ================= بنك الروابط المتجدد =================
def build_dynamic_stores(run_seed: int) -> list:
    amz_pages = [1, 2]
    p1 = amz_pages[run_seed % len(amz_pages)]
    p2 = amz_pages[(run_seed + 1) % len(amz_pages)]

    amazon_guaranteed = [
        "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
        "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
        "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE",
    ]
    amazon_extra = [
        f"https://www.amazon.eg/s?i=electronics&rh=p_n_pct-off-with-tax%3A15-&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=kitchen&rh=p_n_pct-off-with-tax%3A15-&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=appliances&rh=p_n_pct-off-with-tax%3A10-&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=grocery&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=beauty&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
    ]
    random.shuffle(amazon_extra)

    noon_pool = [
        f"https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/appliances/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/beauty-and-health/?sort[by]=discount&sort[dir]=desc&page={p2}",
        f"https://www.noon.com/egypt-ar/grocery-store/?sort[by]=discount&sort[dir]=desc&page={p1}",
    ]
    random.shuffle(noon_pool)

    return [
        {
            "id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg",
            "type": "amazon", "max_deals": MAX_DEALS_AMAZON,
            "urls": amazon_guaranteed + amazon_extra[:3]
        },
        {
            "id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com",
            "type": "noon", "max_deals": MAX_DEALS_OTHER,
            "urls": noon_pool[:4]
        },
        {
            "id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                f"https://2b.com.eg/ar/deals.html?p={p1}",
                f"https://2b.com.eg/ar/computers.html?p={p1}",
                f"https://2b.com.eg/ar/mobiles-tablets.html?p={p1}",
                f"https://2b.com.eg/ar/home-appliances.html?p={p1}"
            ]
        },
        {
            "id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                f"https://www.raneen.com/ar/deals?p={p1}",
                "https://www.raneen.com/ar/deals/appliances-electronics-offers",
                "https://www.raneen.com/ar/mobiles-tablets"
            ]
        },
        {
            "id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                f"https://dream2000.com/mobiles.html?p={p1}",
                f"https://dream2000.com/home-appliances.html?p={p1}"
            ]
        }
    ]

def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")

def commit_state():
    try:
        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)
        for f in (STATE_FILE, HISTORY_FILE):
            if os.path.exists(f):
                subprocess.run(["git", "add", f], check=False)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
            subprocess.run(["git", "commit", "-m", "chore: update radar memory"], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            subprocess.run(["git", "push"], check=False)
            log("💾 تم حفظ الذاكرة في GitHub بنجاح.")
    except Exception as e:
        log(f"⚠ خطأ حفظ الذاكرة: {e}")

def main():
    t0 = time.time()
    now = int(t0)
    run_seed = int(now // 1800)
    log(f"🚀 بدأ الرادار المتجدد (V18.1) - دورة التدوير #{run_seed % 10}...")

    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)

    state = {k: v for k, v in state.items() if isinstance(v, dict) and (now - v.get("ts", 0)) < REALERT_HOURS * 3600}

    stores = build_dynamic_stores(run_seed)
    summary_lines = ["📊 <b>ملخص جولة الصفقات المتجددة (V18.1)</b>", ""]
    total_sent = 0

    for st in stores:
        sname = st["name"]
        stype = st["type"]
        max_store_deals = st.get("max_deals", MAX_DEALS_OTHER)
        is_amz = (stype == "amazon")
        found_deals = []
        opened = False

        for u in st["urls"]:
            html_txt = smart_fetch(u, sname, is_amazon=is_amz)
            if html_txt:
                opened = True
                if is_amz:
                    extracted = extract_amazon_deals(html_txt, history)
                elif stype == "noon":
                    extracted = extract_noon_deals(html_txt, history)
                else:
                    extracted = extract_general_deals(html_txt, st["domain"], history)
                log(f"   📦 استُخرج {len(extracted)} عرض من الرابط")
                found_deals.extend(extracted)
            time.sleep(random.uniform(1
