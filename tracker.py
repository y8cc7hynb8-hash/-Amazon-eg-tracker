#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار الصفقات المصري الاحترافي — V38 (Fuzzy Cross-Matcher & Autonomous Learning 5.0)
====================================================================================
المعمارية الهندسية في V38:
1. مطابقة ذكية بنظام عدم التعارض (Two-Tier Non-Conflict Spec Matcher) + تعريب رموز الموديلات.
2. منظم سرعة لكل متجر (Per-Domain Semaphore) لمنع الحظر أثناء الفحص المتوازي (12 Workers).
3. التعلم الذاتي الصامت 5.0 (Autonomous Market Learning): يكافئ الصفقات التي يرتفع سعرها لاحقاً تلقائياً.
4. درع التقييمات الشامل (1.0-5.0 نجوم + عدد المقيمين >= 10) لمنع المنتجات الرديئة والوهمية.
5. كشف صفقات نفس الموديل داخل المتجر حتى لو كان السعر المشطوب (old = 0).
6. فحص العملة في العنصر والأب والجد لاستخراج 100% من أسعار جوميا و2B وأرخص فين.
7. حماية كاملة للذاكرة عبر try/finally وتأمين روابط HTML.
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

print("⚡ بدء تشغيل محرك الصفقات الذكي V38 (Fuzzy Matcher & Autonomous Learning 5.0)...", flush=True)

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

# ================= الإعدادات والمعايير العامة =================
DAY = 86400
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID")

MIN_DISCOUNT_REGULAR = int(os.environ.get("MIN_DISCOUNT", 12))
MIN_DISCOUNT_HIGH = 7
MIN_UNVERIFIED_DISCOUNT = 14
MAX_REALISTIC_DISC = int(os.environ.get("MAX_REALISTIC_DISC", 75))

MIN_PRODUCT_PRICE_GLOBAL = int(os.environ.get("MIN_PRODUCT_PRICE", 35))

MIN_CONF_VERIFIED = int(os.environ.get("MIN_CONF_VERIFIED", 60))
MIN_CONF_UNVERIFIED = int(os.environ.get("MIN_CONF_UNVERIFIED", 54))

MAX_DEALS_AMAZON = 26
MAX_DEALS_NOON = 18
MAX_DEALS_JUMIA = 12
MAX_DEALS_OTHER = int(os.environ.get("MAX_DEALS_PER_STORE", 10))
REALERT_HOURS = 48
MAX_WORKERS = 12

DATA_DIR = "data" if os.path.isdir("data") else "."
STATE_FILE = os.path.join(DATA_DIR, "sent_deals.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
RULES_FILE = os.path.join(DATA_DIR, "learned_rules.json")

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]
AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"

# منظم السرعة لكل متجر لمنع الحظر أثناء التشغيل المتوازي (بحد أقصى 3 طلبات متزامنة لكل متجر)
_DOMAIN_SEMAPHORES: Dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(3))

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
        return float(str(v).strip())
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
    "جراب", "اسكرينه", "اسكرينة", "لاصقه حمايه", "لاصقة حماية",
    "phone case", "screen protector", "tempered glass", "حامل موبايل", "وصله شاحن"
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
    "شنيور", "صاروخ", "مفك", "طقم", "عدة", "زيت", "ارز", "مكرونة", "قهوة", "شاي"
)}

_CUR_PRICE_RE = re.compile(
    r"(?:(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)"
    r"|(?:ج\.?م\.?|جنيه|جنية|EGP|L\.?E\.?)\s*(\d{1,3}(?:,\d{3})+|\d{2,6}))",
    re.I
)
_EGP_TOKEN_RE = re.compile(r"(?:ج\.?م|جنيه|جنية|\bEGP\b|\bL\.?E\.?\b)", re.I)
_FOREIGN_CUR_RE = re.compile(r"(?:\$|€|£|\bUSD\b|\bSAR\b|\bAED\b|\bKWD\b|\bريال\b|\bدرهم\b|\bدولار\b|\bدينار\b)", re.I)

_REAL_COUPON_CODE_RE = re.compile(
    r"(?:كود\s*خصم\s*[:\s]*([A-Za-z0-9_-]{3,15})|كوبون\s*[:\s]*([A-Za-z0-9_-]{3,15})|خصم\s+\d+%?\s*مع\s*الكوبون|\bpromo\s*code\s*[:\s]*([A-Za-z0-9_-]{3,15})|\bcoupon\s*[:\s]*([A-Za-z0-9_-]{3,15}))",
    re.I
)


# ================= التحقق من البيانات وحفظ Git =================

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
                "store": str(v.get("store") or ""),
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


def sanitize_rules(raw_rules: Any) -> dict:
    if not isinstance(raw_rules, dict):
        return {}
    strikes = {str(k): safe_int(v) for k, v in (raw_rules.get("word_strikes") or {}).items() if safe_int(v) > 0}
    bigram_strikes = {str(k): safe_int(v) for k, v in (raw_rules.get("bigram_strikes") or {}).items() if safe_int(v) > 0}
    liked = {str(k): safe_int(v) for k, v in (raw_rules.get("liked_brands") or {}).items() if safe_int(v) != 0}
    cat_affinity = {str(k): safe_int(v) for k, v in (raw_rules.get("category_affinity") or {}).items() if safe_int(v) != 0}
    store_mod = {str(k): safe_int(v) for k, v in (raw_rules.get("store_discount_modifiers") or {}).items() if -6 <= safe_int(v) <= 15}

    return {
        "webhook_cleared": bool(raw_rules.get("webhook_cleared", False)),
        "last_update_id": safe_int(raw_rules.get("last_update_id")),
        "blocked_deals": [str(x) for x in (raw_rules.get("blocked_deals") or []) if isinstance(x, (str, int))],
        "blocked_keywords": [str(x) for x in (raw_rules.get("blocked_keywords") or []) if isinstance(x, str)],
        "blocked_bigrams": [str(x) for x in (raw_rules.get("blocked_bigrams") or []) if isinstance(x, str)],
        "word_strikes": strikes,
        "bigram_strikes": bigram_strikes,
        "liked_brands": liked,
        "category_affinity": cat_affinity,
        "store_discount_modifiers": store_mod,
        "stats": {
            "total_positive": safe_int(raw_rules.get("stats", {}).get("total_positive")),
            "total_negative": safe_int(raw_rules.get("stats", {}).get("total_negative")),
            "auto_verified_wins": safe_int(raw_rules.get("stats", {}).get("auto_verified_wins")),
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
            ["git", "commit", "-m", "chore: update radar intelligence memory V38", "--only", "--"] + target_files,
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


# ================= تليجرام ومحرك التعلم والتطوير الذاتي 5.0 =================

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
    stats = rules.setdefault("stats", {})
    last_decay = safe_int(stats.get("last_decay_ts"), default=now)
    if (now - last_decay) >= 21 * DAY:
        word_strikes = rules.get("word_strikes", {})
        new_strikes = {w: cnt - 1 for w, cnt in word_strikes.items() if cnt > 1}
        rules["word_strikes"] = new_strikes
        rules["blocked_keywords"] = [w for w, cnt in new_strikes.items() if cnt >= 3]

        bg_strikes = rules.get("bigram_strikes", {})
        new_bg = {bg: cnt - 1 for bg, cnt in bg_strikes.items() if cnt > 1}
        rules["bigram_strikes"] = new_bg
        rules["blocked_bigrams"] = [bg for bg, cnt in new_bg.items() if cnt >= 2]

        stats["last_decay_ts"] = now
        log("🔄 تم تطبيق دورة التآكل الزمني على قواعد الكلمات والعبارات السلبية.")


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
            canonical_store = get_canonical_store_id(store)

            if action == "bad":
                stats["total_negative"] = safe_int(stats.get("total_negative")) + 1
                if deal_hash not in blocked_deals:
                    blocked_deals.append(deal_hash)

                if canonical_store:
                    store_modifiers[canonical_store] = min(8, store_modifiers.get(canonical_store, 0) + 1)
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) - 1
                if cat:
                    cat_affinity[cat] = max(-5, cat_affinity.get(cat, 0) - 1)

                words_list = [w for w in re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", normalize_text(title)) if len(w) >= 4 and not w.isdigit()]
                for w in words_list:
                    if w not in PROTECTED_WORDS_NORM:
                        word_strikes[w] = word_strikes.get(w, 0) + 1
                        if word_strikes[w] >= 3 and w not in blocked_keywords:
                            blocked_keywords.append(w)

                for bg in extract_bigrams(title):
                    bg_tokens = set(bg.split())
                    if not (bg_tokens & PROTECTED_WORDS_NORM):
                        bigram_strikes[bg] = bigram_strikes.get(bg, 0) + 1
                        if bigram_strikes[bg] >= 2 and bg not in blocked_bigrams:
                            blocked_bigrams.append(bg)

                log(f"🧠 تعلم ذاتي 5.0: حظر العرض [{title[:30]}] وتحديث فلتر العبارات والمتجر [{canonical_store}].")
                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "تم حظر العرض وتدريب الذكاء الاصطناعي ✅",
                        "show_alert": False
                    })

            elif action == "good":
                stats["total_positive"] = safe_int(stats.get("total_positive")) + 1
                if canonical_store:
                    store_modifiers[canonical_store] = max(-4, store_modifiers.get(canonical_store, 0) - 1)
                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) + 1
                if cat:
                    cat_affinity[cat] = min(10, cat_affinity.get(cat, 0) + 1)

                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "تم تعزيز أولوية هذه الفئة والماركة 🌟",
                        "show_alert": False
                    })

        rules["blocked_deals"] = list(dict.fromkeys(blocked_deals))[-2500:]
        rules["blocked_keywords"] = list(dict.fromkeys(blocked_keywords))[-600:]
        rules["blocked_bigrams"] = list(dict.fromkeys(blocked_bigrams))[-600:]
        rules["word_strikes"] = dict(sorted(word_strikes.items(), key=lambda kv: kv[1], reverse=True)[:1200])
        rules["bigram_strikes"] = dict(sorted(bigram_strikes.items(), key=lambda kv: kv[1], reverse=True)[:1200])
        rules["liked_brands"] = liked_brands
        rules["category_affinity"] = cat_affinity
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


# ================= قراءة الأسعار وفلتر الأقساط =================

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


def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    return re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)[:190]


# ================= الهوية والمطابقة الذكية بنظام عدم التعارض (Fuzzy Non-Conflict Matcher) =================

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
    "اتش بي": "hp", "ال جي": "lg",
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
    "الضحى": "eldoha", "عافيه": "afia", "سلايت": "slite", "كنور": "knorr"
}

_AR_MODEL_LETTERS = {
    "ايه": "a", "اي": "a", "اس": "s", "ام": "m", "اف": "f",
    "سي": "c", "جي": "g", "اكس": "x", "في": "v", "زد": "z"
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

_PERFUME_WORDS = {"عطر", "برفان", "ماء عطر", "او دو", "تواليت", "بارفيوم", "سبراي جسم", "مزيل عرق", "edp", "edt", "perfume", "parfum", "eau de", "fragrance", "cologne"}
_WATCH_WORDS = {"ساعه يد", "ساعه كلاسيك", "ساعه انالوج", "ساعه رقميه", "ساعه رجالي", "ساعه حريمي", "كرونوغراف", "ساعه كاسيو", "ساعه ذكيه", "سمارت ووتش", "wristwatch", "chronograph", "analog watch", "quartz", "smartwatch", "smart watch"}
_FASHION_WORDS = {"تيشيرت", "قميص", "بنطلون", "جينز", "جاكيت", "سويت شيرت", "هودي", "فستان", "عبايه", "بلوزه", "بيجامه", "شورت", "حذاء", "كوتشي", "سنيكرز", "صندل", "شبشب", "شنطه ظهر", "حقيبه يد", "محفظه", "t-shirt", "shirt", "pants", "jeans", "jacket", "hoodie", "sneakers", "shoes", "slippers", "backpack", "handbag"}
_TOOLS_WORDS = {"شنيور", "مثقاب", "صاروخ", "جلخ", "مفك", "مفتاح", "زراديه", "بنسه", "منشار", "شاكوش", "متر قياس", "طقم عده", "صندوق عده", "مفاتيح", "لقم", "كمبروسر", "منفاخ", "كاويه", "افوميتر", "دريل", "هلتي", "drill", "grinder", "screwdriver", "wrench", "pliers", "tool set", "socket set", "toolbox", "hammer", "saw"}
_GROCERY_WORDS = {"زيت", "سمن", "زبده", "ارز", "مكرونه", "سكر", "دقيق", "شاي", "قهوه", "نسكافيه", "كاكاو", "حليب", "لبن", "جبنه", "تونه", "صلصه", "كاتشب", "مايونيز", "عسل", "مربى", "شوكولاته", "بسكويت", "مسحوق غسيل", "منظف", "منعم اقمشه", "صابون", "شامبو", "بلسم", "شاور جل", "معجون اسنان", "مناديل", "حفاضات", "coffee", "tea", "olive oil", "sunflower oil", "pasta", "rice", "detergent", "shampoo", "lotion", "tissue", "diapers"}
_APPLIANCE_WORDS = {"ثلاجه", "غساله", "بوتاجاز", "تكييف", "سخان", "ميكروويف", "قلايه", "خلاط", "كبه", "مفرمه", "مكواه", "مكنسه", "مروحه", "فلتر مياه", "كاتل", "غلايه", "ماكينه قهوه", "توستر", "مضرب بيض", "فرن كهربائي", "ديب فريزر", "refrigerator", "washer", "air conditioner", "microwave", "air fryer", "blender", "vacuum", "kettle"}


def is_phone_accessory(title: str, price: int) -> bool:
    norm = normalize_text(title)
    if price < 300 and any(w in norm for w in PHONE_ACCESSORY_WORDS_NORM):
        return True
    return False


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
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(ml|مل|ملي|ملل|g|gm|جم|جرام|غ|kg|كجم|كيلو|l|لتر|قطعه|قطعة|كتله)\b", t):
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

    # تحويل الحروف التقنية المكتوبة بالعربية (مثل: ايه 55 -> a55)
    for ar_let, en_let in _AR_MODEL_LETTERS.items():
        t = re.sub(r"(?<!\w)" + re.escape(ar_let) + r"\s*(\d{1,3})\b", f" {en_let}\\1 ", t)

    t = re.sub(r"الجيل\s+الخامس", " 5g ", t)
    t = re.sub(r"الجيل\s+الرابع", " 4g ", t)
    t = re.sub(r"بوصه|بوصة|انش|inches|\"|''", " inch ", t)

    vol_weight_specs = _extract_volume_and_weight(t)

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
        gpu_tokens.append(f"{m_gpu.group(1)}{m_gpu.group(2)}{m_gpu.group(3) or ''}")
    t = gpu_re.sub(" ", t)

    raw_words_set = set(re.findall(r"[a-z0-9]+|[\u0600-\u06FF]+", t))
    is_laptop_ctx = bool(
        cpu_tokens
        or gpu_tokens
        or (raw_words_set & _LAPTOP_FORM_FACTORS)
        or (brand in ("dell", "hp", "lenovo", "asus", "acer", "msi") and not any(w in raw_words_set for w in ("شاشه", "monitor", "موبايل", "phone", "تابلت", "tablet", "ساعه", "شنطه")))
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
    inch_vals = [f"{m}inch" for m in sorted(set(re.findall(r"\b(\d{2}(?:\.\d)?)inch\b", t)))]
    tiers_set = {tok for tok in tokens if tok in _TIER_WORDS}
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
    elif ram_vals or storage_vals or brand in ("samsung", "apple", "xiaomi", "realme", "oppo", "vivo", "infinix", "tecno", "honor", "huawei", "nokia") or hint_cat == "MOB":
        cat = "MOB"
    else:
        cat = hint_cat or "GEN"

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
            or re.fullmatch(r"\d+(?:\.\d)?(?:gb|tb|inch|ml|g|kg|l|pcs)", tok)
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

    # قاموس المواصفات لاستخدامه في فحص عدم التعارض (Non-Conflict Spec Matching)
    specs_dict = {
        "ram": ram_vals[0] if ram_vals else "",
        "storage": storage_vals[0] if storage_vals else "",
        "cpu": cpu_sorted[0] if cpu_sorted else "",
        "gpu": gpu_sorted[0] if gpu_sorted else "",
        "inch": inch_vals[0] if inch_vals else "",
        "vw": vol_weight_specs[0] if vol_weight_specs else "",
        "tiers": "-".join(t for t in tiers_sorted if t in ("pro", "max", "ultra", "plus", "fe", "lite", "mini"))
    }

    has_exact_id = bool(
        brand and (
            models
            or (cat == "LAP" and core_sorted and cpu_sorted and (ram_vals or storage_vals))
            or (cat in ("PERF", "GROC", "TOOLS", "WATCH") and core_sorted and (vol_weight_specs or models))
        )
    )
    if has_exact_id:
        model_tag = "-".join(models) if models else "-".join(core_sorted[:3])
        # مفتاح أساسي مرن يجمع نفس الموديل من كل المتاجر، ثم تفصل دالة specs_are_compatible أي اختلاف في المواصفات
        core_match_key = f"{cat}|{brand}|{model_tag}|{specs_dict['tiers']}"
        return {
            "sig": f"{core_match_key}|{specs_dict['ram']}|{specs_dict['storage']}",
            "core_match_key": core_match_key,
            "specs": specs_dict,
            "exact_model": True,
            "brand": brand,
            "category": cat
        }

    return {
        "sig": f"{cat}|{brand}|W:{'-'.join(core_sorted)}",
        "core_match_key": "",
        "specs": specs_dict,
        "exact_model": False,
        "brand": brand,
        "category": cat
    }


def specs_are_compatible(s1: Dict[str, str], s2: Dict[str, str], cat: str) -> bool:
    """
    فحص التوافق بدون تعارض:
    - إذا كانت السعة التخزينية أو الشاشة موجودة في الاثنين ومختلفة -> غير متوافقين.
    - إذا كان الرام مكتوباً في متجر واحد فقط (والآخر فارغ) مع تطابق السعة والموديل -> متوافقان!
    """
    for vital_key in ("storage", "inch", "vw", "cpu", "gpu"):
        v1, v2 = s1.get(vital_key, ""), s2.get(vital_key, "")
        if v1 and v2 and v1 != v2:
            return False
        # في اللابتوب والهواتف، لابد أن تتطابق سعة التخزين صراحةً إن وجدت في أحدهما
        if cat in ("MOB", "LAP") and vital_key == "storage" and (v1 != v2):
            return False

    # الرام: إذا ذُكر في المتجرين معاً يجب أن يتطابق، أما إذا أغفله أحدهما فيُسمح بالمطابقة
    r1, r2 = s1.get("ram", ""), s2.get("ram", "")
    if r1 and r2 and r1 != r2:
        return False

    return True


# ================= سجل الأسعار والتعلم التلقائي الصامت =================

def record_and_analyze_history(
    history: dict,
    state: dict,
    rules: dict,
    deal_hash: str,
    clean_link: str,
    cur_price: int,
    now: int
) -> Dict[str, Any]:
    # التعلم التلقائي الصامت (Autonomous Learning):
    # إذا كنا قد أرسلنا هذا المنتج سابقاً بسعر منخفض، ثم رصدنا ارتفاع سعره الآن بنسبة >= 8%، نكافئ القسم والماركة والمتجر تلقائياً!
    prev_sent = state.get(deal_hash)
    if isinstance(prev_sent, dict) and not prev_sent.get("auto_rewarded"):
        sent_price = safe_int(prev_sent.get("price"))
        if sent_price > 0 and cur_price >= sent_price * 1.08:
            prev_sent["auto_rewarded"] = True
            b_name = prev_sent.get("brand", "")
            c_name = prev_sent.get("category", "")
            st_canon = get_canonical_store_id(prev_sent.get("store", ""))
            if b_name:
                rules.setdefault("liked_brands", {})[b_name] = rules.get("liked_brands", {}).get(b_name, 0) + 1
            if c_name:
                rules.setdefault("category_affinity", {})[c_name] = min(10, rules.get("category_affinity", {}).get(c_name, 0) + 1)
            if st_canon:
                rules.setdefault("store_discount_modifiers", {})[st_canon] = max(-4, rules.get("store_discount_modifiers", {}).get(st_canon, 0) - 1)
            rules.setdefault("stats", {})["auto_verified_wins"] = safe_int(rules.get("stats", {}).get("auto_verified_wins")) + 1
            log(f"🎯 تعلم ذاتي صامت: ارتفع سعر [{prev_sent.get('title', '')[:30]}] من {sent_price} إلى {cur_price} ج.م — تأكد نجاح الصفقة السابقة!")

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


# ================= محرك التقييم ودرع الصفقات الحقيقية (True-Deal Shield) =================

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


def evaluate_deal(
    cur: int,
    old: int,
    cat: str,
    brand: str,
    h_stats: Dict[str, Any],
    market_lowest_other: int,
    same_store_higher: int,
    exact_model: bool,
    has_coupon: bool = False,
    brand_liked: bool = False,
    cat_bonus: int = 0,
    store_disc_mod: int = 0,
    rating: float = 0.0,
    reviews: int = 0
) -> Tuple[bool, str, int, int, int, str]:
    # 1. فلتر التقييمات الشامل (يلتقط من 1.0 إلى 3.3 ويرفضها فوراً)
    if 0.1 <= rating < 3.4:
        return (False, "", 0, 0, 0, "low_product_rating")

    if cat == "GROC":
        min_price_cat = 35
        min_saving_unverified = 18
        base_unverified_disc = 13
    elif cat in ("PERF", "FASH", "TOOLS", "WATCH"):
        min_price_cat = 70
        min_saving_unverified = 40
        base_unverified_disc = 14
    else:
        min_price_cat = 95
        min_saving_unverified = 100
        base_unverified_disc = MIN_UNVERIFIED_DISCOUNT

    if cur < min_price_cat:
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

    # لا يُمنح بونص التقييم إلا إذا كان عدد المقيمين >= 10 لمنع التقييمات الوهمية الفردية
    rating_bonus = 4 if (rating >= 4.3 and reviews >= 10) else (2 if (rating >= 4.0 and reviews >= 5) else 0)
    c_extra = (3 if brand_liked else 0) + min(4, max(-3, cat_bonus)) + rating_bonus
    hist_weights = {"strong": 34, "medium": 30, "weak": 22, "none": 10}

    effective_unverified_disc = max(10, base_unverified_disc + store_disc_mod)

    # 1. المسار الأول (أرخص بين المتاجر المفحوصة)
    if has_market_deal_proof and market_lowest_other >= cur * 1.05:
        gap_pct = round((market_lowest_other - cur) / market_lowest_other * 100)
        saving = market_lowest_other - cur
        if gap_pct >= 5 and (saving >= min_saving_unverified or cur < 1000):
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
        min_req_drop = 9 if strength == "weak" else 6
        if min_req_drop <= drop_pct <= 65 and (saving >= min_saving_unverified or drop_pct >= 10):
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

    # تعويض غياب السعر المشطوب إذا وُجد بائع آخر في نفس المتجر يبيع نفس الموديل بسعر أعلى
    effective_old = old if old > cur else (same_store_higher if same_store_higher >= cur * 1.10 else 0)

    # 3. المسار الثالث (الخصم المعلن مع درع مكافحة التضخم الكاذب)
    if effective_old > cur:
        if median_30d > 0 and cur > median_30d * 1.03 and not has_market_deal_proof:
            return (False, "", 0, 0, 0, "fake_inflated_old_price")

        if competitor_is_slightly_cheaper:
            return (False, "", 0, 0, 0, "not_cheaper_than_competitor")

        if median_30d > 0 and effective_old > median_30d * 1.22:
            effective_old = int(median_30d * 1.08)

        if effective_old > cur:
            disc = round((effective_old - cur) / effective_old * 100)
            saving = effective_old - cur
            if disc > MAX_REALISTIC_DISC:
                return (False, "", 0, 0, 0, "unrealistic_discount_pct")

            # حظر الخصومات غير الموثقة العالية (> 42%) إذا كانت لسلعة مجهولة بلا ماركة وبلا تقييمات موثوقة
            if not has_history_proof and not has_market_deal_proof and disc > 42 and not brand and reviews < 5:
                return (False, "", 0, 0, 0, "suspicious_unverified_high_disc")

            if strength in ("strong", "medium") or has_market_deal_proof:
                if (cur >= 1500 and (disc >= MIN_DISCOUNT_HIGH or saving >= 160)) or (disc >= MIN_DISCOUNT_REGULAR):
                    c_proof = 30 if (strength == "strong" or (has_history_proof and has_market_deal_proof)) else 24
                    c_match = 18 if exact_model else 14
                    c_auth = min(24, 14 + disc // 3)
                    total_conf = min(96, c_proof + c_match + c_auth + c_extra)
                    if total_conf >= MIN_CONF_VERIFIED:
                        return (True, "خصم معلن موثق", effective_old, disc, total_conf, "ok")
            else:
                if has_competitor and not is_cheaper_than_market:
                    return (False, "", 0, 0, 0, "not_cheaper_than_competitor")
                is_good_unverified = (
                    (has_coupon and disc >= 8)
                    or (cur >= 1800 and (disc >= 9 or saving >= 220))
                    or (disc >= effective_unverified_disc and saving >= min_saving_unverified)
                    or (cat in ("GROC", "PERF", "FASH", "TOOLS", "WATCH") and disc >= 15 and saving >= 18)
                )
                if is_good_unverified:
                    c_base = 35
                    c_match = 16 if exact_model else 12
                    c_auth = min(19, 10 + disc // 4)
                    total_conf = min(76, c_base + c_match + c_auth + c_extra)
                    if total_conf >= MIN_CONF_UNVERIFIED:
                        return (True, "خصم معلن غير مؤكد (قيد التوثيق)", effective_old, disc, total_conf, "ok")
                return (False, "", 0, 0, 0, "unverified_low_discount")

    return (False, "", 0, 0, 0, "no_discount_or_drop")


# ================= الجلب المتوازي مع منظم السرعة لكل متجر =================

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


def smart_fetch(url: str, store_id: str, is_amazon: bool = False) -> str:
    sem = _DOMAIN_SEMAPHORES[store_id]
    with sem:
        time.sleep(random.uniform(0.25, 0.65))
        base_headers = {
            "Accept-Language": AR_LANG,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://www.google.com.eg/",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": random.choice(UA_LIST)
        }
        if is_amazon:
            base_headers["Cookie"] = "i18n-prefs=EGP; lc-acbamo=ar_AE"

        if HAS_CFFI:
            fps = ["safari17_0", "chrome120", "chrome116", "edge101"]
            random.shuffle(fps)
            for fp in fps[:3]:
                try:
                    sess = cffi_requests.Session(impersonate=fp)
                    r = sess.get(url, headers=base_headers, timeout=11)
                    if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                        return r.text
                except Exception:
                    pass

        if HAS_CLOUDSCRAPER:
            try:
                local_scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
                r = local_scraper.get(url, headers=base_headers, timeout=11)
                if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                    return r.text
            except Exception:
                pass

        enc = urllib.parse.quote(url, safe="")
        proxies = [
            ("CodeTabs", f"https://api.codetabs.com/v1/proxy?quest={enc}"),
            ("AllOrigins-Raw", f"https://api.allorigins.win/raw?url={enc}"),
            ("JinaReader-HTML", f"https://r.jina.ai/{url}")
        ]
        for pname, purl in proxies:
            try:
                h = {"User-Agent": random.choice(UA_LIST), "Accept-Language": AR_LANG}
                if pname == "JinaReader-HTML":
                    h["X-Return-Format"] = "html"
                    h["X-With-Links-Summary"] = "true"
                r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=15) if HAS_CFFI else requests.get(purl, headers=h, timeout=15)
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
    """فحص وجود رمز العملة في العنصر نفسه أو الأب المباشر أو الجد."""
    if price_el is None:
        return False
    for node in (price_el, price_el.parent, price_el.parent.parent if price_el.parent else None):
        if node is not None:
            txt = node.get_text(" ", strip=True)
            if len(txt) <= 120 and _EGP_TOKEN_RE.search(txt):
                return True
    return False


def extract_amazon_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "") -> List[dict]:
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
            if has_installment_or_blocked(parent_txt, custom_blocked):
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
        # التقاط التقييم من 1.0 إلى 5.0 وعدد المقيمين
        rating = 0.0
        reviews = 0
        m_rate = re.search(r"([1-5](?:\.\d)?)\s*(?:من\s*5|out\s*of\s*5)", card_text, re.I)
        if m_rate:
            rating = safe_float(m_rate.group(1))
        m_rev = re.search(r"\((\d{1,3}(?:,\d{3})*|\d+)\)", card_text)
        if m_rev:
            reviews = safe_int(m_rev.group(1).replace(",", ""))

        if not old:
            m_pct = re.search(r"(?:[-−]\s*(\d{1,2})\s*%|وفر\s*(\d{1,2})\s*%|\bsave\s*(\d{1,2})\s*%)", card_text, re.I)
            if m_pct:
                pct_val = safe_int(m_pct.group(1) or m_pct.group(2) or m_pct.group(3))
                if 10 <= pct_val <= 70:
                    old = int(round(cur / (1.0 - pct_val / 100.0)))

        m_coup = _REAL_COUPON_CODE_RE.search(card_text)
        coupon_txt = m_coup.group(0).strip() if m_coup else ""

        seen_asins.add(asin)
        items.append({
            "title": title,
            "price": cur,
            "old": old if old > cur else 0,
            "link": f"https://www.amazon.eg/dp/{asin}",
            "coupon": coupon_txt,
            "rating": rating,
            "reviews": reviews,
            "hint_cat": hint_cat
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


def extract_noon_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "") -> List[dict]:
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

                        link = f"https://www.noon.com/egypt-ar/{str(node.get('url') or 'deal').strip('/')}/{sku}/p/"
                        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(name, cur) and link not in seen_links:
                            seen_links.add(link)
                            items.append({"title": clean_title(name), "price": cur, "old": old, "link": link, "coupon": "", "rating": rating, "reviews": reviews, "hint_cat": hint_cat})
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

        if not old and cur > 0:
            card_txt = a.get_text(" ", strip=True).translate(_TRANS)
            m_pct = re.search(r"(\d{1,2})\s*%\s*(?:خصم|off)", card_txt, re.I)
            if m_pct:
                pct_v = safe_int(m_pct.group(1))
                if 10 <= pct_v <= 70:
                    old = int(round(cur / (1.0 - pct_v / 100.0)))

        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(title, cur):
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": "", "rating": 0.0, "reviews": 0, "hint_cat": hint_cat})
    return items


def extract_jumia_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "") -> List[dict]:
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
            m_rt = re.search(r"([1-5](?:\.\d)?)\s*out\s*of\s*5", rev_txt, re.I)
            if m_rt:
                rating = safe_float(m_rt.group(1))
            m_rv = re.search(r"\((\d+)\)", rev_txt)
            if m_rv:
                reviews = safe_int(m_rv.group(1))

        if cur >= MIN_PRODUCT_PRICE_GLOBAL and not is_phone_accessory(title, cur):
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": "", "rating": rating, "reviews": reviews, "hint_cat": hint_cat})
    return items


def extract_arkhasfeen_items(html_text: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "") -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not any(p in href for p in ("/product/", "/p/", "/item/", "/deal/")):
            continue
        link = urllib.parse.urljoin("https://arkhasfeen.com", href).split("?")[0]
        if link in seen_links:
            continue

        card = a_tag.find_parent(["div", "article", "li"]) or a_tag
        card_txt = card.get_text(" ", strip=True)
        t_el = card.find(["h2", "h3", "h4", "strong"]) or a_tag
        title = clean_title(t_el.get_text(" ", strip=True))
        if len(title) < 5 or has_installment_or_blocked(title, custom_blocked, custom_bigrams):
            continue

        prices_found = [parse_price(m.group(0), custom_blocked, explicit_egp=True) for m in _CUR_PRICE_RE.finditer(card_txt.translate(_TRANS))]
        prices_found = [p for p in prices_found if p >= MIN_PRODUCT_PRICE_GLOBAL]
        if prices_found:
            cur = min(prices_found)
            old = max(prices_found) if len(prices_found) > 1 and max(prices_found) > cur else 0
            seen_links.add(link)
            items.append({
                "title": title, "price": cur, "old": old,
                "link": link, "coupon": "", "seller": "متجر محلي", "rating": 0.0, "reviews": 0, "hint_cat": hint_cat
            })

    for sc in soup.find_all("script"):
        sc_txt = sc.string or ""
        if "price" not in sc_txt and "best_price" not in sc_txt:
            continue
        unescaped = sc_txt.replace('\\"', '"').replace("\\\\", "\\")
        for m in re.finditer(r'\{[^{}]*"(?:title|name)"\s*:\s*"([^"]{6,160})"[^{}]*"(?:price|best_price|lowest_price)"\s*:\s*(\d+(?:\.\d+)?)[^{}]*\}', unescaped):
            name = clean_title(m.group(1))
            cur = safe_int(m.group(2))
            if cur >= MIN_PRODUCT_PRICE_GLOBAL and not has_installment_or_blocked(name, custom_blocked, custom_bigrams):
                obj_slice = m.group(0)
                m_old = re.search(r'"(?:old_price|regular_price|was_price)"\s*:\s*(\d+(?:\.\d+)?)', obj_slice)
                old = safe_int(m_old.group(1)) if m_old else 0
                m_slug = re.search(r'"(?:slug|url|id)"\s*:\s*"([^"]+)"', obj_slice)
                slug = m_slug.group(1) if m_slug else hashlib.md5(name.encode()).hexdigest()[:10]
                prod_url = slug if slug.startswith("http") else f"https://arkhasfeen.com/p/{slug.lstrip('/')}"
                if prod_url not in seen_links:
                    seen_links.add(prod_url)
                    items.append({
                        "title": name, "price": cur, "old": old if old > cur else 0,
                        "link": prod_url, "coupon": "", "seller": "متجر محلي", "rating": 0.0, "reviews": 0, "hint_cat": hint_cat
                    })

    return items


def extract_general_items(html_text: str, base_domain: str, custom_blocked: Set[str], custom_bigrams: Set[str], hint_cat: str = "") -> List[dict]:
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

        # المتاجر المحلية الخالصة (2B, Raneen, Dream2000) أسعارها دائماً بالجنيه المصري
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
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": "", "rating": 0.0, "reviews": 0, "hint_cat": hint_cat})
    return items


# ================= مصفوفة الروابط العملاقة (Hyper-Scale Multi-Page Matrix) =================

def build_dynamic_stores(run_seed: int) -> List[dict]:
    pages = [1, 2, 3, 4, 5, 6]
    p1 = pages[run_seed % 6]
    p2 = pages[(run_seed + 1) % 6]
    p3 = pages[(run_seed + 2) % 6]

    amazon_base_queries = [
        ("https://www.amazon.eg/s?i=grocery&rh=p_n_pct-off-with-tax%3A10-", "GROC"),
        ("https://www.amazon.eg/s?i=grocery&rh=p_n_deal_type%3A26901098031", "GROC"),
        ("https://www.amazon.eg/s?k=زيت+سمن+ارز+مكرونه+سكر+دقيق&i=grocery", "GROC"),
        ("https://www.amazon.eg/s?k=نسكافيه+قهوه+شاي+ليبتون+لبن&i=grocery", "GROC"),
        ("https://www.amazon.eg/s?k=اريال+برسيل+تايد+داوني+فيري+مناديل&rh=p_n_pct-off-with-tax%3A10-", "GROC"),
        ("https://www.amazon.eg/s?k=perfume+edp+edt+عطر+برفان&i=beauty&rh=p_n_pct-off-with-tax%3A10-", "PERF"),
        ("https://www.amazon.eg/s?i=beauty&rh=p_n_deal_type%3A26901098031", "PERF"),
        ("https://www.amazon.eg/s?k=lattafa+armaf+afnan+rasasi+عطر&i=beauty", "PERF"),
        ("https://www.amazon.eg/s?k=شامبو+شاور+كريم+مزيل+عرق+لوريال+نيفيا&i=beauty&rh=p_n_pct-off-with-tax%3A15-", "PERF"),
        ("https://www.amazon.eg/s?k=casio+seiko+citizen+fossil+ساعه+يد&rh=p_n_pct-off-with-tax%3A10-", "WATCH"),
        ("https://www.amazon.eg/s?i=fashion&bbn=21833215031&rh=p_n_pct-off-with-tax%3A15-", "WATCH"),
        ("https://www.amazon.eg/s?k=ساعه+رجالي+ساعه+حريمي+smart+watch&rh=p_n_pct-off-with-tax%3A15-", "WATCH"),
        ("https://www.amazon.eg/s?i=fashion&rh=p_n_deal_type%3A26901098031", "FASH"),
        ("https://www.amazon.eg/s?k=adidas+nike+puma+skechers+حذاء+كوتشي&i=fashion&rh=p_n_pct-off-with-tax%3A15-", "FASH"),
        ("https://www.amazon.eg/s?k=تيشيرت+قميص+بنطلون+جينز+جاكيت+رجالي&i=fashion&rh=p_n_pct-off-with-tax%3A20-", "FASH"),
        ("https://www.amazon.eg/s?k=ملابس+حريمي+فستان+بيجامه+شنطه&i=fashion&rh=p_n_pct-off-with-tax%3A20-", "FASH"),
        ("https://www.amazon.eg/s?i=hi&rh=p_n_pct-off-with-tax%3A10-", "TOOLS"),
        ("https://www.amazon.eg/s?k=bosch+total+ingco+stanley+شنيور+صاروخ+طقم+عده&rh=p_n_pct-off-with-tax%3A10-", "TOOLS"),
        ("https://www.amazon.eg/s?i=automotive&rh=p_n_pct-off-with-tax%3A15-", "TOOLS"),
        ("https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031", "MOB"),
        ("https://www.amazon.eg/s?i=electronics&rh=p_n_pct-off-with-tax%3A10-", "MOB"),
        ("https://www.amazon.eg/s?k=samsung+iphone+xiaomi+realme+oppo+vivo&i=electronics", "MOB"),
        ("https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031", "LAP"),
        ("https://www.amazon.eg/s?k=laptop+لابتوب+شاشه+كمبيوتر&i=computers", "LAP"),
        ("https://www.amazon.eg/s?k=tv+smart+شاشه+تلفزيون&i=electronics", "TV"),
        ("https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031", "APPL"),
        ("https://www.amazon.eg/s?i=appliances&rh=p_n_pct-off-with-tax%3A10-", "APPL"),
        ("https://www.amazon.eg/s?k=قلايه+خلاط+ميكروويف+مكنسه+مكواه+كاتل&i=kitchen", "APPL")
    ]

    amazon_urls = []
    for base_q, cat_code in amazon_base_queries:
        amazon_urls.append((f"{base_q}&page={p1}&language=ar_AE", cat_code))
        amazon_urls.append((f"{base_q}&page={p2}&language=ar_AE", cat_code))

    noon_base_queries = [
        ("https://www.noon.com/egypt-ar/grocery/?sort[by]=discount&sort[dir]=desc", "GROC"),
        ("https://www.noon.com/egypt-ar/grocery/?sort[by]=popularity&sort[dir]=desc", "GROC"),
        ("https://www.noon.com/egypt-ar/beauty-and-health/fragrance/?sort[by]=discount&sort[dir]=desc", "PERF"),
        ("https://www.noon.com/egypt-ar/beauty-and-health/?sort[by]=discount&sort[dir]=desc", "PERF"),
        ("https://www.noon.com/egypt-ar/watches/?sort[by]=discount&sort[dir]=desc", "WATCH"),
        ("https://www.noon.com/egypt-ar/fashion/?sort[by]=discount&sort[dir]=desc", "FASH"),
        ("https://www.noon.com/egypt-ar/fashion-men/?sort[by]=discount&sort[dir]=desc", "FASH"),
        ("https://www.noon.com/egypt-ar/fashion-women/?sort[by]=discount&sort[dir]=desc", "FASH"),
        ("https://www.noon.com/egypt-ar/tools-and-home-improvement/?sort[by]=discount&sort[dir]=desc", "TOOLS"),
        ("https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc", "MOB"),
        ("https://www.noon.com/egypt-ar/electronics-and-mobiles/computers-and-accessories/laptops-16014/?sort[by]=discount&sort[dir]=desc", "LAP"),
        ("https://www.noon.com/egypt-ar/electronics-and-mobiles/television-and-video/televisions/?sort[by]=discount&sort[dir]=desc", "TV"),
        ("https://www.noon.com/egypt-ar/appliances/?sort[by]=discount&sort[dir]=desc", "APPL"),
        ("https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc", "APPL"),
        ("https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc", "GEN")
    ]

    noon_urls = []
    for base_q, cat_code in noon_base_queries:
        noon_urls.append((f"{base_q}&limit=50&page={p1}", cat_code))
        noon_urls.append((f"{base_q}&limit=50&page={p2}", cat_code))

    jumia_urls = [
        (f"https://www.jumia.com.eg/ar/flash-sales/?page={p1}", "GEN"),
        (f"https://www.jumia.com.eg/ar/groceries/?sort=highest-discount&page={p1}", "GROC"),
        (f"https://www.jumia.com.eg/ar/groceries/?sort=popularity&page={p2}", "GROC"),
        (f"https://www.jumia.com.eg/ar/health-beauty/?sort=highest-discount&page={p1}", "PERF"),
        (f"https://www.jumia.com.eg/ar/watches-sunglasses/?sort=highest-discount&page={p1}", "WATCH"),
        (f"https://www.jumia.com.eg/ar/category-fashion-by-jumia/?sort=highest-discount&page={p1}", "FASH"),
        (f"https://www.jumia.com.eg/ar/home-improvement-tools/?sort=highest-discount&page={p1}", "TOOLS"),
        (f"https://www.jumia.com.eg/ar/phones-tablets/?sort=highest-discount&page={p1}", "MOB"),
        (f"https://www.jumia.com.eg/ar/computing/?sort=highest-discount&page={p1}", "LAP"),
        (f"https://www.jumia.com.eg/ar/home-office-appliances/?sort=highest-discount&page={p1}", "APPL")
    ]

    return [
        {
            "id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg",
            "type": "amazon", "max_deals": MAX_DEALS_AMAZON,
            "urls": amazon_urls
        },
        {
            "id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com",
            "type": "noon", "max_deals": MAX_DEALS_NOON,
            "urls": noon_urls
        },
        {
            "id": "jumia", "name": "جوميا مصر (Jumia)", "domain": "https://www.jumia.com.eg",
            "type": "jumia", "max_deals": MAX_DEALS_JUMIA,
            "urls": jumia_urls
        },
        {
            "id": "arkhasfeen", "name": "أرخص فين (35+ متجر محلي)", "domain": "https://arkhasfeen.com",
            "type": "arkhasfeen", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                ("https://arkhasfeen.com/deals", "GEN"),
                ("https://arkhasfeen.com/c/mobile", "MOB"),
                ("https://arkhasfeen.com/c/tv", "TV"),
                ("https://arkhasfeen.com/c/laptop", "LAP"),
                ("https://arkhasfeen.com/c/ac", "APPL")
            ]
        },
        {
            "id": "2b", "name": "2B Egypt", "domain": "https://2b.com.eg",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                (f"https://2b.com.eg/ar/deals.html?p={p1}", "GEN"),
                (f"https://2b.com.eg/ar/deals.html?p={p2}", "GEN"),
                (f"https://2b.com.eg/ar/computers.html?p={p1}", "LAP"),
                (f"https://2b.com.eg/ar/mobiles-tablets.html?p={p1}", "MOB"),
                (f"https://2b.com.eg/ar/mobiles-tablets.html?p={p2}", "MOB"),
                (f"https://2b.com.eg/ar/home-appliances.html?p={p1}", "APPL"),
                (f"https://2b.com.eg/ar/tvs.html?p={p1}", "TV")
            ]
        },
        {
            "id": "raneen", "name": "رنين (Raneen)", "domain": "https://www.raneen.com",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                (f"https://www.raneen.com/ar/deals?p={p1}", "APPL"),
                (f"https://www.raneen.com/ar/deals?p={p2}", "APPL"),
                (f"https://www.raneen.com/ar/deals?p={p3}", "APPL"),
                ("https://www.raneen.com/ar/deals/appliances-electronics-offers", "APPL")
            ]
        },
        {
            "id": "dream2000", "name": "دريم 2000 (Dream 2000)", "domain": "https://dream2000.com",
            "type": "general", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                (f"https://dream2000.com/mobiles.html?p={p1}", "MOB"),
                (f"https://dream2000.com/mobiles.html?p={p2}", "MOB"),
                (f"https://dream2000.com/home-appliances.html?p={p1}", "APPL"),
                (f"https://dream2000.com/laptops-and-ipads.html?p={p1}", "LAP")
            ]
        }
    ]


# ================= دالة العامل المتوازي (Parallel Worker) =================

def _fetch_and_extract_task(
    task: Tuple[dict, str, str],
    custom_blocked: Set[str],
    custom_bigrams: Set[str]
) -> Tuple[str, bool, List[dict]]:
    st, url, hint_cat = task
    sid, sname, stype = st["id"], st["name"], st["type"]
    is_amz = (stype == "amazon")

    html_txt = smart_fetch(url, sid, is_amazon=is_amz)
    if not html_txt:
        return (sid, False, [])

    if is_amz:
        ext = extract_amazon_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat)
    elif stype == "noon":
        ext = extract_noon_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat)
    elif stype == "jumia":
        ext = extract_jumia_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat)
    elif stype == "arkhasfeen":
        ext = extract_arkhasfeen_items(html_txt, custom_blocked, custom_bigrams, hint_cat=hint_cat)
    else:
        ext = extract_general_items(html_txt, st["domain"], custom_blocked, custom_bigrams, hint_cat=hint_cat)

    for item in ext:
        if stype == "arkhasfeen" and item.get("seller") and item["seller"] != "متجر محلي":
            item["store"] = f"أرخص فين ({item['seller']})"
            item["canonical_seller"] = get_canonical_store_id(item["seller"])
        else:
            item["store"] = sname
            item["canonical_seller"] = get_canonical_store_id(sname)

        item["sid"] = sid
        ident = analyze_product_identity(item["title"], hint_cat=item.get("hint_cat", ""))
        item["sig"] = ident["sig"]
        item["core_match_key"] = ident["core_match_key"]
        item["specs"] = ident["specs"]
        item["exact_model"] = ident["exact_model"]
        item["brand"] = ident["brand"]
        item["category"] = ident["category"]

    return (sid, True, ext)


# ================= الحلقة التنفيذية الرئيسية المحمية بـ try/finally =================

def main() -> None:
    t0 = time.time()
    now = int(t0)
    run_seed = int(now // 1800)
    log(f"🚀 تشغيل محرك الصفقات الذكي (V38) - دورة #{run_seed % 10}...")

    raw_state = load_json(STATE_FILE)
    raw_history = load_json(HISTORY_FILE)
    raw_rules = load_json(RULES_FILE)

    state = sanitize_state(raw_state)
    history = sanitize_history(raw_history, now)
    rules = sanitize_rules(raw_rules)

    try:
        process_telegram_feedback(rules, state, now)
        custom_blocked = {normalize_text(w) for w in rules.get("blocked_keywords", []) if w}
        custom_bigrams = {normalize_text(bg) for bg in rules.get("blocked_bigrams", []) if bg}
        blocked_deals = set(rules.get("blocked_deals", []))
        liked_brands = rules.get("liked_brands", {})
        cat_affinity = rules.get("category_affinity", {})
        store_modifiers = rules.get("store_discount_modifiers", {})
        stats = rules.get("stats", {})

        state = {k: v for k, v in state.items() if isinstance(v, dict) and (now - v.get("ts", 0)) < REALERT_HOURS * 3600}

        stores = build_dynamic_stores(run_seed)
        all_raw_items: List[dict] = []
        store_diag: Dict[str, dict] = {
            st["id"]: {
                "opened": False, "raw": 0, "unique": 0, "exact_models": 0,
                "cross_matched": 0, "accepted": 0, "sent": 0, "reject_reasons": Counter()
            }
            for st in stores
        }

        tasks: List[Tuple[dict, str, str]] = []
        for st in stores:
            for u_entry in st["urls"]:
                url_str, h_cat = u_entry if isinstance(u_entry, tuple) else (u_entry, "GEN")
                tasks.append((st, url_str, h_cat))

        log(f"⚡ جاري مسح {len(tasks)} صفحة أقسام بالتوازي عبر {MAX_WORKERS} مساراً مع منظم سرعة لكل متجر...")

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
                    log(f"⚠ خطأ في مهمة فحص فرعية: {e}")

        log(f"📦 إجمالي المنتجات الخام المستخرجة في هذه الجولة: {len(all_raw_items):,} منتج!")

        # تجميع الموديلات عبر المفتاح الأساسي المرن (Fuzzy Core Match Index)
        market_by_core = defaultdict(list)
        for it in all_raw_items:
            ckey = it.get("core_match_key")
            if ckey:
                market_by_core[ckey].append((it["price"], it["store"], it.get("canonical_seller", ""), it.get("specs", {}), it["link"]))

        items_by_store = defaultdict(list)
        for it in all_raw_items:
            items_by_store[it["sid"]].append(it)

        total_sent = 0
        cat_sent_counter: Counter = Counter()

        for st in stores:
            sid = st["id"]
            sname = st["name"]
            max_deals = st.get("max_deals", MAX_DEALS_OTHER)
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
            verified_candidates = []

            for clean_link, it in unique_store_items.items():
                if it["exact_model"]:
                    diag["exact_models"] += 1

                deal_hash = hashlib.md5(clean_link.encode()).hexdigest()[:16]
                if deal_hash in blocked_deals:
                    diag["reject_reasons"]["user_blocked"] += 1
                    continue

                h_stats = record_and_analyze_history(history, state, rules, deal_hash, clean_link, it["price"], now)

                lowest_other = 0
                same_store_higher = 0
                ckey = it.get("core_match_key")
                my_canonical = it.get("canonical_seller", "")
                my_specs = it.get("specs", {})
                item_cat = it.get("category", "GEN")

                if ckey:
                    valid_competitors = []
                    same_store_prices = []
                    for p, st_name, comp_canonical, comp_specs, comp_link in market_by_core.get(ckey, []):
                        if not specs_are_compatible(my_specs, comp_specs, item_cat):
                            continue
                        if st_name != it["store"] and (not my_canonical or comp_canonical != my_canonical):
                            valid_competitors.append(p)
                        elif comp_link.split("?")[0].rstrip("/").lower() != clean_link and p > it["price"]:
                            same_store_prices.append(p)

                    if valid_competitors:
                        lowest_other = min(valid_competitors)
                        diag["cross_matched"] += 1
                    if same_store_prices:
                        same_store_higher = max(same_store_prices)

                is_deal, deal_type, ref_price, saving_pct, conf_score, reason = evaluate_deal(
                    cur=it["price"],
                    old=it["old"],
                    cat=item_cat,
                    brand=it.get("brand", ""),
                    h_stats=h_stats,
                    market_lowest_other=lowest_other,
                    same_store_higher=same_store_higher,
                    exact_model=it["exact_model"],
                    has_coupon=bool(it.get("coupon")),
                    brand_liked=bool(liked_brands.get(it["brand"], 0) > 0),
                    cat_bonus=cat_affinity.get(item_cat, 0),
                    store_disc_mod=store_disc_mod,
                    rating=safe_float(it.get("rating")),
                    reviews=safe_int(it.get("reviews"))
                )

                if not is_deal:
                    diag["reject_reasons"][reason] += 1
                    continue

                prev_sent = state.get(deal_hash)
                if prev_sent and it["price"] >= prev_sent.get("price", it["price"]) * 0.96:
                    diag["reject_reasons"]["recently_sent"] += 1
                    continue

                diag["accepted"] += 1
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
                verified_candidates.append(it)

            top_reasons = ", ".join(f"{k}:{v}" for k, v in diag["reject_reasons"].most_common(4)) or "لا يوجد"
            log(
                f"📋 تشخيص [{sname}]: فريد={diag['unique']} | بموديل={diag['exact_models']} "
                f"| مقارن={diag['cross_matched']} | مقبول={diag['accepted']} | أسباب الاستبعاد=({top_reasons})"
            )

            verified_candidates.sort(
                key=lambda x: (
                    "قيد التوثيق" not in x["deal_type"],
                    x["conf_score"],
                    x["saving_pct"],
                    x["ref_price"] - x["price"]
                ),
                reverse=True
            )

            selected_for_store: List[dict] = []
            cat_bucket_counts: Counter = Counter()
            max_per_cat_in_store = max(3, max_deals // 3)

            for cand in verified_candidates:
                c_cat = cand.get("category", "GEN")
                if cat_bucket_counts[c_cat] < max_per_cat_in_store and len(selected_for_store) < max_deals:
                    selected_for_store.append(cand)
                    cat_bucket_counts[c_cat] += 1

            for cand in verified_candidates:
                if len(selected_for_store) >= max_deals:
                    break
                if cand not in selected_for_store:
                    selected_for_store.append(cand)

            sent_store = 0
            for it in selected_for_store:
                saving = it["ref_price"] - it["price"]
                dtype = it["deal_type"]
                conf = it["conf_score"]
                obs_d = it.get("obs_days_30d", 0)
                h_str = it.get("history_strength", "none")
                item_cat = it.get("category", "GEN")
                cat_badge = CATEGORY_LABELS_AR.get(item_cat, "🛍 عروض متنوعة")
                strength_ar = {"strong": "قوي", "medium": "متوسط", "weak": "محدود"}.get(h_str, "جديد")

                if dtype == "هبوط سعر صامت موثق":
                    tag = f"📉 <b>هبوط سعر صامت موثق — {htmllib.escape(it['store'])}</b>"
                    ref_txt = f"🏷 وسيط السعر ({obs_d} أيام رصد - إثبات {strength_ar}): {it['ref_price']:,} ج.م (نزل {it['saving_pct']}% — وفر {saving:,} ج.م)"
                elif dtype == "هبوط تاريخي بالمتجر (يتوفر منافس قريب)":
                    tag = f"📉 <b>هبوط تاريخي بالمتجر — {htmllib.escape(it['store'])}</b>\n<i>(ملاحظة: يتوفر منافس بسعر {it['lowest_other']:,} ج.م)</i>"
                    ref_txt = f"🏷 وسيط سعر المتجر ({obs_d} أيام رصد): {it['ref_price']:,} ج.م (نزل {it['saving_pct']}%)"
                elif dtype == "أرخص بين المتاجر المفحوصة":
                    tag = f"🏆 <b>أقل سعر بين المتاجر المفحوصة — {htmllib.escape(it['store'])}</b>"
                    ref_txt = f"🏪 أقل سعر لدى المنافسين حالياً: {it['ref_price']:,} ج.م (أرخص بـ {saving:,} ج.م)"
                elif dtype == "خصم معلن موثق":
                    tag = f"✅ <b>عرض معلن موثق — {htmllib.escape(it['store'])}</b> (خصم {it['saving_pct']}%)"
                    ref_txt = f"🏷 السعر المرجعي الموثق: {it['ref_price']:,} ج.م (توفير {saving:,} ج.م)"
                else:
                    tag = f"🔥 <b>خصم مميز مرصود — {htmllib.escape(it['store'])}</b> (خصم {it['saving_pct']}%)"
                    ref_txt = f"🏷 السعر السابق بالموقع: {it['ref_price']:,} ج.م (وفر {saving:,} ج.م)"

                hist_line = (
                    f"📊 أقل سعر مسجل ({obs_d} يوم رصد في آخر 30 يوماً): {it['min_30d']:,} ج.م\n"
                    if it.get("min_30d") and obs_d > 0 else ""
                )
                rev_cnt = safe_int(it.get("reviews"))
                rev_str = f" ({rev_cnt:,} تقييم)" if rev_cnt > 0 else ""
                rate_line = f"⭐ تقييم المنتج: <b>{it['rating']}/5</b>{rev_str}\n" if it.get("rating", 0) >= 3.4 else ""
                coupon_line = f"🎟 <b>كود خصم إضافي: {htmllib.escape(it['coupon'])}</b>\n" if it.get("coupon") else ""
                safe_link = sanitize_url(it["link"])

                msg = (
                    f"{tag}\n"
                    f"📂 القسم: <b>{cat_badge}</b>\n\n"
                    f"📦 {htmllib.escape(it['title'])}\n"
                    f"💰 السعر النقدي الآن: <b>{it['price']:,} ج.م</b>\n"
                    f"{ref_txt}\n"
                    f"{hist_line}"
                    f"{rate_line}"
                    f"{coupon_line}"
                    f"🛡 مؤشر ثقة الصفقة: <b>{conf}/100</b>\n"
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
                        "store": it["store"],
                        "link": it["clean_link"],
                        "message_id": msg_id,
                        "auto_rewarded": False,
                        "rated_by": []
                    }
                    sent_store += 1
                    total_sent += 1
                    cat_sent_counter[cat_badge] += 1
                    if total_sent % 5 == 0:
                        save_json(STATE_FILE, state)
                    time.sleep(1.1)

            diag["sent"] = sent_store

    finally:
        # ضمان حفظ الذاكرة دائماً حتى في حال حدوث أي انقطاع مفاجئ
        if len(history) > 35000:
            sorted_h = sorted(history.items(), key=lambda kv: safe_int(kv[1].get("ts") if isinstance(kv[1], dict) else 0))
            history = dict(sorted_h[-35000:])
        save_json(STATE_FILE, state)
        save_json(HISTORY_FILE, history)
        save_json(RULES_FILE, rules)
        git_ok = commit_state(state, history, rules)

    pos = safe_int(rules.get("stats", {}).get("total_positive"))
    neg = safe_int(rules.get("stats", {}).get("total_negative"))
    auto_wins = safe_int(rules.get("stats", {}).get("auto_verified_wins"))
    total_feedback = pos + neg + auto_wins
    accuracy_pct = round(((pos + auto_wins) / total_feedback * 100), 1) if total_feedback > 0 else 100.0

    elapsed = (time.time() - t0) / 60.0
    total_raw_scanned = sum(d["raw"] for d in store_diag.values())
    total_unique_scanned = sum(d["unique"] for d in store_diag.values())
    summary_lines = [
        "📊 <b>ملخص جولة محرك الصفقات الذكي (V38)</b>",
        f"🔎 إجمالي السلع المفحوصة: <b>{total_raw_scanned:,} إجمالي ({total_unique_scanned:,} سلعة فريدة)</b>",
        ""
    ]
    for st in stores:
        sid, sname = st["id"], st["name"]
        d = store_diag[sid]
        if d["opened"]:
            summary_lines.append(
                f"✅ <b>{sname}</b>: فُحص {d['unique']} | بموديل {d['exact_models']} "
                f"| مقارن {d['cross_matched']} | قُبل {d['accepted']} | أُرسل <b>{d['sent']}</b>"
            )
        else:
            summary_lines.append(f"❌ <b>{sname}</b>: تعذر الفتح في هذه الجولة")

    if cat_sent_counter:
        cat_summary = " | ".join(f"{k}: {v}" for k, v in cat_sent_counter.most_common())
        summary_lines.append(f"\n📂 <b>توزيع الأقسام المرسلة</b>:\n{cat_summary}")

    summary_lines.append(f"\n🧠 <b>التعلم الذاتي 5.0</b>: الدقة {accuracy_pct}% ({pos}👍 | {neg}👎 | {auto_wins}🎯 توثيق تلقائي)")
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
