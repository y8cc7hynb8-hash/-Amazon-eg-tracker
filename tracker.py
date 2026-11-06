#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار الصفقات المصري الاحترافي — V22 (Advanced Deal Intelligence Engine)
========================================================================
المواصفات المعمارية:
1. محرك هوية المنتج متعدد المستويات (Exact Model + Specs + Alphabetical Core).
2. ذاكرة أسعار زمنية [timestamp, price] لتحليل نافذة 30 و90 يوماً.
3. مؤشر ثقة من 5 عوامل مستقلة مع كشف رفع السعر الوهمي قبل الخصم.
4. تعلم ذاتي يعتمد على الكلمات الكاملة (Full Token Matching) ويدعم التقييم الإيجابي والسلبي.
5. عزل الأقساط عبر تفكيك عقد DOM فاسدة الكلاسات أو النصوص.
6. حماية Git من تعارضات Rebase + نسخ احتياطي تلقائي للملفات التالفة (.bak).
7. اختبارات ذاتية مدمجة (8 Unit Tests) تعمل تلقائياً قبل بدء الكشط.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import urllib.parse
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

print("⚡ بدء تشغيل محرك ذكاء الصفقات المصري (الإصدار V22)...", flush=True)

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

# ================= الإعدادات العامة =================
DAY = 86400
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MIN_DISCOUNT_REGULAR = int(os.environ.get("MIN_DISCOUNT", 14))
MIN_DISCOUNT_HIGH = 8
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 120))
MAX_DEALS_AMAZON = 12
MAX_DEALS_OTHER = int(os.environ.get("MAX_DEALS_PER_STORE", 6))
REALERT_HOURS = 48

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
RULES_FILE = os.path.join(DATA_DIR, "learned_rules.json")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

BASE_INSTALLMENT_WORDS = (
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "per month",
    "تقسيط", "أقساط", "اقساط", "installment", "/mo", "بدون فوائد", "دفعة مقدمة", "تمويل"
)

ACCESSORY_WORDS = {
    "جراب", "كفر", "اسكرينه", "اسكرينة", "لاصقه", "لاصقة", "حمايه", "حماية",
    "سوار", "استيك", "حامل", "ستاند", "شنطه", "شنطة", "حقيبه", "حقيبة",
    "كابل", "وصله", "وصلة", "محول", "ادابتر", "غطاء", "ماوس باد",
    "case", "cover", "protector", "glass", "strap", "band", "holder",
    "stand", "cable", "adapter", "pouch", "bag", "sleeve"
}

PROTECTED_BRANDS_AND_CORE = {
    "samsung", "سامسونج", "apple", "ابل", "iphone", "ايفون", "ipad", "ايباد",
    "xiaomi", "شاومي", "redmi", "ريدمي", "poco", "بوكو", "realme", "ريلمي",
    "oppo", "اوبو", "vivo", "فيفو", "infinix", "انفينكس", "tecno", "تكنو",
    "honor", "هونر", "huawei", "هواوي", "nokia", "نوكيا", "anker", "انكر",
    "soundcore", "ساوندكور", "oraimo", "اورايمو", "jbl", "sony", "سوني",
    "lg", "toshiba", "توشيبا", "sharp", "شارب", "tornado", "تورنيدو",
    "fresh", "فريش", "beko", "بيكو", "zanussi", "زانوسي", "kiriazi", "كريازي",
    "unionaire", "يونيون", "carrier", "كاريير", "midea", "ميديا", "haier", "هاير",
    "dell", "ديل", "hp", "lenovo", "لينوفو", "asus", "اسوس", "acer", "ايسر",
    "msi", "philips", "فيليبس", "braun", "براون", "tefal", "تيفال", "تروفال",
    "moulinex", "مولينكس", "kenwood", "كينوود", "black", "decker", "dyson",
    "karcher", "كارشر", "logitech", "razer", "redragon", "hyperx", "سماعه", "سماعة",
    "موبايل", "هاتف", "شاشه", "شاشة", "تلفزيون", "لابتوب", "غساله", "غسالة",
    "ثلاجه", "ثلاجة", "تكييف", "ميكروويف", "قلايه", "قلاية", "خلاط", "مكواه", "مكواة",
    "مكنسه", "مكنسة", "شاحن", "باور", "بانك", "ساعه", "ساعة", "ماكينه", "ماكينة",
    "حلاقه", "حلاقة", "بوتاجاز", "سخان", "مروحه", "مروحة", "كبه", "كبة"
}

_AR_DIGITS = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR_DIGITS, "0123456789" * 2 + ".,,")
_AR_NORM_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": ""})
_DIACRITICS_RE = re.compile(r"[\u064B-\u065F]")

_CUR_PRICE_RE = re.compile(
    r"(?:(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)"
    r"|(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)\s*(\d{1,3}(?:,\d{3})+|\d{2,6}))",
    re.I
)
_FOREIGN_CUR_RE = re.compile(r"(?:\$|€|£|\bUSD\b|\bSAR\b|\bAED\b|\bريال\b|\bدرهم\b|\bدولار\b)", re.I)
_COUPON_RE = re.compile(
    r"(وفر\s+\d+[^<\n]{0,25}|خصم\s+\d+%?\s*مع\s*الكوبون|save\s+\d+[^<\n]{0,20}|كود\s*[:\s]\s*[A-Za-z0-9]+)",
    re.I
)


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    s = _DIACRITICS_RE.sub("", text.lower().translate(_TRANS).translate(_AR_NORM_MAP))
    return re.sub(r"\s+", " ", s).strip()


def tokenize_words(text: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(text)))


# ================= إدارة الملفات وحماية Git من التعارض والتلف =================

def load_json(path: str) -> dict:
    for p in (path, os.path.basename(path), os.path.join("data", os.path.basename(path))):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                bak_path = p + f".bak.{int(time.time())}"
                try:
                    shutil.copy2(p, bak_path)
                    log(f"⚠ الملف {p} تالف ({type(e).__name__}) — تم حفظ نسخة احتياطية في {bak_path}")
                except Exception:
                    pass
    return {}


def save_json(path: str, data: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")
        return False


def commit_state(state: dict, history: dict, rules: dict) -> bool:
    """
    حفظ محصن ضد تعارضات Git:
    1. يضيف ملفات الذاكرة فقط دون أي ملفات عشوائية.
    2. يتحقق من نجاح git pull --rebase، وفي حال التعارض يلغي الـ rebase،
       يسحب أحدث فرع نظيف، يعيد كتابة الذاكرة من الرام، ثم يرفعها بأمان.
    """
    try:
        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)

        target_files = [f for f in (STATE_FILE, HISTORY_FILE, RULES_FILE) if os.path.exists(f)]
        if not target_files:
            return False

        subprocess.run(["git", "add", "--"] + target_files, check=False)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
            log("ℹ️ لا توجد تغييرات جديدة في الذاكرة لرفعها.")
            return True

        c_res = subprocess.run(
            ["git", "commit", "-m", "chore: update radar memory V22", "--only", "--"] + target_files,
            capture_output=True, text=True, check=False
        )
        if c_res.returncode != 0:
            log(f"⚠ فشل git commit: {c_res.stderr.strip()[:150]}")
            return False

        pull_res = subprocess.run(
            ["git", "pull", "--rebase", "-X", "theirs", "origin", "main"],
            capture_output=True, text=True, check=False
        )
        if pull_res.returncode != 0:
            log("⚠ تعارض أثناء git pull --rebase، جاري معالجة التعارض تلقائياً...")
            subprocess.run(["git", "rebase", "--abort"], capture_output=True, check=False)
            subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
            subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)
            save_json(STATE_FILE, state)
            save_json(HISTORY_FILE, history)
            save_json(RULES_FILE, rules)
            subprocess.run(["git", "add", "--"] + target_files, check=False)
            subprocess.run(["git", "commit", "-m", "chore: resolve and update radar memory V22"], capture_output=True, check=False)

        push_res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
        if push_res.returncode == 0:
            log("💾 تم حفظ الذاكرة والقواعد في GitHub بنجاح مؤكد.")
            return True
        log(f"⚠ فشل git push: {push_res.stderr.strip()[:180]}")
        return False
    except Exception as e:
        log(f"⚠ استثناء أثناء حفظ Git: {e}")
        return False


# ================= إرسال تليجرام الموثق + التعلم الذاتي (Self-Learning V3) =================

def _tg_post(endpoint: str, payload: dict) -> Optional[dict]:
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                body = r.json()
                if body.get("ok") is True:
                    return body
            if r.status_code == 429:
                wait = int(r.json().get("parameters", {}).get("retry_after", 4))
                time.sleep(wait + 1)
                continue
            if r.status_code == 400 and payload.get("parse_mode") == "HTML":
                payload = dict(payload)
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", payload.get("text", ""))[:4000]
                continue
            log(f"⚠ تليجرام أرجع كود {r.status_code}: {r.text[:100]}")
        except Exception as e:
            if attempt == 2:
                log(f"⚠ خطأ اتصال تليجرام: {e}")
        time.sleep(1.5)
    return None


def send_telegram_deal(message: str, deal_hash: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
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
    return _tg_post("sendMessage", payload) is not None


def send_telegram_summary(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    payload = {
        "chat_id": CHAT_ID,
        "text": message[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    return _tg_post("sendMessage", payload) is not None


def process_telegram_feedback(rules: dict, state: dict) -> None:
    """
    محرك التعلم الذاتي V3:
    1. يتأكد من عدم وجود Webhook يعطل getUpdates.
    2. يحافظ على الترتيب الزمني لقائمة العروض المحظورة (Ordered Dedup).
    3. يسجل التقييمات الإيجابية (👍) لرفع درجة ثقة الماركات والموديلات المفضلة.
    4. يستخدم مطابقة الكلمات الكاملة ويضع حداً أقصى لحجم قاموس Strikes.
    """
    if not BOT_TOKEN:
        return
    try:
        if not rules.get("webhook_cleared"):
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook", json={"drop_pending_updates": False}, timeout=6)
            rules["webhook_cleared"] = True

        offset = int(rules.get("last_update_id", 0)) + 1
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 4, "allowed_updates": json.dumps(["callback_query"])},
            timeout=10
        )
        if r.status_code != 200 or not r.json().get("ok"):
            return

        updates = r.json().get("result", [])
        blocked_deals: List[str] = list(rules.get("blocked_deals", []))
        word_strikes: Dict[str, int] = dict(rules.get("word_strikes", {}))
        blocked_keywords: List[str] = list(rules.get("blocked_keywords", []))
        liked_brands: Dict[str, int] = dict(rules.get("liked_brands", {}))

        for u in updates:
            rules["last_update_id"] = u["update_id"]
            cb = u.get("callback_query")
            if not cb:
                continue

            cb_data = cb.get("data", "")
            cb_id = cb.get("id")

            if cb_data.startswith("bad:"):
                deal_hash = cb_data.split("bad:", 1)[1]
                if deal_hash not in blocked_deals:
                    blocked_deals.append(deal_hash)

                deal_info = state.get(deal_hash, {})
                title = deal_info.get("title", "")

                for w in tokenize_words(title):
                    if len(w) >= 4 and not w.isdigit() and w not in PROTECTED_BRANDS_AND_CORE:
                        word_strikes[w] = word_strikes.get(w, 0) + 1
                        if word_strikes[w] >= 3 and w not in blocked_keywords:
                            blocked_keywords.append(w)

                log(f"🧠 تعلم ذاتي V3: حظر العرض [{title[:35]}] نهائياً.")
                _tg_post("answerCallbackQuery", {
                    "callback_query_id": cb_id,
                    "text": "تم حظر هذا العرض نهائياً وتحديث قواعد الفلترة ✅",
                    "show_alert": False
                })

            elif cb_data.startswith("good:"):
                deal_hash = cb_data.split("good:", 1)[1]
                deal_info = state.get(deal_hash, {})
                brand = deal_info.get("brand", "")
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) + 1
                _tg_post("answerCallbackQuery", {
                    "callback_query_id": cb_id,
                    "text": "تم تسجيل إعجابك! سيعطي الرادار أولوية لهذه الفئة 🌟",
                    "show_alert": False
                })

        # الحفاظ على الترتيب الزمني مع إزالة التكرار وتحديد السقف
        rules["blocked_deals"] = list(dict.fromkeys(blocked_deals))[-2000:]
        rules["blocked_keywords"] = list(dict.fromkeys(blocked_keywords))[-500:]
        if len(word_strikes) > 1500:
            sorted_ws = sorted(word_strikes.items(), key=lambda kv: kv[1], reverse=True)[:1000]
            word_strikes = dict(sorted_ws)
        rules["word_strikes"] = word_strikes
        rules["liked_brands"] = liked_brands
    except Exception as e:
        log(f"⚠ تعذر معالجة تقييمات تليجرام: {e}")


# ================= معالجة الأسعار وفلاتر الأقساط بالكلمات الكاملة =================

def has_installment_or_blocked(text: str, custom_blocked_tokens: Set[str]) -> bool:
    if not text:
        return False
    norm = normalize_text(text)
    for phrase in BASE_INSTALLMENT_WORDS:
        if normalize_text(phrase) in norm:
            return True
    if re.search(r"(\d+\s*شهر|شهريا|بدون مقدم|تقسيط|فائده\s*0)", norm):
        return True
    if custom_blocked_tokens:
        tokens = tokenize_words(norm)
        if tokens & custom_blocked_tokens:
            return True
    return False


def parse_price(value: Any, custom_blocked_tokens: Set[str], require_currency: bool = False) -> int:
    """
    قارئ أسعار محصن:
    1. يرفض الأسعار بعملات أجنبية (دولار/ريال/درهم) أو نصوص التقسيط.
    2. يعطي الأولوية للأرقام المقترنة بـ (ج.م / جنيه / EGP).
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        v = int(round(value))
        return v if 60 <= v <= 500000 else 0

    text = str(value)
    if _FOREIGN_CUR_RE.search(text) and not re.search(r"(ج\.?م|جنيه|EGP)", text, re.I):
        return 0
    if has_installment_or_blocked(text, custom_blocked_tokens):
        return 0

    text = text.translate(_TRANS).replace("\u00a0", " ")
    text_no_dec = re.sub(r"[.,]\d{1,2}(?!\d)", " ", text)

    m_cur = _CUR_PRICE_RE.search(text_no_dec)
    if m_cur:
        raw_num = (m_cur.group(1) or m_cur.group(2)).replace(",", "")
        v = int(raw_num)
        if 60 <= v <= 500000:
            return v

    if require_currency:
        return 0

    for m in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text_no_dec):
        v = int(m.replace(",", ""))
        if v in (2022, 2023, 2024, 2025, 2026) and "," not in m:
            continue
        if 60 <= v <= 500000:
            return v
    return 0


def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)[:185]


# ================= محرك هوية المنتج متعدد المستويات (Multi-Tier Identity) =================

_BRAND_ALIASES = {
    "سامسونج": "samsung", "ابل": "apple", "ايفون": "iphone", "ايباد": "ipad",
    "شاومي": "xiaomi", "ريدمي": "redmi", "بوكو": "poco", "ريلمي": "realme",
    "اوبو": "oppo", "فيفو": "vivo", "انفينكس": "infinix", "تكنو": "tecno",
    "هونر": "honor", "هواوي": "huawei", "نوكيا": "nokia", "انكر": "anker",
    "ساوندكور": "soundcore", "اورايمو": "oraimo", "سوني": "sony", "توشيبا": "toshiba",
    "شارب": "sharp", "تورنيدو": "tornado", "فريش": "fresh", "بيكو": "beko",
    "زانوسي": "zanussi", "كريازي": "kiriazi", "كاريير": "carrier", "ميديا": "midea",
    "هاير": "haier", "ديل": "dell", "لينوفو": "lenovo", "اسوس": "asus",
    "ايسر": "acer", "فيليبس": "philips", "براون": "braun", "تيفال": "tefal",
    "تروفال": "truval", "مولينكس": "moulinex", "كينوود": "kenwood", "كارشر": "karcher"
}

_TIER_WORDS = {"pro", "max", "ultra", "plus", "lite", "fe", "mini", "se", "note", "fold", "flip", "air", "5g", "4g"}
_AR_TIERS = {"برو": "pro", "ماكس": "max", "الترا": "ultra", "بلس": "plus", "لايت": "lite", "ميني": "mini", "نوت": "note"}


def is_accessory(title: str) -> bool:
    tokens = tokenize_words(title)
    norm = normalize_text(title)
    return bool(tokens & ACCESSORY_WORDS) or any(w in norm for w in ("ماوس باد", "screen protector"))


def analyze_product_identity(title: str) -> Dict[str, Any]:
    """
    يحلل اسم المنتج ويستخرج:
    - sig: البصمة الموحدة.
    - exact_model: True فقط إذا توفرت ماركة + رقم موديل صريح (مسموح له بالمقارنة عبر المتاجر).
    - brand: اسم الماركة الموحد.
    """
    t = normalize_text(title)
    for ar_b, en_b in _BRAND_ALIASES.items():
        t = re.sub(r"(?<!\w)" + re.escape(normalize_text(ar_b)) + r"(?!\w)", f" {en_b} ", t)
    for ar_t, en_t in _AR_TIERS.items():
        t = re.sub(r"(?<!\w)" + re.escape(normalize_text(ar_t)) + r"(?!\w)", f" {en_t} ", t)

    # دمج أرقام السلاسل الشهيرة مثل iphone 15 -> iphone15 و galaxy s 24 -> s24
    t = re.sub(r"\b(iphone|ipad|redmi|poco|note|series)\s+(\d{1,2}[a-z]?)\b", r"\1\2", t)
    t = re.sub(r"(\d+)\s*(?:جيجا\s*بايت|جيجابايت|جيجا|gb|g\.b)", r" \1gb ", t)
    t = re.sub(r"(\d+)\s*(?:تيرا\s*بايت|تيرابايت|تيرا|tb)", r" \1tb ", t)
    t = re.sub(r"(\d{2})\s*(?:بوصه|بوصة|انش|inch|inches|\"|'')", r" \1inch ", t)
    t = re.sub(r"\b([a-z]{1,4})[-_](\d{1,5}[a-z]{0,2})\b", r"\1\2", t)

    tokens = re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", t)
    acc_flag = "ACC" if is_accessory(t) else "DEV"

    brand = ""
    sizes_caps = sorted(set(re.findall(r"\b\d+(?:gb|tb|inch)\b", t)))
    tiers = sorted({tok for tok in tokens if tok in _TIER_WORDS})
    models = []
    core_words = []

    stop_words = {
        "عرض", "خصم", "جديد", "اصلي", "ضمان", "مصر", "سنه", "مع", "من", "في",
        "لون", "اسود", "ابيض", "ازرق", "ذهبي", "فضي", "رمادي", "شاشه", "موبايل",
        "هاتف", "تلفزيون", "سمارت", "ذكي", "new", "egypt", "with", "for", "and",
        "the", "black", "white", "blue", "smart", "tv", "mobile", "phone"
    }

    known_brands = set(_BRAND_ALIASES.values()) | {"hp", "lg", "jbl", "msi", "dyson", "razer", "redragon", "logitech"}

    for tok in tokens:
        if tok in known_brands:
            if not brand:
                brand = "apple" if tok in ("iphone", "ipad") else ("xiaomi" if tok in ("redmi", "poco") else tok)
            if re.search(r"\d", tok):
                models.append(tok)
            continue
        if re.fullmatch(r"\d+(?:gb|tb|inch)", tok) or tok in _TIER_WORDS or tok in stop_words:
            continue
        if len(tok) >= 2 and re.search(r"[a-z]", tok) and re.search(r"\d", tok):
            models.append(tok)
        elif len(tok) >= 2 and not tok.isdigit():
            core_words.append(tok)

    models = sorted(set(models))
    core_sorted = sorted(set(core_words))[:5]

    if brand and models:
        sig = f"{acc_flag}|{brand}|M:{'-'.join(models[:3])}|C:{'-'.join(sizes_caps)}|T:{'-'.join(tiers)}"
        return {"sig": sig, "exact_model": True, "brand": brand}

    sig = f"{acc_flag}|{brand}|W:{'-'.join(core_sorted)}|C:{'-'.join(sizes_caps)}|T:{'-'.join(tiers)}"
    return {"sig": sig, "exact_model": False, "brand": brand}


# ================= ذاكرة الأسعار الزمنية (30/90 Day Analytics) + التقييم الموزون =================

def record_and_analyze_history(history: dict, clean_link: str, cur_price: int, now: int) -> Dict[str, int]:
    """
    تخزين السعر مع الطابع الزمني [ts, price] وحساب مؤشرات 30 و90 يوماً:
    - min_30d: أقل سعر خلال 30 يوماً.
    - median_30d: الوسيط السعري خلال 30 يوماً.
    - min_90d: أقل سعر خلال 90 يوماً.
    - obs_count: عدد نقاط الرصد المستقلة.
    """
    rec = history.get(clean_link)
    pts: List[Tuple[int, int]] = []

    if isinstance(rec, dict):
        raw_pts = rec.get("pts") or rec.get("prices") or []
        for item in raw_pts:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                ts_v, p_v = int(item[0]), int(item[1])
                if p_v >= MIN_PRODUCT_PRICE:
                    pts.append((ts_v, p_v))
            elif isinstance(item, (int, float)) and item >= MIN_PRODUCT_PRICE:
                pts.append((now - DAY, int(item)))
    elif isinstance(rec, (int, float)) and rec >= MIN_PRODUCT_PRICE:
        pts.append((now - DAY, int(rec)))

    # تصفية النقاط الأقدم من 90 يوماً
    cutoff_90 = now - 90 * DAY
    cutoff_30 = now - 30 * DAY
    pts = [(ts, p) for ts, p in pts if ts >= cutoff_90]

    prices_30d = [p for ts, p in pts if ts >= cutoff_30]
    prices_90d = [p for _, p in pts]

    stats = {
        "min_30d": min(prices_30d) if prices_30d else 0,
        "median_30d": int(statistics.median(prices_30d)) if prices_30d else 0,
        "min_90d": min(prices_90d) if prices_90d else 0,
        "last_price": pts[-1][1] if pts else 0,
        "obs_count": len(pts)
    }

    # إضافة السعر الجديد إذا تغير السعر أو مر 12 ساعة على آخر نقطة
    if not pts or pts[-1][1] != cur_price or (now - pts[-1][0]) >= 12 * 3600:
        pts.append((now, cur_price))

    history[clean_link] = {"pts": [[ts, p] for ts, p in pts[-30:]], "ts": now}
    return stats


def evaluate_deal(
    cur: int,
    old: int,
    h_stats: Dict[str, int],
    market_lowest_other: int,
    exact_model: bool,
    has_coupon: bool = False,
    brand_liked: bool = False
) -> Tuple[bool, str, int, int, int]:
    """
    حساب التقييم الموزون للصفقة من 5 عوامل مستقلة:
    1. Historical Price Confidence (0..30)
    2. Market Comparison Confidence (0..25)
    3. Product Matching Confidence (0..15)
    4. Discount Authenticity Confidence (0..20)
    5. Coupon & Brand Preference Bonus (0..10)
    """
    if cur < MIN_PRODUCT_PRICE:
        return (False, "", 0, 0, 0)

    median_30d = h_stats.get("median_30d", 0)
    min_30d = h_stats.get("min_30d", 0)
    obs_count = h_stats.get("obs_count", 0)

    # كشف التلاعب (Fake Inflated Discount): إذا كان السعر الحالي أعلى من وسيط 30 يوماً بـ 4%
    if median_30d > 0 and cur > median_30d * 1.04:
        return (False, "", 0, 0, 0)

    # إذا كان هناك متجر منافس يبيع نفس الموديل المؤكد بسعر أرخص بـ 5%، نرفض العرض
    if exact_model and market_lowest_other > 0 and market_lowest_other < cur * 0.95:
        return (False, "", 0, 0, 0)

    # حساب العوامل الـ 5 المستقلة لدرجة الثقة
    c_hist = 12
    if obs_count >= 3 and min_30d > 0 and cur <= min_30d:
        c_hist = 30
    elif min_30d > 0 and cur <= min_30d:
        c_hist = 24
    elif median_30d > cur:
        c_hist = 20

    c_match = 15 if exact_model else 8

    c_market = 10
    if exact_model and cur * 1.06 <= market_lowest_other <= cur * 1.85:
        c_market = 25
    elif exact_model and market_lowest_other >= cur:
        c_market = 18

    c_extra = (6 if has_coupon else 0) + (4 if brand_liked else 0)

    # المسار 1: الهبوط الصامت المؤكد تاريخياً
    if median_30d > cur and (min_30d == 0 or cur <= min_30d):
        drop_pct = round((median_30d - cur) / median_30d * 100)
        saving = median_30d - cur
        if 8 <= drop_pct <= 60 and (saving >= 200 or drop_pct >= 12):
            c_auth = min(20, 10 + drop_pct // 2)
            total_conf = min(99, c_hist + c_match + c_market + c_auth + c_extra)
            return (True, "هبوط سعر صامت", median_30d, drop_pct, total_conf)

    # المسار 2: فجوة السوق اللحظية (يشترط تطابق رقم الموديل الصريح Exact Model)
    if exact_model and cur * 1.10 <= market_lowest_other <= cur * 1.85:
        gap_pct = round((market_lowest_other - cur) / market_lowest_other * 100)
        saving = market_lowest_other - cur
        if gap_pct >= 10 and saving >= 250:
            c_auth = min(20, 10 + gap_pct // 2)
            total_conf = min(98, c_hist + c_match + c_market + c_auth + c_extra)
            return (True, "أرخص من السوق", market_lowest_other, gap_pct, total_conf)

    # المسار 3: الخصم المعلن (مع فحص عدم المبالغة في السعر القديم مقارنة بالوسيط التاريخي)
    if old > cur:
        effective_old = old
        if median_30d > 0 and old > median_30d * 1.25:
            effective_old = int(median_30d * 1.10)
        if effective_old > cur:
            disc = round((effective_old - cur) / effective_old * 100)
            saving = effective_old - cur
            if disc <= MAX_REALISTIC_DISC:
                if (has_coupon and disc >= 8) or (cur >= 2000 and (disc >= MIN_DISCOUNT_HIGH or saving >= 250)) or (disc >= MIN_DISCOUNT_REGULAR):
                    c_auth = min(20, 8 + disc // 3)
                    total_conf = min(96, c_hist + c_match + c_market + c_auth + c_extra)
                    return (True, "خصم معلن", effective_old, disc, total_conf)

    return (False, "", 0, 0, 0)


# ================= الجلب الذكي وتجاوز الحظر =================

def is_blocked_page(text: str, status: int, is_amazon: bool = False) -> bool:
    if not text:
        return True
    if status in (403, 429, 503) and len(text) < 35000:
        return True

    head = text[:8000].lower()
    if any(c in head for c in ("validatecaptcha", "api-services-support@amazon.com", "enter the characters you see below", "cf-browser-verification", "vercel security checkpoint")):
        return True

    if is_amazon:
        if len(text) < 20000 and "/dp/" not in text:
            return True
        if "data-asin" not in text and "/dp/" not in text:
            return True
    elif len(text) < 3000:
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


# ================= محركات استخراج المنتجات (عزل الأقساط بالكلاس والنص) =================

def _strip_installment_nodes(container) -> None:
    """حذف أي عناصر فرعية تخص التقسيط سواء عبر اسم الكلاس أو محتوى النص القصير."""
    for bad_el in container.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara|bnpl|valu", re.I)):
        bad_el.decompose()
    for tag in container.find_all(["span", "div", "p", "small", "li"]):
        if not tag.find(True):
            txt = tag.get_text(" ", strip=True)
            if 0 < len(txt) <= 90 and has_installment_or_blocked(txt, set()):
                tag.decompose()


def extract_amazon_items(html_text: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_asins = set()
    soup = BeautifulSoup(html_text, "html.parser")

    cards = soup.select('div[data-component-type="s-search-result"], div[data-asin]')
    for div in cards:
        asin = (div.get("data-asin") or "").strip()
        if len(asin) != 10 or asin in seen_asins:
            continue

        _strip_installment_nodes(div)

        h2 = div.find(["h2", "h3"]) or div.find("span", class_=re.compile(r"a-text-normal", re.I))
        title = clean_title(h2.get_text(" ", strip=True)) if h2 else ""
        if len(title) < 6 and div.find("img", alt=True):
            title = clean_title(div.find("img", alt=True)["alt"])
        if len(title) < 6 or has_installment_or_blocked(title, custom_blocked):
            continue

        cur = old = 0
        for p in div.select("span.a-price"):
            p_txt = (p.select_one("span.a-offscreen") or p).get_text(" ", strip=True)
            parent_txt = p.parent.get_text(" ", strip=True) if p.parent else p_txt
            if has_installment_or_blocked(parent_txt, custom_blocked):
                continue
            val = parse_price(p_txt, custom_blocked)
            if p.get("data-a-strike") == "true" or "a-text-price" in (p.get("class") or []):
                old = old or val
            else:
                cur = cur or val

        if not cur and div.select_one("span.a-price-whole"):
            whole_el = div.select_one("span.a-price-whole")
            parent_txt = whole_el.parent.get_text(" ", strip=True) if whole_el.parent else ""
            if not has_installment_or_blocked(parent_txt, custom_blocked):
                cur = parse_price(whole_el.get_text(" ", strip=True), custom_blocked)

        if cur < MIN_PRODUCT_PRICE:
            continue

        card_text = div.get_text(" ", strip=True)
        m_coup = _COUPON_RE.search(card_text)
        coupon_txt = m_coup.group(1).strip() if m_coup else ""

        seen_asins.add(asin)
        items.append({
            "title": title,
            "price": cur,
            "old": old if old > cur else 0,
            "link": f"https://www.amazon.eg/dp/{asin}",
            "coupon": coupon_txt
        })
    return items


def extract_noon_items(html_text: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            stack = [json.loads(next_script.string)]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    sku = node.get("sku") or node.get("catalog_sku")
                    name = node.get("name") or node.get("title")
                    if sku and isinstance(name, str) and len(name) >= 6 and not has_installment_or_blocked(name, custom_blocked):
                        p_sale = parse_price(node.get("sale_price"), custom_blocked)
                        p_reg = parse_price(node.get("price"), custom_blocked)
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

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/p/" not in href:
            continue
        link = urllib.parse.urljoin("https://www.noon.com", href).split("?")[0]
        if link in seen_links:
            continue

        _strip_installment_nodes(a)

        t_el = a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)}) or a.find(["h2", "h3"])
        title = clean_title(t_el.get_text(" ", strip=True) if t_el else a.get("title") or "")
        if len(title) < 6 and a.find("img", alt=True):
            title = clean_title(a.find("img", alt=True)["alt"])
        if len(title) < 6 or has_installment_or_blocked(title, custom_blocked):
            continue

        p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
        p_was = a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)}) or a.find(class_=re.compile(r"priceWas|oldPrice|strike", re.I))

        cur = parse_price(p_now.get_text(" ", strip=True), custom_blocked) if p_now else 0
        old = parse_price(p_was.get_text(" ", strip=True), custom_blocked) if p_was else 0

        if cur >= MIN_PRODUCT_PRICE:
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": ""})
    return items


def extract_general_items(html_text: str, base_domain: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    cards = soup.find_all(
        ["li", "div", "article"],
        class_=re.compile(r"product-item|productCard|prd|product-layout|plp-product-card|product-card|grid-item", re.I)
    )
    for card in cards:
        _strip_installment_nodes(card)

        a = card.find("a", href=True)
        if not a or not a["href"].strip() or a["href"].startswith("#") or "javascript:" in a["href"]:
            continue
        link = urllib.parse.urljoin(base_domain, a["href"].strip()).split("?")[0]
        if link in seen_links:
            continue

        t_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I)) or a
        title = clean_title(t_el.get_text(" ", strip=True) or a.get("title") or "")
        if len(title) < 6 or has_installment_or_blocked(title, custom_blocked):
            continue

        sp_el = (
            card.find(attrs={"data-price-type": "finalPrice"})
            or card.find(class_=re.compile(r"special-price|finalPrice|price-new|prc|current", re.I))
            or card.find("ins")
        )
        old_el = (
            card.find(attrs={"data-price-type": "oldPrice"})
            or card.find(class_=re.compile(r"old-price|oldPrice|price-old|old|was|regular", re.I))
            or card.find(["del", "s", "strike"])
        )

        cur = parse_price(sp_el.get_text(" ", strip=True), custom_blocked) if sp_el else 0
        old = parse_price(old_el.get_text(" ", strip=True), custom_blocked) if old_el else 0

        if not cur:
            for pt in card.find_all(class_=re.compile(r"price", re.I)):
                v = parse_price(pt.get_text(" ", strip=True), custom_blocked, require_currency=True)
                if v >= MIN_PRODUCT_PRICE:
                    cur = v
                    break

        if cur >= MIN_PRODUCT_PRICE:
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": ""})
    return items


# ================= التدوير الذكي للصفحات والأقسام =================

def build_dynamic_stores(run_seed: int) -> List[dict]:
    pages = [1, 2, 3]
    p1 = pages[run_seed % 3]
    p2 = pages[(run_seed + 1) % 3]

    amazon_guaranteed = [
        f"https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
    ]
    amazon_extra = [
        f"https://www.amazon.eg/s?i=electronics&rh=p_n_pct-off-with-tax%3A15-&page={p2}&language=ar_AE",
        f"https://www.amazon.eg/s?i=kitchen&rh=p_n_pct-off-with-tax%3A15-&page={p2}&language=ar_AE",
        f"https://www.amazon.eg/s?i=appliances&rh=p_n_pct-off-with-tax%3A10-&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=grocery&rh=p_n_deal_type%3A26901098031&page={p2}&language=ar_AE",
        f"https://www.amazon.eg/s?i=beauty&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
    ]
    random.shuffle(amazon_extra)

    noon_pool = [
        f"https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc&page={p2}",
        f"https://www.noon.com/egypt-ar/appliances/?sort[by]=discount&sort[dir]=desc&page={p2}",
        f"https://www.noon.com/egypt-ar/beauty-and-health/?sort[by]=discount&sort[dir]=desc&page={p1}",
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
                f"https://2b.com.eg/ar/computers.html?p={p2}",
                f"https://2b.com.eg/ar/mobiles-tablets.html?p={p1}",
                f"https://2b.com.eg/ar/home-appliances.html?p={p2}"
            ]
        },
        {
            "id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                f"https://www.raneen.com/ar/deals?p={p1}",
                f"https://www.raneen.com/ar/deals?p={p2}",
                "https://www.raneen.com/ar/deals/appliances-electronics-offers"
            ]
        },
        {
            "id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                f"https://dream2000.com/mobiles.html?p={p1}",
                f"https://dream2000.com/home-appliances.html?p={p2}"
            ]
        }
    ]


# ================= الاختبارات الذاتية الـ 8 قبل التشغيل (Unit Tests) =================

def run_self_tests() -> None:
    """تشغيل 8 اختبارات قياسية للتحقق من سلامة المنطق قبل الاتصال بالإنترنت."""
    # 1. اختبار عدم خلط السعات 128GB مقابل 256GB
    s1 = analyze_product_identity("Samsung Galaxy A55 5G 128GB")
    s2 = analyze_product_identity("Samsung Galaxy A55 5G 256GB")
    assert s1["sig"] != s2["sig"] and s1["exact_model"], "فشل اختبار فصل السعات 128GB/256GB"

    # 2. اختبار عدم خلط مقاسات الشاشات 55 بوصة مقابل 65 بوصة
    tv1 = analyze_product_identity("شاشة سامسونج 55 بوصة سمارت 4K موديل DU7000")
    tv2 = analyze_product_identity("شاشة سامسونج 65 بوصة سمارت 4K موديل DU7000")
    assert tv1["sig"] != tv2["sig"], "فشل اختبار فصل مقاسات الشاشات 55/65"

    # 3. اختبار فصل الجهاز عن الجراب
    dev = analyze_product_identity("موبايل ايفون 15 برو ماكس 256 جيجا")
    acc = analyze_product_identity("جراب حماية موبايل ايفون 15 برو ماكس")
    assert dev["sig"].startswith("DEV|") and acc["sig"].startswith("ACC|"), "فشل اختبار فصل الجراب عن الجهاز"

    # 4. اختبار رفض سعر القسط الشهري
    assert parse_price("500 جنيه شهرياً لمدة 24 شهر", set()) == 0, "فشل اختبار رفض القسط الشهري"

    # 5. اختبار قراءة السعر الصحيح مع العملة
    assert parse_price("EGP 15,000.00", set()) == 15000, "فشل اختبار قراءة EGP 15,000"

    # 6. اختبار كشف التلاعب برفع السعر قبل الخصم (Fake Discount)
    fake_ok, *_ = evaluate_deal(
        cur=12000, old=15000,
        h_stats={"median_30d": 10000, "min_30d": 9500, "obs_count": 5},
        market_lowest_other=0, exact_model=True
    )
    assert not fake_ok, "فشل اختبار كشف الخصم الوهمي المرفوع"

    # 7. اختبار قبول الهبوط الصامت الحقيقي
    real_ok, dtype, *_ = evaluate_deal(
        cur=15000, old=0,
        h_stats={"median_30d": 19000, "min_30d": 18500, "obs_count": 4},
        market_lowest_other=0, exact_model=True
    )
    assert real_ok and dtype == "هبوط سعر صامت", "فشل اختبار الهبوط الصامت"

    # 8. اختبار المطابقة الكاملة للكلمات المحظورة (حظر كلمة قصيرة لا يحظر كلمة أطول)
    assert not has_installment_or_blocked("موبايل ايفون 15", {"فون"}), "فشل اختبار مطابقة الكلمات الكاملة"
    log("✅ اجتاز النظام جميع الاختبارات الذاتية الـ 8 بنجاح (Pre-Flight Checks OK).")


# ================= الحلقة الرئيسية (Main Engine) =================

def main() -> None:
    run_self_tests()

    t0 = time.time()
    now = int(t0)
    run_seed = int(now // 1800)
    log(f"🚀 تشغيل محرك الصفقات الذكي (V22) - دورة التدوير #{run_seed % 10}...")

    state = load_json(STATE_FILE)
    history = load_json(HISTORY_FILE)
    rules = load_json(RULES_FILE)

    # 1. قراءة تقييمات تليجرام وتحديث القواعد
    process_telegram_feedback(rules, state)
    custom_blocked = set(rules.get("blocked_keywords", []))
    blocked_deals = set(rules.get("blocked_deals", []))
    liked_brands = rules.get("liked_brands", {})
    log(f"🧠 التعلم الذاتي V3: {len(blocked_deals)} عرض محظور | {len(custom_blocked)} كلمة مستبعدة.")

    # تنظيف سجل الإرسال لما مر عليه أكثر من REALERT_HOURS
    state = {k: v for k, v in state.items() if isinstance(v, dict) and (now - v.get("ts", 0)) < REALERT_HOURS * 3600}

    stores = build_dynamic_stores(run_seed)
    all_raw_items: List[dict] = []
    store_status: Dict[str, Tuple[bool, int, int]] = {}
    total_sent = 0

    # 2. الكشط وجمع البيانات من المتاجر
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
                    ident = analyze_product_identity(item["title"])
                    item["sig"] = ident["sig"]
                    item["exact_model"] = ident["exact_model"]
                    item["brand"] = ident["brand"]
                store_items.extend(ext)
            time.sleep(random.uniform(1.0, 1.8))

        all_raw_items.extend(store_items)
        store_status[sid] = (opened, len(store_items), 0)

    # 3. بناء فهرس مقارنة السوق للمنتجات ذات الموديل الصريح المؤكد فقط (Exact Model Only)
    market_by_sig = defaultdict(list)
    for it in all_raw_items:
        if it["exact_model"]:
            market_by_sig[it["sig"]].append((it["price"], it["store"]))

    # 4. تحديث الذاكرة الزمنية لجميع المنتجات وتقييم الصفقات وترتيبها
    items_by_store = defaultdict(list)
    for it in all_raw_items:
        items_by_store[it["sid"]].append(it)

    for st in stores:
        sid = st["id"]
        max_deals = st.get("max_deals", MAX_DEALS_OTHER)
        raw_list = items_by_store.get(sid, [])

        unique_store_items: Dict[str, dict] = {}
        for it in raw_list:
            clean_link = it["link"].split("?")[0].rstrip("/").lower()
            if clean_link not in unique_store_items or it["price"] < unique_store_items[clean_link]["price"]:
                unique_store_items[clean_link] = it

        verified_candidates = []

        for clean_link, it in unique_store_items.items():
            deal_hash = hashlib.md5(clean_link.encode()).hexdigest()[:16]
            if deal_hash in blocked_deals:
                continue

            h_stats = record_and_analyze_history(history, clean_link, it["price"], now)

            lowest_other = 0
            if it["exact_model"]:
                other_prices = [p for p, st_name in market_by_sig.get(it["sig"], []) if st_name != it["store"]]
                lowest_other = min(other_prices) if other_prices else 0

            is_deal, deal_type, ref_price, saving_pct, conf_score = evaluate_deal(
                cur=it["price"],
                old=it["old"],
                h_stats=h_stats,
                market_lowest_other=lowest_other,
                exact_model=it["exact_model"],
                has_coupon=bool(it.get("coupon")),
                brand_liked=bool(liked_brands.get(it["brand"], 0) > 0)
            )

            if not is_deal:
                continue

            prev_sent = state.get(deal_hash)
            if prev_sent and it["price"] >= prev_sent.get("price", it["price"]) * 0.96:
                continue

            it["clean_link"] = clean_link
            it["deal_hash"] = deal_hash
            it["deal_type"] = deal_type
            it["ref_price"] = ref_price
            it["saving_pct"] = saving_pct
            it["conf_score"] = conf_score
            it["min_30d"] = h_stats["min_30d"]
            verified_candidates.append(it)

        verified_candidates.sort(
            key=lambda x: (x["conf_score"], x["saving_pct"], x["ref_price"] - x["price"]),
            reverse=True
        )

        sent_store = 0
        for it in verified_candidates[:max_deals]:
            saving = it["ref_price"] - it["price"]
            dtype = it["deal_type"]
            conf = it["conf_score"]

            if dtype == "هبوط سعر صامت":
                tag = f"📉 <b>هبوط مفاجئ في السعر — {htmllib.escape(it['store'])}</b>\n<i>(انخفاض تاريخي صامت بدون شطب)</i>"
                ref_txt = f"🏷 وسيط السعر (30 يوم): {it['ref_price']:,} ج.م (نزل {it['saving_pct']}% — وفر {saving:,} ج.م)"
            elif dtype == "أرخص من السوق":
                tag = f"🏆 <b>أقل سعر في مصر لنفس الموديل — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"🏪 أقل سعر منافس حالياً: {it['ref_price']:,} ج.م (أرخص بـ {saving:,} ج.م)"
            else:
                tag = f"🔥 <b>عرض معلن موثق — {htmllib.escape(it['store'])}</b> (خصم {it['saving_pct']}%)"
                ref_txt = f"🏷 السعر المرجعي السابق: {it['ref_price']:,} ج.م (توفير {saving:,} ج.م)"

            hist_line = f"📊 أقل سعر مسجل خلال 30 يوماً: {it['min_30d']:,} ج.م\n" if it.get("min_30d") else ""
            coupon_line = f"🎟 <b>كوبون إضافي: {htmllib.escape(it['coupon'])}</b>\n" if it.get("coupon") else ""
            msg = (
                f"{tag}\n\n"
                f"📦 {htmllib.escape(it['title'])}\n"
                f"💰 السعر النقدي الآن: <b>{it['price']:,} ج.م</b>\n"
                f"{ref_txt}\n"
                f"{hist_line}"
                f"{coupon_line}"
                f"🛡 مؤشر ثقة الصفقة: <b>{conf}/100</b>\n"
                f"🔗 {it['link']}"
            )

            if send_telegram_deal(msg, it["deal_hash"]):
                state[it["deal_hash"]] = {
                    "price": it["price"],
                    "ts": now,
                    "title": it["title"],
                    "brand": it["brand"],
                    "link": it["clean_link"]
                }
                sent_store += 1
                total_sent += 1
                time.sleep(1.2)

        opened, count, _ = store_status[sid]
        store_status[sid] = (opened, count, sent_store)

    # 5. تقليم وحفظ الذاكرة وإرسال الملخص
    if len(history) > 12000:
        sorted_h = sorted(history.items(), key=lambda kv: kv[1].get("ts", 0) if isinstance(kv[1], dict) else 0)
        history = dict(sorted_h[-12000:])

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    save_json(RULES_FILE, rules)
    git_ok = commit_state(state, history, rules)

    elapsed = (time.time() - t0) / 60.0
    summary_lines = ["📊 <b>ملخص جولة محرك الصفقات الذكي (V22)</b>", ""]
    for st in stores:
        sid, sname = st["id"], st["name"]
        opened, count, sent = store_status[sid]
        if opened:
            summary_lines.append(f"✅ {sname}: فُحص {count} منتج | أُرسل {sent}")
        else:
            summary_lines.append(f"❌ {sname}: تعذر الفتح في هذه الجولة")

    summary_lines.append(f"\n💾 حالة الذاكرة: {'تم الحفظ في GitHub ✅' if git_ok else 'محفوظ محلياً فقط ⚠️'}")
    summary_lines.append(f"⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي الصفقات الموثقة: <b>{total_sent}</b>")
    summary_text = "\n".join(summary_lines)
    log("--- انتهت الجولة ---\n" + summary_text)
    send_telegram_summary(summary_text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        send_telegram_summary("❌ خطأ غير متوقع في الرادار:\n" + tb[-1500:])
        sys.exit(1)
