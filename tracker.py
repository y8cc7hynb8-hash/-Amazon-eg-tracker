#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, random, html as htmllib, subprocess, urllib.parse

print("⚡ بدء تشغيل رادار العروض المباشرة مع الحفظ الدائم (V16)...", flush=True)

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

MIN_DISCOUNT = int(os.environ.get("MIN_DISCOUNT", 15))           # أقل خصم معلن
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75)) # منع الأقساط والنسب الخيالية
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 150)) # استبعاد الإكسسوارات الزهيدة
MAX_DEALS_PER_STORE = int(os.environ.get("MAX_DEALS_PER_STORE", 8)) # إرسال حتى 8 عروض قوية من كل متجر
REALERT_DAYS = 5                                                 # منع تكرار نفس العرض لمدة 5 أيام

STATE_FILE = "sent_deals.json"
UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

INSTALLMENT_WORDS = (
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month",
    "تقسيط", "أقساط", "installment", "/mo", "فائدة", "بدون فوائد", "دفعة"
)

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
                elif not is_blocked_page(r.text, r.status_code):
                    log(f"   ✅ فتح مباشر ({fp}) - {len(r.text)//1024}KB")
                    return r.text
            except Exception:
                pass

    if scraper and not expect_json:
        try:
            r = scraper.get(url, headers=headers, timeout=20)
            if not is_blocked_page(r.text, r.status_code):
                log(f"   ✅ فتح Cloudscraper - {len(r.text)//1024}KB")
                return r.text
        except Exception:
            pass

    # بوابات وسيطة بديلة
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
            r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=28) if HAS_CFFI else requests.get(purl, headers=h, timeout=28)
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
            elif not is_blocked_page(body, 200):
                log(f"   🔓 تم التخطي عبر ({pname}) - {len(body)//1024}KB")
                return body
        except Exception:
            continue

    log(f"   ⛔ تعذر فتح الرابط في [{store_name}]")
    return ""

# ================= استخراج صفقات المتاجر =================

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
        p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
        p_was = a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)}) or a.find(class_=re.compile(r"priceWas|oldPrice", re.I)) or a.find("span", class_=re.compile(r"strike", re.I))

        if p_now:
            cur = parse_price(p_now.get_text())
        if p_was:
            old = parse_price(p_was.get_text())

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

def extract_general_deals(html_text: str, base_domain: str) -> list:
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|productCard|prd|product-layout|plp-product-card", re.I))
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

# ================= خريطة المتاجر =================
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
     ]},
    {"id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg", "type": "general",
     "urls": [
         "https://2b.com.eg/ar/deals.html",
         "https://2b.com.eg/ar/computers.html"
     ]},
    {"id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com", "type": "general",
     "urls": [
         "https://www.raneen.com/ar/deals"
     ]}
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
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")

def commit_state():
    """حفظ دائم للملفات في ريبو جيتهاب لعدم فقدان الذاكرة."""
    try:
        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)
        if os.path.exists(STATE_FILE):
            subprocess.run(["git", "add", STATE_FILE], check=False)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
            subprocess.run(["git", "commit", "-m", "chore: persist sent deals memory [skip ci]"], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            r = subprocess.run(["git", "push"], check=False)
            log("💾 تم حفظ الذاكرة في مستودع GitHub بنجاح لمنع التكرار.")
    except Exception as e:
        log(f"⚠ خطأ أثناء حفظ الذاكرة للريبو: {e}")

# ================= حلقة التشغيل الرئيسية =================
def main():
    t0 = time.time()
    now = int(t0)
    log("🚀 بدأ رادار الصفقات مع تفعيل الذاكرة الدائمة...")
    state = load_json(STATE_FILE)

    summary_lines = ["📊 <b>ملخص جولة الصفقات الحية</b>", ""]
    total_sent = 0

    for st in STORES:
        sname = st["name"]
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
                else:
                    extracted = extract_general_deals(html_txt, st["domain"])

                found_deals.extend(extracted)
            time.sleep(random.uniform(0.6, 1.2))

        # ترتيب العروض وتصفية المتكرر في نفس الصفحة
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

            # التحقق الدائم: الرابط النظيف للمنتج
            clean_link = d["link"].split("?")[0].rstrip("/").lower()
            prev = state.get(clean_link)

            # إذا تم إرساله من قبل خلال 5 أيام وبنفس السعر، يتم تخطيه
            if prev:
                age_days = (now - prev.get("ts", 0)) / 86400.0
                if age_days < REALERT_DAYS and d["price"] >= prev.get("price", d["price"]):
                    continue

            saving = d["old"] - d["price"]
            msg = (
                f"🔥 <b>عرض جديد من {htmllib.escape(sname)}</b> (خصم {d['discount']}%)\n\n"
                f"📦 {htmllib.escape(d['title'])}\n"
                f"💰 السعر الحالي: <b>{d['price']:,} ج.م</b>\n"
                f"🏷 السعر السابق: {d['old']:,} ج.م (توفير {saving:,} ج.م)\n"
                f"🔗 {d['link']}"
            )

            if send_telegram(msg, use_html=True):
                state[clean_link] = {"price": d["price"], "ts": now}
                sent_store += 1
                total_sent += 1
                time.sleep(1.2)

        if opened:
            summary_lines.append(f"✅ {sname}: وُجد {len(unique_deals)} عرض | أُرسل جديد {sent_store}")
        else:
            summary_lines.append(f"❌ {sname}: محجوب")

    save_json(STATE_FILE, state)
    commit_state()

    elapsed = (time.time() - t0) / 60.0
    summary_lines.append(f"\n⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي العروض الجديدة المرسلة: <b>{total_sent}</b>")
    summary_text = "\n".join(summary_lines)
    log("--- انتهت الجولة بنجاح ---\n" + summary_text)
    send_telegram(summary_text, use_html=True)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        send_telegram("❌ خطأ في تشغيل الرادار:\n" + tb[-1500:])
        sys.exit(1)
