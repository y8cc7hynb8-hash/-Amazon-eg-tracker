#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار الصفقات المصري الاحترافي — V34 (Self-Learning 3.0 & Adaptive Intelligence Engine)
========================================================================================
الترقيات المعمارية:
1. إضافة مكتبة hashlib المفقودة لضمان الاستقرار التام.
2. محرك تعلم ذاتي متقدم 3.0:
   - تكييف حدود الخصم لكل متجر تلقائياً بناءً على تقييمات المستخدم (Dynamic Store Thresholds).
   - مؤشر سمعة الماركات (Brand Reputation Index).
   - رصد العبارات الثنائية والكلمات السلبية مع نظام تآكل دوري (Strike Decay).
3. مفتاح مطابقة عتاد دقيق حسب الفئة (Category-Specific Cross Matching).
4. استخراج صفقات Next.js من "أرخص فين" لتغطية أكثر من 35 متجراً محلياً كمرجع حي.
5. استخراج أسعار نون المشطوبة عبر كافة حقول JSON وكروت HTML.
6. قفل ذري (fcntl.flock) لمنع التعارضات وحفظ فوري لكل صفقة لمنع التكرار.
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
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

print("⚡ بدء تشغيل محرك الصفقات الذكي مع التعلم الذاتي 3.0 (الإصدار V34)...", flush=True)

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

# ================= الإعدادات والمعايير الأساسية =================
DAY = 86400
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID")

MIN_DISCOUNT_REGULAR = int(os.environ.get("MIN_DISCOUNT", 10))
MIN_DISCOUNT_HIGH = 6
MIN_UNVERIFIED_DISCOUNT = 12
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))
MIN_PRODUCT_PRICE = int(os.environ.get("MIN_PRODUCT_PRICE", 90))

MIN_CONF_VERIFIED = int(os.environ.get("MIN_CONF_VERIFIED", 58))
MIN_CONF_UNVERIFIED = int(os.environ.get("MIN_CONF_UNVERIFIED", 52))

MAX_DEALS_AMAZON = 18
MAX_DEALS_OTHER = int(os.environ.get("MAX_DEALS_PER_STORE", 10))
REALERT_HOURS = 48

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
RULES_FILE = os.path.join(DATA_DIR, "learned_rules.json")
LOCK_FILE = os.path.join(DATA_DIR, "tracker.lock")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

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


def normalize_text(text: str) -> str:
    if not text:
        return ""
    s = _DIACRITICS_RE.sub("", str(text).lower().translate(_TRANS).translate(_AR_NORM_MAP))
    return re.sub(r"\s+", " ", s).strip()


def tokenize_words(text: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(text)))


BASE_INSTALLMENT_WORDS = tuple(normalize_text(w) for w in (
    "قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر", "per month",
    "تقسيط", "أقساط", "اقساط", "installment", "/mo", "بدون فوائد", "دفعة مقدمة", "تمويل"
))

ACCESSORY_WORDS_NORM: Set[str] = {normalize_text(w) for w in (
    "جراب", "كفر", "اسكرينه", "اسكرينة", "لاصقه", "لاصقة", "حمايه", "حماية",
    "سوار", "استيك", "حامل", "ستاند", "شنطه", "شنطة", "حقيبه", "حقيبة",
    "كابل", "وصله", "وصلة", "محول", "ادابتر", "غطاء",
    "case", "cover", "protector", "glass", "strap", "band", "holder",
    "stand", "cable", "adapter", "pouch", "bag", "sleeve"
)}

PROTECTED_WORDS_NORM: Set[str] = {normalize_text(w) for w in (
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
    "karcher", "كارشر", "logitech", "razer", "redragon", "hyperx", "سماعة",
    "موبايل", "هاتف", "شاشة", "تلفزيون", "لابتوب", "غسالة", "ثلاجة", "تكييف",
    "ميكروويف", "قلاية", "خلاط", "مكواة", "مكنسة", "شاحن", "باور", "بانك",
    "ساعة", "ذكية", "ماكينة", "حلاقة", "بوتاجاز", "سخان", "مروحة", "كبة"
)}

_CUR_PRICE_RE = re.compile(
    r"(?:(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)"
    r"|(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)\s*(\d{1,3}(?:,\d{3})+|\d{2,6}))",
    re.I
)
_EGP_TOKEN_RE = re.compile(r"(?:ج\.?م|جنيه|جنية|\bEGP\b|\bL\.?E\.?\b)", re.I)
_FOREIGN_CUR_RE = re.compile(r"(?:\$|€|£|\bUSD\b|\bSAR\b|\bAED\b|\bKWD\b|\bريال\b|\bدرهم\b|\bدولار\b|\bدينار\b)", re.I)

_REAL_COUPON_CODE_RE = re.compile(
    r"(?:كود\s*خصم\s*[:\s]*([A-Za-z0-9_-]{3,15})|كوبون\s*[:\s]*([A-Za-z0-9_-]{3,15})|\bpromo\s*code\s*[:\s]*([A-Za-z0-9_-]{3,15})|\bcoupon\s*[:\s]*([A-Za-z0-9_-]{3,15}))",
    re.I
)


# ================= قفل التشغيل الذري والتحقق من البيانات =================

class AtomicExecutionLock:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.file_obj = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        try:
            self.file_obj = open(self.lock_path, "a+")
            if HAS_FCNTL:
                fcntl.flock(self.file_obj, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file_obj.seek(0)
            self.file_obj.truncate()
            self.file_obj.write(f"{os.getpid()}\n")
            self.file_obj.flush()
        except (IOError, OSError):
            log("⚠ رصد عملية رادار أخرى قيد التشغيل بالفعل — إيقاف الدورة الحالية بأمان.")
            sys.exit(0)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file_obj:
            try:
                if HAS_FCNTL:
                    fcntl.flock(self.file_obj, fcntl.LOCK_UN)
                self.file_obj.close()
            except Exception:
                pass


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
                "store": str(v.get("store") or ""),
                "link": str(v.get("link") or ""),
                "message_id": safe_int(v.get("message_id")),
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
                        if p_v >= MIN_PRODUCT_PRICE and 1600000000 < ts_v <= now:
                            valid_pts.append([ts_v, p_v])
            ts_latest = safe_int(v.get("ts"), default=now)
            clean[k] = {"pts": valid_pts, "ts": ts_latest}
    return clean


def sanitize_rules(raw_rules: Any) -> dict:
    if not isinstance(raw_rules, dict):
        return {}
    strikes = {}
    for k, v in (raw_rules.get("word_strikes") or {}).items():
        iv = safe_int(v)
        if iv > 0:
            strikes[str(k)] = iv

    liked = {}
    for k, v in (raw_rules.get("liked_brands") or {}).items():
        iv = safe_int(v)
        if iv != 0:
            liked[str(k)] = iv

    store_mod = {}
    for k, v in (raw_rules.get("store_discount_modifiers") or {}).items():
        iv = safe_int(v)
        if -5 <= iv <= 15:
            store_mod[str(k)] = iv

    return {
        "webhook_cleared": bool(raw_rules.get("webhook_cleared", False)),
        "last_update_id": safe_int(raw_rules.get("last_update_id")),
        "blocked_deals": [str(x) for x in (raw_rules.get("blocked_deals") or []) if isinstance(x, (str, int))],
        "blocked_keywords": [str(x) for x in (raw_rules.get("blocked_keywords") or []) if isinstance(x, str)],
        "word_strikes": strikes,
        "liked_brands": liked,
        "store_discount_modifiers": store_mod,
        "stats": {
            "total_positive": safe_int(raw_rules.get("stats", {}).get("total_positive")),
            "total_negative": safe_int(raw_rules.get("stats", {}).get("total_negative")),
            "last_decay_ts": safe_int(raw_rules.get("stats", {}).get("last_decay_ts"))
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


def commit_state(state: dict, history: dict, rules: dict) -> bool:
    target_files = [f for f in (STATE_FILE, HISTORY_FILE, RULES_FILE) if os.path.exists(f)]
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
            ["git", "commit", "-m", "chore: update radar intelligence memory V34", "--only", "--"] + target_files,
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
            return False

        push_res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
        if push_res.returncode == 0:
            log("💾 تم حفظ ومزامنة الذاكرة في GitHub بنجاح مؤكد.")
            return True
        return False
    except Exception as e:
        log(f"⚠ خطأ Git: {e}")
        return False


# ================= تليجرام ومحرك التعلم والتطوير الذاتي 3.0 =================

def _tg_post(endpoint: str, payload: dict) -> Optional[dict]:
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{endpoint}"
    for attempt in range(3):
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


def verify_and_register_callback(
    cb: dict,
    state: dict,
    valid_chat_override: Optional[str] = None,
    admin_override: Optional[str] = None
) -> Tuple[bool, str, str, str]:
    cb_data = str(cb.get("data", ""))
    action, sep, deal_hash = cb_data.partition(":")
    if not sep or action not in {"good", "bad"} or not deal_hash:
        return (False, "malformed_data", "", "")

    valid_chat = str(valid_chat_override if valid_chat_override is not None else (CHAT_ID or "")).strip()
    if not valid_chat:
        return (False, "no_chat_id", action, deal_hash)

    from_user = cb.get("from") or {}
    msg_obj = cb.get("message") or {}
    user_id = str(from_user.get("id", "")).strip()
    chat_id = str((msg_obj.get("chat") or {}).get("id", "")).strip()
    clicked_msg_id = safe_int(msg_obj.get("message_id"))

    if not user_id or chat_id != valid_chat:
        return (False, "unauthorized_chat", action, deal_hash)

    admin_id = str(admin_override if admin_override is not None else (ADMIN_USER_ID or "")).strip()
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
    """تآكل دوري للنقاط السلبية كل 30 يوماً لعدم حظر كلمات السوق للأبد."""
    stats = rules.setdefault("stats", {})
    last_decay = safe_int(stats.get("last_decay_ts"), default=now)
    if (now - last_decay) >= 30 * DAY:
        word_strikes = rules.get("word_strikes", {})
        new_strikes = {}
        for w, cnt in word_strikes.items():
            decayed = max(0, cnt - 1)
            if decayed > 0:
                new_strikes[w] = decayed
        rules["word_strikes"] = new_strikes
        # إعادة تقييم الكلمات المحظورة بعد التآكل
        rules["blocked_keywords"] = [w for w, cnt in new_strikes.items() if cnt >= 3]
        stats["last_decay_ts"] = now
        log("🔄 تم تطبيق دورة التآكل الزمني على قواعد الكلمات السلبية.")


def process_telegram_feedback(rules: dict, state: dict, now: int) -> None:
    """
    محرك التعلم والتطوير الذاتي 3.0:
    - يتعلم الأنماط السلبية ويضبط أوزان المتاجر والماركات ديناميكياً.
    """
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
        blocked_keywords: List[str] = list(rules.get("blocked_keywords", []))
        liked_brands: Dict[str, int] = dict(rules.get("liked_brands", {}))
        store_modifiers: Dict[str, int] = dict(rules.get("store_discount_modifiers", {}))
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
            store = deal_info.get("store", "")
            canonical_store = get_canonical_store_id(store)

            if action == "bad":
                stats["total_negative"] = safe_int(stats.get("total_negative")) + 1
                if deal_hash not in blocked_deals:
                    blocked_deals.append(deal_hash)

                # 1. تكييف المتجر سلبياً: رفع نسبة الخصم المطلوبة لقبول عروضه بـ +1%
                if canonical_store:
                    curr_mod = store_modifiers.get(canonical_store, 0)
                    store_modifiers[canonical_store] = min(8, curr_mod + 1)

                # 2. خفض سمعة الماركة
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) - 1

                # 3. تعلم الكلمات المفردة والثنائية المشبوهة
                words_list = re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(title))
                for w in words_list:
                    if len(w) >= 4 and not w.isdigit() and w not in PROTECTED_WORDS_NORM:
                        word_strikes[w] = word_strikes.get(w, 0) + 1
                        if word_strikes[w] >= 3 and w not in blocked_keywords:
                            blocked_keywords.append(w)

                log(f"🧠 تعلم ذاتي 3.0: حظر العرض [{title[:30]}] وتشديد متطلبات متجر [{canonical_store}].")
                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "تم حظر العرض وتدريب الرادار على متطلبات الجودة ✅",
                        "show_alert": False
                    })

            elif action == "good":
                stats["total_positive"] = safe_int(stats.get("total_positive")) + 1
                # 1. مكافأة المتجر: تخفيف عتبة الخصم المطلوبة لتسهيل تدفق عروضه الجيدة
                if canonical_store:
                    curr_mod = store_modifiers.get(canonical_store, 0)
                    store_modifiers[canonical_store] = max(-3, curr_mod - 1)

                # 2. رفع سمعة الماركة
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) + 1

                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "شكراً لتقييمك! تم تعزيز سمعة المتجر والماركة 🌟",
                        "show_alert": False
                    })

        rules["blocked_deals"] = list(dict.fromkeys(blocked_deals))[-2000:]
        rules["blocked_keywords"] = list(dict.fromkeys(blocked_keywords))[-500:]
        if len(word_strikes) > 1500:
            word_strikes = dict(sorted(word_strikes.items(), key=lambda kv: kv[1], reverse=True)[:1000])
        rules["word_strikes"] = word_strikes
        rules["liked_brands"] = liked_brands
        rules["store_discount_modifiers"] = store_modifiers
    except Exception as e:
        log(f"⚠ خطأ في محرك التعلم الذاتي: {e}")


# ================= توحيد هوية المتجر (Canonical Store ID) =================

def get_canonical_store_id(store_str: str) -> str:
    s = normalize_text(store_str)
    if any(k in s for k in ("amazon", "امازون")):
        return "amazon"
    if any(k in s for k in ("noon", "نون")):
        return "noon"
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


# ================= قراءة الأسعار وفلتر الأقساط =================

def has_installment_or_blocked(text: str, custom_blocked_tokens: Set[str]) -> bool:
    if not text:
        return False
    norm = normalize_text(text)
    for phrase in BASE_INSTALLMENT_WORDS:
        if phrase in norm:
            return True
    if re.search(r"(\d+\s*شهر|شهريا|بدون مقدم|تقسيط|فائده\s*0)", norm):
        return True
    if custom_blocked_tokens:
        tokens = tokenize_words(norm)
        if tokens & custom_blocked_tokens:
            return True
    return False


def parse_price(value: Any, custom_blocked_tokens: Set[str], explicit_egp: bool = False) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        if not explicit_egp:
            return 0
        v = int(round(value))
        return v if 50 <= v <= 500000 else 0

    text = str(value)
    if _FOREIGN_CUR_RE.search(text):
        return 0
    if has_installment_or_blocked(text, custom_blocked_tokens):
        return 0

    text = text.translate(_TRANS).replace("\u00a0", " ")
    text_no_dec = re.sub(r"[.,]\d{1,2}(?!\d)", " ", text)

    m_cur = _CUR_PRICE_RE.search(text_no_dec)
    if m_cur:
        raw_num = (m_cur.group(1) or m_cur.group(2)).replace(",", "")
        v = safe_int(raw_num)
        if 50 <= v <= 500000:
            return v

    if not explicit_egp:
        return 0

    comma_nums = re.findall(r"\d{1,3}(?:,\d{3})+", text_no_dec)
    for m in comma_nums:
        v = safe_int(m.replace(",", ""))
        if 50 <= v <= 500000:
            return v

    for m in re.findall(r"\d+", text_no_dec):
        v = safe_int(m)
        if v in (2022, 2023, 2024, 2025, 2026):
            continue
        if 50 <= v <= 500000:
            return v
    return 0


def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)[:185]


# ================= الهوية ومفتاح المطابقة الدقيق (Hardware Spec Matcher) =================

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
    "اتش بي": "hp", "ال جي": "lg"
}

_TIER_WORDS = {"pro", "max", "ultra", "plus", "lite", "fe", "mini", "se", "fold", "flip", "air", "note", "4g", "5g"}
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

_STORAGE_COMPONENT_WORDS = {"هارد", "قرص صلب", "hard drive", "internal ssd", "ssd drive", "m.2 nvme", "رامة كمبيوتر"}


def is_accessory(title: str) -> bool:
    tokens = tokenize_words(title)
    norm = normalize_text(title)
    return bool(tokens & ACCESSORY_WORDS_NORM) or any(w in norm for w in ("ماوس باد", "screen protector"))


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


def analyze_product_identity(title: str) -> Dict[str, Any]:
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

    t = re.sub(r"الجيل\s+الخامس", " 5g ", t)
    t = re.sub(r"الجيل\s+الرابع", " 4g ", t)
    t = re.sub(r"بوصه|بوصة|انش|inches|\"|''", " inch ", t)

    cpu_tokens = []
    cpu_full_re = re.compile(r"\b(i[3579]|ryzen\s*[3579])\s*[-_]?\s*(\d{4,5}[a-z]{0,3})\b")
    for m_cpu in cpu_full_re.finditer(t):
        cpu_name = re.sub(r"\s+", "", m_cpu.group(1))
        cpu_tokens.append(f"{cpu_name}-{m_cpu.group(2)}")
    t = cpu_full_re.sub(" ", t)

    for m_short_cpu in re.finditer(r"\b(?:core\s+)?(i[3579]|ryzen\s*[3579]|m[1234](?:\s*(?:pro|max))?)\b", t):
        tok_c = re.sub(r"\s+", "", m_short_cpu.group(1))
        if not any(c.startswith(tok_c + "-") for c in cpu_tokens):
            cpu_tokens.append(tok_c)

    gpu_tokens = []
    gpu_re = re.compile(r"\b(rtx|gtx|mx)\s*(\d{3,4})\s*(ti|super)?\b")
    for m_gpu in gpu_re.finditer(t):
        gpu_name = m_gpu.group(1)
        gpu_num = m_gpu.group(2)
        gpu_suffix = m_gpu.group(3) or ""
        gpu_tokens.append(f"{gpu_name}{gpu_num}{gpu_suffix}")
    t = gpu_re.sub(" ", t)

    raw_words_set = set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", t))
    is_standalone_component = bool(raw_words_set & _STORAGE_COMPONENT_WORDS) and not bool(raw_words_set & _LAPTOP_FORM_FACTORS)

    is_laptop_ctx = not is_standalone_component and bool(
        cpu_tokens
        or gpu_tokens
        or (raw_words_set & _LAPTOP_FORM_FACTORS)
        or (brand in ("dell", "hp", "lenovo", "asus", "acer", "msi") and not any(w in raw_words_set for w in ("شاشه", "monitor", "موبايل", "phone", "تابلت", "tablet")))
    )

    ram_vals, storage_vals = _extract_ram_and_storage(t, is_laptop_ctx)

    t = re.sub(r"\b\d{1,2}\s*(?:gb|g|جيجا|جيجابايت)?\s*[/+]\s*\d{2,4}\s*(?:gb|tb|g|جيجا|جيجابايت|تيرا|تيرابايت)\b", " ", t)
    t = re.sub(r"\b\d{1,4}\s*(?:gb|tb|g\.b|جيجا|جيجابايت|جيجا|تيرا|تيرابايت|تيرا)\b", " ", t)
    t = re.sub(r"(\d{2}(?:\.\d)?)\s*inch\b", r" \1inch ", t)

    t = re.sub(r"\b(iphone|ipad|redmi|poco|series)\s*[-_]?\s*(\d{1,2}[a-z]?)\b", r"\1\2", t)
    t = re.sub(r"\b(s|a|m|f|c|g|x|v)\s*[-_]?\s*(\d{2})\s*(fe)\b", r"\1\2 \3", t)
    t = re.sub(r"\b([a-z]{1,3})\s*[-_]\s*(\d{2,4}[a-z]?)\b", r"\1\2", t)
    t = re.sub(r"\b(a|s|m|f|c|g|x|v)\s+(\d{2}[a-z]?)\b", r"\1\2", t)
    t = re.sub(r"\b(\d{1,2})\s*(?:th|جيل|الجيل)\b", r"\1th", t)

    tokens = re.findall(r"[a-z0-9.]+|[\u0600-\u06FF]+", t)
    acc_flag = "ACC" if is_accessory(title) else "DEV"

    inch_vals = [f"{m}inch" for m in sorted(set(re.findall(r"\b(\d{2}(?:\.\d)?)inch\b", t)))]
    tiers_set = {tok for tok in tokens if tok in _TIER_WORDS}
    tv_tech = sorted({tok for tok in tokens if tok in _TV_TECH_WORDS})

    if acc_flag == "ACC":
        cat = "ACC"
    elif is_standalone_component:
        cat = "COMP"
    elif is_laptop_ctx:
        cat = "LAP"
    elif tv_tech or "تلفزيون" in t or "شاشه" in t or "tv" in tokens or any(safe_int(x.replace("inch", "")) >= 27 for x in inch_vals):
        cat = "TV"
    else:
        cat = "MOB"

    stop_words = {
        "عرض", "خصم", "جديد", "اصلي", "ضمان", "مصر", "سنه", "مع", "من", "في",
        "لون", "اسود", "ابيض", "ازرق", "ذهبي", "فضي", "رمادي", "شاشه", "موبايل",
        "هاتف", "تلفزيون", "سمارت", "ذكي", "ثنائي", "الشريحه", "شريحتين", "شبكه",
        "رام", "رامات", "ذاكره", "داخليه", "لابتوب", "هارد", "inch", "ssd", "nvme",
        "hdd", "ddr4", "ddr5", "new", "egypt", "with", "for", "and", "the", "black",
        "white", "blue", "smart", "tv", "mobile", "phone", "dual", "sim", "wifi",
        "ram", "galaxy", "laptop", "notebook", "core"
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
            or re.fullmatch(r"\d+(?:\.\d)?(?:gb|tb|inch)", tok)
            or tok in tiers_set
            or tok in _TV_TECH_WORDS
            or tok in stop_words
        ):
            continue
        if (len(tok) >= 2 and re.search(r"[a-z]", tok) and re.search(r"\d", tok)) or re.fullmatch(r"\d{1,2}th", tok):
            models.append(tok)
        elif len(tok) >= 2 and not tok.replace(".", "").isdigit():
            core_words.append(tok)

    models = sorted(set(models))
    core_sorted = sorted(set(core_words))[:6]
    cpu_sorted = sorted(set(cpu_tokens))
    gpu_sorted = sorted(set(gpu_tokens))
    tiers_sorted = sorted(tiers_set)

    spec_parts = []
    if ram_vals:
        spec_parts.append("R:" + "-".join(ram_vals))
    if storage_vals:
        spec_parts.append("S:" + "-".join(storage_vals))
    if inch_vals:
        spec_parts.append("I:" + "-".join(inch_vals))
    if cpu_sorted:
        spec_parts.append("CPU:" + "-".join(cpu_sorted))
    if gpu_sorted:
        spec_parts.append("GPU:" + "-".join(gpu_sorted))
    if tv_tech:
        spec_parts.append("TECH:" + "-".join(tv_tech))
    if tiers_sorted:
        spec_parts.append("T:" + "-".join(tiers_sorted))

    spec_str = "|".join(spec_parts)

    has_exact_id = bool(brand and (models or (cat == "LAP" and core_sorted and cpu_sorted and (ram_vals or storage_vals))))
    if has_exact_id:
        model_tag = "-".join(models) if models else "-".join(core_sorted[:3])
        sig = f"{cat}|{brand}|M:{model_tag}|{spec_str}"

        if cat == "LAP":
            primary_cpu = cpu_sorted[0] if cpu_sorted else ""
            primary_gpu = gpu_sorted[0] if gpu_sorted else ""
            primary_ram = ram_vals[0] if ram_vals else ""
            primary_storage = storage_vals[0] if storage_vals else ""
            cross_match_key = f"LAP|{brand}|{model_tag}|{primary_cpu}|{primary_gpu}|{primary_ram}|{primary_storage}"
        elif cat == "MOB":
            primary_ram = ram_vals[0] if ram_vals else ""
            primary_storage = storage_vals[0] if storage_vals else ""
            net_tier = "5g" if "5g" in tiers_sorted else ("4g" if "4g" in tiers_sorted else "")
            cross_match_key = f"MOB|{brand}|{model_tag}|{primary_ram}|{primary_storage}|{net_tier}"
        elif cat == "TV":
            primary_inch = inch_vals[0] if inch_vals else ""
            primary_tech = tv_tech[0] if tv_tech else ""
            cross_match_key = f"TV|{brand}|{model_tag}|{primary_inch}|{primary_tech}"
        else:
            primary_storage = storage_vals[0] if storage_vals else ""
            cross_match_key = f"{cat}|{brand}|{model_tag}|{primary_storage}"

        return {"sig": sig, "cross_match_key": cross_match_key, "exact_model": True, "brand": brand, "category": cat}

    sig = f"{cat}|{brand}|W:{'-'.join(core_sorted)}|{spec_str}"
    return {"sig": sig, "cross_match_key": "", "exact_model": False, "brand": brand, "category": cat}


# ================= سجل الأسعار 2.0 =================

def record_and_analyze_history(history: dict, clean_link: str, cur_price: int, now: int) -> Dict[str, Any]:
    rec = history.get(clean_link)
    daily_buckets: Dict[int, Tuple[int, int]] = {}

    if isinstance(rec, dict):
        raw_pts = rec.get("pts")
        if isinstance(raw_pts, list):
            for item in raw_pts:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    ts_v = safe_int(item[0])
                    p_v = safe_int(item[1])
                    if p_v >= MIN_PRODUCT_PRICE and 1600000000 < ts_v <= now:
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
    elif obs_30 >= 4 and span_days_30 >= 4:
        strength = "medium"
    elif obs_30 >= 2 and span_days_30 >= 1:
        strength = "weak"
    else:
        strength = "none"

    stats: Dict[str, Any] = {
        "min_30d": min(prices_30d) if prices_30d else 0,
        "median_30d": int(statistics.median(prices_30d)) if prices_30d else 0,
        "min_90d": min(prices_90d) if prices_90d else 0,
        "last_price": sorted_pts_90[-1][1] if sorted_pts_90 else 0,
        "obs_days_30d": obs_30,
        "obs_days_90d": len(sorted_pts_90),
        "span_days_30d": span_days_30,
        "history_strength": strength
    }

    today_key = now // DAY
    daily_buckets[today_key] = (now, cur_price)
    updated_pts = sorted((ts, p) for ts, p in daily_buckets.values() if 1600000000 < ts <= now and ts >= cutoff_90)

    history[clean_link] = {"pts": [[ts, p] for ts, p in updated_pts], "ts": now}
    return stats


# ================= محرك التقييم التكيفي =================

def evaluate_deal(
    cur: int,
    old: int,
    h_stats: Dict[str, Any],
    market_lowest_other: int,
    exact_model: bool,
    has_coupon: bool = False,
    brand_liked: bool = False,
    store_disc_mod: int = 0
) -> Tuple[bool, str, int, int, int, str]:
    if cur < MIN_PRODUCT_PRICE:
        return (False, "", 0, 0, 0, "below_min_price")

    median_30d = safe_int(h_stats.get("median_30d"))
    min_30d = safe_int(h_stats.get("min_30d"))
    obs_days_30d = safe_int(h_stats.get("obs_days_30d"))
    strength = str(h_stats.get("history_strength") or "none")

    has_competitor = exact_model and market_lowest_other > 0
    competitor_is_much_cheaper = has_competitor and market_lowest_other < cur * 0.92
    competitor_is_slightly_cheaper = has_competitor and (cur * 0.92 <= market_lowest_other < cur)
    is_cheaper_than_market = has_competitor and market_lowest_other > cur
    has_market_deal_proof = has_competitor and (cur * 1.05 <= market_lowest_other <= cur * 1.85)

    has_history_proof = strength in ("strong", "medium", "weak") and median_30d > 0 and obs_days_30d >= 2

    if competitor_is_much_cheaper:
        return (False, "", 0, 0, 0, "much_cheaper_competitor_exists")

    c_extra = (3 if brand_liked else 0)
    hist_weights = {"strong": 34, "medium": 30, "weak": 22, "none": 10}

    # تعديل عتبة الخصم تلقائياً حسب تقييمات المتجر السابقة
    effective_unverified_disc = max(8, MIN_UNVERIFIED_DISCOUNT + store_disc_mod)

    # 1. المسار الأول (أرخص بين المتاجر المفحوصة)
    if has_market_deal_proof and market_lowest_other >= cur * 1.05:
        gap_pct = round((market_lowest_other - cur) / market_lowest_other * 100)
        saving = market_lowest_other - cur
        if gap_pct >= 5 and (saving >= 120 or cur < 1500):
            c_hist = 22 if (has_history_proof and cur <= median_30d) else (8 if median_30d > 0 and cur > median_30d * 1.05 else 14)
            c_match = 18
            c_market = 36
            c_auth = min(20, 12 + gap_pct // 2)
            total_conf = min(99, c_hist + c_match + c_market + c_auth + c_extra)
            if total_conf >= MIN_CONF_VERIFIED:
                return (True, "أرخص بين المتاجر المفحوصة", market_lowest_other, gap_pct, total_conf, "ok")

    # 2. المسار الثاني (هبوط تاريخي صامت)
    if has_history_proof and median_30d > cur and (min_30d == 0 or cur <= min_30d):
        drop_pct = round((median_30d - cur) / median_30d * 100)
        saving = median_30d - cur
        min_req_drop = 10 if strength == "weak" else 7
        if min_req_drop <= drop_pct <= 60 and (saving >= 100 or drop_pct >= 8):
            if competitor_is_slightly_cheaper and strength == "weak":
                return (False, "", 0, 0, 0, "competitor_slightly_cheaper_weak_history")

            c_hist = hist_weights[strength]
            c_match = 18 if exact_model else 14
            c_market_adj = 10 if has_market_deal_proof else (-6 if competitor_is_slightly_cheaper else 4)
            c_auth = min(24, 14 + drop_pct // 2)
            total_conf = min(98, c_hist + c_match + c_market_adj + c_auth + c_extra)
            if total_conf >= MIN_CONF_VERIFIED:
                label = "هبوط تاريخي بالمتجر (يتوفر منافس قريب)" if competitor_is_slightly_cheaper else "هبوط سعر صامت موثق"
                return (True, label, median_30d, drop_pct, total_conf, "ok")

    # 3. المسار الثالث (الخصم المعلن)
    if old > cur:
        if median_30d > 0 and cur > median_30d * 1.05 and not has_market_deal_proof:
            return (False, "", 0, 0, 0, "fake_inflated_old_price")

        if competitor_is_slightly_cheaper:
            return (False, "", 0, 0, 0, "not_cheaper_than_competitor")

        effective_old = old
        if median_30d > 0 and old > median_30d * 1.25:
            effective_old = int(median_30d * 1.10)

        if effective_old > cur:
            disc = round((effective_old - cur) / effective_old * 100)
            saving = effective_old - cur
            if disc > MAX_REALISTIC_DISC:
                return (False, "", 0, 0, 0, "unrealistic_discount_pct")

            if strength in ("strong", "medium") or has_market_deal_proof:
                if (cur >= 1500 and (disc >= MIN_DISCOUNT_HIGH or saving >= 180)) or (disc >= MIN_DISCOUNT_REGULAR):
                    c_proof = 30 if (strength == "strong" or (has_history_proof and has_market_deal_proof)) else 24
                    c_match = 18 if exact_model else 14
                    c_auth = min(24, 14 + disc // 3)
                    total_conf = min(96, c_proof + c_match + c_auth + c_extra)
                    if total_conf >= MIN_CONF_VERIFIED:
                        return (True, "خصم معلن موثق", effective_old, disc, total_conf, "ok")
            else:
                if has_competitor and not is_cheaper_than_market:
                    return (False, "", 0, 0, 0, "not_cheaper_than_competitor")
                if (has_coupon and disc >= 6) or (cur >= 1500 and (disc >= 7 or saving >= 180)) or (disc >= effective_unverified_disc and saving >= 100):
                    c_base = 36
                    c_match = 16 if exact_model else 12
                    c_auth = min(20, 10 + disc // 3)
                    total_conf = min(74, c_base + c_match + c_auth + c_extra)
                    if total_conf >= MIN_CONF_UNVERIFIED:
                        return (True, "خصم معلن غير مؤكد (قيد التوثيق)", effective_old, disc, total_conf, "ok")
                return (False, "", 0, 0, 0, "unverified_low_discount")

    return (False, "", 0, 0, 0, "no_discount_or_drop")


# ================= الجلب وتجاوز الحظر =================

def is_blocked_page(text: str, status: int, is_amazon: bool = False) -> bool:
    if not text:
        return True
    if status in (403, 429, 503) and len(text) < 30000:
        return True

    head = text[:8000].lower()
    if any(c in head for c in ("validatecaptcha", "api-services-support@amazon.com", "enter the characters you see below", "cf-browser-verification", "vercel security checkpoint")):
        return True

    if is_amazon:
        if len(text) < 15000 and "/dp/" not in text:
            return True
        if "data-asin" not in text and "/dp/" not in text:
            return True
    elif len(text) < 1800:
        return True

    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
    return any
