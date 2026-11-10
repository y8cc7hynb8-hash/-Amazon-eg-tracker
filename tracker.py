#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار الصفقات المصري الاحترافي — V35 (Fully Hardened Production Engine)
======================================================================
1. تشغيل فوري بدون أقفال ملفات محلية تسبب الخروج المبكر في GitHub Actions.
2. إضافة مكتبة hashlib المفقودة لضمان حساب البصمات دون NameError.
3. نظام التعلم والتطوير الذاتي 3.0 (تكييف حدود الخصم وسمعة المتاجر والماركات).
4. مفتاح مطابقة عتاد دقيق حسب الفئة (CPU+GPU+RAM+Storage للابتوب / RAM+Storage+5G للهواتف).
5. استخراج Next.js المباشر من "أرخص فين" لتغطية أكثر من 35 متجراً محلياً كمرجع سوقي.
6. استخراج أسعار نون المشطوبة وحساب نسب الخصم عبر كائنات JSON وكروت DOM.
7. الحفظ الفوري لكل صفقة لمنع تكرار الإرسال تحت أي ظرف.
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

print("⚡ بدء تشغيل محرك الصفقات المعتمد V35...", flush=True)

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

# ================= الإعدادات والمعايير =================
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
            ["git", "commit", "-m", "chore: update radar intelligence memory V35", "--only", "--"] + target_files,
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
        rules["blocked_keywords"] = [w for w, cnt in new_strikes.items() if cnt >= 3]
        stats["last_decay_ts"] = now
        log("🔄 تم تطبيق دورة التآكل الزمني على قواعد الكلمات السلبية.")


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

                if canonical_store:
                    curr_mod = store_modifiers.get(canonical_store, 0)
                    store_modifiers[canonical_store] = min(8, curr_mod + 1)

                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) - 1

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
                if canonical_store:
                    curr_mod = store_modifiers.get(canonical_store, 0)
                    store_modifiers[canonical_store] = max(-3, curr_mod - 1)

                if brand:
                    liked_brands[brand] = liked_brands.get(brand, 0) + 1

                if cb_id:
                    _tg_post("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "تم تسجيل تقييمك الإيجابي 🌟",
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
    return any(b in title for b in ("just a moment", "attention required", "access denied", "403 forbidden", "security checkpoint", "robot check", "one more step", "sorry! something went wrong"))


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
                r = sess.get(url, headers=base_headers, timeout=12)
                if not is_blocked_page(r.text, r.status_code, is_amazon=is_amazon):
                    log(f"   ✅ فتح مباشر ({fp}) - {len(r.text)//1024}KB")
                    return r.text
            except Exception:
                pass

    if scraper:
        try:
            r = scraper.get(url, headers=base_headers, timeout=12)
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
            r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=20) if HAS_CFFI else requests.get(purl, headers=h, timeout=20)
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


# ================= استخراج المنتجات =================

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
    txt = price_el.get_text(" ", strip=True)
    if _EGP_TOKEN_RE.search(txt):
        return True
    if price_el.parent:
        for sib in price_el.parent.find_all(["span", "sup", "small", "bdi"], recursive=False):
            sib_cls = " ".join(sib.get("class") or []).lower()
            sib_txt = sib.get_text(" ", strip=True)
            if ("symbol" in sib_cls or "curr" in sib_cls or len(sib_txt) <= 8) and _EGP_TOKEN_RE.search(sib_txt):
                return True
    return False


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

        if cur < MIN_PRODUCT_PRICE:
            continue

        card_text = div.get_text(" ", strip=True)
        m_coup = _REAL_COUPON_CODE_RE.search(card_text)
        coupon_txt = m_coup.group(0).strip() if m_coup else ""

        seen_asins.add(asin)
        items.append({
            "title": title,
            "price": cur,
            "old": old if old > cur else 0,
            "link": f"https://www.amazon.eg/dp/{asin}",
            "coupon": coupon_txt
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


def extract_noon_items(html_text: str, custom_blocked: Set[str]) -> List[dict]:
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
                    if sku and is_node_egp and isinstance(name, str) and len(name) >= 6 and not has_installment_or_blocked(name, custom_blocked):
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
        if "/p/" not in href or ("/egypt-" not in href and href.startswith("http") and "egypt" not in href):
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

        cur = parse_price(p_now.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(p_now)) if p_now else 0
        old = parse_price(p_was.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(p_was)) if p_was else 0

        if cur >= MIN_PRODUCT_PRICE:
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": ""})
    return items


def extract_arkhasfeen_items(html_text: str, custom_blocked: Set[str]) -> List[dict]:
    items = []
    seen_links = set()
    soup = BeautifulSoup(html_text, "html.parser")

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            root_json = json.loads(next_script.string)
            stack = [root_json]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    name = node.get("title") or node.get("name")
                    price_val = node.get("price") or node.get("best_price") or node.get("lowest_price")
                    slug = str(node.get("slug") or node.get("id") or "").strip()
                    curr_val = str(node.get("currency") or "EGP").upper()

                    if isinstance(name, str) and len(name) >= 6 and price_val is not None and curr_val in ("EGP", "LE", "ج.م"):
                        cur = parse_price(price_val, custom_blocked, explicit_egp=True)
                        old = parse_price(node.get("old_price") or node.get("regular_price"), custom_blocked, explicit_egp=True)
                        store_name = str(node.get("store") or node.get("merchant") or node.get("seller") or "متجر محلي")
                        
                        if slug.startswith("http"):
                            prod_url = slug
                        elif slug:
                            prod_url = f"https://arkhasfeen.com/p/{slug.lstrip('/')}"
                        else:
                            prod_url = "https://arkhasfeen.com/deals"

                        if cur >= MIN_PRODUCT_PRICE and prod_url not in seen_links and not has_installment_or_blocked(name, custom_blocked):
                            seen_links.add(prod_url)
                            items.append({
                                "title": clean_title(name),
                                "price": cur,
                                "old": old if old > cur else 0,
                                "link": prod_url,
                                "coupon": "",
                                "seller": store_name
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

    if not items:
        cards = soup.find_all(["div", "article", "li"], class_=re.compile(r"product|deal|card|item", re.I))
        for card in cards:
            a_tag = card.find("a", href=True)
            if not a_tag or not a_tag["href"].strip():
                continue
            link = urllib.parse.urljoin("https://arkhasfeen.com", a_tag["href"].strip()).split("?")[0]
            if link in seen_links:
                continue

            _strip_installment_nodes(card)
            t_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"title|name|header", re.I)) or a_tag
            title = clean_title(t_el.get_text(" ", strip=True) if t_el else "")
            if len(title) < 6 or has_installment_or_blocked(title, custom_blocked):
                continue

            seller_el = card.find(class_=re.compile(r"store|merchant|seller|shop", re.I))
            seller_name = seller_el.get_text(" ", strip=True) if seller_el else "متجر محلي"

            cur = old = 0
            sp_el = card.find(class_=re.compile(r"price|current|now|special", re.I))
            old_el = card.find(class_=re.compile(r"old|was|before|strike", re.I)) or card.find(["del", "s"])

            if sp_el:
                cur = parse_price(sp_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True)
            if old_el:
                old = parse_price(old_el.get_text(" ", strip=True), custom_blocked, explicit_egp=True)

            if cur >= MIN_PRODUCT_PRICE:
                seen_links.add(link)
                items.append({
                    "title": title,
                    "price": cur,
                    "old": old if old > cur else 0,
                    "link": link,
                    "coupon": "",
                    "seller": seller_name
                })
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

        cur = parse_price(sp_el.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(sp_el)) if sp_el else 0
        old = parse_price(old_el.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(old_el)) if old_el else 0

        if not cur:
            for pt in card.find_all(class_=re.compile(r"price", re.I)):
                v = parse_price(pt.get_text(" ", strip=True), custom_blocked, explicit_egp=_element_has_egp(pt))
                if v >= MIN_PRODUCT_PRICE:
                    cur = v
                    break

        if cur >= MIN_PRODUCT_PRICE:
            seen_links.add(link)
            items.append({"title": title, "price": cur, "old": old if old > cur else 0, "link": link, "coupon": ""})
    return items


# ================= تدوير الروابط الموسع =================

def build_dynamic_stores(run_seed: int) -> List[dict]:
    pages = [1, 2, 3, 4]
    p1 = pages[run_seed % 4]
    p2 = pages[(run_seed + 1) % 4]
    p3 = pages[(run_seed + 2) % 4]

    amazon_pool = [
        f"https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=electronics&rh=p_n_pct-off-with-tax%3A10-&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?k=apple+iphone+ipad&i=electronics&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?k=samsung+galaxy&i=electronics&page={p2}&language=ar_AE",
        f"https://www.amazon.eg/s?k=xiaomi+redmi&i=electronics&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?k=laptop&i=computers&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&page={p2}&language=ar_AE",
        f"https://www.amazon.eg/s?k=tv+smart&i=electronics&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=appliances&rh=p_n_pct-off-with-tax%3A10-&page={p2}&language=ar_AE",
        f"https://www.amazon.eg/s?k=anker+soundcore&i=electronics&page={p1}&language=ar_AE",
        f"https://www.amazon.eg/s?i=beauty&rh=p_n_deal_type%3A26901098031&page={p3}&language=ar_AE"
    ]

    noon_pool = [
        f"https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc&page={p2}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/computers-and-accessories/laptops-16014/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/television-and-video/televisions/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/electronics-and-mobiles/audio-and-video/headphones-24021/?sort[by]=discount&sort[dir]=desc&page={p1}",
        f"https://www.noon.com/egypt-ar/appliances/?sort[by]=discount&sort[dir]=desc&page={p2}",
        f"https://www.noon.com/egypt-ar/home-and-kitchen/?sort[by]=discount&sort[dir]=desc&page={p2}",
        f"https://www.noon.com/egypt-ar/beauty-and-health/?sort[by]=discount&sort[dir]=desc&page={p1}"
    ]

    return [
        {
            "id": "amazon", "name": "أمازون مصر (Amazon)", "domain": "https://www.amazon.eg",
            "type": "amazon", "max_deals": MAX_DEALS_AMAZON,
            "urls": amazon_pool[:9]
        },
        {
            "id": "noon", "name": "نون مصر (Noon)", "domain": "https://www.noon.com",
            "type": "noon", "max_deals": MAX_DEALS_OTHER,
            "urls": noon_pool[:7]
        },
        {
            "id": "arkhasfeen", "name": "أرخص فين (35+ متجر محلي)", "domain": "https://arkhasfeen.com",
            "type": "arkhasfeen", "max_deals": MAX_DEALS_OTHER,
            "urls": [
                "https://arkhasfeen.com/deals",
                "https://arkhasfeen.com/c/mobile",
                "https://arkhasfeen.com/c/tv",
                "https://arkhasfeen.com/c/laptop"
            ]
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


# ================= الحلقة التنفيذية الرئيسية =================

def main() -> None:
    t0 = time.time()
    now = int(t0)
    run_seed = int(now // 1800)
    log(f"🚀 تشغيل محرك الصفقات الذكي مع التعلم الذاتي 3.0 (V35) - دورة #{run_seed % 10}...")

    raw_state = load_json(STATE_FILE)
    raw_history = load_json(HISTORY_FILE)
    raw_rules = load_json(RULES_FILE)

    state = sanitize_state(raw_state)
    history = sanitize_history(raw_history, now)
    rules = sanitize_rules(raw_rules)

    process_telegram_feedback(rules, state, now)
    custom_blocked = {normalize_text(w) for w in rules.get("blocked_keywords", []) if w}
    blocked_deals = set(rules.get("blocked_deals", []))
    liked_brands = rules.get("liked_brands", {})
    store_modifiers = rules.get("store_discount_modifiers", {})
    stats = rules.get("stats", {})

    pos = safe_int(stats.get("total_positive"))
    neg = safe_int(stats.get("total_negative"))
    total_feedback = pos + neg
    accuracy_pct = round((pos / total_feedback * 100), 1) if total_feedback > 0 else 100.0

    log(f"🧠 التعلم الذاتي 3.0: {len(blocked_deals)} عرض محظور | {len(custom_blocked)} كلمة مستبعدة | دقة التقييم: {accuracy_pct}% ({pos}👍 / {neg}👎).")

    state = {k: v for k, v in state.items() if isinstance(v, dict) and (now - v.get("ts", 0)) < REALERT_HOURS * 3600}

    stores = build_dynamic_stores(run_seed)
    all_raw_items: List[dict] = []
    store_diag: Dict[str, dict] = {}
    total_sent = 0

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
                elif stype == "arkhasfeen":
                    ext = extract_arkhasfeen_items(html_txt, custom_blocked)
                else:
                    ext = extract_general_items(html_txt, st["domain"], custom_blocked)
                for item in ext:
                    if stype == "arkhasfeen" and item.get("seller") and item["seller"] != "متجر محلي":
                        item["store"] = f"أرخص فين ({item['seller']})"
                        item["canonical_seller"] = get_canonical_store_id(item["seller"])
                    else:
                        item["store"] = sname
                        item["canonical_seller"] = get_canonical_store_id(sname)

                    item["sid"] = sid
                    ident = analyze_product_identity(item["title"])
                    item["sig"] = ident["sig"]
                    item["cross_match_key"] = ident["cross_match_key"]
                    item["exact_model"] = ident["exact_model"]
                    item["brand"] = ident["brand"]
                store_items.extend(ext)
            time.sleep(random.uniform(0.7, 1.3))

        all_raw_items.extend(store_items)
        store_diag[sid] = {
            "opened": opened,
            "raw": len(store_items),
            "unique": 0,
            "exact_models": 0,
            "cross_matched": 0,
            "accepted": 0,
            "sent": 0,
            "reject_reasons": Counter()
        }

    market_by_match = defaultdict(list)
    for it in all_raw_items:
        mkey = it.get("cross_match_key")
        if mkey:
            market_by_match[mkey].append((it["price"], it["store"], it.get("canonical_seller", "")))

    items_by_store = defaultdict(list)
    for it in all_raw_items:
        items_by_store[it["sid"]].append(it)

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

            h_stats = record_and_analyze_history(history, clean_link, it["price"], now)

            lowest_other = 0
            mkey = it.get("cross_match_key")
            my_canonical = it.get("canonical_seller", "")
            if mkey:
                valid_competitors = [
                    p for p, st_name, comp_canonical in market_by_match.get(mkey, [])
                    if st_name != it["store"] and (not my_canonical or comp_canonical != my_canonical)
                ]
                if valid_competitors:
                    lowest_other = min(valid_competitors)
                    diag["cross_matched"] += 1

            is_deal, deal_type, ref_price, saving_pct, conf_score, reason = evaluate_deal(
                cur=it["price"],
                old=it["old"],
                h_stats=h_stats,
                market_lowest_other=lowest_other,
                exact_model=it["exact_model"],
                has_coupon=bool(it.get("coupon")),
                brand_liked=bool(liked_brands.get(it["brand"], 0) > 0),
                store_disc_mod=store_disc_mod
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
            f"📋 تشخيص [{sname}]: فريد={diag['unique']} | بموديل دقيق={diag['exact_models']} "
            f"| مقارن={diag['cross_matched']} | مقبول={diag['accepted']} | تعديل الخصم={store_disc_mod:+d}% | أسباب الاستبعاد=({top_reasons})"
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

        sent_store = 0
        for it in verified_candidates[:max_deals]:
            saving = it["ref_price"] - it["price"]
            dtype = it["deal_type"]
            conf = it["conf_score"]
            obs_d = it.get("obs_days_30d", 0)
            h_str = it.get("history_strength", "none")
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
                tag = f"⚠️ <b>خصم معلن من المتجر (قيد التوثيق - غير مؤكد تاريخياً) — {htmllib.escape(it['store'])}</b>"
                ref_txt = f"ℹ️ السعر المشطوب بالموقع (غير موثق بعد): {it['ref_price']:,} ج.م (نسبة معلنة {it['saving_pct']}%)"

            hist_line = (
                f"📊 أقل سعر مسجل ({obs_d} يوم رصد في آخر 30 يوماً): {it['min_30d']:,} ج.م\n"
                if it.get("min_30d") and obs_d > 0 else ""
            )
            coupon_line = f"🎟 <b>كود خصم موثق: {htmllib.escape(it['coupon'])}</b>\n" if it.get("coupon") else ""
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

            msg_id = send_telegram_deal(msg, it["deal_hash"])
            if msg_id is not None:
                state[it["deal_hash"]] = {
                    "price": it["price"],
                    "ts": now,
                    "title": it["title"],
                    "brand": it["brand"],
                    "store": it["store"],
                    "link": it["clean_link"],
                    "message_id": msg_id,
                    "rated_by": []
                }
                save_json(STATE_FILE, state)
                sent_store += 1
                total_sent += 1
                time.sleep(1.2)

        diag["sent"] = sent_store

    if len(history) > 15000:
        sorted_h = sorted(history.items(), key=lambda kv: safe_int(kv[1].get("ts") if isinstance(kv[1], dict) else 0))
        history = dict(sorted_h[-15000:])

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    save_json(RULES_FILE, rules)
    git_ok = commit_state(state, history, rules)

    elapsed = (time.time() - t0) / 60.0
    summary_lines = ["📊 <b>ملخص جولة محرك الصفقات الذكي (V35)</b>", ""]
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

    summary_lines.append(f"\n🧠 <b>التعلم الذاتي</b>: دقة الرادار {accuracy_pct}% ({pos}👍 مقابل {neg}👎)")
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
