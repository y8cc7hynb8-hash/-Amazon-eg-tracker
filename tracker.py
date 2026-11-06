#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار الصفقات المصري الاحترافي — V20 (الإصدار الشامل الذكي)
=============================================================
المميزات المدمجة:
1. صيد الهبوط الصامت للأسعار (Silent Price Drop) بدون اشتراط وجود سعر مشطوب.
2. مقارنة السوق اللحظية الحية (Live Cross-Store Arbitrage) لكشف أقل سعر في مصر.
3. التقييم التفاعلي الذكي عبر تليجرام (👍 / 👎) والتعلم الذاتي التلقائي.
4. فلترة صارمة للأقساط في دريم 2000 وبي تك ونون وأمازون.
5. حفظ دائم ومستمر للذاكرة والقواعد في مستودع GitHub.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

print("⚡ بدء تشغيل رادار الصفقات المتكامل والتعلم الذاتي (V20)...", flush=True)

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

try:
    import cloudscraper
    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
except ImportError:
    scraper = None

# ================= الإعدادات والمتغيرات =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MIN_DISCOUNT_REGULAR = int(os.environ.get("MIN_DISCOUNT", 14))      # أقل خصم للسلع العادية
MIN_DISCOUNT_HIGH = 8                                               # أقل خصم للسلع الغالية (> 2000 ج.م)
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))   # منع الخصومات الوهمية
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 100))   # استبعاد السلع الزهيدة جداً
MAX_DEALS_AMAZON = 12                                               # حصة أمازون القصوى
MAX_DEALS_OTHER = int(os.environ.get("MAX_DEALS_PER_STORE", 6))     # حصة باقي المتاجر
REALERT_HOURS = 48                                                  # مدة تبريد الرابط لمنع التكرار

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
RULES_FILE = os.path.join(DATA_DIR, "learned_rules.json")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

BASE_INSTALLMENT_WORDS = [
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "month",
    "تقسيط", "أقساط", "installment", "/mo", "فائدة", "بدون فوائد", "دفعة"
]

_AR = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR, "0123456789" * 2 + ".,,")
_BADGE_PCT_RE = re.compile(r"(?:خصم|وفر|off|save|-|\b)\s*(\d{1,2})\s*%", re.I)
_COUPON_RE = re.compile(r"(وفر\s+\d+[^<\n]{0,25}|خصم\s+\d+%?\s*مع\s*الكوبون|save\s+\d+[^<\n]{0,20}|كود\s*[:\s]\s*[A-Za-z0-9]+)", re.I)


def log(msg: str) -> None:
    print(msg, flush=True)


# ================= إدارة الملفات والذاكرة التراكمية =================

def load_json(path: str) -> dict:
    for p in (path, os.path.basename(path), os.path.join("data", os.path.basename(path))):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def save_json(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")


def commit_state() -> None:
    """حفظ دائم ومباشر للملفات في مستودع GitHub لمنع ضياع الذاكرة."""
    try:
        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)
        for f in (STATE_FILE, HISTORY_FILE, RULES_FILE):
            if os.path.exists(f):
                subprocess.run(["git", "add", f], check=False)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
            subprocess.run(["git", "commit", "-m", "chore: persist deals memory and learned rules"], check=False)
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            subprocess.run(["git", "push"], check=False)
            log("💾 تم حفظ الذاكرة والقواعد في GitHub بنجاح.")
    except Exception as e:
        log(f"⚠ خطأ أثناء حفظ الذاكرة: {e}")


# ================= محرك التعلم الذاتي والتفاعل مع تليجرام =================

def process_telegram_feedback(rules: dict, state: dict) -> None:
    """قراءة تقييمات الأزرار (👍 / 👎) من تليجرام وتعلم الكلمات المحظورة ذاتياً."""
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        offset = rules.get("last_update_id", 0) + 1
        r = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        if r.status_code != 200:
            return
        updates = r.json().get("result", [])
        blocked_words = set(rules.get("blocked_keywords", []))

        for u in updates:
            rules["last_update_id"] = u["update_id"]
            cb = u.get("callback_query")
            if not cb:
                continue

            cb_data = cb.get("data", "")
            cb_id = cb.get("id")

            if cb_data.startswith("bad:"):
                deal_hash = cb_data.split("bad:", 1)[1]
                deal_info = state.get(deal_hash, {})
                title = deal_info.get("title", "")

                words = [w for w in re.sub(r"[^\w\s]", " ", title.lower()).split() if len(w) >= 3]
                if words:
                    common_ignore = {"سماعة", "موبايل", "شاحن", "هاتف", "تلفزيون", "شاشة", "كابل", "جراب", "لابتوب"}
                    for w in words[:3]:
                        if w not in common_ignore:
                            blocked_words.add(w)

                log(f"🧠 تعلم ذاتي: تم استبعاد النمط المرتبط بـ [{title[:30]}]")
                ans_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
                requests.post(ans_url, json={"callback_query_id": cb_id, "text": "تم تسجيل الملاحظة وتدريب الرادار بنجاح ✅", "show_alert": False}, timeout=6)

            elif cb_data.startswith("good:"):
                ans_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
                requests.post(ans_url, json={"callback_query_id": cb_id, "text": "شكراً لتقييمك! تم تأكيد جودة الصفقة 🌟", "show_alert": False}, timeout=6)

        rules["blocked_keywords"] = list(blocked_words)
    except Exception as e:
        log(f"⚠ خطأ في معالجة تقييمات تليجرام: {e}")


def send_telegram_deal(message: str, deal_hash: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    # أزرار التقييم الفورية المرفقة أسفل كل صفقة
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "👍 صفقة ممتازة", "callback_data": f"good:{deal_hash}"},
                {"text": "👎 قسط / سعر وهمي", "callback_data": f"bad:{deal_hash}"}
            ]
        ]
    }

    payload = {
        "chat_id": CHAT_ID,
        "text": message[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": keyboard
    }
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                wait = int(r.json().get("parameters", {}).get("retry_after", 4))
                time.sleep(wait + 1)
                continue
            time.sleep(1.2)
        except Exception:
            time.sleep(1.2)
    return False


def send_telegram_summary(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message[:4000], "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception:
        pass
    return True


# ================= معالجة الأسعار والنصوص =================

def parse_price(value: Any, custom_blocked: Set[str]) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value)) if 50 <= value <= 500000 else 0
    text = str(value)
    all_blocked = BASE_INSTALLMENT_WORDS + list(custom_blocked)
    if any(w in text.lower() for w in all_blocked):
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
    return re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)[:180]


def norm_title(title: str) -> str:
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    stop = {"عرض", "خصم", "جديد", "أصلي", "ضمان", "مصر", "سنة", "new", "egypt", "with", "and"}
    words = [w for w in clean.split() if len(w) > 1 and w not in stop]
    return " ".join(words[:8])


# ================= محرك تقييم نوع الصفقة (المعلن + الصامت + فجوة السوق) =================

def check_deal_type(cur: int, old: int, prev_price: int, market_lowest_other: int, has_coupon: bool = False) -> Tuple[bool, str, int, int]:
    """
    يحدد نوع العرض بدقة:
    1. خصم معلن (سعر مشطوب).
    2. هبوط سعر صامت (مقارنة بآخر سعر مسجل لنفس المنتج بدون إعلان خصم).
    3. فجوة السوق (أرخص من نفس الموديل في باقي المتاجر بوضوح).
    يرجع: (هل العرض معتمد؟، نوع العرض، السعر المرجعي، نسبة التوفير %)
    """
    if cur < MIN_PRODUCT_PRICE:
        return (False, "", 0, 0)

    # 1. الخصم التقليدي المعلن (سعر مشطوب)
    if old > cur:
        disc = round((old - cur) / old * 100)
        saving = old - cur
        if disc <= MAX_REALISTIC_DISC:
            if (has_coupon and disc >= 8) or (cur >= 2000 and (disc >= MIN_DISCOUNT_HIGH or saving >= 250)) or (disc >= MIN_DISCOUNT_REGULAR):
                return (True, "خصم معلن", old, disc)

    # 2. الهبوط الصامت (السعر انخفض مقارنة بالجولات السابقة دون خط مشطوب)
    if prev_price > cur:
        drop_pct = round((prev_price - cur) / prev_price * 100)
        saving = prev_price - cur
        if 8 <= drop_pct <= 60 and (saving >= 200 or drop_pct >= 12):
            return (True, "هبوط سعر صامت", prev_price, drop_pct)

    # 3. فجوة السوق الحية (أرخص سعر لنفس الموديل بين المتاجر المنافسة)
    if market_lowest_other > cur * 1.08:
        gap_pct = round((market_lowest_other - cur) / market_lowest_other * 100)
        saving = market_lowest_other - cur
        if gap_pct >= 10 and saving >= 200:
            return (True, "أرخص من السوق", market_lowest_other, gap_pct)

    return (False, "", 0, 0)


# ================= جلب الصفحات وتخطي الحظر =================

def is_blocked_page(text: str, status: int, is_amazon: bool = False) -> bool:
    if not text:
        return True
    min_len = 25000 if is_amazon else 12000
    if len(text) < min_len and not (len(text) >= 3500 and ("/dp/" in text or "ج.م" in text or "جنيه" in text)):
        return True
    if status in (403, 429, 503) and len(text) < 40000:
        return True

    head = text[:8000].lower()
    if any(c in head for c in ("validatecaptcha", "api-services-support@amazon.com", "enter the characters you see below", "cf-browser-verification", "vercel security checkpoint")):
        return True
    if is_amazon and ("data-asin" not in text and "/dp/" not in text):
        return True

    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
    return any(b in title for b in ("just a moment", "attention required", "access denied", "403 forbidden", "security checkpoint", "robot check", "sorry! something went wrong"))


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
        ("JinaReader-HTML", f"https://r.jina.ai/{url}")
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
            body = json.loads(r.text).get("contents") or "" if pname == "AllOrigins-JSON" else r.text
            if not is_blocked_page(body, 200, is_amazon=is_amazon):
                log(f"   🔓 تم التخطي عبر ({pname}) - {len(body)//1024}KB")
                return body
        except Exception:
            continue

    log(f"   ⛔ تعذر فتح الرابط في [{store_name}]")
    return ""


# ================= استخراج المنتجات الشامل من المتاجر =================

def extract_amazon_items(html_text: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_asins = set()
    soup = BeautifulSoup(html_text, "html.parser")

    cards = soup.select('div[data-component-type="s-search-result"], div[data-asin]')
    for div in cards:
        asin = (div.get("data-asin") or "").strip()
        if len(asin) != 10 or asin in seen_asins:
            continue
        h2 = div.find(["h2", "h3"]) or div.find("span", class_=re.compile(r"a-text-normal", re.I))
        title = clean_title(h2.get_text(" ", strip=True)) if h2 else ""
        if len(title) < 6 and div.find("img", alt=True):
            title = clean_title(div.find("img", alt=True)["alt"])
        if len(title) < 6 or any(bad_w in title.lower() for bad_w in custom_blocked):
            continue

        cur = old = 0
        for p in div.select("span.a-price"):
            val = parse_price((p.select_one("span.a-offscreen") or p).get_text(" ", strip=True), custom_blocked)
            if p.get("data-a-strike") == "true" or "a-text-price" in (p.get("class") or []):
                old = old or val
            else:
                cur = cur or val
        if not cur and div.select_one("span.a-price-whole"):
            cur = parse_price(div.select_one("span.a-price-whole").get_text(" ", strip=True), custom_blocked)

        card_text = div.get_text(" ", strip=True)
        if not cur:
            clean_txt = re.sub(r"[.,]\d{1,2}(?!\d)", " ", card_text.translate(_TRANS))
            nums = []
            for m1, m2 in re.findall(r"(?:(\d{1,3}(?:,\d{3})+|\d{3,6})\s*(?:ج\.?م|جنيه|EGP)|(?:ج\.?م|جنيه|EGP)\s*(\d{1,3}(?:,\d{3})+|\d{3,6}))", clean_txt, re.I):
                v = int((m1 or m2).replace(",", ""))
                if MIN_PRODUCT_PRICE <= v <= 400000 and v not in nums:
                    nums.append(v)
            if len(nums) >= 2:
                cur, old = min(nums[:2]), max(nums[:2])
            elif len(nums) == 1:
                cur = nums[0]

        if cur < MIN_PRODUCT_PRICE:
            continue

        if not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct and 10 <= int(m_pct.group(1)) <= MAX_REALISTIC_DISC:
                old = int(round(cur / (1.0 - int(m_pct.group(1)) / 100.0)))

        m_coup = _COUPON_RE.search(card_text)
        coupon_txt = m_coup.group(1).strip() if m_coup else ""

        seen_asins.add(asin)
        items.append({
            "title": title, "price": cur, "old": old if old > cur else 0,
            "link": f"https://www.amazon.eg/dp/{asin}", "coupon": coupon_txt
        })
    return items


def extract_noon_items(html_text: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    # 1. الاستخراج من Next.js
    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            stack = [json.loads(next_script.string)]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    sku = node.get("sku") or node.get("catalog_sku")
                    name = node.get("name") or node.get("title")
                    if sku and isinstance(name, str) and len(name) >= 6:
                        p_sale, p_reg = parse_price(node.get("sale_price"), custom_blocked), parse_price(node.get("price"), custom_blocked)
                        cur = p_sale if p_sale > 0 else p_reg
                        old = p_reg if (p_sale > 0 and p_reg > p_sale) else 0
                        link = f"https://www.noon.com/egypt-ar/{str(node.get('url') or 'deal').strip('/')}/{sku}/p/"
                        if cur >= MIN_PRODUCT_PRICE and link not in seen_links:
                            seen_links.add(link)
                            items.append({"title": clean_title(name), "price": cur, "old": old, "link": link, "coupon": ""})
                    for v in node.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(node, list):
                    for v in node:
                        if isinstance(v, (dict, list)):
                            stack.append(v)
        except Exception:
            pass

    # 2. الاستخراج من كروت DOM
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/p/" not in href:
            continue
        link = urllib.parse.urljoin("https://www.noon.com", href).split("?")[0]
        if link in seen_links:
            continue
        card_text = a.get_text(" ", strip=True)
        if any(w in card_text.lower() for w in BASE_INSTALLMENT_WORDS) or any(w in card_text.lower() for w in custom_blocked):
            continue

        t_el = a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)}) or a.find(["h2", "h3"])
        title = clean_title(t_el.get_text(" ", strip=True) if t_el else a.get("title") or "")
        if len(title) < 6 and a.find("img", alt=True):
            title = clean_title(a.find("img", alt=True)["alt"])
        if len(title) < 6 or any(bad_w in title.lower() for bad_w in custom_blocked):
            continue

        p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
        p_was = a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)}) or a.find(class_=re.compile(r"priceWas|oldPrice|strike", re.I))
        cur = parse_price(p_now.get_text(), custom_blocked) if p_now else 0
        old = parse_price(p_was.get_text(), custom_blocked) if p_was else 0

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card_text.translate(_TRANS))
            if m_pct and 10 <= int(m_pct.group(1)) <= MAX_REALISTIC_DISC:
                old = int(round(cur / (1.0 - int(m_pct.group(1)) / 100.0)))

        if cur >= MIN_PRODUCT_PRICE:
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": ""})
    return items


def extract_general_items(html_text: str, base_domain: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    for card in soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|productCard|prd|product-layout|plp-product-card|product-card|grid-item", re.I)):
        for inst in card.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara", re.I)):
            inst.decompose()
        a = card.find("a", href=True)
        if not a or not a["href"].strip() or a["href"].startswith("#") or "javascript:" in a["href"]:
            continue
        link = urllib.parse.urljoin(base_domain, a["href"].strip()).split("?")[0]
        if link in seen_links:
            continue

        t_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I)) or a
        title = clean_title(t_el.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6 or any(w in title.lower() for w in BASE_INSTALLMENT_WORDS) or any(bad_w in title.lower() for bad_w in custom_blocked):
            continue

        sp_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I)) or card.find("ins")
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I)) or card.find(["del", "s", "strike"])
        cur = parse_price(sp_el.get_text(), custom_blocked) if sp_el else 0
        old = parse_price(old_el.get_text(), custom_blocked) if old_el else 0

        if not cur:
            for pt in card.find_all(class_=re.compile(r"price", re.I)):
                v = parse_price(pt.get_text(), custom_blocked)
                if v >= MIN_PRODUCT_PRICE:
                    cur = v
                    break

        if cur >= MIN_PRODUCT_PRICE and not old:
            m_pct = _BADGE_PCT_RE.search(card.get_text(" ", strip=True).translate(_TRANS))
            if m_pct and 10 <= int(m_pct.group(1)) <= MAX_REALISTIC_DISC:
                old = int(round(cur / (1.0 - int(m_pct.group(1)) / 100.0)))

        if cur >= MIN_PRODUCT_PRICE:
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": ""})
    return items


# ================= تدوير الروابط الذكي =================

def build_dynamic_stores(run_seed: int) -> List[dict]:
    p1, p2 = [1, 2][run_seed % 2], [1, 2][(run_seed + 1) % 2]
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
    ]
    random.shuffle(amazon_extra)

    noon_pool = [
        f"https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/appliances/?sort[by]=discount&sort[dir]=desc&page={p1}",
    ]
    random.shuffle(noon_pool)

    return [
        {"id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg", "type": "amazon", "max_deals": MAX_DEALS_AMAZON, "urls": amazon_guaranteed + amazon_extra[:3]},
        {"id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com", "type": "noon", "max_deals": MAX_DEALS_OTHER, "urls": noon_pool[:4]},
        {"id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg", "type": "general", "max_deals": MAX_DEALS_OTHER, "urls": [f"https://2b.com.eg/ar/deals.html?p={p1}", f"https://2b.com.eg/ar/computers.html?p={p1}", f"https://2b.com.eg/ar/mobiles-tablets.html?p={p1}"]},
        {"id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com", "type": "general", "max_deals": MAX_DEALS_OTHER, "urls": [f"https://www.raneen.com/ar/deals?p={p1}", "https://www.raneen.com/ar/deals/appliances-electronics-offers"]},
        {"id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com", "type": "general", "max_deals": MAX_DEALS_OTHER, "urls": [f"https://dream2000.com/mobiles.html?p={p1}", f"https://dream2000.com/home-appliances.html?p={p1}"]}
    ]


# ================= دورة التشغيل الرئيسية =================

def main() -> None:
    t0 = time.time()
    now = int(t0)
    run_seed = int(now // 1800)
    log(f"🚀 تشغيل الرادار المتكامل (V20) - دورة #{run_seed % 10}...")

    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)
    rules = load_json(RULES_FILE)

    # 1. فحص تقييماتك على تليجرام وتدريب الرادار ذاتياً قبل بدء الجولة
    process_telegram_feedback(rules, state)
    custom_blocked = set(rules.get("blocked_keywords", []))
    log(f"🧠 الفلتر الذاتي يحتوي على {len(custom_blocked)} كلمة محظورة تم تعلمها منك.")

    # تنظيف العروض القديمة التي مر عليها أكثر من مهلة التبريد
    state = {k: v for k, v in state.items() if isinstance(v, dict) and (now - v.get("ts", 0)) < REALERT_HOURS * 3600}

    stores = build_dynamic_stores(run_seed)
    all_raw_items: List[dict] = []
    store_status: Dict[str, Tuple[bool, int, int]] = {}
    total_sent = 0

    # 2. كشط السلع والأسعار من جميع المتاجر
    for st in stores:
        sname, sid, stype = st["name"], st["id"], st["type"]
        is_amz = (stype == "amazon")
        opened = False
        store_items = []

        for u in st["urls"]:
            html_txt = smart_fetch(u, sname, is_amazon=is_amz)
            if html_txt:
                opened = True
                if is_amz:
                    ext = extract_amazon_items(html_txt, custom_blocked)
                elif stype == "noon":
                    ext = extract_noon_items(html_txt, custom_blocked)
                else:
                    ext = extract_general_items(html_txt, st["domain"], custom_blocked)
                for item in ext:
                    item["store"] = sname
                    item["sid"] = sid
                store_items.extend(ext)
            time.sleep(random.uniform(1.0, 1.8))

        all_raw_items.extend(store_items)
        store_status[sid] = (opened, len(store_items), 0)

    # 3. بناء جدول مقارنة السوق اللحظي لكل موديل (Cross-Store Index)
    market_models = defaultdict(list)
    for it in all_raw_items:
        k = norm_title(it["title"])
        if len(k) >= 3:
            market_models[k].append((it["price"], it["store"]))

    # 4. تقييم وإرسال الصفقات (معلن + صامت + فجوة السوق)
    items_by_store = defaultdict(list)
    for it in all_raw_items:
        items_by_store[it["sid"]].append(it)

    for st in stores:
        sid = st["id"]
        max_deals = st.get("max_deals", MAX_DEALS_OTHER)
        raw_list = items_by_store.get(sid, [])
        sent_store = 0

        # إزالة التكرار للموديل في نفس المتجر واختيار السعر الأرخص
        unique_store_items = {}
        for it in raw_list:
            k = norm_title(it["title"])
            if k and (k not in unique_store_items or it["price"] < unique_store_items[k]["price"]):
                unique_store_items[k] = it

        for it in unique_store_items.values():
            if sent_store >= max_deals:
                break

            clean_link = it["link"].split("?")[0].rstrip("/").lower()
            deal_hash = hashlib.md5(clean_link.encode()).hexdigest()[:16]

            # السعر المسجل سابقاً لنفس المنتج في ذاكرة history
            prev_recorded = history.get(clean_link, 0)

            # أقل سعر لنفس الموديل في المتاجر الأخرى المنافسة
            k = norm_title(it["title"])
            other_prices = [p for p, st_name in market_models.get(k, []) if st_name != it["store"]]
            lowest_other = min(other_prices) if other_prices else 0

            is_deal, deal_type, ref_price, saving_pct = check_deal_type(
                cur=it["price"],
                old=it["old"],
                prev_price=prev_recorded,
                market_lowest_other=lowest_other,
                has_coupon=bool(it.get("coupon"))
            )

            # تحديث السعر في الذاكرة للجولات القادمة
            history[clean_link] = it["price"]

            if not is_deal:
                continue

            # تخطي إذا أُرسل حديثاً ولم ينزل السعر بنسبة إضافية (4%)
            prev_sent = state.get(deal_hash)
            if prev_sent and it["price"] >= prev_sent.get("price", it["price"]) * 0.96:
                continue

            # صياغة نص الرسالة الاحترافي حسب نوع التخفيض المكتشف
            saving = ref_price - it["price"]
            if deal_type == "هبوط سعر صامت":
                tag = f"📉 <b>هبوط مفاجئ في السعر — {htmllib.escape(it['store'])}</b>\n<i>(تخفيض صامت بدون شطب)</i>"
                ref_txt = f"🏷 كان يباع بـ: {ref_price:,} ج.م (نزل {saving_pct}% — وفر {saving:,} ج.م)"
            elif deal_type == "أرخص من السوق":
                tag = f"🏆 <b>أقل سعر في مصر لنفس الموديل — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"🏪 سعره في باقي المتاجر: {ref_price:,} ج.م (أرخص بـ {saving:,} ج.م)"
            else:
                tag = f"🔥 <b>عرض معلن قوي — {htmllib.escape(it['store'])}</b> (خصم {saving_pct}%)"
                ref_txt = f"🏷 السعر السابق: {ref_price:,} ج.م (توفير {saving:,} ج.م)"

            coupon_line = f"🎟 <b>كوبون إضافي: {htmllib.escape(it['coupon'])}</b>\n" if it.get("coupon") else ""
            msg = (
                f"{tag}\n\n"
                f"📦 {htmllib.escape(it['title'])}\n"
                f"💰 السعر الحالي: <b>{it['price']:,} ج.م</b>\n"
                f"{ref_txt}\n"
                f"{coupon_line}🔗 {it['link']}"
            )

            if send_telegram_deal(msg, deal_hash):
                state[deal_hash] = {"price": it["price"], "ts": now, "title": it["title"], "link": clean_link}
                sent_store += 1
                total_sent += 1
                time.sleep(1.2)

        opened, count, _ = store_status[sid]
        store_status[sid] = (opened, count, sent_store)

    # 5. تلخيص الجولة والحفظ التلقائي
    summary_lines = ["📊 <b>ملخص جولة الصفقات الشاملة (V20)</b>", ""]
    for st in stores:
        sid, sname = st["id"], st["name"]
        opened, count, sent = store_status[sid]
        if opened:
            summary_lines.append(f"✅ {sname}: فُحص {count} منتج | أُرسل جديد {sent}")
        else:
            summary_lines.append(f"❌ {sname}: تعذر الفتح في هذه الجولة")

    if len(history) > 10000:
        history = dict(list(history.items())[-10000:])

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    save_json(RULES_FILE, rules)
    commit_state()

    elapsed = (time.time() - t0) / 60.0
    summary_lines.append(f"\n⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي الصفقات المرسلة: <b>{total_sent}</b>")
    summary_text = "\n".join(summary_lines)
    log("--- انتهت الجولة بنجاح ---\n" + summary_text)
    send_telegram_summary(summary_text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        send_telegram_summary("❌ خطأ في تشغيل الرادار:\n" + tb[-1500:])
        sys.exit(1)
