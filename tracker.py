#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار الصفقات المصري الاحترافي — V40 (Live Cross-Store Verifier & Zero-Fake Engine)
===================================================================================
التحولات المعمارية الجذرية في V40:
1. التحقق المباشر الحي من المنافسين قبل الإرسال (Live Cross-Store Verification):
   - قبل إرسال أي صفقة من أمازون -> يفحص سعر نفس المنتج لحظياً في نون مصر.
   - قبل إرسال أي صفقة من نون/جوميا/2B/رنين/دريم -> يفحص سعر نفس المنتج لحظياً في أمازون مصر.
   - إذا وُجد المنافس أرخص أو بفارق غير مجزٍ -> يتم إحباط الصفقة فوراً وتسجيلها كخصم وهمي!
2. سياسة عدم الثقة في السعر المشطوب (Zero-Trust Strikethrough Policy):
   - لا يُقبل أي عرض بناءً على السعر المشطوب وحده؛ يُشترط إثبات منافس مباشر، أو هبوط تاريخي موثق (3+ أيام)،
     أو هبوط عن متوسط الموديل في السوق، أو عرض فعالية/كوبون لماركة معروفة اجتاز الفحص المباشر بلا منافس أرخص.
3. منع التكرار لمدة 14 يوماً بالرابط وبـهوية المنتج معاً (Dual URL + Variant 14-Day Cooldown).
4. ذاكرة أسعار سوقية دائمة عبر المتاجر (data/market_index.json) تتراكم يومياً لتعزيز المقارنة.
5. مراكز فعاليات نوفمبر والعروض الموسمية (الجمعة البيضاء / الصفراء / البلاك فرايدي / الفلاش سيل).
6. التعلم الذاتي 7.0 النظيف: تطهير الكلمات العامة المحظورة خطأً، ومنع تسمم النقاط، والمعاقبة التلقائية للخصومات الوهمية.
"""

from __future__ import annotations

import concurrent.futures
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
import threading
import time
import traceback
import urllib.parse
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

print("⚡ بدء تشغيل محرك الصفقات V40 (التحقق المباشر من المنافسين + درع العروض الوهمية)...", flush=True)

import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# ================= الإعدادات والمعايير الصارمة =================
DAY = 86400
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID")

MIN_PRODUCT_PRICE_GLOBAL = int(os.environ.get("MIN_PRODUCT_PRICE", 40))
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 70))

MIN_CONF_VERIFIED = int(os.environ.get("MIN_CONF_VERIFIED", 66))
MIN_CONF_EVENT_FALLBACK = int(os.environ.get("MIN_CONF_UNVERIFIED", 62))

MAX_DEALS_AMAZON = 15
MAX_DEALS_NOON = 12
MAX_DEALS_JUMIA = 8
MAX_DEALS_OTHER = int(os.environ.get("MAX_DEALS_PER_STORE", 6))
MAX_TOTAL_SENT_PER_RUN = 30
MAX_UNVERIFIED_EVENT_PER_RUN = 6

# رفع فترة منع التكرار من 48 ساعة إلى 14 يوماً كاملة!
REALERT_DAYS = 14
REALERT_MIN_EXTRA_DROP_RATIO = 0.90  # لا يُعاد الإرسال خلال 14 يوماً إلا لو نزل السعر 10% إضافية على الأقل

MAX_WORKERS = 10
VERIFY_WORKERS = 6
MAX_LIVE_VERIFICATIONS = 42

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
RULES_FILE = os.path.join(DATA_DIR, "learned_rules.json")
MARKET_FILE = os.path.join(DATA_DIR, "market_index.json")

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
]
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

_DOMAIN_SEMAPHORES: Dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(3))
_LIVE_CACHE_LOCK = threading.Lock()
_LIVE_SEARCH_CACHE: Dict[Tuple[str, str], List[dict]] = {}

_AR_DIGITS = "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)) + "\u066b\u066c\u060c"
_TRANS = str.maketrans(_AR_DIGITS, "0123456789" * 2 + ".,,")
_AR_NORM_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": ""})
_DIACRITICS_RE = re.compile(r"[\u064B-\u065F]")


def log(msg: str) -> None:
    print(msg, flush=True)


def safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(str(v).strip().replace(",", "."))
    except (ValueError, TypeError):
        return default


def normalize_text(text: str) -> str:
    if not text:
        return ""
    s = _DIACRITICS_RE.sub("", str(text).lower().translate(_TRANS).translate(_AR_NORM_MAP))
    return re.sub(r"\s+", " ", s).strip()


def tokenize_words(text: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(text)))


def extract_bigrams(text: str) -> Set[str]:
    words = [w for w in re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(text)) if len(w) >= 3]
    return {f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)}


def sanitize_url(url: str) -> str:
    if not url:
        return ""
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        return ""
    return htmllib.escape(u, quote=True)


BASE_INSTALLMENT_WORDS = tuple(normalize_text(w) for w in (
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "per month",
    "تقسيط", "أقساط", "اقساط", "installment", "/mo", "بدون فوائد", "دفعة مقدمة", "تمويل"
))

PHONE_ACCESSORY_WORDS_NORM: Set[str] = {normalize_text(w) for w in (
    "جراب", "اسكرينه", "اسكرينة", "لاصقه حمايه", "لاصقة حماية", "حامي شاشه",
    "phone case", "screen protector", "tempered glass", "حامل موبايل", "وصله شاحن",
    "كفر موبايل", "غطاء حمايه", "حافظه موبايل", "استيك ساعه", "سوار بديل"
)}

# درع الكلمات الشائعة (يمنع التعلم الذاتي من حظر كلمات وصفية طبيعية وتسميم البحث!)
GENERIC_SHIELD_WORDS: Set[str] = {normalize_text(w) for w in (
    "رجالي", "حريمي", "نسائي", "اطفال", "للرجال", "للنساء", "للجنسين", "شبابي",
    "اسود", "ابيض", "ازرق", "احمر", "اخضر", "رمادي", "فضي", "ذهبي", "بيج", "بني", "كحلي", "وردي",
    "كبير", "صغير", "متوسط", "مقاس", "حجم", "لون", "اصلي", "جديد", "ضمان", "محلي", "مصر", "الوكيل",
    "بلوتوث", "لاسلكي", "سلكي", "شحن", "سريع", "بطاريه", "قويه", "سمارت", "ذكي", "رقمي", "انالوج",
    "قطن", "جلد", "معدن", "ستانلس", "بلاستيك", "زجاج", "كهربائي", "يدوي", "متعدد", "الاستخدامات",
    "مجموعه", "طقم", "عبوه", "قطعه", "قطع", "كيلو", "جرام", "لتر", "ملي", "بوصه", "واط", "فولت",
    "black", "white", "blue", "silver", "gold", "grey", "gray", "red", "green", "men", "women",
    "original", "new", "smart", "wireless", "bluetooth", "fast", "charging", "dual", "sim", "edition",
    "middle", "east", "egypt", "warranty", "official", "pack", "piece", "pieces", "set", "kit",
    "stainless", "steel", "cotton", "leather", "digital", "analog", "electric", "portable", "mini"
)}

PROTECTED_WORDS_NORM: Set[str] = GENERIC_SHIELD_WORDS | {normalize_text(w) for w in (
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
    "karcher", "كارشر", "logitech", "razer", "redragon", "hyperx",
    "casio", "كاسيو", "seiko", "سيكو", "citizen", "سيتيزن", "fossil", "فوسيل",
    "tommy", "تومي", "adidas", "اديداس", "nike", "نايكي", "puma", "بوما",
    "skechers", "سكيتشرز", "defacto", "ديفاكتو", "lc waikiki", "وايكيكي",
    "lattafa", "لطافه", "armaf", "ارماف", "afnan", "افنان", "rasasi", "رصاصي",
    "sauvage", "سوفاج", "dior", "ديور", "chanel", "شانيل", "versace", "فرزاتشي",
    "bosch", "بوش", "makita", "ماكيتا", "dewalt", "ديوالت", "stanley", "ستانلي",
    "total", "توتال", "ingco", "انجكو", "apt", "crown", "كراون",
    "nescafe", "نسكافيه", "lipton", "ليبتون", "ariel", "اريال", "persil", "برسيل",
    "tide", "تايد", "downy", "داوني", "fairy", "فيري", "crystal", "كريستال",
    "سماعة", "موبايل", "هاتف", "شاشة", "تلفزيون", "لابتوب", "غسالة", "ثلاجة",
    "تكييف", "ميكروويف", "قلاية", "خلاط", "مكواة", "مكنسة", "شاحن", "باور",
    "ساعة", "عطر", "برفان", "حذاء", "كوتشي", "تيشيرت", "بنطلون", "قميص", "جاكيت",
    "شنيور", "صاروخ", "مفك", "عدة", "زيت", "ارز", "مكرونة", "قهوة", "شاي"
)}

_GENERIC_BRAND_WORDS = {"generic", "جينيريك", "جينريك", "عام", "بدون علامه", "غير محدد", "no brand", "unbranded"}

_CUR_PRICE_RE = re.compile(
    r"(?:(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)"
    r"|(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)\s*(\d{1,3}(?:,\d{3})+|\d{2,6}))",
    re.I
)
_EGP_TOKEN_RE = re.compile(r"(?:ج\.?م|جنيه|جنية|\bEGP\b|\bL\.?E\.?\b)", re.I)
_FOREIGN_CUR_RE = re.compile(r"(?:\$|€|£|\bUSD\b|\bSAR\b|\bAED\b|\bKWD\b|\bريال\b|\bدرهم\b|\bدولار\b|\bدينار\b)", re.I)
_UNIT_PRICE_RE = re.compile(r"(?:/\s*(?:\d+\s*)?(?:جرام|جم|كجم|كيلو|مل|ملي|لتر|قطعه|قطعة|count|g|kg|ml|l|oz)\b|لكل\s+\d+)", re.I)

_REAL_COUPON_CODE_RE = re.compile(
    r"(?:كود\s*خصم\s*[:\s]*([A-Za-z0-9_-]{3,15})|كوبون\s*[:\s]*([A-Za-z0-9_-]{3,15})|وفر\s+\d+\s*(?:%|ج\.?م|جنيه)\s*مع\s*الكوبون|خصم\s+\d+\s*(?:%|ج\.?م|جنيه)\s*مع\s*الكوبون|\bsave\s+\d+%?\s*with\s*coupon|\bpromo\s*code\s*[:\s]*([A-Za-z0-9_-]{3,15})|\bcoupon\s*[:\s]*([A-Za-z0-9_-]{3,15}))",
    re.I
)

_EVENT_BADGE_RE = re.compile(
    r"(?:الجمعه\s*البيضاء|الجمعه\s*الصفراء|بلاك\s*فرايدي|عرض\s*محدود|صفقه\s*اليوم|عرض\s*اليوم|فلاش\s*سيل|تخفيضات\s*نوفمبر|white\s*friday|yellow\s*friday|black\s*friday|limited\s*time\s*deal|lightning\s*deal|flash\s*sale)",
    re.I
)

# رموز مواصفات ومعايير تقنية تُستبعد من مفتاح الموديل حتى لا تُفشل المطابقة بين المتاجر
_NON_MODEL_ALPHANUM_RE = re.compile(
    r"^(?:\d+(?:w|mah|mp|hz|v|a|mm|cm|m|rpm|btu|bar|psi|db|fps|nit|nits|gb|tb|ml|gm|g|kg|l|pcs|th|nm|ghz|mhz|kw|hp|oz)"
    r"|ip\d{2}|usb\d?|usbc|typec|wifi\d?|bt\d(?:\.\d)?|ddr\d|lpddr\d[x]?|pcie\d|win\d{2}|hdr\d*|gen\d{1,2}|cat\d[a-z]?"
    r"|m\.?2|fhd|uhd|qhd|4k|8k|2k|3d|5g|4g|3g|2g|lte|nfc|gps|led|lcd|ips|oled|qled|edp|edt|edc|202\d)$",
    re.I
)


# ================= إدارة الملفات وتنظيف البيانات وتطهير الذاكرة =================

def sanitize_state(raw_state: Any) -> dict:
    if not isinstance(raw_state, dict):
        return {}
    clean = {}
    for k, v in raw_state.items():
        if isinstance(k, str) and isinstance(v, dict):
            clean[k] = {
                "price": safe_int(v.get("price")),
                "ts": safe_int(v.get("ts")),
                "title": str(v.get("title") or ""),
                "brand": str(v.get("brand") or ""),
                "category": str(v.get("category") or "GEN"),
                "variant_key": str(v.get("variant_key") or ""),
                "store": str(v.get("store") or ""),
                "kw": str(v.get("kw") or ""),
                "link": str(v.get("link") or ""),
                "message_id": safe_int(v.get("message_id")),
                "auto_rewarded": bool(v.get("auto_rewarded", False)),
                "rated_by": [str(u) for u in (v.get("rated_by") or []) if isinstance(u, (str, int))]
            }
    return clean


def sanitize_history(raw_history: Any, now: int) -> dict:
    if not isinstance(raw_history, dict):
        return {}
    clean = {}
    for k, v in raw_history.items():
        if isinstance(k, str) and isinstance(v, dict):
            valid_pts = []
            raw_pts = v.get("pts")
            if isinstance(raw_pts, list):
                for item in raw_pts:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        ts_v = safe_int(item[0])
                        p_v = safe_int(item[1])
                        if p_v >= MIN_PRODUCT_PRICE_GLOBAL and 1600000000 < ts_v <= now:
                            valid_pts.append([ts_v, p_v])
            ts_latest = safe_int(v.get("ts"), default=now)
            clean[k] = {"pts": valid_pts, "ts": ts_latest}
    return clean


def sanitize_market_index(raw_market: Any, now: int) -> dict:
    """ذاكرة السوق الدائمة (10 أيام) لتخزين أقل سعر رُصد لكل موديل في كل متجر."""
    if not isinstance(raw_market, dict):
        return {}
    clean = {}
    cutoff = now - 10 * DAY
    for mkey, store_map in raw_market.items():
        if not isinstance(mkey, str) or not isinstance(store_map, dict):
            continue
        valid_stores = {}
        for st_id, rec in store_map.items():
            if isinstance(rec, (list, tuple)) and len(rec) >= 2:
                p_v = safe_int(rec[0])
                ts_v = safe_int(rec[1])
                if p_v >= MIN_PRODUCT_PRICE_GLOBAL and ts_v >= cutoff:
                    valid_stores[str(st_id)] = [p_v, ts_v, str(rec[2]) if len(rec) > 2 else ""]
        if valid_stores:
            clean[mkey] = valid_stores
    return clean


def sanitize_rules(raw_rules: Any) -> dict:
    if not isinstance(raw_rules, dict):
        raw_rules = {}

    # تطهير تلقائي للذاكرة من أي كلمات وصفية عامة حُظرت خطأً في النسخ السابقة
    raw_strikes = raw_rules.get("word_strikes") or {}
    strikes = {
        str(k): safe_int(v)
        for k, v in raw_strikes.items()
        if safe_int(v) > 0 and normalize_text(str(k)) not in PROTECTED_WORDS_NORM
    }

    raw_blocked_kw = raw_rules.get("blocked_keywords") or []
    blocked_keywords = [
        str(x) for x in raw_blocked_kw
        if isinstance(x, str) and normalize_text(x) not in PROTECTED_WORDS_NORM
    ]

    bigram_strikes = {
        str(k): safe_int(v)
        for k, v in (raw_rules.get("bigram_strikes") or {}).items()
        if safe_int(v) > 0 and not (set(normalize_text(str(k)).split()) <= PROTECTED_WORDS_NORM)
    }
    blocked_bigrams = [
        str(x) for x in (raw_rules.get("blocked_bigrams") or [])
        if isinstance(x, str) and not (set(normalize_text(x).split()) <= PROTECTED_WORDS_NORM)
    ]

    liked = {str(k): safe_int(v) for k, v in (raw_rules.get("liked_brands") or {}).items() if safe_int(v) != 0}
    cat_affinity = {str(k): safe_int(v) for k, v in (raw_rules.get("category_affinity") or {}).items() if safe_int(v) != 0}
    store_mod = {str(k): safe_int(v) for k, v in (raw_rules.get("store_discount_modifiers") or {}).items() if -4 <= safe_int(v) <= 15}
    page_cursors = {str(k): safe_int(v, 1) for k, v in (raw_rules.get("page_cursors") or {}).items() if 1 <= safe_int(v, 1) <= 6}

    # تصفير النقاط المسمومة من V39 مرة واحدة عند الترقية إلى V40
    schema_ver = safe_int(raw_rules.get("schema_version"), 0)
    if schema_ver < 40:
        kw_scores: Dict[str, int] = {}
        log("🧹 ترقية الذاكرة إلى V40: تم تطهير نقاط الكلمات البحثية والكلمات العامة المحظورة خطأً.")
    else:
        kw_scores = {str(k): safe_int(v, 0) for k, v in (raw_rules.get("keyword_scores") or {}).items() if -10 <= safe_int(v, 0) <= 100}

    model_baselines = {}
    for k, v in (raw_rules.get("model_baselines") or {}).items():
        if isinstance(k, str) and isinstance(v, dict):
            med = safe_int(v.get("med"))
            cnt = safe_int(v.get("cnt"))
            ts = safe_int(v.get("ts"))
            if med >= MIN_PRODUCT_PRICE_GLOBAL and cnt > 0:
                model_baselines[k] = {"med": med, "cnt": cnt, "ts": ts}

    stats_raw = raw_rules.get("stats") or {}
    return {
        "schema_version": 40,
        "webhook_cleared": bool(raw_rules.get("webhook_cleared", False)),
        "last_update_id": safe_int(raw_rules.get("last_update_id")),
        "run_counter": safe_int(raw_rules.get("run_counter")),
        "blocked_deals": [str(x) for x in (raw_rules.get("blocked_deals") or []) if isinstance(x, (str, int))],
        "blocked_keywords": blocked_keywords,
        "blocked_bigrams": blocked_bigrams,
        "word_strikes": strikes,
        "bigram_strikes": bigram_strikes,
        "liked_brands": liked,
        "category_affinity": cat_affinity,
        "store_discount_modifiers": store_mod,
        "page_cursors": page_cursors,
        "keyword_scores": kw_scores,
        "model_baselines": model_baselines,
        "stats": {
            "total_positive": safe_int(stats_raw.get("total_positive")),
            "total_negative": safe_int(stats_raw.get("total_negative")),
            "auto_verified_wins": safe_int(stats_raw.get("auto_verified_wins")),
            "fake_caught_live": safe_int(stats_raw.get("fake_caught_live")),
            "last_decay_ts": safe_int(stats_raw.get("last_decay_ts"))
        }
    }


def load_json(path: str) -> dict:
    for p in (path, os.path.basename(path), os.path.join("data", os.path.basename(path))):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    try:
                        shutil.copy2(p, p + ".last_good_bak")
                    except Exception:
                        pass
                    return data
            except Exception:
                if os.path.exists(p + ".last_good_bak"):
                    try:
                        with open(p + ".last_good_bak", encoding="utf-8") as fb:
                            d_bak = json.load(fb)
                        if isinstance(d_bak, dict):
                            shutil.copy2(p + ".last_good_bak", p)
                            return d_bak
                    except Exception:
                        pass
    return {}


def save_json(path: str, data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_path = path + ".tmp"
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        json.loads(serialized)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log(f"⚠ تعذر حفظ {path}: {e}")
        return False


def _is_git_rebase_in_progress() -> bool:
    res = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return False
    git_dir = res.stdout.strip()
    return os.path.exists(os.path.join(git_dir, "rebase-merge")) or os.path.exists(os.path.join(git_dir, "rebase-apply"))


def commit_state(state: dict, history: dict, rules: dict, market_index: dict) -> bool:
    target_files = [f for f in (STATE_FILE, HISTORY_FILE, RULES_FILE, MARKET_FILE) if os.path.exists(f)]
    if not target_files:
        return False

    try:
        if _is_git_rebase_in_progress():
            return False

        subprocess.run(["git", "config", "user.name", "deals-radar[bot]"], check=False)
        subprocess.run(["git", "config", "user.email", "deals-radar@users.noreply.github.com"], check=False)

        add_res = subprocess.run(["git", "add", "--"] + target_files, capture_output=True, text=True, check=False)
        if add_res.returncode != 0:
            return False

        diff_res = subprocess.run(["git", "diff", "--cached", "--quiet", "--"] + target_files, check=False)
        if diff_res.returncode == 0:
            return True

        c_res = subprocess.run(
            ["git", "commit", "-m", "chore: update radar verified memory V40", "--only", "--"] + target_files,
            capture_output=True, text=True, check=False
        )
        if c_res.returncode != 0:
            return False

        pull_res = subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True, check=False)
        if pull_res.returncode != 0:
            if _is_git_rebase_in_progress():
                subprocess.run(["git", "rebase", "--abort"], capture_output=True, check=False)
            save_json(STATE_FILE, state)
            save_json(HISTORY_FILE, history)
            save_json(RULES_FILE, rules)
            save_json(MARKET_FILE, market_index)
            return False

        push_res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
        if push_res.returncode == 0:
            log("💾 تم حفظ ومزامنة الذاكرة والسجل السوقي في GitHub بنجاح.")
            return True
        return False
    except Exception as e:
        log(f"⚠ خطأ Git: {e}")
        return False


# ================= تليجرام والتعلم الذاتي 7.0 =================

def _tg_post(endpoint: str, payload: dict) -> Optional[dict]:
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=12)
            if r.status_code == 200:
                body = r.json()
                if body.get("ok") is True:
                    return body
            if r.status_code == 429:
                wait = safe_int(r.json().get("parameters", {}).get("retry_after"), default=4)
                time.sleep(wait + 1)
                continue
            if r.status_code == 400 and payload.get("parse_mode") == "HTML":
                payload = dict(payload)
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", payload.get("text", ""))[:4000]
                continue
        except Exception:
            pass
        time.sleep(1.2)
    return None


def send_telegram_deal(message: str, deal_hash: str) -> Optional[int]:
    if not BOT_TOKEN or not CHAT_ID:
        return None
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "👍 صفقة حقيقية ممتازة", "callback_data": f"good:{deal_hash}"},
                {"text": "👎 وهمي / أرخص بمكان تاني", "callback_data": f"bad:{deal_hash}"}
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
    res = _tg_post("sendMessage", payload)
    if res and isinstance(res.get("result"), dict):
        msg_id = safe_int(res["result"].get("message_id"))
        return msg_id if msg_id > 0 else None
    return None


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


def verify_and_register_callback(cb: dict, state: dict) -> Tuple[bool, str, str, str]:
    cb_data = str(cb.get("data", ""))
    action, sep, deal_hash = cb_data.partition(":")
    if not sep or action not in {"good", "bad"} or not deal_hash:
        return (False, "malformed_data", "", "")

    valid_chat = str(CHAT_ID or "").strip()
    if not valid_chat:
        return (False, "no_chat_id", action, deal_hash)

    from_user = cb.get("from") or {}
    msg_obj = cb.get("message") or {}
    user_id = str(from_user.get("id", "")).strip()
    chat_id = str((msg_obj.get("chat") or {}).get("id", "")).strip()
    clicked_msg_id = safe_int(msg_obj.get("message_id"))

    if not user_id or chat_id != valid_chat:
        return (False, "unauthorized_chat", action, deal_hash)

    admin_id = str(ADMIN_USER_ID or "").strip()
    if valid_chat.startswith("-"):
        if not admin_id or user_id != admin_id:
            return (False, "unauthorized_group_user", action, deal_hash)
    else:
        if user_id != valid_chat and (not admin_id or user_id != admin_id):
            return (False, "unauthorized_user", action, deal_hash)

    rec = state.get(deal_hash)
    if not isinstance(rec, dict):
        return (False, "unknown_deal", action, deal_hash)

    saved_msg_id = safe_int(rec.get("message_id"))
    if saved_msg_id <= 0 or clicked_msg_id <= 0 or saved_msg_id != clicked_msg_id:
        return (False, "message_id_mismatch", action, deal_hash)

    rated_by: List[str] = list(rec.get("rated_by", []))
    if user_id in rated_by:
        return (False, "already_rated", action, deal_hash)

    rated_by.append(user_id)
    rec["rated_by"] = rated_by
    return (True, "ok", action, deal_hash)


def apply_strike_decay(rules: dict, now: int) -> None:
    stats = rules.setdefault("stats", {})
    last_decay = safe_int(stats.get("last_decay_ts"), default=now)
    if (now - last_decay) >= 14 * DAY:
        word_strikes = rules.get("word_strikes", {})
        new_strikes = {w: cnt - 1 for w, cnt in word_strikes.items() if cnt > 1}
        rules["word_strikes"] = new_strikes
        rules["blocked_keywords"] = [w for w, cnt in new_strikes.items() if cnt >= 4]

        bg_strikes = rules.get("bigram_strikes", {})
        new_bg = {bg: cnt - 1 for bg, cnt in bg_strikes.items() if cnt > 1}
        rules["bigram_strikes"] = new_bg
        rules["blocked_bigrams"] = [bg for bg, cnt in new_bg.items() if cnt >= 3]

        stats["last_decay_ts"] = now


def process_telegram_feedback(rules: dict, state: dict, now: int) -> None:
    if not BOT_TOKEN:
        return
    try:
        apply_strike_decay(rules, now)

        if not rules.get("webhook_cleared"):
            wh_res = _tg_post("deleteWebhook", {"drop_pending_updates": False})
            if wh_res and wh_res.get("ok") is True:
                rules["webhook_cleared"] = True

        offset = safe_int(rules.get("last_update_id")) + 1
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
        bigram_strikes: Dict[str, int] = dict(rules.get("bigram_strikes", {}))
        blocked_keywords: List[str] = list(rules.get("blocked_keywords", []))
        blocked_bigrams: List[str] = list(rules.get("blocked_bigrams", []))
        liked_brands: Dict[str, int] = dict(rules.get("liked_brands", {}))
        cat_affinity: Dict[str, int] = dict(rules.get("category_affinity", {}))
        store_modifiers: Dict[str, int] = dict(rules.get("store_discount_modifiers", {}))
        kw_scores: Dict[str, int] = dict(rules.get("keyword_scores", {}))
        stats = rules.setdefault("stats", {})

        reject_messages = {
            "already_rated": "تم تسجيل تقييمك لهذا العرض مسبقاً ℹ️",
            "unauthorized_group_user": "التقييم متاح لمشرف الرادار فقط 🔒",
            "unauthorized_user": "غير مصرح لك بتعديل قواعد التعلم 🔒",
            "unknown_deal": "انتهت صلاحية بيانات هذا العرض ⌛",
            "message_id_mismatch": "رسالة غير موثقة ⚠️",
            "malformed_data": "بيانات الزر غير صالحة ⚠️"
        }

        for u in updates:
            rules["last_update_id"] = u["update_id"]
            cb = u.get("callback_query")
            if not cb:
                continue

            cb_id = cb.get("id")
            allowed, status_reason, action, deal_hash = verify_and_register_callback(cb, state)
            if not allowed:
                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": reject_messages.get(status_reason, "تعذر قبول التقييم ⚠️"),
                        "show_alert": False
                    })
                continue

            deal_info = state.get(deal_hash, {})
            title = deal_info.get("title", "")
            brand = deal_info.get("brand", "")
            cat = deal_info.get("category", "GEN")
            store = deal_info.get("store", "")
            kw = deal_info.get("kw", "")
            canonical_store = get_canonical_store_id(store)

            if action == "bad":
                stats["total_negative"] = safe_int(stats.get("total_negative")) + 1
                if deal_hash not in blocked_deals:
                    blocked_deals.append(deal_hash)

                if canonical_store:
                    store_modifiers[canonical_store] = min(10, store_modifiers.get(canonical_store, 0) + 2)
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) - 1
                if cat:
                    cat_affinity[cat] = max(-5, cat_affinity.get(cat, 0) - 1)
                if kw:
                    kw_scores[kw] = max(-10, kw_scores.get(kw, 0) - 3)

                # تشديد حظر الكلمات (4 مخالفات + محمي بدرع الكلمات الشائعة لعدم تسميم البحث)
                words_list = [w for w in re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(title)) if len(w) >= 5 and not w.isdigit()]
                for w in words_list:
                    if w not in PROTECTED_WORDS_NORM:
                        word_strikes[w] = word_strikes.get(w, 0) + 1
                        if word_strikes[w] >= 4 and w not in blocked_keywords:
                            blocked_keywords.append(w)

                for bg in extract_bigrams(title):
                    bg_tokens = set(bg.split())
                    if not (bg_tokens & PROTECTED_WORDS_NORM):
                        bigram_strikes[bg] = bigram_strikes.get(bg, 0) + 1
                        if bigram_strikes[bg] >= 3 and bg not in blocked_bigrams:
                            blocked_bigrams.append(bg)

                log(f"🧠 تعلم ذاتي 7.0: حظر العرض [{title[:35]}] وتشديد الفلتر على [{canonical_store}].")
                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "تم حظر العرض وتشديد الفلتر على المتجر ✅",
                        "show_alert": False
                    })

            elif action == "good":
                stats["total_positive"] = safe_int(stats.get("total_positive")) + 1
                if canonical_store:
                    store_modifiers[canonical_store] = max(-3, store_modifiers.get(canonical_store, 0) - 1)
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) + 2
                if cat:
                    cat_affinity[cat] = min(10, cat_affinity.get(cat, 0) + 1)
                if kw:
                    kw_scores[kw] = min(100, kw_scores.get(kw, 0) + 4)

                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "ممتاز! تم تعزيز أولوية الماركة والقسم 🌟",
                        "show_alert": False
                    })

        rules["blocked_deals"] = list(dict.fromkeys(blocked_deals))[-3000:]
        rules["blocked_keywords"] = list(dict.fromkeys(blocked_keywords))[-400:]
        rules["blocked_bigrams"] = list(dict.fromkeys(blocked_bigrams))[-400:]
        rules["word_strikes"] = dict(sorted(word_strikes.items(), key=lambda kv: kv[1], reverse=True)[:1000])
        rules["bigram_strikes"] = dict(sorted(bigram_strikes.items(), key=lambda kv: kv[1], reverse=True)[:1000])
        rules["liked_brands"] = liked_brands
        rules["category_affinity"] = cat_affinity
        rules["store_discount_modifiers"] = store_modifiers
        rules["keyword_scores"] = kw_scores
    except Exception as e:
        log(f"⚠ خطأ في محرك التعلم الذاتي: {e}")


# ================= توحيد هوية المتجر =================

def get_canonical_store_id(store_str: str) -> str:
    s = normalize_text(store_str)
    if any(k in s for k in ("amazon", "امازون")):
        return "amazon"
    if any(k in s for k in ("noon", "نون")):
        return "noon"
    if any(k in s for k in ("jumia", "جوميا")):
        return "jumia"
    if any(k in s for k in ("2b", "تو بي")):
        return "2b"
    if any(k in s for k in ("raneen", "رنين")):
        return "raneen"
    if any(k in s for k in ("dream2000", "دريم")):
        return "dream2000"
    if any(k in s for k in ("b.tech", "btech", "بي تك")):
        return "btech"
    if any(k in s for k in ("raya", "رايه", "راية")):
        return "raya"
    if any(k in s for k in ("cairo sales", "كايرو سيلز")):
        return "cairosales"
    if any(k in s for k in ("elghazawy", "الغزاوي")):
        return "elghazawy"
    return s[:15]


CANONICAL_AR_NAMES = {
    "amazon": "أمازون مصر",
    "noon": "نون مصر",
    "jumia": "جوميا مصر",
    "2b": "2B Egypt",
    "raneen": "رنين",
    "dream2000": "دريم 2000",
    "btech": "بي تك",
    "raya": "راية"
}


# ================= قراءة الأسعار وحاسبة الكوبون =================

def has_installment_or_blocked(text: str, custom_blocked_tokens: Set[str], custom_blocked_bigrams: Set[str] = set()) -> bool:
    if not text:
        return False
    norm = normalize_text(text)
    for phrase in BASE_INSTALLMENT_WORDS:
        if phrase in norm:
            return True
    if re.search(r"(\d+\s*شهر|شهريا|بدون مقدم|تقسيط|فائده\s*0)", norm):
        return True
    if custom_blocked_tokens and (tokenize_words(norm) & custom_blocked_tokens):
        return True
    if custom_blocked_bigrams and (extract_bigrams(norm) & custom_blocked_bigrams):
        return True
    return False


def parse_price(value: Any, custom_blocked_tokens: Set[str], explicit_egp: bool = False) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        if not explicit_egp:
            return 0
        v = int(round(value))
        return v if MIN_PRODUCT_PRICE_GLOBAL <= v <= 500000 else 0

    text = str(value)
    if _FOREIGN_CUR_RE.search(text):
        return 0
    if _UNIT_PRICE_RE.search(text):
        return 0
    if has_installment_or_blocked(text, custom_blocked_tokens):
        return 0

    text = text.translate(_TRANS).replace("\u00a0", " ")
    text_no_dec = re.sub(r"[.,]\d{1,2}(?!\d)", " ", text)

    m_cur = _CUR_PRICE_RE.search(text_no_dec)
    if m_cur:
        raw_num = (m_cur.group(1) or m_cur.group(2)).replace(",", "")
        v = safe_int(raw_num)
        if MIN_PRODUCT_PRICE_GLOBAL <= v <= 500000:
            return v

    if not explicit_egp:
        return 0

    comma_nums = re.findall(r"\d{1,3}(?:,\d{3})+", text_no_dec)
    for m in comma_nums:
        v = safe_int(m.replace(",", ""))
        if MIN_PRODUCT_PRICE_GLOBAL <= v <= 500000:
            return v

    for m in re.findall(r"\d+", text_no_dec):
        v = safe_int(m)
        if v in (2022, 2023, 2024, 2025, 2026):
            continue
        if MIN_PRODUCT_PRICE_GLOBAL <= v <= 500000:
            return v
    return 0


def compute_net_coupon_price(cur_price: int, coupon_text: str) -> int:
    if not coupon_text or cur_price <= 0:
        return cur_price
    txt = coupon_text.translate(_TRANS)
    m_pct = re.search(r"(\d{1,2})\s*%", txt)
    if m_pct:
        pct = safe_int(m_pct.group(1))
        if 2 <= pct <= 50:
            return max(MIN_PRODUCT_PRICE_GLOBAL, int(round(cur_price * (1.0 - pct / 100.0))))
    m_egp = re.search(r"(\d{2,4})\s*(?:ج\.?م|جنيه|egp)", txt, re.I)
    if m_egp:
        val = safe_int(m_egp.group(1))
        if 10 <= val <= cur_price * 0.5:
            return max(MIN_PRODUCT_PRICE_GLOBAL, cur_price - val)
    return cur_price


def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)[:190]


# ================= محرك الهوية ومطابقة الموديلات الدقيق V4 =================

_BRAND_ALIASES = {
    "سامسونج": "samsung", "ابل": "apple", "ايفون": "apple", "ايباد": "apple",
    "شاومي": "xiaomi", "redmi": "xiaomi", "ريدمي": "xiaomi", "poco": "xiaomi", "بوكو": "xiaomi",
    "ريلمي": "realme", "اوبو": "oppo", "فيفو": "vivo", "انفينكس": "infinix", "تكنو": "tecno",
    "هونر": "honor", "هواوي": "huawei", "نوكيا": "nokia", "انكر": "anker",
    "ساوندكور": "soundcore", "اورايمو": "oraimo", "سوني": "sony", "توشيبا": "toshiba",
    "شارب": "sharp", "تورنيدو": "tornado", "فريش": "fresh", "بيكو": "beko",
    "زانوسي": "zanussi", "كريازي": "kiriazi", "كاريير": "carrier", "ميديا": "midea",
    "هاير": "haier", "ديل": "dell", "لينوفو": "lenovo", "اسوس": "asus",
    "ايسر": "acer", "فيليبس": "philips", "براون": "braun", "تيفال": "tefal",
    "تروفال": "truval", "مولينكس": "moulinex", "كينوود": "kenwood", "كارشر": "karcher",
    "اتش بي": "hp", "ال جي": "lg", "جالاكسي": "samsung",
    "كاسيو": "casio", "سيكو": "seiko", "سيتيزن": "citizen", "فوسيل": "fossil",
    "تومي": "tommy", "اديداس": "adidas", "نايكي": "nike", "نايك": "nike",
    "بوما": "puma", "سكيتشرز": "skechers", "ديفاكتو": "defacto", "وايكيكي": "lcwaikiki",
    "لطافه": "lattafa", "لطافة": "lattafa", "ارماف": "armaf", "افنان": "afnan",
    "رصاصي": "rasasi", "ديور": "dior", "شانيل": "chanel", "فرزاتشي": "versace",
    "لوريال": "loreal", "نيفيا": "nivea", "غارنييه": "garnier", "ميبيلين": "maybelline",
    "بوش": "bosch", "ماكيتا": "makita", "ديوالت": "dewalt", "ستانلي": "stanley",
    "توتال": "total", "انجكو": "ingco", "كراون": "crown", "بلاك اند ديكر": "blackanddecker",
    "نسكافيه": "nescafe", "ليبتون": "lipton", "اريال": "ariel", "برسيل": "persil",
    "تايد": "tide", "داوني": "downy", "فيري": "fairy", "كريستال": "crystal",
    "جهينه": "juhayna", "المراعي": "almarai", "نستله": "nestle", "احمد تي": "ahmadtea",
    "الضحى": "eldoha", "عافيه": "afia", "سلايت": "slite", "كنور": "knorr", "موتورولا": "motorola"
}

_AR_MODEL_LETTERS = {
    "ايه": "a", "اي": "a", "اس": "s", "ام": "m", "اف": "f",
    "سي": "c", "جي": "g", "اكس": "x", "في": "v", "زد": "z"
}

_STRICT_TIERS = {"pro", "max", "ultra", "plus", "lite", "fe", "mini", "se", "fold", "flip", "air"}
_AR_TIERS = {
    "برو": "pro", "ماكس": "max", "الترا": "ultra", "بلس": "plus",
    "لايت": "lite", "ميني": "mini", "نوت": "note", "اف اي": "fe"
}
_TV_TECH_WORDS = {"oled", "qled", "miniled", "nanocell", "crystal", "uhd", "fhd", "hd", "4k", "8k", "qned"}

_LAPTOP_FORM_FACTORS = {
    "laptop", "notebook", "macbook", "ideapad", "thinkpad", "thinkbook", "yoga", "legion", "loq",
    "vivobook", "zenbook", "tuf", "rog", "strix", "zephyrus", "inspiron", "vostro", "latitude",
    "xps", "alienware", "pavilion", "envy", "spectre", "omen", "victus", "elitebook", "probook",
    "15s", "14s", "aspire", "nitro", "swift", "predator", "katana", "cyborg", "stealth",
    "لابتوب", "لاب توب", "نوت بوك"
}

_PERFUME_WORDS = {"عطر", "برفان", "ماء عطر", "او دو", "تواليت", "بارفيوم", "سبراي جسم", "مزيل عرق", "edp", "edt", "perfume", "parfum", "eau de", "fragrance", "cologne"}
_WATCH_WORDS = {"ساعه يد", "ساعه كلاسيك", "ساعه انالوج", "ساعه رقميه", "ساعه رجالي", "ساعه حريمي", "كرونوغراف", "ساعه كاسيو", "ساعه ذكيه", "سمارت ووتش", "wristwatch", "chronograph", "analog watch", "quartz", "smartwatch", "smart watch"}
_FASHION_WORDS = {"تيشيرت", "قميص", "بنطلون", "جينز", "جاكيت", "سويت شيرت", "هودي", "فستان", "عبايه", "بلوزه", "بيجامه", "شورت", "حذاء", "كوتشي", "سنيكرز", "صندل", "شبشب", "شنطه ظهر", "حقيبه يد", "محفظه", "t-shirt", "shirt", "pants", "jeans", "jacket", "hoodie", "sneakers", "shoes", "slippers", "backpack", "handbag"}
_TOOLS_WORDS = {"شنيور", "مثقاب", "صاروخ", "جلخ", "مفك", "مفتاح", "زراديه", "بنسه", "منشار", "شاكوش", "متر قياس", "طقم عده", "صندوق عده", "مفاتيح", "لقم", "كمبروسر", "منفاخ", "كاويه", "افوميتر", "دريل", "هلتي", "drill", "grinder", "screwdriver", "wrench", "pliers", "tool set", "socket set", "toolbox", "hammer", "saw"}
_GROCERY_WORDS = {"زيت", "سمن", "زبده", "ارز", "مكرونه", "سكر", "دقيق", "شاي", "قهوه", "نسكافيه", "كاكاو", "حليب", "لبن", "جبنه", "تونه", "صلصه", "كاتشب", "مايونيز", "عسل", "مربى", "شوكولاته", "بسكويت", "مسحوق غسيل", "منظف", "منعم اقمشه", "صابون", "شامبو", "بلسم", "شاور جل", "معجون اسنان", "مناديل", "حفاضات", "coffee", "tea", "olive oil", "sunflower oil", "pasta", "rice", "detergent", "shampoo", "lotion", "tissue", "diapers"}
_APPLIANCE_WORDS = {"ثلاجه", "غساله", "بوتاجاز", "تكييف", "سخان", "ميكروويف", "قلايه", "خلاط", "كبه", "مفرمه", "مكواه", "مكنسه", "مروحه", "فلتر مياه", "كاتل", "غلايه", "ماكينه قهوه", "توستر", "مضرب بيض", "فرن كهربائي", "ديب فريزر", "refrigerator", "washer", "air conditioner", "microwave", "air fryer", "blender", "vacuum", "kettle"}


def is_phone_accessory(title: str, price: int) -> bool:
    norm = normalize_text(title)
    if price < 450 and any(w in norm for w in PHONE_ACCESSORY_WORDS_NORM):
        return True
    return False


def is_generic_product(title: str, brand: str) -> bool:
    if brand:
        return False
    norm = normalize_text(title)
    return any(g in norm for g in _GENERIC_BRAND_WORDS)


def _extract_ram_and_storage(t: str, is_laptop_context: bool) -> Tuple[List[str], List[str]]:
    ram_set: Set[int] = set()
    storage_gb_set: Set[int] = set()
    storage_tb_set: Set[int] = set()
    work = t

    combo_re = re.compile(r"\b(\d{1,2})\s*(?:gb|g|جيجا|جيجابايت)?\s*[/+]\s*(\d{2,4})\s*(gb|tb|g|جيجا|جيجابايت|تيرا|تيرابايت)\b")
    for m in combo_re.finditer(work):
        r_val, s_val, s_unit = safe_int(m.group(1)), safe_int(m.group(2)), m.group(3)
        if 2 <= r_val <= 64:
            ram_set.add(r_val)
        if s_unit in ("tb", "تيرا", "تيرابايت") or s_val in (1, 2, 4):
            storage_tb_set.add(s_val)
        elif s_val >= 32:
            storage_gb_set.add(s_val)
    work = combo_re.sub(" ", work)

    ram_explicit_patterns = [
        r"\b(\d{1,3})\s*(?:gb|g|جيجا|جيجابايت)\s*(?:ram|رام|رامات|ddr4|ddr5|lpddr\d?)\b",
        r"\b(?:ram|رام|رامات)\s*(\d{1,3})\s*(?:gb|g|جيجا|جيجابايت)?\b"
    ]
    for pat in ram_explicit_patterns:
        for m in re.finditer(pat, work):
            v = safe_int(m.group(1))
            if 2 <= v <= 128:
                ram_set.add(v)
        work = re.sub(pat, " ", work)

    tb_pat = r"\b(\d{1,2})\s*(?:tb|تيرا\s*بايت|تيرابايت|تيرا)\b"
    for m in re.finditer(tb_pat, work):
        v = safe_int(m.group(1))
        if 1 <= v <= 16:
            storage_tb_set.add(v)
    work = re.sub(tb_pat, " ", work)

    rem_gb = []
    for m in re.finditer(r"\b(\d{1,4})\s*(?:gb|g\.b|جيجا\s*بايت|جيجابايت|جيجا)\b", work):
        rem_gb.append((safe_int(m.group(1)), m.start(), m.end()))

    for v, _, end_pos in rem_gb:
        after_slice = work[end_pos:end_pos + 20]
        if re.search(r"\b(ssd|nvme|hdd|هارد|تخزين|ذاكره)\b", after_slice):
            if v >= 32:
                storage_gb_set.add(v)
            continue
        if v >= 128:
            storage_gb_set.add(v)
        elif v == 64:
            if is_laptop_context and (storage_tb_set or "ssd" in t or "nvme" in t):
                ram_set.add(v)
            else:
                storage_gb_set.add(v)
        elif v == 32:
            has_larger_storage = bool(storage_tb_set or storage_gb_set or any(x >= 128 for x, _, _ in rem_gb))
            if has_larger_storage or is_laptop_context:
                ram_set.add(v)
            else:
                storage_gb_set.add(v)
        elif 2 <= v <= 24:
            ram_set.add(v)

    return [f"{v}gb" for v in sorted(ram_set)], [f"{v}gb" for v in sorted(storage_gb_set)] + [f"{v}tb" for v in sorted(storage_tb_set)]


def _extract_volume_and_weight(t: str) -> List[str]:
    specs = []
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(ml|مل|ملي|ملل|g|gm|جم|جرام|غ|kg|كجم|كيلو|l|لتر|قطعه|قطعة)\b", t):
        val = m.group(1)
        unit = m.group(2)
        if unit in ("ml", "مل", "ملي", "ملل"):
            specs.append(f"{val}ml")
        elif unit in ("g", "gm", "جم", "جرام", "غ"):
            specs.append(f"{val}g")
        elif unit in ("kg", "كجم", "كيلو"):
            specs.append(f"{val}kg")
        elif unit in ("l", "لتر"):
            specs.append(f"{val}l")
        else:
            specs.append(f"{val}pcs")
    return sorted(set(specs))[:3]


def analyze_product_identity(title: str, hint_cat: str = "") -> Dict[str, Any]:
    t = normalize_text(title)
    brand = ""

    if re.search(r"\b(iphone|ipad|ايفون|ايباد|ماك بوك|macbook|airpods|ايربودز)\b", t):
        brand = "apple"
    elif re.search(r"\b(xiaomi|redmi|poco|شاومي|ريدمي|بوكو)\b", t):
        brand = "xiaomi"

    for ar_b, en_b in _BRAND_ALIASES.items():
        norm_ar = normalize_text(ar_b)
        if not brand and re.search(r"(?<!\w)" + re.escape(norm_ar) + r"(?!\w)", t):
            brand = en_b
        t = re.sub(r"(?<!\w)" + re.escape(norm_ar) + r"(?!\w)", f" {en_b} ", t)

    for ar_t, en_t in _AR_TIERS.items():
        norm_ar_t = normalize_text(ar_t)
        t = re.sub(r"(?<!\w)" + re.escape(norm_ar_t) + r"(?!\w)", f" {en_t} ", t)

    for ar_let, en_let in _AR_MODEL_LETTERS.items():
        t = re.sub(r"(?<!\w)" + re.escape(ar_let) + r"\s*(\d{1,3})\b", f" {en_let}\\1 ", t)

    t = re.sub(r"الجيل\s+الخامس", " 5g ", t)
    t = re.sub(r"الجيل\s+الرابع", " 4g ", t)
    t = re.sub(r"بوصه|بوصة|انش|inches|\"|''", " inch ", t)

    vol_weight_specs = _extract_volume_and_weight(t)

    # استخراج عائلة المعالج (i3/i5/i7/i9/ryzen5/m2)
    cpu_family = ""
    m_cpu_full = re.search(r"\b(i[3579]|ryzen\s*[3579]|m[1234](?:\s*(?:pro|max))?)\s*[-_]?\s*(\d{4,5}[a-z]{0,3})?\b", t)
    if m_cpu_full:
        cpu_family = re.sub(r"\s+", "", m_cpu_full.group(1))
        t = re.sub(r"\b(i[3579]|ryzen\s*[3579])\s*[-_]?\s*\d{4,5}[a-z]{0,3}\b", " ", t)

    gpu_token = ""
    m_gpu = re.search(r"\b(rtx|gtx|mx)\s*(\d{3,4})\s*(ti|super)?\b", t)
    if m_gpu:
        gpu_token = f"{m_gpu.group(1)}{m_gpu.group(2)}{m_gpu.group(3) or ''}"
        t = re.sub(r"\b(rtx|gtx|mx)\s*\d{3,4}\s*(?:ti|super)?\b", " ", t)

    raw_words_set = set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", t))
    is_laptop_ctx = bool(
        cpu_family
        or gpu_token
        or (raw_words_set & _LAPTOP_FORM_FACTORS)
        or (brand in ("dell", "hp", "lenovo", "asus", "acer", "msi") and not any(w in raw_words_set for w in ("شاشه", "monitor", "موبايل", "phone", "تابلت", "tablet", "ساعه", "شنطه", "ماوس", "كيبورد")))
    )

    ram_vals, storage_vals = _extract_ram_and_storage(t, is_laptop_ctx)

    t = re.sub(r"\b\d{1,2}\s*(?:gb|g|جيجا|جيجابايت)?\s*[/+]\s*\d{2,4}\s*(?:gb|tb|g|جيجا|جيجابايت|تيرا|تيرابايت)\b", " ", t)
    t = re.sub(r"\b\d{1,4}\s*(?:gb|tb|g\.b|جيجا|جيجابايت|جيجا|تيرا|تيرابايت|تيرا)\b", " ", t)
    t = re.sub(r"(\d{2}(?:\.\d)?)\s*inch\b", r" \1inch ", t)

    # دمج السلاسل الشهيرة مع أرقامها قبل التقطيع
    t = re.sub(r"\b(iphone|ipad|redmi|poco|note|tab|watch|band|airpods|series|mi|reno|narzo|spark|hot|camon|pop|moto|edge)\s*[-_]?\s*(\d{1,3}[a-z]?)\b", r"\1\2", t)
    t = re.sub(r"\b(s|a|m|f|c|g|x|v)\s*[-_]?\s*(\d{2})\s*(fe)\b", r"\1\2 \3", t)
    t = re.sub(r"\b([a-z]{1,3})\s*[-_]\s*(\d{2,4}[a-z]?)\b", r"\1\2", t)
    t = re.sub(r"\b(a|s|m|f|c|g|x|v)\s+(\d{2}[a-z]?)\b", r"\1\2", t)

    tokens = re.findall(r"[a-z0-9.]+|[\u0600-\u06FF]+", t)
    inch_vals = [f"{m}inch" for m in sorted(set(re.findall(r"\b(\d{2}(?:\.\d)?)inch\b", t)))]
    strict_tiers = sorted({tok for tok in tokens if tok in _STRICT_TIERS})
    net_tier = "5g" if "5g" in tokens else ("4g" if "4g" in tokens else "")
    tv_tech = sorted({tok for tok in tokens if tok in _TV_TECH_WORDS})

    if any(p in t for p in _PERFUME_WORDS) or hint_cat == "PERF":
        cat = "PERF"
    elif any(w in t for w in _WATCH_WORDS) or brand in ("casio", "seiko", "citizen", "fossil") or hint_cat == "WATCH":
        cat = "WATCH"
    elif any(tl in t for tl in _TOOLS_WORDS) or brand in ("bosch", "makita", "dewalt", "stanley", "total", "ingco", "crown") or hint_cat == "TOOLS":
        cat = "TOOLS"
    elif any(g in t for g in _GROCERY_WORDS) or brand in ("nescafe", "lipton", "ariel", "persil", "tide", "downy", "fairy", "crystal", "juhayna", "almarai", "eldoha", "afia") or hint_cat == "GROC":
        cat = "GROC"
    elif any(f in t for f in _FASHION_WORDS) or brand in ("adidas", "nike", "puma", "skechers", "defacto", "lcwaikiki", "tommy") or hint_cat == "FASH":
        cat = "FASH"
    elif is_laptop_ctx:
        cat = "LAP"
    elif tv_tech or "تلفزيون" in t or "شاشه" in t or "tv" in tokens or any(safe_int(x.replace("inch", "")) >= 27 for x in inch_vals):
        cat = "TV"
    elif any(ap in t for ap in _APPLIANCE_WORDS) or hint_cat == "APPL":
        cat = "APPL"
    elif ram_vals or storage_vals or brand in ("samsung", "apple", "xiaomi", "realme", "oppo", "vivo", "infinix", "tecno", "honor", "huawei", "nokia", "motorola") or hint_cat == "MOB":
        cat = "MOB"
    else:
        cat = hint_cat or "GEN"

    stop_words = GENERIC_SHIELD_WORDS | {
        "عرض", "خصم", "سنه", "مع", "من", "في", "شاشه", "موبايل", "هاتف", "تلفزيون",
        "ثنائي", "الشريحه", "شريحتين", "شبكه", "رام", "رامات", "ذاكره", "داخليه",
        "لابتوب", "هارد", "inch", "ram", "galaxy", "laptop", "notebook", "core",
        "mobile", "phone", "tv", "with", "for", "and", "the", "pro", "max", "ultra",
        "plus", "lite", "fe", "mini", "se", "fold", "flip", "air", "5g", "4g"
    }

    known_brands = set(_BRAND_ALIASES.values()) | {"hp", "lg", "jbl", "msi", "dyson", "razer", "redragon", "logitech", "apple", "xiaomi"}
    models = []
    core_words = []

    for tok in tokens:
        if not brand and tok in known_brands:
            brand = tok
            continue
        if (
            tok in known_brands
            or tok in stop_words
            or tok in _TV_TECH_WORDS
            or _NON_MODEL_ALPHANUM_RE.match(tok)
            or re.fullmatch(r"\d+(?:\.\d)?(?:gb|tb|inch|ml|g|kg|l|pcs)", tok)
        ):
            continue
        if len(tok) >= 2 and re.search(r"[a-z]", tok) and re.search(r"\d", tok):
            models.append(tok)
        elif len(tok) >= 3 and not tok.replace(".", "").isdigit():
            core_words.append(tok)

    # ترتيب الموديلات بحيث يأتي الموديل الأقصر والأشهر أولاً (مثل a55 قبل sma556e)
    models = sorted(set(models), key=lambda x: (len(x), x))
    core_sorted = list(dict.fromkeys(core_words))[:5]

    specs_dict = {
        "ram": ram_vals[0] if ram_vals else "",
        "storage": storage_vals[0] if storage_vals else "",
        "cpu": cpu_family,
        "gpu": gpu_token,
        "inch": inch_vals[0] if inch_vals else "",
        "vw": vol_weight_specs[0] if vol_weight_specs else "",
        "tiers": "-".join(strict_tiers),
        "net": net_tier
    }

    has_exact_id = bool(
        brand and (
            models
            or (cat == "LAP" and core_sorted and cpu_family and (ram_vals or storage_vals))
            or (cat in ("PERF", "GROC", "TOOLS", "WATCH", "APPL") and core_sorted and (vol_weight_specs or len(core_sorted) >= 2))
        )
    )

    # بناء مفاتيح فهرسة مرنة متعددة (Multi-Key Lookup) لربط a55 مع a55 + sma556e فوراً!
    lookup_keys: List[str] = []
    if has_exact_id:
        primary_model = models[0] if models else "-".join(core_sorted[:2])
        if models:
            for m_tok in models[:3]:
                lookup_keys.append(f"{cat}|{brand}|{m_tok}")
        else:
            lookup_keys.append(f"{cat}|{brand}|{primary_model}")

        variant_key = f"{cat}|{brand}|{primary_model}|{specs_dict['tiers']}|{specs_dict['storage']}|{specs_dict['inch']}|{specs_dict['vw']}|{specs_dict['cpu']}|{specs_dict['gpu']}"
        # بناء استعلام بحث نظيف للتحقق المباشر من المنافسين
        q_parts = [brand, primary_model]
        if specs_dict["tiers"]:
            q_parts.append(specs_dict["tiers"].replace("-", " "))
        if specs_dict["storage"]:
            q_parts.append(specs_dict["storage"])
        elif specs_dict["inch"]:
            q_parts.append(specs_dict["inch"].replace("inch", ""))
        elif specs_dict["vw"]:
            q_parts.append(specs_dict["vw"])
        elif specs_dict["cpu"]:
            q_parts.append(specs_dict["cpu"])
        search_query = " ".join(p for p in q_parts if p).strip()

        return {
            "sig": variant_key,
            "lookup_keys": lookup_keys,
            "variant_key": variant_key,
            "search_query": search_query,
            "core_tokens": set(models) | set(core_sorted),
            "specs": specs_dict,
            "exact_model": True,
            "brand": brand,
            "category": cat
        }

    fallback_var = f"{cat}|{brand}|W:{'-'.join(core_sorted[:3])}|{specs_dict['vw']}|{specs_dict['storage']}"
    q_parts = ([brand] if brand else []) + core_sorted[:3] + ([specs_dict["vw"]] if specs_dict["vw"] else [])
    return {
        "sig": fallback_var,
        "lookup_keys": [],
        "variant_key": fallback_var,
        "search_query": " ".join(q_parts).strip(),
        "core_tokens": set(core_sorted),
        "specs": specs_dict,
        "exact_model": False,
        "brand": brand,
        "category": cat
    }


def specs_are_compatible(s1: Dict[str, str], s2: Dict[str, str], cat: str) -> bool:
    """فحص التطابق الصارم للمواصفات لمنع مقارنة نسخة عادية بنسخة Pro أو سعة بسعة مختلفة."""
    # 1. الفئة الفرعية (Pro / Ultra / Max / Plus / FE / Lite / Mini) يجب أن تتطابق تماماً!
    if s1.get("tiers", "") != s2.get("tiers", ""):
        return False

    # 2. شبكة 4G مقابل 5G لو ذُكرت في الاثنين
    n1, n2 = s1.get("net", ""), s2.get("net", "")
    if n1 and n2 and n1 != n2:
        return False

    # 3. سعة التخزين في الهواتف واللابتوبات يجب أن تتطابق
    st1, st2 = s1.get("storage", ""), s2.get("storage", "")
    if cat in ("MOB", "LAP"):
        if st1 != st2:
            return False
    elif st1 and st2 and st1 != st2:
        return False

    # 4. الرام: لو ذُكر في الاثنين يجب أن يتطابق، ولو أغفله متجر واحد يُسمح بالمطابقة
    r1, r2 = s1.get("ram", ""), s2.get("ram", "")
    if r1 and r2 and r1 != r2:
        return False

    # 5. المعالج وكارت الشاشة في اللابتوبات
    if cat == "LAP":
        c1, c2 = s1.get("cpu", ""), s2.get("cpu", "")
        if c1 != c2:
            return False
        g1, g2 = s1.get("gpu", ""), s2.get("gpu", "")
        if g1 != g2:
            return False

    # 6. مقاس الشاشة في التلفزيونات
    i1, i2 = s1.get("inch", ""), s2.get("inch", "")
    if cat == "TV" and i1 != i2:
        return False
    if i1 and i2 and i1 != i2:
        return False

    # 7. الحجم/الوزن في العطور والبقالة (مثلاً 100ml لا يقارن بـ 50ml)
    v1, v2 = s1.get("vw", ""), s2.get("vw", "")
    if cat in ("PERF", "GROC") and (v1 and v2 and v1 != v2):
        return False

    return True


# ================= الجلب المتوازي مع منظم السرعة =================

def is_blocked_page(text: str, status: int, is_amazon: bool = False) -> bool:
    if not text:
        return True
    if status in (403, 429, 503) and len(text) < 25000:
        return True

    head = text[:8000].lower()
    if any(c in head for c in ("validatecaptcha", "api-services-support@amazon.com", "enter the characters you see below", "cf-browser-verification", "vercel security checkpoint")):
        return True

    if is_amazon:
        if len(text) < 12000 and "/dp/" not in text:
            return True
        if "data-asin" not in text and "/dp/" not in text:
            return True
    elif len(text) < 1500:
        return True

    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
    return any(b in title for b in ("just a moment", "attention required", "access denied", "403 forbidden", "security checkpoint", "robot check", "one more step", "sorry! something went wrong"))


def smart_fetch(url: str, store_id: str, is_amazon: bool = False, fast_mode: bool = False) -> str:
    sem = _DOMAIN_SEMAPHORES[store_id]
    with sem:
        time.sleep(random.uniform(0.15, 0.45) if fast_mode else random.uniform(0.25, 0.55))
        base_headers = {
            "Accept-Language": AR_LANG,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://www.google.com.eg/",
            "Cache-Control": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": random.choice(UA_LIST)
        }
        if is_amazon:
            base_headers["Cookie"] = "i18n-prefs=EGP; lc-acbamo=ar_AE"

        if HAS_CFFI:
            fps = ["safari17_0", "chrome120", "chrome116", "edge101"]
            random.shuffle(fps)
            for fp in fps[:2 if fast_mode else 3]:
                try:
                    sess = cffi_requests.Session(impersonate=fp)
                    r = sess.get(url, headers=base_headers, timeout=9 if fast_mode else 11)
                    if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                        return r.text
                except Exception:
                    pass

        if HAS_CLOUDSCRAPER:
            try:
                local_scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
                r = local_scraper.get(url, headers=base_headers, timeout=9 if fast_mode else 11)
                if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                    return r.text
            except Exception:
                pass

        if fast_mode:
            return ""

        sep_cb = "&" if "?" in url else "?"
        fresh_url = f"{url}{sep_cb}_cb={int(time.time() // 300)}"
        enc = urllib.parse.quote(fresh_url, safe="")
        proxies = [
            ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
            ("AllOrigins-Raw", f"https://api.allorigins.win/raw?url={enc}"),
            ("JinaReader-HTML", f"https://r.jina.ai/{url}")
        ]
        for pname, purl in proxies:
            try:
                h = {"User-Agent": random.choice(UA_LIST), "Accept-Language": AR_LANG, "Cache-Control": "no-cache"}
                if pname == "JinaReader-HTML":
                    h["X-Return-Format"] = "html"
                    h["X-With-Links-Summary"] = "true"
                r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=14) if HAS_CFFI else requests.get(purl, headers=h, timeout=14)
                if r.status_code != 200 or not r.text:
                    continue
                if not is_blocked_page(r.text, 200, is_amazon=is_amazon):
                    return r.text
            except Exception:
                continue

        return ""


# ================= استخراج المنتجات للمتاجر =================

def _strip_installment_nodes(container) -> None:
    for bad_el in container.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara|bnpl|valu", re.I)):
        bad_el.decompose()
    for tag in container.find_all(["span", "div", "p", "small", "li"]):
        if not tag.find(True):
            txt = tag.get_text(" ", strip=True)
            if 0 < len(txt) <= 90 and has_installment_or_blocked(txt, set()):
                tag.decompose()


def _element_has_egp(price_el) -> bool:
    if price_el is None:
        return False
    for node in (price_el, price_el.parent, price_el.parent.parent if price_el.parent else None):
        if node is not None:
            txt = node.get_text(" ", strip=True)
            if len(txt) <= 120 and _EGP_TOKEN_RE.search(txt):
                return True
    return False


def extract_amazon_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "", kw_tag: str = "") -> List[dict]:
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
        if len(title) < 5 and div.find("img", alt=True):
            title = clean_title(div.find("img", alt=True)["alt"])
        if len(title) < 5 or has_installment_or_blocked(title, custom_blocked, custom_bigrams):
            continue

        cur = old = 0
        for p in div.select("span.a-price"):
            full_price_txt = p.get_text(" ", strip=True)
            parent_txt = p.parent.get_text(" ", strip=True) if p.parent else full_price_txt
            if _UNIT_PRICE_RE.search(parent_txt) or has_installment_or_blocked(parent_txt, custom_blocked):
                continue
            has_local_egp = _element_has_egp(p)
            off_txt = (p.select_one("span.a-offscreen") or p).get_text(" ", strip=True)
            val = parse_price(off_txt, custom_blocked, explicit_egp=has_local_egp)
            if p.get("data-a-strike") == "true" or "a-text-price" in (p.get("class") or []):
                old = old or val
            else:
                cur = cur or val

        if not cur and div.select_one("span.a-price-whole"):
            whole_el = div.select_one("span.a-price-whole")
            if _element_has_egp(whole_el):
                cur = parse_price(whole_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True)

        if cur < MIN_PRODUCT_PRICE_GLOBAL or is_phone_accessory(title, cur):
            continue

        card_text = div.get_text(" ", strip=True).translate(_TRANS)
        rating = 0.0
        reviews = 0
        m_rate = re.search(r"([1-5](?:[.,]\d)?)\s*(?:من\s*5|out\s*of\s*5)", card_text, re.I)
        if m_rate:
            rating = safe_float(m_rate.group(1))
        m_rev = re.search(r"\((\d{1,3}(?:,\d{3})*|\d+)\)", card_text)
        if m_rev:
            reviews = safe_int(m_rev.group(1).replace(",", ""))

        has_event_badge = bool(_EVENT_BADGE_RE.search(card_text))

        m_coup = _REAL_COUPON_CODE_RE.search(card_text)
        coupon_txt = m_coup.group(0).strip() if m_coup else ""
        net_cur = compute_net_coupon_price(cur, coupon_txt)
        if net_cur < cur and not old:
            old = cur

        seen_asins.add(asin)
        items.append({
            "title": title,
            "price": net_cur,
            "old": old if old > net_cur else 0,
            "link": f"https://www.amazon.eg/dp/{asin}",
            "coupon": coupon_txt,
            "has_event_badge": has_event_badge,
            "rating": rating,
            "reviews": reviews,
            "hint_cat": hint_cat,
            "kw": kw_tag
        })
    return items


def _is_noon_root_egp(root_json: dict) -> bool:
    if not isinstance(root_json, dict):
        return False
    props = root_json.get("props")
    if not isinstance(props, dict):
        return False
    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        return False
    for key in ("CleanState", "initialState", "appState", "config", "locale"):
        sub = page_props.get(key)
        if isinstance(sub, dict):
            curr = str(sub.get("currency") or sub.get("currencyCode") or "").upper()
            country = str(sub.get("country") or sub.get("countryCode") or "").lower()
            if curr == "EGP" or country == "eg":
                return True
    return False


def extract_noon_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "", kw_tag: str = "") -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            root_json = json.loads(next_script.string)
            app_curr_egp = _is_noon_root_egp(root_json)

            stack = [root_json]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    sku = node.get("sku") or node.get("catalog_sku")
                    name = node.get("name") or node.get("title")
                    raw_curr = str(node.get("currency") or node.get("currency_code") or "").upper()
                    is_node_egp = (raw_curr == "EGP") or (raw_curr == "" and app_curr_egp)
                    if sku and is_node_egp and isinstance(name, str) and len(name) >= 5 and not has_installment_or_blocked(name, custom_blocked, custom_bigrams):
                        raw_sale = node.get("sale_price")
                        raw_price = node.get("price")
                        raw_was = (
                            node.get("was_price")
                            or node.get("crossed_price")
                            or node.get("original_price")
                            or node.get("retail_price")
                            or (node.get("pricing", {}).get("was") if isinstance(node.get("pricing"), dict) else None)
                        )

                        p_sale = parse_price(raw_sale, custom_blocked, explicit_egp=True)
                        p_reg = parse_price(raw_price, custom_blocked, explicit_egp=True)
                        p_was = parse_price(raw_was, custom_blocked, explicit_egp=True)

                        cur = p_sale if p_sale > 0 else p_reg
                        old = p_was if p_was > cur else (p_reg if (p_sale > 0 and p_reg > p_sale) else 0)
                        rating = safe_float(node.get("product_rating") or node.get("rating"))
                        reviews = safe_int(node.get("num_reviews") or node.get("rating_count") or node.get("reviews_count"))
                        plp_tags = str(node.get("plp_specifications") or node.get("deal_tag") or node.get("badge") or "")
                        has_ev = bool(_EVENT_BADGE_RE.search(plp_tags)) or bool(node.get("is_express") and old > cur)

                        link = f"https://www.noon.com/egypt-ar/{str(node.get('url') or 'deal').strip('/')}/{sku}/p/"
                        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(name, cur) and link not in seen_links:
                            seen_links.add(link)
                            items.append({
                                "title": clean_title(name), "price": cur, "old": old, "link": link,
                                "coupon": "", "has_event_badge": has_ev, "rating": rating,
                                "reviews": reviews, "hint_cat": hint_cat, "kw": kw_tag
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
        if "/p/" not in href or ("/egypt-" not in href and href.startswith("http") and "egypt" not in href):
            continue
        link = urllib.parse.urljoin("https://www.noon.com", href).split("?")[0]
        if link in seen_links:
            continue

        _strip_installment_nodes(a)

        t_el = a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)}) or a.find(["h2", "h3"])
        title = clean_title(t_el.get_text(" ", strip=True) if t_el else a.get("title") or "")
        if len(title) < 5 and a.find("img", alt=True):
            title = clean_title(a.find("img", alt=True)["alt"])
        if len(title) < 5 or has_installment_or_blocked(title, custom_blocked, custom_bigrams):
            continue

        p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
        p_was = a.find(attrs={"data-qa": re.compile(r"old-price|price-was", re.I)}) or a.find(class_=re.compile(r"priceWas|oldPrice|strike", re.I))

        cur = parse_price(p_now.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(p_now)) if p_now else 0
        old = parse_price(p_was.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(p_was)) if p_was else 0
        has_ev = bool(_EVENT_BADGE_RE.search(a.get_text(" ", strip=True)))

        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(title, cur):
            seen_links.add(link)
            items.append({
                "title": title, "price": cur, "old": old if old > cur else 0, "link": link,
                "coupon": "", "has_event_badge": has_ev, "rating": 0.0, "reviews": 0,
                "hint_cat": hint_cat, "kw": kw_tag
            })
    return items


def extract_jumia_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "", kw_tag: str = "") -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    cards = soup.select("article.prd, div.sku, article.c-prd")
    for card in cards:
        a = card.find("a", href=True)
        if not a or not a["href"].strip():
            continue
        link = urllib.parse.urljoin("https://www.jumia.com.eg", a["href"].strip()).split("?")[0]
        if link in seen_links:
            continue

        _strip_installment_nodes(card)

        name_el = card.select_one("h3.name, div.name, h2.name")
        title = clean_title(name_el.get_text(" ", strip=True) if name_el else (a.get("title") or ""))
        if len(title) < 5 or has_installment_or_blocked(title, custom_blocked, custom_bigrams):
            continue

        prc_el = card.select_one("div.prc, span.prc")
        old_el = card.select_one("div.old, span.old")

        cur = parse_price(prc_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True) if prc_el else 0
        old = parse_price(old_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True) if old_el else 0

        rating = 0.0
        reviews = 0
        rev_el = card.select_one("div.rev")
        if rev_el:
            rev_txt = rev_el.get_text(" ", strip=True).translate(_TRANS)
            m_rt = re.search(r"([1-5](?:[.,]\d)?)\s*out\s*of\s*5", rev_txt, re.I)
            if m_rt:
                rating = safe_float(m_rt.group(1))
            m_rv = re.search(r"\((\d+)\)", rev_txt)
            if m_rv:
                reviews = safe_int(m_rv.group(1))

        has_ev = bool(_EVENT_BADGE_RE.search(card.get_text(" ", strip=True))) or bool(card.select_one(".bdg._mall, .bdg._bf"))

        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(title, cur):
            seen_links.add(link)
            items.append({
                "title": title, "price": cur, "old": old if old > cur else 0, "link": link,
                "coupon": "", "has_event_badge": has_ev, "rating": rating, "reviews": reviews,
                "hint_cat": hint_cat, "kw": kw_tag
            })
    return items


def extract_general_items(html_text: str, base_domain: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "", kw_tag: str = "") -> List[dict]:
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
        if len(title) < 5 or has_installment_or_blocked(title, custom_blocked, custom_bigrams):
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

        cur = parse_price(sp_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True) if sp_el else 0
        old = parse_price(old_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True) if old_el else 0

        if not cur:
            for pt in card.find_all(class_=re.compile(r"price", re.I)):
                v = parse_price(pt.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(pt))
                if v >= MIN_PRODUCT_PRICE_GLOBAL:
                    cur = v
                    break

        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(title, cur):
            seen_links.add(link)
            items.append({
                "title": title, "price": cur, "old": old if old > cur else 0, "link": link,
                "coupon": "", "has_event_badge": False, "rating": 0.0, "reviews": 0,
                "hint_cat": hint_cat, "kw": kw_tag
            })
    return items


# ================= محرك التحقق المباشر من المنافسين قبل الإرسال (Live Cross-Store Verifier) =================

def _live_search_store(target_store: str, query: str) -> List[dict]:
    cache_key = (target_store, query.lower())
    with _LIVE_CACHE_LOCK:
        if cache_key in _LIVE_SEARCH_CACHE:
            return _LIVE_SEARCH_CACHE[cache_key]

    enc_q = urllib.parse.quote_plus(query)
    if target_store == "noon":
        url = f"https://www.noon.com/egypt-ar/search/?q={enc_q}&limit=20"
        html_txt = smart_fetch(url, "noon", is_amazon=False, fast_mode=True)
        res = extract_noon_items(html_txt, set(), set()) if html_txt else []
    else:
        url = f"https://www.amazon.eg/s?k={enc_q}&language=ar_AE"
        html_txt = smart_fetch(url, "amazon", is_amazon=True, fast_mode=True)
        res = extract_amazon_items(html_txt, set(), set()) if html_txt else []

    enriched = []
    for item in res[:18]:
        ident = analyze_product_identity(item["title"])
        item["brand"] = ident["brand"]
        item["category"] = ident["category"]
        item["specs"] = ident["specs"]
        item["lookup_keys"] = ident["lookup_keys"]
        item["core_tokens"] = ident["core_tokens"]
        item["exact_model"] = ident["exact_model"]
        enriched.append(item)

    with _LIVE_CACHE_LOCK:
        _LIVE_SEARCH_CACHE[cache_key] = enriched
    return enriched


def live_verify_candidate(cand: dict) -> Tuple[int, str, str]:
    """
    التحقق المباشر الحي من سعر نفس المنتج في المتجر المنافس الرئيسي قبل إرسال التنبيه:
    - لو العرض من أمازون -> يبحث عنه فوراً في نون.
    - لو العرض من نون أو أي متجر آخر -> يبحث عنه فوراً في أمازون (ونون لو العرض ليس من نون).
    يُرجع: (أقل سعر مطابق لدى المنافس، اسم المتجر المنافس، رابط المنافس).
    """
    query = cand.get("search_query", "").strip()
    if len(query) < 4:
        return (0, "", "")

    my_canon = cand.get("canonical_seller", "")
    targets = ["noon"] if my_canon == "amazon" else (["amazon"] if my_canon == "noon" else ["amazon", "noon"])

    cand_cat = cand.get("category", "GEN")
    cand_brand = cand.get("brand", "")
    cand_specs = cand.get("specs", {})
    cand_keys = set(cand.get("lookup_keys", []))
    cand_tokens = set(cand.get("core_tokens", set()))
    cur_p = cand["price"]

    best_comp_price = 0
    best_comp_store = ""
    best_comp_link = ""

    for t_store in targets:
        results = _live_search_store(t_store, query)
        for item in results:
            p = item["price"]
            # حماية ضد مقارنة إكسسوار رخيص بجهاز غالٍ
            if not (cur_p * 0.45 <= p <= cur_p * 2.25):
                continue
            if item["category"] != cand_cat:
                continue
            if cand_brand and item["brand"] and item["brand"] != cand_brand:
                continue
            if not specs_are_compatible(cand_specs, item["specs"], cand_cat):
                continue

            item_keys = set(item.get("lookup_keys", []))
            matched = False
            if cand_keys and item_keys and (cand_keys & item_keys):
                matched = True
            elif cand_tokens and item.get("core_tokens"):
                overlap = len(cand_tokens & item["core_tokens"])
                union = len(cand_tokens | item["core_tokens"])
                if union > 0 and (overlap / union) >= 0.55 and overlap >= 2:
                    matched = True

            if matched:
                if best_comp_price == 0 or p < best_comp_price:
                    best_comp_price = p
                    best_comp_store = t_store
                    best_comp_link = item["link"]

    return (best_comp_price, best_comp_store, best_comp_link)


# ================= سجل الأسعار وذاكرة الموديل والتعلم التلقائي الصامت =================

def update_and_get_model_baseline(rules: dict, variant_key: str, cur_price: int, exact_model: bool, now: int) -> int:
    if not exact_model or not variant_key or cur_price < MIN_PRODUCT_PRICE_GLOBAL:
        return 0
    baselines = rules.setdefault("model_baselines", {})
    rec = baselines.get(variant_key)
    if isinstance(rec, dict):
        old_med = safe_int(rec.get("med"))
        cnt = safe_int(rec.get("cnt"))
        if old_med > 0:
            new_med = int(round((old_med * min(cnt, 10) + cur_price) / (min(cnt, 10) + 1)))
            baselines[variant_key] = {"med": new_med, "cnt": min(150, cnt + 1), "ts": now}
            return old_med if cnt >= 3 else 0
    baselines[variant_key] = {"med": cur_price, "cnt": 1, "ts": now}
    return 0


def record_and_analyze_history(
    history: dict,
    state: dict,
    rules: dict,
    deal_hash: str,
    clean_link: str,
    cur_price: int,
    now: int
) -> Dict[str, Any]:
    # التعلم التلقائي الصامت: لو ارتفع سعر صفقة أرسلناها خلال آخر 14 يوماً بنسبة >= 10% -> تأكد أنها كانت صفقة حقيقية!
    prev_sent = state.get(deal_hash)
    if isinstance(prev_sent, dict) and not prev_sent.get("auto_rewarded"):
        sent_price = safe_int(prev_sent.get("price"))
        if sent_price > 0 and cur_price >= sent_price * 1.10:
            prev_sent["auto_rewarded"] = True
            b_name = prev_sent.get("brand", "")
            c_name = prev_sent.get("category", "")
            kw_used = prev_sent.get("kw", "")
            st_canon = get_canonical_store_id(prev_sent.get("store", ""))
            if b_name:
                rules.setdefault("liked_brands", {})[b_name] = rules.get("liked_brands", {}).get(b_name, 0) + 1
            if c_name:
                rules.setdefault("category_affinity", {})[c_name] = min(10, rules.get("category_affinity", {}).get(c_name, 0) + 1)
            if kw_used:
                rules.setdefault("keyword_scores", {})[kw_used] = min(100, rules.get("keyword_scores", {}).get(kw_used, 0) + 3)
            if st_canon:
                rules.setdefault("store_discount_modifiers", {})[st_canon] = max(-3, rules.get("store_discount_modifiers", {}).get(st_canon, 0) - 1)
            rules.setdefault("stats", {})["auto_verified_wins"] = safe_int(rules.get("stats", {}).get("auto_verified_wins")) + 1
            log(f"🎯 توثيق تلقائي: ارتفع سعر [{prev_sent.get('title', '')[:30]}] من {sent_price} إلى {cur_price} ج.م!")

    rec = history.get(clean_link)
    daily_buckets: Dict[int, Tuple[int, int]] = {}

    if isinstance(rec, dict):
        raw_pts = rec.get("pts")
        if isinstance(raw_pts, list):
            for item in raw_pts:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    ts_v = safe_int(item[0])
                    p_v = safe_int(item[1])
                    if p_v >= MIN_PRODUCT_PRICE_GLOBAL and 1600000000 < ts_v <= now:
                        day_key = ts_v // DAY
                        if day_key not in daily_buckets or ts_v >= daily_buckets[day_key][0]:
                            daily_buckets[day_key] = (ts_v, p_v)

    cutoff_90 = now - 90 * DAY
    cutoff_30 = now - 30 * DAY

    sorted_pts_90 = sorted((ts, p) for ts, p in daily_buckets.values() if ts >= cutoff_90)
    sorted_pts_30 = [(ts, p) for ts, p in sorted_pts_90 if ts >= cutoff_30]

    prices_30d = [p for _, p in sorted_pts_30]
    prices_90d = [p for _, p in sorted_pts_90]
    obs_30 = len(sorted_pts_30)
    span_days_30 = ((sorted_pts_30[-1][0] - sorted_pts_30[0][0]) // DAY) if obs_30 >= 2 else 0

    if obs_30 >= 6 and span_days_30 >= 7:
        strength = "strong"
    elif obs_30 >= 3 and span_days_30 >= 3:
        strength = "medium"
    elif obs_30 >= 2 and span_days_30 >= 1:
        strength = "weak"
    else:
        strength = "none"

    stats: Dict[str, Any] = {
        "min_30d": min(prices_30d) if prices_30d else 0,
        "median_30d": int(statistics.median(prices_30d)) if prices_30d else 0,
        "min_90d": min(prices_90d) if prices_90d else 0,
        "obs_days_30d": obs_30,
        "span_days_30d": span_days_30,
        "history_strength": strength
    }

    today_key = now // DAY
    daily_buckets[today_key] = (now, cur_price)
    updated_pts = sorted((ts, p) for ts, p in daily_buckets.values() if 1600000000 < ts <= now and ts >= cutoff_90)
    history[clean_link] = {"pts": [[ts, p] for ts, p in updated_pts], "ts": now}
    return stats


# ================= محرك التقييم الصارم (Zero-Trust Strikethrough Engine) =================

CATEGORY_LABELS_AR = {
    "GROC": "🛒 بقالة وسوبرماركت",
    "PERF": "✨ عطور وتجميل",
    "FASH": "👕 ملابس وأحذية",
    "WATCH": "⌚ ساعات",
    "TOOLS": "🛠 عدد وأدوات",
    "MOB": "📱 موبايلات وتابلت",
    "LAP": "💻 لابتوب وكمبيوتر",
    "TV": "📺 شاشات وتلفزيونات",
    "APPL": "🏠 أجهزة منزلية ومطبخ",
    "GEN": "🛍 عروض متنوعة"
}


def evaluate_deal_strict(
    cur: int,
    old: int,
    cat: str,
    brand: str,
    title: str,
    h_stats: Dict[str, Any],
    market_lowest_other: int,
    market_comp_store: str,
    model_baseline_med: int,
    exact_model: bool,
    has_coupon: bool = False,
    has_event_badge: bool = False,
    brand_liked: bool = False,
    cat_bonus: int = 0,
    store_disc_mod: int = 0,
    rating: float = 0.0,
    reviews: int = 0
) -> Tuple[bool, str, int, int, int, str, bool]:
    """
    ترجع: (هل مقبول؟, نوع الصفقة, السعر المرجعي, نسبة التوفير, درجة الثقة, سبب الرفض, هل يحتاج تحقق مباشر؟)
    """
    if 0.1 <= rating < 3.6:
        return (False, "", 0, 0, 0, "low_product_rating", False)

    # رفض المنتجات المجهولة/الجينيريك التي ليس لها ماركة ولا تقييمات قوية
    if is_generic_product(title, brand) and not (reviews >= 20 and rating >= 4.2):
        return (False, "", 0, 0, 0, "generic_unbranded_junk", False)

    if cat == "GROC":
        min_price_cat, min_saving = 40, 20
    elif cat in ("PERF", "FASH", "TOOLS", "WATCH"):
        min_price_cat, min_saving = 85, 50
    else:
        min_price_cat, min_saving = 120, 120

    if cur < min_price_cat:
        return (False, "", 0, 0, 0, "below_min_price", False)

    median_30d = safe_int(h_stats.get("median_30d"))
    min_30d = safe_int(h_stats.get("min_30d"))
    obs_days_30d = safe_int(h_stats.get("obs_days_30d"))
    strength = str(h_stats.get("history_strength") or "none")

    has_competitor = market_lowest_other > 0
    # لو يوجد منافس أرخص أو بفارق تافه (< 4%) -> رفض فوري!
    if has_competitor and market_lowest_other <= cur * 1.03:
        return (False, "", 0, 0, 0, "competitor_is_cheaper_or_equal", False)

    has_market_proof = has_competitor and (cur * 1.06 <= market_lowest_other <= cur * 1.95)
    has_strong_history = strength in ("strong", "medium") and median_30d > 0 and obs_days_30d >= 3

    rating_bonus = 5 if (rating >= 4.3 and reviews >= 15) else (3 if (rating >= 4.0 and reviews >= 6) else 0)
    c_extra = (4 if brand_liked else 0) + min(4, max(-3, cat_bonus)) + rating_bonus

    # 1. المسار الأول الأقوى: أرخص فعلياً من المنافسين (تم إثباته من السوق)
    if has_market_proof:
        gap_pct = round((market_lowest_other - cur) / market_lowest_other * 100)
        saving = market_lowest_other - cur
        if gap_pct >= 6 and (saving >= min_saving or cur < 800):
            # تأكد أيضاً أن السعر الحالي ليس أعلى من تاريخه الخاص
            if median_30d > 0 and cur > median_30d * 1.05:
                return (False, "", 0, 0, 0, "above_own_history_median", False)
            c_score = min(99, 62 + min(22, gap_pct) + (10 if has_strong_history else 0) + c_extra)
            if c_score >= MIN_CONF_VERIFIED:
                return (True, "أرخص من المنافسين", market_lowest_other, gap_pct, c_score, "ok", False)

    # 2. المسار الثاني: هبوط تاريخي حقيقي موثق في الذاكرة (3+ أيام رصد منفصلة)
    if has_strong_history and median_30d > cur and (min_30d == 0 or cur <= min_30d):
        drop_pct = round((median_30d - cur) / median_30d * 100)
        saving = median_30d - cur
        if 9 <= drop_pct <= 60 and (saving >= min_saving or drop_pct >= 12):
            c_score = min(97, 58 + min(20, drop_pct) + (8 if strength == "strong" else 4) + c_extra)
            if c_score >= MIN_CONF_VERIFIED:
                return (True, "هبوط تاريخي صامت موثق", median_30d, drop_pct, c_score, "ok", True)

    # 3. المسار الثالث: أرخص من متوسط سعر الموديل في الذاكرة السوقية
    if exact_model and model_baseline_med >= cur * 1.12:
        drop_pct = round((model_baseline_med - cur) / model_baseline_med * 100)
        saving = model_baseline_med - cur
        if 11 <= drop_pct <= 55 and saving >= min_saving:
            c_score = min(92, 58 + min(18, drop_pct) + c_extra)
            if c_score >= MIN_CONF_VERIFIED:
                return (True, "أقل من متوسط سعر السوق للموديل", model_baseline_med, drop_pct, c_score, "ok", True)

    # 4. المسار الرابع المقيد: عروض الفعاليات/الكوبونات للماركات المعروفة فقط (تُرسل أولاً للتحقق المباشر!)
    # نرفض تماماً الخصم المشطوب الأعمى! لابد من (ماركة معروفة) + (شارة فعالية أو كوبون أو تقييمات قوية)
    if old > cur:
        if median_30d > 0 and cur > median_30d * 1.02:
            return (False, "", 0, 0, 0, "fake_inflated_old_price", False)

        effective_old = int(median_30d * 1.06) if (median_30d > 0 and old > median_30d * 1.20) else old
        if effective_old <= cur:
            return (False, "", 0, 0, 0, "fake_inflated_old_price", False)

        disc = round((effective_old - cur) / effective_old * 100)
        saving = effective_old - cur
        if disc > MAX_REALISTIC_DISC:
            return (False, "", 0, 0, 0, "unrealistic_discount_pct", False)

        # أي خصم معلن غير موثق فوق 55% يُرفض لأنه في الغالب سعر مشطوب مضخم
        if disc > 55:
            return (False, "", 0, 0, 0, "suspicious_unverified_high_disc", False)

        min_disc_req = max(16, 18 + store_disc_mod)
        has_quality_signal = bool(has_coupon or has_event_badge or (rating >= 4.1 and reviews >= 10))

        if brand and has_quality_signal and disc >= min_disc_req and saving >= min_saving:
            c_score = min(74, 48 + min(14, disc // 2) + (6 if has_coupon else 0) + (5 if has_event_badge else 0) + c_extra)
            if c_score >= MIN_CONF_EVENT_FALLBACK:
                return (True, "مرشح فعالية/كوبون (قيد التحقق المباشر)", effective_old, disc, c_score, "ok", True)

        return (False, "", 0, 0, 0, "rejected_unverified_strikethrough", False)

    return (False, "", 0, 0, 0, "no_real_proof", False)


# ================= مراكز الفعاليات الموسمية (الجمعة البيضاء/الصفراء/البلاك فرايدي) =================

SINGLE_KEYWORD_POOLS: Dict[str, List[str]] = {
    "GROC": ["نسكافيه", "قهوة", "زيت كريستال", "اريال", "برسيل", "داوني", "فيري", "شاي ليبتون", "عسل نحل", "حفاضات بامبرز"],
    "PERF": ["lattafa perfume", "armaf perfume", "afnan perfume", "rasasi perfume", "عطر رجالي اصلي", "لوريال سيروم", "نيفيا", "ماكينة حلاقة براون"],
    "WATCH": ["casio watch", "seiko watch", "citizen watch", "fossil watch", "huawei watch", "xiaomi band"],
    "FASH": ["adidas shoes", "nike shoes", "skechers", "puma shoes", "defacto", "شنطة ظهر لابتوب"],
    "TOOLS": ["شنيور بوش", "شنيور توتال", "شنيور انجكو", "طقم عدة ستانلي", "غسالة ضغط كارتشر", "منفاخ اطارات سيارة"],
    "MOB": ["samsung galaxy a55", "samsung galaxy s24", "iphone 15", "iphone 16", "xiaomi redmi note 13", "realme", "oppo reno", "anker powerbank", "soundcore"],
    "LAP": ["laptop lenovo", "laptop hp", "laptop dell", "laptop asus", "macbook air", "logitech mouse"],
    "TV": ["شاشة سامسونج سمارت", "شاشة ال جي 4k", "شاشة توشيبا", "شاشة شارب", "شاشة تورنيدو"],
    "APPL": ["قلاية هوائية فيليبس", "قلاية هوائية تيفال", "ميكروويف شارب", "خلاط مولينكس", "مكنسة كهربائية", "مكواة بخار تيفال", "تكييف كاريير"]
}


def _next_page_bounded(cursors: Dict[str, int], cursor_key: str, max_page: int = 4) -> Tuple[int, int]:
    """تدوير الصفحات داخل النطاق الذهبي (1 إلى 4) حيث تتركز عروض الفعاليات الحقيقية."""
    cur_p = safe_int(cursors.get(cursor_key), default=1)
    if cur_p < 1 or cur_p > max_page:
        cur_p = 1
    p1 = cur_p
    p2 = (cur_p % max_page) + 1
    cursors[cursor_key] = (p2 % max_page) + 1
    return p1, p2


def _select_evolving_keywords(pool: List[str], kw_scores: Dict[str, int], count: int, run_counter: int) -> List[str]:
    if len(pool) <= count:
        return list(pool)
    scored = []
    for idx, kw in enumerate(pool):
        base_score = kw_scores.get(kw, 0)
        explore_wave = ((idx + run_counter * 2) % len(pool)) * 0.5
        scored.append((base_score + explore_wave + random.uniform(0, 1.5), kw))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [kw for _, kw in scored[:count]]


def build_dynamic_stores(rules: dict) -> List[dict]:
    run_counter = safe_int(rules.get("run_counter")) + 1
    rules["run_counter"] = run_counter
    cursors = rules.setdefault("page_cursors", {})
    kw_scores = rules.setdefault("keyword_scores", {})

    # استخدام ترتيبات عالية الجودة فقط (الأكثر مبيعاً / المميزة / الأعلى تقييماً) وإلغاء price-asc-rank
    amz_sorts = ["exact-aware-popularity-rank", "featured", "review-rank"]
    amz_sort = amz_sorts[run_counter % len(amz_sorts)]
    noon_sort = "sort[by]=popularity&sort[dir]=desc" if run_counter % 2 == 0 else "sort[by]=discount&sort[dir]=desc"

    log(f"🧭 جولة الفعاليات والتحقق المباشر #{run_counter} | ترتيب أمازون=({amz_sort}) | نون=({noon_sort.split('&')[0]})")

    # 1. مراكز الفعاليات وعروض اليوم في أمازون (الجمعة البيضاء / صفقات اليوم / خصم 15%+)
    amazon_urls: List[Tuple[str, str, str]] = [
        ("https://www.amazon.eg/s?rh=p_n_deal_type%3A26901099031&page=1&language=ar_AE", "GEN", "amz_lightning"),
        ("https://www.amazon.eg/s?rh=p_n_deal_type%3A26901099031&page=2&language=ar_AE", "GEN", "amz_lightning"),
        (f"https://www.amazon.eg/s?rh=p_n_deal_type%3A26901098031&s={amz_sort}&page=1&language=ar_AE", "GEN", "amz_today_deals"),
        (f"https://www.amazon.eg/s?rh=p_n_deal_type%3A26901098031&s={amz_sort}&page=2&language=ar_AE", "GEN", "amz_today_deals"),
    ]

    amz_cat_bases = [
        ("amz_elec_wf", "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031", "MOB"),
        ("amz_comp_wf", "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031", "LAP"),
        ("amz_kit_wf", "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031", "APPL"),
        ("amz_appl_wf", "https://www.amazon.eg/s?i=appliances&rh=p_n_pct-off-with-tax%3A15-", "APPL"),
        ("amz_groc_wf", "https://www.amazon.eg/s?i=grocery&rh=p_n_deal_type%3A26901098031", "GROC"),
        ("amz_perf_wf", "https://www.amazon.eg/s?i=beauty&rh=p_n_deal_type%3A26901098031", "PERF"),
        ("amz_fash_wf", "https://www.amazon.eg/s?i=fashion&rh=p_n_deal_type%3A26901098031", "FASH"),
        ("amz_watch_wf", "https://www.amazon.eg/s?i=fashion&bbn=21833215031&rh=p_n_pct-off-with-tax%3A15-", "WATCH"),
        ("amz_tools_wf", "https://www.amazon.eg/s?i=hi&rh=p_n_deal_type%3A26901098031", "TOOLS")
    ]
    for c_key, base_u, cat_code in amz_cat_bases:
        p1, p2 = _next_page_bounded(cursors, c_key, max_page=4)
        amazon_urls.append((f"{base_u}&s={amz_sort}&page={p1}&language=ar_AE", cat_code, c_key))
        amazon_urls.append((f"{base_u}&s=featured&page={p2}&language=ar_AE", cat_code, c_key))

    for cat_code, kw_pool in SINGLE_KEYWORD_POOLS.items():
        chosen_kws = _select_evolving_keywords(kw_pool, kw_scores, count=2, run_counter=run_counter)
        for kw in chosen_kws:
            enc_kw = urllib.parse.quote_plus(kw)
            amazon_urls.append((f"https://www.amazon.eg/s?k={enc_kw}&rh=p_n_pct-off-with-tax%3A10-&s={amz_sort}&page=1&language=ar_AE", cat_code, kw))

    # 2. مراكز فعاليات نون مصر (الجمعة الصفراء + الصفقات اليومية + الأقسام)
    noon_urls: List[Tuple[str, str, str]] = [
        ("https://www.noon.com/egypt-ar/deals/?limit=50&page=1", "GEN", "noon_deals"),
        ("https://www.noon.com/egypt-ar/deals/?limit=50&page=2", "GEN", "noon_deals"),
        ("https://www.noon.com/egypt-ar/yellow-friday/?limit=50&page=1", "GEN", "noon_yf"),
    ]
    noon_cat_bases = [
        ("noon_mob", "https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/", "MOB"),
        ("noon_lap", "https://www.noon.com/egypt-ar/electronics-and-mobiles/computers-and-accessories/laptops-16014/", "LAP"),
        ("noon_tv", "https://www.noon.com/egypt-ar/electronics-and-mobiles/television-and-video/televisions/", "TV"),
        ("noon_appl", "https://www.noon.com/egypt-ar/appliances/", "APPL"),
        ("noon_kit", "https://www.noon.com/egypt-ar/home-and-kitchen/", "APPL"),
        ("noon_groc", "https://www.noon.com/egypt-ar/grocery/", "GROC"),
        ("noon_perf", "https://www.noon.com/egypt-ar/beauty-and-health/fragrance/", "PERF"),
        ("noon_watch", "https://www.noon.com/egypt-ar/watches/", "WATCH"),
        ("noon_fash", "https://www.noon.com/egypt-ar/fashion/", "FASH"),
        ("noon_tools", "https://www.noon.com/egypt-ar/tools-and-home-improvement/", "TOOLS")
    ]
    for c_key, base_u, cat_code in noon_cat_bases:
        p1, p2 = _next_page_bounded(cursors, c_key, max_page=4)
        noon_urls.append((f"{base_u}?{noon_sort}&limit=50&page={p1}", cat_code, c_key))
        noon_urls.append((f"{base_u}?sort[by]=popularity&sort[dir]=desc&limit=50&page={p2}", cat_code, c_key))

    # 3. فعاليات جوميا مصر (البلاك فرايدي + الفلاش سيل + المتاجر الرسمية)
    jumia_urls: List[Tuple[str, str, str]] = [
        ("https://www.jumia.com.eg/ar/flash-sales/?page=1", "GEN", "jum_flash"),
        ("https://www.jumia.com.eg/ar/mlp-black-friday/?page=1", "GEN", "jum_bf"),
        ("https://www.jumia.com.eg/ar/phones-tablets/?sort=popularity&page=1", "MOB", "jum_mob"),
        ("https://www.jumia.com.eg/ar/computing/?sort=popularity&page=1", "LAP", "jum_lap"),
        ("https://www.jumia.com.eg/ar/home-office-appliances/?sort=popularity&page=1", "APPL", "jum_appl"),
        ("https://www.jumia.com.eg/ar/groceries/?sort=popularity&page=1", "GROC", "jum_groc"),
        ("https://www.jumia.com.eg/ar/health-beauty/?sort=popularity&page=1", "PERF", "jum_perf"),
        ("https://www.jumia.com.eg/ar/watches-sunglasses/?sort=popularity&page=1", "WATCH", "jum_watch"),
        ("https://www.jumia.com.eg/ar/home-improvement-tools/?sort=popularity&page=1", "TOOLS", "jum_tools")
    ]

    p_2b1, p_2b2 = _next_page_bounded(cursors, "store_2b", max_page=4)
    p_ran1, p_ran2 = _next_page_bounded(cursors, "store_raneen", max_page=4)
    p_drm1, p_drm2 = _next_page_bounded(cursors, "store_dream", max_page=4)

    return [
        {"id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg", "type": "amazon", "max_deals": MAX_DEALS_AMAZON, "urls": amazon_urls},
        {"id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com", "type": "noon", "max_deals": MAX_DEALS_NOON, "urls": noon_urls},
        {"id": "jumia", "name": "جوميا مصر (Jumia)", "domain": "https://www.jumia.com.eg", "type": "jumia", "max_deals": MAX_DEALS_JUMIA, "urls": jumia_urls},
        {
            "id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg", "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                (f"https://2b.com.eg/ar/deals.html?p={p_2b1}", "GEN", "2b_deals"),
                (f"https://2b.com.eg/ar/mobiles-tablets.html?p={p_2b1}", "MOB", "2b_mob"),
                (f"https://2b.com.eg/ar/computers.html?p={p_2b1}", "LAP", "2b_lap"),
                (f"https://2b.com.eg/ar/tvs.html?p={p_2b1}", "TV", "2b_tv"),
                (f"https://2b.com.eg/ar/home-appliances.html?p={p_2b2}", "APPL", "2b_appl")
            ]
        },
        {
            "id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com", "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                (f"https://www.raneen.com/ar/deals?p={p_ran1}", "APPL", "ran_deals"),
                (f"https://www.raneen.com/ar/deals?p={p_ran2}", "APPL", "ran_deals"),
                ("https://www.raneen.com/ar/deals/appliances-electronics-offers", "APPL", "ran_elec")
            ]
        },
        {
            "id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com", "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                (f"https://dream2000.com/mobiles.html?p={p_drm1}", "MOB", "drm_mob"),
                (f"https://dream2000.com/mobiles.html?p={p_drm2}", "MOB", "drm_mob"),
                (f"https://dream2000.com/home-appliances.html?p={p_drm1}", "APPL", "drm_appl"),
                (f"https://dream2000.com/laptops-and-ipads.html?p={p_drm1}", "LAP", "drm_lap")
            ]
        }
    ]


# ================= دالة العامل المتوازي =================

def _fetch_and_extract_task(
    task: Tuple[dict, str, str, str],
    custom_blocked: Set[str],
    custom_bigrams: Set[str]
) -> Tuple[str, bool, List[dict]]:
    st, url, hint_cat, kw_tag = task
    sid, sname, stype = st["id"], st["name"], st["type"]
    is_amz = (stype == "amazon")

    html_txt = smart_fetch(url, sid, is_amazon=is_amz)
    if not html_txt:
        return (sid, False, [])

    if is_amz:
        ext = extract_amazon_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat, kw_tag=kw_tag)
    elif stype == "noon":
        ext = extract_noon_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat, kw_tag=kw_tag)
    elif stype == "jumia":
        ext = extract_jumia_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat, kw_tag=kw_tag)
    else:
        ext = extract_general_items(html_txt, st["domain"], custom_blocked, custom_bigrams, hint_cat=hint_cat, kw_tag=kw_tag)

    for item in ext:
        item["store"] = sname
        item["canonical_seller"] = get_canonical_store_id(sname)
        item["sid"] = sid
        ident = analyze_product_identity(item["title"], hint_cat=item.get("hint_cat", ""))
        item["sig"] = ident["sig"]
        item["lookup_keys"] = ident["lookup_keys"]
        item["variant_key"] = ident["variant_key"]
        item["search_query"] = ident["search_query"]
        item["core_tokens"] = ident["core_tokens"]
        item["specs"] = ident["specs"]
        item["exact_model"] = ident["exact_model"]
        item["brand"] = ident["brand"]
        item["category"] = ident["category"]

    return (sid, True, ext)


# ================= الحلقة التنفيذية الرئيسية =================

def main() -> None:
    t0 = time.time()
    now = int(t0)

    state = sanitize_state(load_json(STATE_FILE))
    history = sanitize_history(load_json(HISTORY_FILE), now)
    rules = sanitize_rules(load_json(RULES_FILE))
    market_index = sanitize_market_index(load_json(MARKET_FILE), now)

    git_ok = False
    stores: List[dict] = []
    store_diag: Dict[str, dict] = {}
    total_sent = 0
    fake_caught_this_run = 0
    live_verified_wins = 0
    cat_sent_counter: Counter = Counter()

    try:
        process_telegram_feedback(rules, state, now)
        custom_blocked = {normalize_text(w) for w in rules.get("blocked_keywords", []) if w}
        custom_bigrams = {normalize_text(bg) for bg in rules.get("blocked_bigrams", []) if bg}
        blocked_deals = set(rules.get("blocked_deals", []))
        liked_brands = rules.get("liked_brands", {})
        cat_affinity = rules.get("category_affinity", {})
        store_modifiers = rules.get("store_discount_modifiers", {})
        kw_scores = rules.setdefault("keyword_scores", {})

        # الاحتفاظ بسجل الصفقات المرسلة لمدة 14 يوماً كاملة لمنع أي تكرار وحفظ صلاحية أزرار تليجرام
        state = {k: v for k, v in state.items() if isinstance(v, dict) and (now - v.get("ts", 0)) < REALERT_DAYS * DAY}

        # بناء فهرس بأقل سعر أُرسل لكل هوية منتج خلال آخر 14 يوماً
        sent_by_variant: Dict[str, int] = {}
        for rec in state.values():
            vk = rec.get("variant_key", "")
            p_sent = safe_int(rec.get("price"))
            if vk and p_sent > 0:
                if vk not in sent_by_variant or p_sent < sent_by_variant[vk]:
                    sent_by_variant[vk] = p_sent

        stores = build_dynamic_stores(rules)
        all_raw_items: List[dict] = []
        store_diag = {
            st["id"]: {
                "opened": False, "raw": 0, "unique": 0, "exact_models": 0,
                "cross_matched": 0, "accepted": 0, "sent": 0, "reject_reasons": Counter()
            }
            for st in stores
        }

        tasks: List[Tuple[dict, str, str, str]] = []
        for st in stores:
            for u_entry in st["urls"]:
                tasks.append((st, u_entry[0], u_entry[1], u_entry[2]))

        log(f"⚡ جاري مسح {len(tasks)} صفحة فعاليات ومتاجر بالتوازي عبر {MAX_WORKERS} مسارات...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_fetch_and_extract_task, t, custom_blocked, custom_bigrams) for t in tasks]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    sid, ok, ext_items = fut.result()
                    if ok:
                        store_diag[sid]["opened"] = True
                        store_diag[sid]["raw"] += len(ext_items)
                        all_raw_items.extend(ext_items)
                except Exception as e:
                    log(f"⚠ خطأ فرعي: {e}")

        log(f"📦 إجمالي السلع المستخرجة: {len(all_raw_items):,} سلعة.")

        # 1. بناء الفهرس الحي داخل الجولة + تحديث ذاكرة السوق الدائمة (market_index.json)
        in_run_by_key = defaultdict(list)
        for it in all_raw_items:
            c_st = it.get("canonical_seller", "")
            for lk in it.get("lookup_keys", []):
                in_run_by_key[lk].append((it["price"], c_st, it.get("specs", {}), it["link"]))
            if it["exact_model"] and it.get("variant_key") and c_st:
                vk = it["variant_key"]
                st_bucket = market_index.setdefault(vk, {})
                prev_m = st_bucket.get(c_st)
                if not prev_m or (now - safe_int(prev_m[1]) > DAY) or it["price"] < safe_int(prev_m[0]):
                    st_bucket[c_st] = [it["price"], now, it["link"]]

        items_by_store = defaultdict(list)
        for it in all_raw_items:
            items_by_store[it["sid"]].append(it)

        # 2. مرحلة الفرز الأولي (توليد قائمة المرشحين الأقوياء قبل التحقق المباشر)
        pre_candidates: List[dict] = []

        for st in stores:
            sid, sname = st["id"], st["name"]
            raw_list = items_by_store.get(sid, [])
            diag = store_diag[sid]
            canonical_sid = get_canonical_store_id(sname)
            store_disc_mod = store_modifiers.get(canonical_sid, 0)

            unique_store_items: Dict[str, dict] = {}
            for it in raw_list:
                clean_link = it["link"].split("?")[0].rstrip("/").lower()
                if clean_link not in unique_store_items or it["price"] < unique_store_items[clean_link]["price"]:
                    unique_store_items[clean_link] = it

            diag["unique"] = len(unique_store_items)

            for clean_link, it in unique_store_items.items():
                if it["exact_model"]:
                    diag["exact_models"] += 1

                deal_hash = hashlib.md5(clean_link.encode()).hexdigest()[:16]
                if deal_hash in blocked_deals:
                    diag["reject_reasons"]["user_blocked"] += 1
                    continue

                # منع التكرار الصارم لمدة 14 يوماً (بالرابط وبـهوية المنتج معاً)
                prev_sent = state.get(deal_hash)
                if prev_sent and it["price"] >= prev_sent.get("price", it["price"]) * REALERT_MIN_EXTRA_DROP_RATIO:
                    diag["reject_reasons"]["recently_sent_14d"] += 1
                    continue

                v_key = it.get("variant_key", "")
                if it["exact_model"] and v_key in sent_by_variant and it["price"] >= sent_by_variant[v_key] * REALERT_MIN_EXTRA_DROP_RATIO:
                    diag["reject_reasons"]["recently_sent_variant_14d"] += 1
                    continue

                h_stats = record_and_analyze_history(history, state, rules, deal_hash, clean_link, it["price"], now)
                model_baseline_med = update_and_get_model_baseline(rules, v_key, it["price"], it["exact_model"], now)

                # البحث عن أقل سعر منافس (من الجولة الحالية + من ذاكرة الـ 10 أيام market_index)
                lowest_other = 0
                comp_store_id = ""
                my_canon = it.get("canonical_seller", "")
                my_specs = it.get("specs", {})
                item_cat = it.get("category", "GEN")

                comp_prices: List[Tuple[int, str]] = []
                for lk in it.get("lookup_keys", []):
                    for p, c_st, c_specs, _ in in_run_by_key.get(lk, []):
                        if c_st and c_st != my_canon and specs_are_compatible(my_specs, c_specs, item_cat):
                            if it["price"] * 0.45 <= p <= it["price"] * 2.25:
                                comp_prices.append((p, c_st))

                if it["exact_model"] and v_key in market_index:
                    for c_st, rec_m in market_index[v_key].items():
                        if c_st != my_canon:
                            p_m = safe_int(rec_m[0])
                            if it["price"] * 0.45 <= p_m <= it["price"] * 2.25:
                                comp_prices.append((p_m, c_st))

                if comp_prices:
                    comp_prices.sort(key=lambda x: x[0])
                    lowest_other, comp_store_id = comp_prices[0]
                    diag["cross_matched"] += 1

                is_deal, deal_type, ref_price, saving_pct, conf_score, reason, needs_live = evaluate_deal_strict(
                    cur=it["price"],
                    old=it["old"],
                    cat=item_cat,
                    brand=it.get("brand", ""),
                    title=it["title"],
                    h_stats=h_stats,
                    market_lowest_other=lowest_other,
                    market_comp_store=comp_store_id,
                    model_baseline_med=model_baseline_med,
                    exact_model=it["exact_model"],
                    has_coupon=bool(it.get("coupon")),
                    has_event_badge=bool(it.get("has_event_badge")),
                    brand_liked=bool(liked_brands.get(it["brand"], 0) > 0),
                    cat_bonus=cat_affinity.get(item_cat, 0),
                    store_disc_mod=store_disc_mod,
                    rating=safe_float(it.get("rating")),
                    reviews=safe_int(it.get("reviews"))
                )

                if not is_deal:
                    diag["reject_reasons"][reason] += 1
                    continue

                it["clean_link"] = clean_link
                it["deal_hash"] = deal_hash
                it["deal_type"] = deal_type
                it["ref_price"] = ref_price
                it["saving_pct"] = saving_pct
                it["conf_score"] = conf_score
                it["min_30d"] = h_stats["min_30d"]
                it["obs_days_30d"] = h_stats["obs_days_30d"]
                it["history_strength"] = h_stats["history_strength"]
                it["lowest_other"] = lowest_other
                it["comp_store_id"] = comp_store_id
                it["needs_live_verify"] = needs_live
                it["live_verified"] = False
                pre_candidates.append(it)

        # دمج الألوان والمقاسات قبل التحقق المباشر لتوفير الوقت
        variant_groups: Dict[str, List[dict]] = defaultdict(list)
        for cand in pre_candidates:
            vk = f"{cand['sid']}|{cand.get('variant_key') or cand['clean_link']}"
            variant_groups[vk].append(cand)

        deduped_pool: List[dict] = []
        for grp in variant_groups.values():
            grp.sort(key=lambda x: (x["conf_score"], x["saving_pct"]), reverse=True)
            best = grp[0]
            best["variant_count"] = len(grp)
            deduped_pool.append(best)

        deduped_pool.sort(
            key=lambda x: (
                not x["needs_live_verify"],
                x["conf_score"],
                x["saving_pct"]
            ),
            reverse=True
        )

        # 3. مرحلة التحقق المباشر الحي من المنافسين (Live Cross-Store Verification) لأفضل المرشحين!
        to_verify = deduped_pool[:MAX_LIVE_VERIFICATIONS]
        log(f"🔎 جاري إجراء التحقق المباشر الحي من أسعار المنافسين لـ {len(to_verify)} مرشح قبل الإرسال...")

        final_verified: List[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as v_exec:
            future_to_cand = {v_exec.submit(live_verify_candidate, c): c for c in to_verify}
            for fut in concurrent.futures.as_completed(future_to_cand):
                cand = future_to_cand[fut]
                sid = cand["sid"]
                diag = store_diag[sid]
                try:
                    live_p, live_st, live_link = fut.result()
                except Exception:
                    live_p, live_st, live_link = 0, "", ""

                if live_p > 0 and live_st:
                    # تحديث ذاكرة السوق بالسعر الحي الجديد
                    if cand["exact_model"] and cand.get("variant_key"):
                        market_index.setdefault(cand["variant_key"], {})[live_st] = [live_p, now, live_link]

                    # لو المنافس أرخص أو سعره قريب (الفرق أقل من 5%) -> إحباط العرض فوراً!
                    if live_p <= cand["price"] * 1.04:
                        fake_caught_this_run += 1
                        diag["reject_reasons"]["caught_cheaper_on_competitor_live"] += 1
                        rules["stats"]["fake_caught_live"] = safe_int(rules["stats"].get("fake_caught_live")) + 1
                        # معاقبة تلقائية للمتجر الذي ادّعى خصماً وهمياً
                        my_canon = cand.get("canonical_seller", "")
                        if my_canon and cand["old"] > cand["price"]:
                            store_modifiers[my_canon] = min(10, store_modifiers.get(my_canon, 0) + 1)
                        log(f"🚫 إحباط عرض وهمي [{cand['store']}]: {cand['title'][:40]} بـ {cand['price']:,} ج.م بينما {CANONICAL_AR_NAMES.get(live_st, live_st)} يبيعه بـ {live_p:,} ج.م!")
                        continue

                    # لو السعر الحالي فعلاً أرخص من المنافس المباشر بنسبة >= 6% -> ترقيته لصفقة مؤكدة بالتحقق المباشر!
                    if live_p >= cand["price"] * 1.06:
                        gap_pct = round((live_p - cand["price"]) / live_p * 100)
                        cand["deal_type"] = "أرخص من المنافسين (تحقق مباشر)"
                        cand["ref_price"] = live_p
                        cand["saving_pct"] = gap_pct
                        cand["lowest_other"] = live_p
                        cand["comp_store_id"] = live_st
                        cand["live_verified"] = True
                        cand["conf_score"] = min(99, max(cand["conf_score"], 78 + min(18, gap_pct)))
                        live_verified_wins += 1
                        # مكافأة نظيفة للكلمة البحثية التي أتت بصفقة مثبتة فعلياً
                        if cand.get("kw"):
                            kw_scores[cand["kw"]] = min(100, kw_scores.get(cand["kw"], 0) + 2)
                        diag["accepted"] += 1
                        final_verified.append(cand)
                        continue

                # لم يُعثر على نفس المنتج عند المنافس أثناء البحث الحي:
                # يُقبل فقط إذا كان مدعوماً بدليل قوي (تاريخ حقيقي أو متوسط سوق أو مقارن مسبق) أو ضمن حصة الفعاليات الصارمة
                if "قيد التحقق المباشر" not in cand["deal_type"]:
                    diag["accepted"] += 1
                    final_verified.append(cand)
                else:
                    # عرض فعالية/كوبون لماركة معروفة لم يثبت وجود منافس أرخص منه
                    cand["deal_type"] = "عرض فعالية/كوبون لماركة معروفة"
                    diag["accepted"] += 1
                    final_verified.append(cand)

        for st in stores:
            d = store_diag[st["id"]]
            top_r = ", ".join(f"{k}:{v}" for k, v in d["reject_reasons"].most_common(4)) or "لا يوجد"
            log(f"📋 تشخيص [{st['name']}]: فريد={d['unique']} | موديل={d['exact_models']} | مقارن={d['cross_matched']} | مقبول نهائياً={d['accepted']} | استبعاد=({top_r})")

        # 4. الترتيب النهائي وإرسال الصفوة فقط لتليجرام مع توازن الأقسام والمتاجر
        final_verified.sort(
            key=lambda x: (
                x["live_verified"],
                "فعالية/كوبون" not in x["deal_type"],
                x["conf_score"],
                x["saving_pct"]
            ),
            reverse=True
        )

        store_sent_counts: Counter = Counter()
        cat_sent_limits: Counter = Counter()
        unverified_event_sent = 0
        sent_variant_in_run: Set[str] = set()

        for it in final_verified:
            if total_sent >= MAX_TOTAL_SENT_PER_RUN:
                break

            sid = it["sid"]
            st_obj = next((s for s in stores if s["id"] == sid), None)
            max_st = st_obj["max_deals"] if st_obj else MAX_DEALS_OTHER
            if store_sent_counts[sid] >= max_st:
                continue

            item_cat = it.get("category", "GEN")
            if cat_sent_limits[item_cat] >= 6:
                continue

            is_event_fallback = ("فعالية/كوبون" in it["deal_type"])
            if is_event_fallback and unverified_event_sent >= MAX_UNVERIFIED_EVENT_PER_RUN:
                continue

            vk = it.get("variant_key", "")
            if it["exact_model"] and vk in sent_variant_in_run:
                continue

            saving = it["ref_price"] - it["price"]
            dtype = it["deal_type"]
            conf = it["conf_score"]
            obs_d = it.get("obs_days_30d", 0)
            cat_badge = CATEGORY_LABELS_AR.get(item_cat, "🛍 عروض متنوعة")
            comp_ar = CANONICAL_AR_NAMES.get(it.get("comp_store_id", ""), "المنافسين")

            if dtype == "أرخص من المنافسين (تحقق مباشر)":
                tag = f"🏆 <b>صفقة مؤكدة (تحقق مباشر) — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"🏪 سعر نفس المنتج الآن في <b>{comp_ar}</b>: {it['ref_price']:,} ج.م (أرخص بـ {saving:,} ج.م — {it['saving_pct']}%)"
            elif dtype == "أرخص من المنافسين":
                tag = f"🏆 <b>أقل سعر في السوق — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"🏪 أقل سعر مسجل لدى <b>{comp_ar}</b>: {it['ref_price']:,} ج.م (توفير {saving:,} ج.م — {it['saving_pct']}%)"
            elif dtype == "هبوط تاريخي صامت موثق":
                tag = f"📉 <b>هبوط سعر تاريخي موثق — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"🏷 وسيط السعر ({obs_d} أيام رصد): {it['ref_price']:,} ج.م (نزل {it['saving_pct']}% — وفر {saving:,} ج.م)"
            elif dtype == "أقل من متوسط سعر السوق للموديل":
                tag = f"✅ <b>أقل من متوسط السوق — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"🏷 متوسط سعر الموديل بالسوق: {it['ref_price']:,} ج.م (توفير {saving:,} ج.م — {it['saving_pct']}%)"
            else:
                tag = f"🔥 <b>عرض فعالية/كوبون — {htmllib.escape(it['store'])}</b> (خصم {it['saving_pct']}%)"
                ref_txt = f"ℹ️ السعر السابق بالموقع: {it['ref_price']:,} ج.م (لم يُرصد منافس أرخص أثناء الفحص المباشر)"

            hist_line = (
                f"📊 أقل سعر مسجل آخر 30 يوماً ({obs_d} يوم): {it['min_30d']:,} ج.م\n"
                if it.get("min_30d") and obs_d >= 2 else ""
            )
            rev_cnt = safe_int(it.get("reviews"))
            rev_str = f" ({rev_cnt:,} تقييم)" if rev_cnt > 0 else ""
            rate_line = f"⭐ التقييم: <b>{it['rating']}/5</b>{rev_str}\n" if it.get("rating", 0) >= 3.6 else ""
            var_cnt = safe_int(it.get("variant_count"), 1)
            var_line = f"🎨 <i>متوفر بـ {var_cnt} ألوان/مقاسات</i>\n" if var_cnt > 1 else ""
            coupon_line = f"🎟 <b>السعر يشمل الكوبون: {htmllib.escape(it['coupon'])}</b>\n" if it.get("coupon") else ""
            safe_link = sanitize_url(it["link"])

            msg = (
                f"{tag}\n"
                f"📂 القسم: <b>{cat_badge}</b>\n\n"
                f"📦 {htmllib.escape(it['title'])}\n"
                f"💰 السعر النقدي الصافي: <b>{it['price']:,} ج.م</b>\n"
                f"{ref_txt}\n"
                f"{hist_line}"
                f"{rate_line}"
                f"{var_line}"
                f"{coupon_line}"
                f"🛡 مؤشر الثقة: <b>{conf}/100</b>\n"
                f"🔗 {safe_link}"
            )

            msg_id = send_telegram_deal(msg, it["deal_hash"])
            if msg_id is not None:
                state[it["deal_hash"]] = {
                    "price": it["price"],
                    "ts": now,
                    "title": it["title"],
                    "brand": it["brand"],
                    "category": item_cat,
                    "variant_key": vk,
                    "store": it["store"],
                    "kw": it.get("kw", ""),
                    "link": it["clean_link"],
                    "message_id": msg_id,
                    "auto_rewarded": False,
                    "rated_by": []
                }
                if vk:
                    sent_variant_in_run.add(vk)
                store_sent_counts[sid] += 1
                store_diag[sid]["sent"] += 1
                cat_sent_limits[item_cat] += 1
                cat_sent_counter[cat_badge] += 1
                total_sent += 1
                if is_event_fallback:
                    unverified_event_sent += 1
                if total_sent % 5 == 0:
                    save_json(STATE_FILE, state)
                time.sleep(1.1)

    finally:
        if len(history) > 30000:
            sorted_h = sorted(history.items(), key=lambda kv: safe_int(kv[1].get("ts") if isinstance(kv[1], dict) else 0))
            history = dict(sorted_h[-30000:])
        if len(market_index) > 15000:
            sorted_m = sorted(
                market_index.items(),
                key=lambda kv: max((safe_int(r[1]) for r in kv[1].values()), default=0)
            )
            market_index = dict(sorted_m[-15000:])
        if len(rules.get("model_baselines", {})) > 15000:
            sorted_mb = sorted(rules["model_baselines"].items(), key=lambda kv: safe_int(kv[1].get("ts") if isinstance(kv[1], dict) else 0))
            rules["model_baselines"] = dict(sorted_mb[-15000:])

        save_json(STATE_FILE, state)
        save_json(HISTORY_FILE, history)
        save_json(RULES_FILE, rules)
        save_json(MARKET_FILE, market_index)
        git_ok = commit_state(state, history, rules, market_index)

    pos = safe_int(rules.get("stats", {}).get("total_positive"))
    neg = safe_int(rules.get("stats", {}).get("total_negative"))
    auto_wins = safe_int(rules.get("stats", {}).get("auto_verified_wins"))
    total_fakes_ever = safe_int(rules.get("stats", {}).get("fake_caught_live"))
    total_fb = pos + neg + auto_wins
    accuracy_pct = round(((pos + auto_wins) / total_fb * 100), 1) if total_fb > 0 else 100.0

    elapsed = (time.time() - t0) / 60.0
    total_unique = sum(d["unique"] for d in store_diag.values())
    summary_lines = [
        f"📊 <b>ملخص جولة رادار التحقق المباشر (V40 — جولة #{rules.get('run_counter', 1)})</b>",
        f"🔎 سلع فريدة مفحوصة: <b>{total_unique:,}</b> | 🗂 قاعدة أسعار السوق: <b>{len(market_index):,} موديل</b>",
        f"🛡 درع العروض الوهمية: تم كشف وإحباط <b>{fake_caught_this_run}</b> عرض أرخص عند المنافسين الآن (إجمالي الإحباطات: {total_fakes_ever:,})",
        f"🏆 صفقات مؤكدة بالتحقق المباشر في هذه الجولة: <b>{live_verified_wins}</b>",
        ""
    ]
    for st in stores:
        sid, sname = st["id"], st["name"]
        d = store_diag[sid]
        if d["opened"]:
            summary_lines.append(
                f"✅ <b>{sname}</b>: فُحص {d['unique']} | موديل {d['exact_models']} "
                f"| مقارن {d['cross_matched']} | قُبل {d['accepted']} | أُرسل <b>{d['sent']}</b>"
            )
        else:
            summary_lines.append(f"❌ <b>{sname}</b>: تعذر الفتح")

    if cat_sent_counter:
        cat_summary = " | ".join(f"{k}: {v}" for k, v in cat_sent_counter.most_common())
        summary_lines.append(f"\n📂 <b>الأقسام المرسلة</b>:\n{cat_summary}")

    summary_lines.append(f"\n🧠 <b>التعلم الذاتي 7.0</b>: الدقة {accuracy_pct}% ({pos}👍 | {neg}👎 | {auto_wins}🎯 توثيق تلقائي)")
    summary_lines.append(f"💾 حالة الذاكرة: {'تم الحفظ في GitHub ✅' if git_ok else 'محفوظ محلياً فقط ⚠️'}")
    summary_lines.append(f"⏱ الوقت: {elapsed:.1f} دقيقة | 📨 إجمالي الصفقات المرسلة: <b>{total_sent}</b>")
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
