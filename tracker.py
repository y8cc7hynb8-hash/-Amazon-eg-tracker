#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار عروض المتاجر المصرية — V8
================================
اللي اتغيّر عن V7:
  1) ذاكرة أسعار (history.json): كل منتج بيتسجّل سعره في كل جولة، والعرض بيتحكم عليه من التاريخ الفعلي
     مش من "السعر القديم" اللي المتجر كاتبه:
        ✅ خصم حقيقي | 🏆 أقل سعر اتسجّل | 🚩 وهمي (السعر ثابت / اترفع قبل العرض / الخصم المعلن مش حقيقي) | 🆕 لسه بجمع بيانات
  2) مقارنة بين المتاجر: نفس الموديل (مطابقة تقريبية بالاسم) بيتقارن سعره في باقي المتاجر.
  3) تتبّع كل المنتجات حتى اللي مفيهاش سعر قديم (بي تك / كنز ...) وتنبيه لما السعر ينزل فعلاً عن المعتاد.
  4) جلب أذكى: بيفتكر الطريقة اللي نجحت لكل موقع ويتخطى الميتة، والبروكسي/البوابات بتتجرّب للمحجوب بس وبحد أقصى.
  5) تقرير صحة: المتجر المحجوب بيتعدّ عدد الجولات، وتنبيه لما يقع أو يرجع.

الإعدادات (Secrets / env) — كلها اختيارية ما عدا BOT_TOKEN و CHAT_ID:
  DATA_DIR                  فولدر الذاكرة (افتراضي: data لو موجود وإلا الفولدر الحالي)
  PROXY_URL                 بروكسي http://user:pass@host:port (يفضّل سكني/مصري) — بيتجرّب بعد فشل الاتصال المباشر بس
  SCRAPERAPI_KEY | SCRAPINGBEE_KEY | ZENROWS_KEY | SCRAPEDO_TOKEN     بوابات للمواقع المحجوبة (واحدة تكفي)
  GATEWAY_MIN_INTERVAL_MIN  (360) أقل فاصل بين طلبات البوابة لنفس الرابط — بيحافظ على الرصيد
  GATEWAY_MAX_CALLS         (6)   أقصى عدد طلبات بوابة في الجولة
  GATEWAY_COUNTRY           (eg)  لو الخطة مش بتدعم الدولة بيجرّب من غيرها لوحده
  SCRAPERAPI_MODE           premium | ultra | basic
  JINA_API_KEY              مفتاح Jina Reader (اختياري)
  MIN_DISCOUNT (20)  MAX_REALISTIC_DISC (70)  MIN_REAL_DROP (12)  DROP_ALERT (15)  MIN_PRODUCT_PRICE (200)
  MAX_DEALS_PER_STORE (6)  MAX_DEALS_PER_RUN (30)  MAX_UNVERIFIED_PER_STORE (3)
  SEND_UNVERIFIED=1  SEND_FLAGGED=0  TRACK_ALL=1  CROSS_STORE=1  USE_BROWSER=1
  ONLY_STORES=jumia,noon    تشغيل متاجر معينة بس (للتجربة)
  SUMMARY_MODE=auto|always|never
  COMMIT_STATE=1            (بديل) يحفظ الذاكرة في الريبو نفسه بدل الـ data branch
"""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
import traceback
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

DAY = 86400


def log(msg: str) -> None:
    print(msg, flush=True)


def _ensure(import_name: str, pip_name: Optional[str] = None) -> bool:
    """يتأكد إن المكتبة موجودة، ولو لأ يثبّتها."""
    try:
        __import__(import_name)
        return True
    except ImportError:
        pass
    if os.environ.get("SKIP_PIP") == "1":
        return False
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", pip_name or import_name],
        check=False, capture_output=True, text=True,
    )
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False


_ensure("requests")
_ensure("bs4", "beautifulsoup4")
HAS_CFFI = _ensure("curl_cffi")
HAS_CLOUDSCRAPER = _ensure("cloudscraper")

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

cffi_requests = None
if HAS_CFFI:
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore
    except Exception:
        HAS_CFFI = False

_cloudscraper_mod = None
if HAS_CLOUDSCRAPER:
    try:
        import cloudscraper as _cloudscraper_mod  # type: ignore
    except Exception:
        HAS_CLOUDSCRAPER = False


# =============================================================== الإعدادات
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


BOT_TOKEN = os.environ.get("BOT_TOKEN") or None
CHAT_ID = os.environ.get("CHAT_ID") or None

DATA_DIR = os.environ.get("DATA_DIR") or ("data" if os.path.isdir("data") else ".")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
STATE_FILE = os.environ.get("STATE_FILE") or os.path.join(DATA_DIR, "sent_deals.json")
HEALTH_FILE = os.path.join(DATA_DIR, "health.json")
COMMIT_STATE = os.environ.get("COMMIT_STATE") == "1"

MIN_DISCOUNT = _env_int("MIN_DISCOUNT", 20)               # أقل خصم معلن للعروض غير المؤكدة
MAX_REALISTIC_DISC = _env_int("MAX_REALISTIC_DISC", 70)   # منع نسب الخصم الوهمية
MIN_REAL_DROP = _env_int("MIN_REAL_DROP", 12)             # أقل نزول فعلي عن السعر المعتاد عشان العرض يتحسب حقيقي
DROP_ALERT = _env_int("DROP_ALERT", 15)                   # للمنتجات اللي مفيهاش سعر قديم: أقل نزول يستاهل تنبيه
MIN_PRODUCT_PRICE = _env_int("MIN_PRODUCT_PRICE", 200)
MAX_DEALS_PER_STORE = _env_int("MAX_DEALS_PER_STORE", 6)
MAX_DEALS_PER_RUN = _env_int("MAX_DEALS_PER_RUN", 30)
MAX_UNVERIFIED_PER_STORE = _env_int("MAX_UNVERIFIED_PER_STORE", 3)
MAX_RUNTIME_MIN = _env_int("MAX_RUNTIME_MIN", 25)
REALERT_HOURS = _env_int("REALERT_HOURS", 72)

HISTORY_WINDOW_DAYS = _env_int("HISTORY_WINDOW_DAYS", 60)  # نافذة حساب "السعر المعتاد"
HISTORY_KEEP_DAYS = _env_int("HISTORY_KEEP_DAYS", 90)      # مدة الاحتفاظ بنقاط الأسعار
MIN_HISTORY_DAYS = _env_int("MIN_HISTORY_DAYS", 4)         # أيام متابعة تكفي للحكم "وهمي"
STATIC_MIN_DAYS = _env_int("STATIC_MIN_DAYS", 7)           # أيام ثبات السعر عشان نقول الخصم شكلي
CROSS_MAX_AGE_H = 48

SEND_UNVERIFIED = _env_flag("SEND_UNVERIFIED", True)
SEND_FLAGGED = _env_flag("SEND_FLAGGED", False)
TRACK_ALL = _env_flag("TRACK_ALL", True)
CROSS_STORE = _env_flag("CROSS_STORE", True)
USE_BROWSER = _env_flag("USE_BROWSER", True)
SUMMARY_MODE = (os.environ.get("SUMMARY_MODE") or "auto").strip().lower()
ONLY_STORES = [s.strip().lower() for s in (os.environ.get("ONLY_STORES") or "").split(",") if s.strip()]

PROXY_URL = os.environ.get("PROXY_URL") or None
JINA_API_KEY = os.environ.get("JINA_API_KEY") or None
GATEWAY_MIN_INTERVAL_MIN = _env_int("GATEWAY_MIN_INTERVAL_MIN", 360)
GATEWAY_MAX_CALLS = _env_int("GATEWAY_MAX_CALLS", 6)
GATEWAY_COUNTRY = (os.environ.get("GATEWAY_COUNTRY") if os.environ.get("GATEWAY_COUNTRY") is not None else "eg").strip().lower()
DEAD_RETRY_H = _env_int("DEAD_RETRY_H", 6)                 # كل كام ساعة نعيد تجربة الطريقة الميتة

AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
IMPERSONATE = ("chrome120", "safari17_0", "chrome")

INSTALLMENT_WORDS = ("قسط", "شهري", "شهريا", "شهرياً", "ميني كاش", "minicash", "/شهر",
                     "month", "تقسيط", "أقساط", "installment", "/mo")
COUPON_WORDS = ("coupon", "كوبون", "استخدم", "كود", "promo")


# =============================================================== الأسعار
_AR = ("".join(chr(0x0660 + i) for i in range(10))
       + "".join(chr(0x06F0 + i) for i in range(10))
       + "\u066b\u066c\u060c")
_TRANS = str.maketrans(_AR, "0123456789" * 2 + ".,,")

_CUR = r"(?:ج\s?\.?\s?م\.?|جنيه|جنية|EGP|E\.G\.P|L\.?E\.?)"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
_PRICE_RE = re.compile(
    r"(?<![\w.,])(" + _NUM + r")\s*" + _CUR + r"(?!\w)"
    r"|(?<!\w)" + _CUR + r"\s*(" + _NUM + r")(?![\d])",
    re.I,
)


def _to_int(s: str) -> int:
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return 0


def parse_price(value: Any) -> int:
    """يحوّل نص/رقم لسعر صحيح بالجنيه. يرجّع 0 لو مش سعر (أو قسط)."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(round(value)) if value >= 50 else 0
    text = str(value)
    low = text.lower()
    if any(w in low for w in INSTALLMENT_WORDS):
        return 0
    text = text.translate(_TRANS).replace("\u00a0", " ")
    m = _PRICE_RE.search(text)
    if m:
        v = _to_int(m.group(1) or m.group(2))
        if v >= 50:
            return v
    t2 = re.sub(r"(?<=\d)\.\d{1,2}(?!\d)", "", text)
    for n in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", t2):
        v = int(n.replace(",", ""))
        if v >= 50:
            return v
    return 0


def _num(v: Any) -> Optional[float]:
    """أي قيمة (رقم/نص/dict) -> float أو None."""
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


def _pct(v: Any) -> Optional[float]:
    try:
        x = float(re.sub(r"[^\d.]", "", str(v).translate(_TRANS)) or 0)
    except ValueError:
        return None
    return x if 1 <= x <= 90 else None


def price_mentions(text: str) -> int:
    return len(_PRICE_RE.findall((text or "").translate(_TRANS)))


def money(n: Any) -> str:
    try:
        return format(int(n), ",")
    except (TypeError, ValueError):
        return str(n)


def esc(s: Any) -> str:
    return htmllib.escape(str(s), quote=False)


def short(s: str, n: int = 90) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# =============================================================== العناصر والمفاتيح
_STOP = {"عرض", "خصم", "جديد", "أصلي", "ضمان", "مصر", "سنة", "new", "original", "warranty", "egypt", "with", "and"}


def norm_title(title: str) -> str:
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    words = [w for w in clean.split() if len(w) > 1 and w not in _STOP]
    return " ".join(words[:10])


def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"^[-\u2212\u2013]?\s*\d{1,2}\s?%\s*", "", t)
    return t[:220]


_TRACKING = re.compile(r"^(utm_|ref$|ref_|qid$|sr$|sprefix|crid|pd_rd|pf_rd|psc$|_encoding|th$|keywords|dib|content-id|linkcode|tag$|smid)", re.I)


def clean_link(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))
    except Exception:
        return url


def item_key(link: str, title: str) -> str:
    """مفتاح ثابت للمنتج: مسار اللينك لو محدد، وإلا الاسم."""
    if link:
        try:
            path = urllib.parse.urlsplit(link).path.rstrip("/")
        except ValueError:
            path = ""
        if len(path) > 1:
            return path
    return "t:" + norm_title(title)


def sane_old(cur: int, old: int) -> int:
    """السعر القديم لازم يكون أكبر من الحالي ومعقول (مش أكتر من 10 أضعاف)."""
    return old if old and cur < old <= cur * 10 else 0


@dataclass
class Item:
    title: str
    price: int
    old: int = 0
    link: str = ""
    source: str = ""
    sid: str = ""
    store: str = ""
    verdict: Any = None
    cross: list = field(default_factory=list)
    score: float = 0.0

    @property
    def discount(self) -> int:
        return round((self.old - self.price) / self.old * 100) if self.old and self.old > self.price else 0

    @property
    def pid(self) -> str:
        return "%s|%s" % (self.sid, item_key(self.link, self.title))


def valid_discount(cur: int, old: int) -> bool:
    """الخصم المعلن مقبول (بين MIN_DISCOUNT و MAX_REALISTIC_DISC)؟"""
    if cur < MIN_PRODUCT_PRICE or old <= cur:
        return False
    d = round((old - cur) / old * 100)
    return MIN_DISCOUNT <= d <= MAX_REALISTIC_DISC


# =============================================================== كشف الحظر
_BLOCK_TITLES = ("just a moment", "attention required", "access denied", "403 forbidden", "robot check",
                 "are you a robot", "security check", "pardon our interruption", "verify you are human",
                 "request blocked", "you have been blocked", "one more step", "bot verification",
                 "error 1020", "captcha", "service unavailable", "temporarily unavailable",
                 "checking your browser", "sorry, you have been blocked")
_BLOCK_BODY = ("cf_chl_opt", "cf-browser-verification", "challenge-form", "px-captcha", "captcha-delivery.com",
               "validatecaptcha", "enter the characters you see below", "api-services-support@amazon.com",
               "_incapsula_resource", "incapsula incident", "sucuri website firewall", "errors.edgesuite.net")


def block_reason(text: str) -> str:
    if not text:
        return "رد فاضي"
    head = text[:6000].lower()
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    for b in _BLOCK_TITLES:
        if b in title:
            return "صفحة حماية: " + title[:40]
    if len(text) < 40000:
        low = text.lower()
        for b in _BLOCK_BODY:
            if b in low:
                return "صفحة حماية (" + b + ")"
    return ""


def judge(text: str, status: int, expect_json: bool) -> str:
    """'' = تمام، غير كده = سبب الفشل."""
    if status in (404, 410):
        return "HTTP %d" % status
    if expect_json:
        if status == 200 and (text or "").lstrip()[:1] in ("{", "["):
            return ""
        if status != 200:
            return "HTTP %d" % status
        return block_reason(text) or "مش JSON"
    blk = block_reason(text)
    if status != 200:
        return "HTTP %d%s" % (status, (" - " + blk) if blk else "")
    if blk:
        return blk
    if len(text) < 1500:
        return "صفحة قصيرة (%d)" % len(text)
    return ""


# =============================================================== المتصفح (Playwright)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['ar-EG', 'ar', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""
_WAIT_TITLES = ("just a moment", "attention required", "access denied", "verify", "security checkpoint",
                "checking your browser", "ddos")


def _block_heavy(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()


class Browser:
    """Chromium بيتحمّل مرة واحدة وبس لو احتجناه. كونتكست مباشر وكونتكست عبر البروكسي."""

    def __init__(self) -> None:
        self.state = "new"
        self._pw = None
        self._browser = None
        self._ctxs: Dict[str, Any] = {}
        self._ua: Optional[str] = None

    @staticmethod
    def _proxy_conf() -> Optional[dict]:
        if not PROXY_URL:
            return None
        p = urllib.parse.urlsplit(PROXY_URL)
        conf = {"server": "%s://%s%s" % (p.scheme, p.hostname, (":%d" % p.port) if p.port else "")}
        if p.username:
            conf["username"] = urllib.parse.unquote(p.username)
        if p.password:
            conf["password"] = urllib.parse.unquote(p.password)
        return conf

    def _launch(self, channel: Optional[str] = None) -> None:
        from playwright.sync_api import sync_playwright  # type: ignore
        self._pw = sync_playwright().start()
        kw: Dict[str, Any] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        }
        if channel:
            kw["channel"] = channel
        self._browser = self._pw.chromium.launch(**kw)

    def _reset(self) -> None:
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = None
        self._ctxs = {}

    def _real_ua(self) -> str:
        """UA الحقيقي للمتصفح من غير كلمة Headless (عشان الإصدار يطابق الـ client hints)."""
        if self._ua:
            return self._ua
        try:
            ctx = self._browser.new_context()
            pg = ctx.new_page()
            ua = str(pg.evaluate("navigator.userAgent"))
            ctx.close()
            self._ua = ua.replace("HeadlessChrome", "Chrome")
        except Exception:
            self._ua = UA_CHROME
        return self._ua

    def _ctx(self, via_proxy: bool):
        name = "proxy" if via_proxy else "direct"
        if name not in self._ctxs:
            kw: Dict[str, Any] = dict(
                locale="ar-EG", timezone_id="Africa/Cairo", user_agent=self._real_ua(),
                viewport={"width": 1366, "height": 900},
                extra_http_headers={"Accept-Language": AR_LANG},
            )
            if via_proxy:
                pc = self._proxy_conf()
                if pc:
                    kw["proxy"] = pc
            ctx = self._browser.new_context(**kw)
            ctx.add_init_script(_STEALTH_JS)
            ctx.route("**/*", _block_heavy)
            self._ctxs[name] = ctx
        return self._ctxs[name]

    def start(self) -> bool:
        if self.state != "new":
            return self.state == "ready"
        try:
            if not _ensure("playwright"):
                raise RuntimeError("تعذر تثبيت playwright")
            last: Optional[Exception] = None
            launched = False
            for step in ("chrome", "chromium", "install", "install-deps"):
                try:
                    if step == "chrome":
                        self._launch("chrome")            # كروم الجاهز على سيرفر جيتهاب (من غير تحميل)
                    elif step == "chromium":
                        self._launch()
                    elif step == "install":
                        log("🌐 بحمّل متصفح Chromium (مرة واحدة في الجولة)...")
                        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                                       check=False, timeout=420, capture_output=True)
                        self._launch()
                    else:
                        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                                       check=False, timeout=600, capture_output=True)
                        self._launch()
                    launched = True
                    break
                except Exception as e:  # جرّب الخطوة اللي بعدها
                    last = e
                    self._reset()
            if not launched:
                raise last or RuntimeError("فشل تشغيل المتصفح")
            self.state = "ready"
        except Exception as e:
            log("⚠ المتصفح مش متاح: %s" % str(e)[:120])
            self._reset()
            self.state = "failed"
        return self.state == "ready"

    def render(self, url: str, raw: bool = False, via_proxy: bool = False) -> Tuple[str, list, int, str]:
        """يرجّع (html, json_blobs, status, note). raw=True لنقاط JSON."""
        blobs: list = []
        status = 0
        page = None
        try:
            page = self._ctx(via_proxy).new_page()

            def on_resp(resp):
                try:
                    if resp.request.resource_type not in ("xhr", "fetch") or resp.status != 200:
                        return
                    if "json" not in (resp.headers.get("content-type") or "").lower():
                        return
                    body = resp.body()
                    if 80 < len(body) < 4_000_000:
                        blobs.append(json.loads(body))
                except Exception:
                    pass

            page.on("response", on_resp)
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                status = resp.status if resp else 0
            except Exception as e:
                note = "goto: " + type(e).__name__
                if page.url in ("about:blank", ""):
                    return "", blobs, 0, note
            for _ in range(8):   # استنى لحد ما تعدّي صفحة "Just a moment"
                try:
                    t = (page.title() or "").lower()
                except Exception:
                    break
                if any(b in t for b in _WAIT_TITLES):
                    page.wait_for_timeout(2500)
                else:
                    break
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            if raw:
                return page.inner_text("body"), blobs, status or 200, "ok"
            for _ in range(6):   # سكرول عشان الـ lazy-loading
                try:
                    page.evaluate("window.scrollBy(0, Math.max(600, document.body.scrollHeight / 5))")
                except Exception:
                    break
                page.wait_for_timeout(700)
            return page.content(), blobs, status or 200, "ok"
        except Exception as e:
            return "", blobs, status, "%s: %s" % (type(e).__name__, str(e)[:80])
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass

    def close(self) -> None:
        for c in list(self._ctxs.values()):
            try:
                c.close()
            except Exception:
                pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        self._reset()


# =============================================================== الجلب (طبقات متصاعدة + ذاكرة صحة)
def _gw_scraperapi(key: str, url: str, js: bool, country: str) -> str:
    mode = (os.environ.get("SCRAPERAPI_MODE") or "premium").strip().lower()
    q: Dict[str, str] = {"api_key": key}
    if js:
        q["render"] = "true"
    if mode == "ultra":
        q["ultra_premium"] = "true"
    elif mode != "basic":
        q["premium"] = "true"
    if country:
        q["country_code"] = country
    q["url"] = url                      # الـ url آخر باراميتر (توصية ScraperAPI)
    return "https://api.scraperapi.com/?" + urllib.parse.urlencode(q)


def _gw_scrapingbee(key: str, url: str, js: bool, country: str) -> str:
    q = {"api_key": key, "url": url, "render_js": "true" if js else "false", "premium_proxy": "true"}
    if country:
        q["country_code"] = country     # بيشتغل مع premium_proxy بس
    return "https://app.scrapingbee.com/api/v1/?" + urllib.parse.urlencode(q)


def _gw_zenrows(key: str, url: str, js: bool, country: str) -> str:
    q = {"apikey": key, "url": url, "js_render": "true" if js else "false", "premium_proxy": "true"}
    if country:
        q["proxy_country"] = country    # بيشتغل مع premium_proxy بس
    return "https://api.zenrows.com/v1/?" + urllib.parse.urlencode(q)


def _gw_scrapedo(key: str, url: str, js: bool, country: str) -> str:
    q = {"token": key, "url": url, "super": "true"}
    if js:
        q["render"] = "true"
    if country:
        q["geoCode"] = country          # ممكن يحتاج خطة أعلى — لو اترفض بنجرب من غيره
    return "https://api.scrape.do/?" + urllib.parse.urlencode(q)


GATEWAYS: List[dict] = [
    {"name": "scraperapi", "env": "SCRAPERAPI_KEY", "build": _gw_scraperapi},
    {"name": "scrapingbee", "env": "SCRAPINGBEE_KEY", "build": _gw_scrapingbee},
    {"name": "zenrows", "env": "ZENROWS_KEY", "build": _gw_zenrows},
    {"name": "scrapedo", "env": "SCRAPEDO_TOKEN", "build": _gw_scrapedo},
]


@dataclass
class Fetched:
    ok: bool = False
    text: str = ""
    status: int = 0
    method: str = ""
    hard: bool = False                      # 404/410: مفيش فايدة من التصعيد
    blobs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass
class Layer:
    name: str
    family: str                             # static | render | gateway
    run: Callable[[str, bool], Tuple[str, int, list]]
    proxy: bool = False
    no_challenge: bool = False              # متتجرّبش لو الموقع رد بصفحة تحدّي


@dataclass
class Accept:
    good: bool = False                      # النتيجة كفاية؟
    shell: bool = False                     # الصفحة فتحت بس هيكل JS فاضي
    score: tuple = (0,)
    payload: Any = None


@dataclass
class FetchOutcome:
    best: Optional[tuple] = None            # (score, Fetched, payload)
    notes: list = field(default_factory=list)
    attempted: int = 0
    hard: bool = False
    deferred: bool = False


class Fetcher:
    def __init__(self, health: dict) -> None:
        self.mem: Dict[str, dict] = health.setdefault("hosts", {})
        self.gw_log: Dict[str, int] = health.setdefault("gw", {})
        self.gw_calls = 0
        self.proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        self._sessions: Dict[Tuple[str, str, bool], Any] = {}
        self._warmed: set = set()
        self._scraper = None
        self.browser = Browser() if USE_BROWSER else None
        self.gateways = [g for g in GATEWAYS if os.environ.get(g["env"])]
        self.now = int(time.time())

    # ---- ذاكرة الصحة لكل موقع
    def _hm(self, host: str) -> dict:
        return self.mem.setdefault(host, {"dead": {}})

    def _is_dead(self, host: str, name: str) -> bool:
        d = self._hm(host).get("dead", {}).get(name)
        return bool(d) and d.get("n", 0) >= 2 and time.time() - d.get("ts", 0) < DEAD_RETRY_H * 3600

    def _mark_bad(self, host: str, name: str) -> None:
        d = self._hm(host).setdefault("dead", {}).setdefault(name, {"n": 0, "ts": 0})
        d["n"] = d.get("n", 0) + 1
        d["ts"] = int(time.time())

    def _mark_ok(self, host: str, name: str) -> None:
        self._hm(host).setdefault("dead", {}).pop(name, None)

    @staticmethod
    def _is_challenge(note: str) -> bool:
        low = note.lower()
        return any(k in low for k in ("moment", "captcha", "challenge", "حماية", "403", "429"))

    @staticmethod
    def _headers(expect_json: bool) -> dict:
        accept = ("application/json,text/plain,*/*" if expect_json else
                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
        return {"Accept-Language": AR_LANG, "Accept": accept}

    def _session(self, fp: str, host: str, via_proxy: bool):
        key = (fp, host, via_proxy)
        if key not in self._sessions:
            self._sessions[key] = cffi_requests.Session(impersonate=fp)
        return self._sessions[key]

    # ---- الطبقات
    def _cffi(self, fp: str, via_proxy: bool = False):
        def run(url: str, expect_json: bool):
            parts = urllib.parse.urlsplit(url)
            host = parts.netloc
            home = "%s://%s/" % (parts.scheme, host)
            base_headers = self._headers(expect_json)
            sess = self._session(fp, host, via_proxy)
            proxies = self.proxies if via_proxy else None
            wk = (fp, host, via_proxy)
            if wk not in self._warmed:               # زيارة الرئيسية الأول عشان الكوكيز
                self._warmed.add(wk)
                try:
                    sess.get(home, headers=base_headers, timeout=15, proxies=proxies)
                except Exception:
                    pass
                time.sleep(random.uniform(0.4, 1.0))
            h = dict(base_headers)
            h["Referer"] = home
            last_exc: Optional[Exception] = None
            for attempt in (1, 2):
                try:
                    r = sess.get(url, headers=h, timeout=25, proxies=proxies, allow_redirects=True)
                except Exception as e:
                    last_exc = e
                    time.sleep(2)
                    continue
                if r.status_code in (502, 503, 504) and attempt == 1:
                    time.sleep(2)
                    continue
                return (r.text or ""), r.status_code, []
            raise last_exc or RuntimeError("مفيش رد")
        return run

    def _plain(self, via_proxy: bool = False):
        def run(url: str, expect_json: bool):
            h = self._headers(expect_json)
            h["User-Agent"] = UA_CHROME
            r = requests.get(url, headers=h, timeout=25, proxies=self.proxies if via_proxy else None)
            return r.text or "", r.status_code, []
        return run

    def _cloud(self):
        def run(url: str, expect_json: bool):
            if self._scraper is None:
                self._scraper = _cloudscraper_mod.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False})
            r = self._scraper.get(url, headers={"Accept-Language": AR_LANG}, timeout=25)
            return r.text or "", r.status_code, []
        return run

    def _browser_run(self, via_proxy: bool):
        def run(url: str, expect_json: bool):
            if self.browser is None or not self.browser.start():
                raise RuntimeError("المتصفح مش متاح")
            html, blobs, st, note = self.browser.render(url, raw=expect_json, via_proxy=via_proxy)
            if not html:
                raise RuntimeError(note or "فاضي")
            return html, st, blobs
        return run

    def _jina(self):
        def run(url: str, expect_json: bool):
            h = {"X-Return-Format": "html", "Accept": "text/html", "X-Timeout": "40"}
            if JINA_API_KEY:
                h["Authorization"] = "Bearer " + JINA_API_KEY
            r = requests.get("https://r.jina.ai/" + url, headers=h, timeout=60)
            return r.text or "", r.status_code, []
        return run

    def _gateway(self, g: dict):
        def run(url: str, expect_json: bool):
            key = os.environ.get(g["env"], "")
            js = not expect_json
            countries = [GATEWAY_COUNTRY, ""] if GATEWAY_COUNTRY else [""]
            last: Tuple[str, int] = ("", 0)
            for country in countries:
                r = requests.get(g["build"](key, url, js, country), timeout=110)
                last = (r.text or "", r.status_code)
                if country and 400 <= r.status_code < 500 and r.status_code not in (404, 410, 429):
                    continue             # الدولة مش مدعومة في الخطة؟ جرّب من غيرها
                break
            return last[0], last[1], []
        return run

    def _gateway_gate(self, url: str) -> str:
        """'' = مسموح. غير كده = سبب التأجيل (حماية الرصيد)."""
        if self.gw_calls >= GATEWAY_MAX_CALLS:
            return "تخطّيت (وصلت حد %d طلب في الجولة)" % GATEWAY_MAX_CALLS
        last = self.gw_log.get(hashlib.md5(url.encode()).hexdigest()[:10], 0)
        if self.now - last < GATEWAY_MIN_INTERVAL_MIN * 60:
            return "مؤجل (آخر طلب من %d دقيقة)" % ((self.now - last) // 60)
        return ""

    def _layers(self, host: str, expect_json: bool) -> List[Layer]:
        L: List[Layer] = []
        if HAS_CFFI:
            for fp in IMPERSONATE:
                L.append(Layer("curl_cffi/" + fp, "static", self._cffi(fp)))
        else:
            L.append(Layer("requests", "static", self._plain()))
        if HAS_CLOUDSCRAPER and not expect_json:
            L.append(Layer("cloudscraper", "static", self._cloud(), no_challenge=True))
        if PROXY_URL:
            if HAS_CFFI:
                L.append(Layer("curl_cffi/proxy", "static", self._cffi(IMPERSONATE[0], True), proxy=True))
            else:
                L.append(Layer("requests/proxy", "static", self._plain(True), proxy=True))
        if self.browser is not None:
            L.append(Layer("playwright", "render", self._browser_run(False)))
            if PROXY_URL:
                L.append(Layer("playwright/proxy", "render", self._browser_run(True), proxy=True))
        if not expect_json:
            L.append(Layer("jina", "render", self._jina()))
        for g in self.gateways:
            L.append(Layer("gw:" + g["name"], "gateway", self._gateway(g)))
        good = self.mem.get(host, {}).get("good")
        if good:                                         # الطريقة اللي نجحت قبل كده الأول
            L.sort(key=lambda l: 0 if l.name == good else 1)
        return [l for l in L if not self._is_dead(host, l.name)]

    def _try(self, layer: Layer, url: str, expect_json: bool) -> Fetched:
        out = Fetched(method=layer.name)
        if layer.family == "gateway":
            self.gw_calls += 1
            self.gw_log[hashlib.md5(url.encode()).hexdigest()[:10]] = self.now
        try:
            text, st, blobs = layer.run(url, expect_json)
        except Exception as e:
            msg = str(e)
            out.notes.append("%s: %s%s" % (layer.name, type(e).__name__, (" " + msg[:60]) if msg else ""))
            return out
        reason = judge(text, st, expect_json) if text else "رد فاضي"
        if reason and layer.family == "render" and blobs and st in (0, 200) and not block_reason(text):
            reason = ""                      # المتصفح لقط JSON حقيقي -> كفاية
        if reason:
            snippet = (" | " + re.sub(r"\s+", " ", text[:80])) if (layer.family == "gateway" and text) else ""
            out.notes.append("%s: %s%s" % (layer.name, reason, snippet))
            if st in (404, 410):
                out.hard = True
            return out
        out.ok, out.text, out.status, out.blobs = True, text, st, blobs
        return out

    def fetch(self, url: str, accept: Callable[[Fetched], Accept], expect_json: bool = False) -> FetchOutcome:
        """يجرّب الطبقات بالترتيب لحد ما accept() تقول 'كفاية'. بيرجّع أحسن نتيجة."""
        host = urllib.parse.urlsplit(url).netloc
        out = FetchOutcome()
        skip_static = False
        direct_blocked = False
        for layer in self._layers(host, expect_json):
            if layer.family == "static" and skip_static:
                continue
            if direct_blocked and layer.family == "static" and not layer.proxy:
                continue                     # نفس البصمة هتتحجب برضه
            if layer.no_challenge and direct_blocked:
                continue
            if layer.family == "gateway":
                why = self._gateway_gate(url)
                if why:
                    out.notes.append("%s: %s" % (layer.name, why))
                    out.deferred = True
                    continue
            out.attempted += 1
            res = self._try(layer, url, expect_json)
            if not res.ok:
                out.notes.extend(res.notes)
                for n in res.notes[-1:]:
                    log("   ⛔ " + n)
                if res.hard:
                    out.hard = True
                    break
                self._mark_bad(host, layer.name)
                if layer.family == "static" and not layer.proxy and res.notes and self._is_challenge(res.notes[-1]):
                    direct_blocked = True
                continue
            self._mark_ok(host, layer.name)
            acc = accept(res)
            if out.best is None or acc.score > out.best[0]:
                out.best = (acc.score, res, acc.payload)
            if acc.good:
                self._hm(host)["good"] = layer.name
                break
            if acc.shell and layer.family == "static":
                skip_static = True
        return out

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()
        cutoff = self.now - 7 * DAY               # تنضيف سجل البوابات القديم
        for k in [k for k, ts in self.gw_log.items() if ts < cutoff]:
            self.gw_log.pop(k, None)


# =============================================================== استخراج المنتجات: JSON
_NAME_KEYS = ("name", "title", "product_name", "productName", "display_name", "displayName",
              "name_ar", "title_ar", "nameAr", "titleAr", "name_en")
_CUR_KEYS = ("sale_price", "salePrice", "special_price", "specialPrice", "final_price", "finalPrice",
             "offer_price", "offerPrice", "discounted_price", "discountedPrice", "discount_price",
             "discountPrice", "price_after_discount", "priceAfterDiscount", "current_price", "currentPrice",
             "selling_price", "sellingPrice", "now_price", "nowPrice")
_OLD_KEYS = ("was_price", "wasPrice", "old_price", "oldPrice", "regular_price", "regularPrice",
             "list_price", "listPrice", "original_price", "originalPrice", "compare_at_price",
             "compareAtPrice", "strike_price", "strikePrice", "crossed_price", "crossedPrice",
             "price_before_discount", "priceBeforeDiscount", "price_before", "priceBefore",
             "before_price", "beforePrice", "full_price", "fullPrice", "retail_price", "retailPrice",
             "msrp", "mrp", "previous_price", "previousPrice", "normal_price", "normalPrice",
             "price_without_discount", "priceWithoutDiscount")
_PCT_KEYS = ("discount_percentage", "discountPercentage", "discount_percent", "discountPercent",
             "percent_off", "percentOff", "discount_rate", "discountRate", "percentage_discount")
_PRICE_KEYS = set(_CUR_KEYS) | set(_OLD_KEYS) | {"price", "price_range", "prices", "offers"}
_LINK_KEYS = ("permalink", "url", "link", "product_url", "productUrl", "canonical_url", "uri", "href",
              "url_path", "url_key", "slug", "handle", "seo_url", "deeplink")
_JUNK_NAMES = {"default title", "default", "variant"}
_HAS_LETTERS = re.compile(r"[A-Za-z\u0600-\u06FF]{2,}")


def _prices_from_dict(d: dict) -> Tuple[Optional[float], Optional[float]]:
    # Magento GraphQL
    pr = d.get("price_range")
    if isinstance(pr, dict) and isinstance(pr.get("minimum_price"), dict):
        mp = pr["minimum_price"]
        fin, reg = _num(mp.get("final_price")), _num(mp.get("regular_price"))
        if fin:
            return fin, (reg if reg and reg > fin else None)
    # WooCommerce Store API (الأسعار بالوحدة الصغرى: 299900 = 2999.00)
    pz = d.get("prices")
    if isinstance(pz, dict) and ("regular_price" in pz or "price" in pz):
        try:
            unit = int(pz.get("currency_minor_unit", 2))
        except (TypeError, ValueError):
            unit = 2
        div = float(10 ** unit)
        cur = _num(pz.get("sale_price") or pz.get("price"))
        old = _num(pz.get("regular_price"))
        if cur:
            cur /= div
            old = (old / div) if old else None
            return cur, (old if old and old > cur else None)
    # price ككائن (Algolia / تفاصيل متداخلة)
    p = d.get("price")
    if isinstance(p, dict):
        for v in p.values():
            if isinstance(v, dict) and "default" in v:
                cur = _num(v.get("default"))
                old = _num(v.get("default_original_formated") or v.get("default_original"))
                if cur:
                    return cur, (old if old and old > cur else None)
        cur = _num(p.get("current") or p.get("value") or p.get("amount") or p.get("final"))
        old = _num(p.get("old") or p.get("original") or p.get("was") or p.get("regular") or p.get("before"))
        if cur:
            return cur, (old if old and old > cur else None)
        p = None
    cur = old = None
    for k in _CUR_KEYS:
        v = _num(d.get(k))
        if v:
            cur = v
            break
    for k in _OLD_KEYS:
        v = _num(d.get(k))
        if v:
            old = v
            break
    pv = _num(p) if p is not None else None
    if pv:
        if cur is None:
            cur = pv                       # price هو الحالي
        elif old is None and pv > cur:
            old = pv                       # أسلوب نون: price = القديم، sale_price = الحالي
    if cur is None:                        # JSON-LD
        of = d.get("offers")
        if isinstance(of, list) and of:
            of = of[0]
        if isinstance(of, dict):
            cur = _num(of.get("price") or of.get("lowPrice"))
    if cur and not old:
        for k in _PCT_KEYS:
            pc = _pct(d.get(k))
            if pc:
                old = cur / (1 - pc / 100.0)
                break
    if cur and old and old <= cur:
        old = None
    return cur, old


def _product_from_dict(d: dict, base: str, link_fn) -> Optional[Tuple[str, int, int, str]]:
    if not (d.keys() & _PRICE_KEYS):
        return None
    name = None
    for k in _NAME_KEYS:
        v = d.get(k)
        if (isinstance(v, str) and len(v.strip()) >= 6 and not v.strip().isdigit()
                and v.strip().lower() not in _JUNK_NAMES and _HAS_LETTERS.search(v)):
            name = v.strip()
            break
    if not name:
        return None
    cur, old = _prices_from_dict(d)
    if not cur or cur < MIN_PRODUCT_PRICE:
        return None
    link = link_fn(d) if link_fn else None
    if not link:
        for k in _LINK_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                v = v.strip()
                if k == "url_key":
                    v = "/" + v.strip("/") + str(d.get("url_suffix") or "")
                elif k == "handle":
                    v = "/products/" + v
                link = urllib.parse.urljoin(base, v)
                break
    return clean_title(name), int(round(cur)), int(round(old)) if old else 0, link or ""


def json_products(root: Any, base: str, link_fn=None) -> Tuple[List["Item"], int]:
    """يمشي على أي JSON ويلقط كل المنتجات (بخصم أو من غير). يرجّع (المنتجات, عددها)."""
    items: List[Item] = []
    seen_n = 0
    keys = set()
    stack = [root]
    visited = 0
    while stack and visited < 400000:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            res = _product_from_dict(node, base, link_fn)
            if res:
                title, cur, old, link = res
                seen_n += 1
                k = (norm_title(title), cur)
                if k not in keys:
                    keys.add(k)
                    items.append(Item(title, cur, sane_old(cur, old), link, "json"))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return items, seen_n


def shopify_items(data: Any, base: str) -> Tuple[List["Item"], int]:
    items: List[Item] = []
    seen_n = 0
    prods = data.get("products") if isinstance(data, dict) else None
    for p in prods or []:
        if not isinstance(p, dict):
            continue
        best: Optional[Tuple[int, int]] = None
        for v in p.get("variants") or []:
            cur, old = _num(v.get("price")), _num(v.get("compare_at_price"))
            if not cur:
                continue
            cand = (int(cur), sane_old(int(cur), int(old)) if old else 0)
            if best is None or (cand[1] and not best[1]):
                best = cand
        if best:
            seen_n += 1
            items.append(Item(clean_title(str(p.get("title", ""))), best[0], best[1],
                              "%s/products/%s" % (base.rstrip("/"), p.get("handle", "")), "shopify"))
    return items, seen_n


def _noon_link(d: dict) -> Optional[str]:
    sku = d.get("sku") or d.get("catalog_sku")
    if not isinstance(sku, str) or not sku:
        return None
    slug = d.get("url") if isinstance(d.get("url"), str) and d.get("url") else "-"
    if slug.startswith("http"):
        return slug
    return "https://www.noon.com/egypt-ar/%s/%s/p/" % (slug.strip("/"), sku)


LINK_BUILDERS: Dict[str, Callable[[dict], Optional[str]]] = {"noon": _noon_link}


def script_json_blobs(soup) -> list:
    out = []
    for s in soup.find_all("script"):
        txt = (s.string or s.get_text() or "").strip()
        if len(txt) < 100:
            continue
        typ = (s.get("type") or "").lower()
        if "json" in typ or s.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__"):
            try:
                out.append(json.loads(txt))
            except Exception:
                pass
            continue
        m = re.search(r"(?:window\.)?__[A-Za-z0-9_]+__\s*=\s*(\{.*\}|\[.*\])\s*;?\s*$", txt, re.S)
        if m:
            try:
                out.append(json.loads(m.group(1)))
            except Exception:
                pass
    return out


# =============================================================== استخراج المنتجات: HTML
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_OLD_TOKENS = {"old", "was", "before", "strike", "strikethrough", "struck", "striked", "crossed", "mrp",
               "msrp", "original", "prev", "previous", "compare", "oldprice", "wasprice", "linethrough"}
_CUR_STRONG = {"special", "final", "sale", "current", "now", "offer", "selling", "discounted", "actual", "new", "promo"}
_PRICE_TOK = {"price", "prc", "amount", "cost"}
_BAD_HREF = re.compile(r"^(#|javascript:|tel:|mailto:|whatsapp:)", re.I)
_BAD_PATH = re.compile(r"/(wishlist|compare|cart|checkout|customer|account|login|register|sendfriend|reviews?)(/|\?|$)"
                       r"|add[-_]?to[-_]?(cart|wishlist|compare)", re.I)
_NON_PRODUCT_PATH = re.compile(r"/(tag|tags|category|categories|brand|brands|cat|search|filter)/", re.I)
_NAME_CLS = re.compile(r"name|title", re.I)
_ID_QS = re.compile(r"(?:^|&)(?:id|product_id|pid|sku|p|item|item_id|products_id|productid)=", re.I)


def class_tokens(classes: Any) -> set:
    if not classes:
        return set()
    if isinstance(classes, str):
        classes = classes.split()
    toks = set()
    for c in classes:
        for part in re.split(r"[^A-Za-z0-9]+", _CAMEL.sub(" ", str(c))):
            if part:
                toks.add(part.lower())
    return toks


def _has_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in s.translate(_TRANS))


def _is_old_node(tag) -> bool:
    attrs = tag.attrs or {}
    hit = tag.name in ("del", "s", "strike")
    if not hit:
        hit = str(attrs.get("data-price-type", "")).lower() in ("oldprice", "old_price", "old-price")
    if not hit:
        hit = "data-a-strike" in attrs
    if not hit:
        toks = class_tokens(attrs.get("class"))
        hit = bool(toks & _OLD_TOKENS) or ("line" in toks and "through" in toks)
    if not hit:
        hit = "line-through" in str(attrs.get("style", "")).lower()
    if not hit:
        return False
    txt = tag.get_text(" ", strip=True)
    return 0 < len(txt) <= 60 and _has_digit(txt)


def _node_amount(n) -> int:
    for el in [n] + list(n.find_all(True, limit=8)):
        amt = (el.attrs or {}).get("data-price-amount")
        if amt:
            v = _num(amt)
            if v:
                return int(round(v))
    return parse_price(n.get_text(" ", strip=True))


def _valid_href(h: str) -> bool:
    h = (h or "").strip()
    return bool(h) and not _BAD_HREF.match(h) and not _BAD_PATH.search(h)


def _anchors(container) -> list:
    nodes = container.find_all("a", href=True)
    if container.name == "a" and container.get("href"):
        nodes = [container] + nodes
    return nodes


def _product_hrefs(container, base: str) -> set:
    hs = set()
    for a in _anchors(container):
        h = a["href"].strip()
        if _valid_href(h) and not _NON_PRODUCT_PATH.search(h):
            p = urllib.parse.urlsplit(urllib.parse.urljoin(base, h))
            hs.add(p.netloc + p.path)
    return hs


def _pick_link(card, base: str) -> str:
    cands = []
    for a in _anchors(card):
        h = a["href"].strip()
        if not _valid_href(h):
            continue
        score = len(a.get_text(" ", strip=True))
        if a.find(["h1", "h2", "h3", "h4", "h5"]) or (a.parent is not None and a.parent.name in ("h1", "h2", "h3", "h4", "h5")):
            score += 200
        if re.search(r"/(product|products|p|item|dp)/|\.html?$|/p/?$", h, re.I):
            score += 100
        if _NON_PRODUCT_PATH.search(h):
            score -= 150
        cands.append((score, urllib.parse.urljoin(base, h)))
    return clean_link(max(cands, key=lambda c: c[0])[1]) if cands else ""


def _mostly_numeric(t: str) -> bool:
    return bool(t) and sum(ch.isdigit() or ch in ",. %-" for ch in t.translate(_TRANS)) / len(t) > 0.5


def _pick_title(card) -> str:
    for tag in ("h1", "h2", "h3", "h4", "h5"):
        for h in card.find_all(tag):
            t = clean_title(h.get_text(" ", strip=True))
            if len(t) >= 6 and not _mostly_numeric(t):
                return t
    for el in card.find_all(True, class_=_NAME_CLS):
        t = clean_title(el.get_text(" ", strip=True))
        if 6 <= len(t) <= 200 and not _mostly_numeric(t):
            return t
    for a in card.find_all("a", title=True):
        t = clean_title(a["title"])
        if len(t) >= 6:
            return t
    for img in card.find_all("img", alt=True):
        t = clean_title(img["alt"])
        if len(t) >= 6 and not _mostly_numeric(t):
            return t
    texts = [clean_title(a.get_text(" ", strip=True)) for a in _anchors(card)]
    texts = [_PRICE_RE.sub(" ", t.translate(_TRANS)).strip() for t in texts if t]
    texts = [t for t in texts if len(t) >= 6 and not _mostly_numeric(t)]
    return max(texts, key=len) if texts else ""


def _looks_price(el, txt: str, toks: set) -> bool:
    """للوضع الصارم (من غير سعر قديم): العنصر لازم شكله سعر فعلاً مش عنوان فيه أرقام."""
    t = txt.translate(_TRANS)
    if _PRICE_RE.search(t):
        return True
    if toks & (_PRICE_TOK | _CUR_STRONG):
        return True
    return bool(re.fullmatch(r"[\d.,\s]+", t))


def _current_candidates(card, old_desc: set, old_p: int, strict: bool = False) -> list:
    """مرشحين للسعر الحالي داخل الكارت: [(أولوية, سعر, ترتيب)]."""
    floor = old_p * (1 - MAX_REALISTIC_DISC / 100.0) if old_p else MIN_PRODUCT_PRICE
    cands = []
    for order, el in enumerate(card.find_all(True)):
        if id(el) in old_desc:
            continue
        attrs = el.attrs or {}
        v = 0
        amt = attrs.get("data-price-amount")
        if amt:
            n = _num(amt)
            v = int(round(n)) if n else 0
        elif str(attrs.get("itemprop", "")).lower() == "price" and attrs.get("content"):
            n = _num(attrs.get("content"))
            v = int(round(n)) if n else 0
        txt = el.get_text(" ", strip=True)
        if not v and (not txt or len(txt) > 45 or not _has_digit(txt)):
            continue
        low = txt.lower()
        if any(w in low for w in COUPON_WORDS) or any(w in low for w in INSTALLMENT_WORDS):
            continue
        par = el.parent
        if par is not None and par is not card:
            ptxt = par.get_text(" ", strip=True).lower()
            if len(ptxt) <= 80 and (any(w in ptxt for w in COUPON_WORDS) or any(w in ptxt for w in INSTALLMENT_WORDS)):
                continue
        toks = class_tokens(attrs.get("class"))
        if not v:
            if strict and not _looks_price(el, txt, toks):
                continue
            v = parse_price(txt)
        if v < MIN_PRODUCT_PRICE or v < floor or (old_p and v >= old_p):
            continue
        score = 1
        if (str(attrs.get("data-price-type", "")).lower() == "finalprice" or el.name == "ins"
                or str(attrs.get("itemprop", "")).lower() == "price"):
            score = 4
        elif toks & _CUR_STRONG:
            score = 3
        elif toks & _PRICE_TOK:
            score = 2
        cands.append((score, v, order))
    return cands


def _choose_current(cands: list, strict: bool = False) -> int:
    if not cands:
        return 0
    top = max(c[0] for c in cands)
    vals = [c[1] for c in cands if c[0] == top]
    if strict and max(vals) > min(vals) * 1.01:
        return 0                           # أكتر من سعر محتمل ومفيش سعر قديم يرجّح -> مش واضح، نتخطّاه
    return max(vals)                       # الأقرب للقديم (بيستبعد سعر الكوبون/الباندل)


def _pick_current(card, old_desc: set, old_p: int) -> int:
    return _choose_current(_current_candidates(card, old_desc, old_p))


def html_items(soup, page_url: str) -> List["Item"]:
    """بيدور على "السعر القديم المشطوب" وبيطلع منه لكارت المنتج — مش معتمد على أسماء كلاسات معينة."""
    olds = soup.find_all(_is_old_node)
    if not olds:
        return []
    oset = {id(n) for n in olds}
    top = [n for n in olds if not any(id(p) in oset for p in n.parents)]
    valid = []
    for n in top:
        v = _node_amount(n)
        if v >= MIN_PRODUCT_PRICE:
            valid.append((n, v))
    if not valid:
        return []
    old_desc: set = set()
    for n, _ in valid:
        old_desc.add(id(n))
        for d in n.descendants:
            if getattr(d, "name", None):
                old_desc.add(id(d))
    cnt: Dict[int, int] = defaultdict(int)
    chains: Dict[int, list] = {}
    for n, _ in valid:
        chain = []
        cur = n.parent
        for _i in range(9):
            if cur is None or cur.name in ("html", "body", "[document]"):
                break
            chain.append(cur)
            cnt[id(cur)] += 1
            cur = cur.parent
        chains[id(n)] = chain

    items: List[Item] = []
    for n, old_p in valid:
        chosen = None
        started = False
        extra = 0
        for anc in chains[id(n)]:
            hrefs = _product_hrefs(anc, page_url)
            if cnt[id(anc)] > 1 and len(hrefs) > 1:
                break                                   # وصلنا لحاوية فيها أكتر من منتج
            if not hrefs and not started:
                continue
            started = True
            title = _pick_title(anc)
            if title:
                chosen = (anc, title)
                break
            extra += 1
            if extra > 3:
                break
        if not chosen:
            continue
        card, title = chosen
        if any(w in title.lower() for w in INSTALLMENT_WORDS):
            continue
        cur = _pick_current(card, old_desc, old_p)
        if cur and old_p > cur:
            items.append(Item(title, cur, sane_old(cur, old_p), _pick_link(card, page_url), "html"))
    return items


def _href_key(h: str, base: str) -> str:
    try:
        p = urllib.parse.urlsplit(urllib.parse.urljoin(base, h.strip()))
    except ValueError:
        return ""
    path = p.path.rstrip("/")
    if len(path) < 2:
        return ""
    key = p.netloc.lower().replace("www.", "", 1) + path
    if p.query and _ID_QS.search(p.query):
        key += "?" + p.query
    return key


def html_cards(soup, page_url: str) -> List["Item"]:
    """كروت المنتجات المبنية على لينك المنتج (مش على السعر القديم): بتجيب كمان المنتجات اللي مفيهاش خصم."""
    anchors = []
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if not _valid_href(h) or _NON_PRODUCT_PATH.search(h):
            continue
        key = _href_key(h, page_url)
        if key:
            anchors.append((a, key))
    if not anchors:
        return []
    keyset: Dict[int, set] = defaultdict(set)
    chains: List[list] = []
    for a, key in anchors:
        chain = []
        cur = a
        for _ in range(12):
            if cur is None or getattr(cur, "name", None) in (None, "html", "body", "[document]"):
                break
            keyset[id(cur)].add(key)
            chain.append(cur)
            cur = cur.parent
        chains.append(chain)
    cards: Dict[int, Any] = {}
    for chain in chains:
        card = None
        for el in chain:
            if len(keyset[id(el)]) == 1:
                card = el                       # أعلى حاوية فيها منتج واحد بس
            else:
                break
        if card is not None:
            cards[id(card)] = card

    items: List[Item] = []
    for card in cards.values():
        txt = card.get_text(" ", strip=True)
        if not txt or len(txt) > 700:
            continue
        olds = card.find_all(_is_old_node)
        oset = {id(n) for n in olds}
        top = [n for n in olds if not any(id(p) in oset for p in n.parents)]
        old_p = 0
        old_desc: set = set()
        for n in top:
            v = _node_amount(n)
            if v >= MIN_PRODUCT_PRICE:
                old_p = max(old_p, v)
            old_desc.add(id(n))
            for d in n.descendants:
                if getattr(d, "name", None):
                    old_desc.add(id(d))
        cur = _choose_current(_current_candidates(card, old_desc, old_p, strict=not old_p), strict=not old_p)
        if not cur:
            continue
        title = _pick_title(card)
        if not title or any(w in title.lower() for w in INSTALLMENT_WORDS):
            continue
        items.append(Item(title, cur, sane_old(cur, old_p) if old_p > cur else 0, _pick_link(card, page_url), "card"))
    return items


def price_class_hints(soup, limit: int = 5) -> list:
    """للتشخيص: أكتر كلاسات بتحمل أسعار في الصفحة."""
    c: Counter = Counter()
    for el in soup.find_all(True):
        if el.find(True):
            continue
        txt = el.get_text(" ", strip=True)
        if 0 < len(txt) <= 40 and _PRICE_RE.search(txt.translate(_TRANS)):
            cls = " ".join(el.get("class") or []) or "<%s>" % el.name
            c[cls[:60]] += 1
    return c.most_common(limit)


def extract_amazon(soup) -> Tuple[List["Item"], int]:
    items: List[Item] = []
    seen_n = 0
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
        if not cur or not title:
            continue
        seen_n += 1
        items.append(Item(title, cur, sane_old(cur, old), "https://www.amazon.eg/dp/" + asin, "amazon"))
    return items, seen_n


@dataclass
class Extract:
    items: List[Item] = field(default_factory=list)
    json_seen: int = 0
    mentions: int = 0
    hints: list = field(default_factory=list)

    @property
    def advertised(self) -> int:
        return sum(1 for i in self.items if i.old)


def dedupe_items(items: List[Item]) -> List[Item]:
    ordered = sorted(items, key=lambda x: 0 if x.old else 1)     # اللي معاه سعر قديم الأول
    out: List[Item] = []
    seen_keys = set()
    for it in ordered:
        k1 = ("k", item_key(it.link, it.title))
        k2 = ("t", norm_title(it.title), it.price)
        if k1 in seen_keys or k2 in seen_keys:
            continue
        seen_keys.add(k1)
        seen_keys.add(k2)
        out.append(it)
    return out


def extract_page(html: str, url: str, cfg: dict, blobs=()) -> Extract:
    ex = Extract()
    soup = BeautifulSoup(html or "", "html.parser")
    base = cfg.get("domain") or "%s://%s" % urllib.parse.urlsplit(url)[:2]
    adapter = cfg.get("adapter", "generic")
    found: List[Item] = []
    if adapter == "amazon":
        its, _n = extract_amazon(soup)
        found += its
    link_fn = LINK_BUILDERS.get(adapter)
    for blob in list(script_json_blobs(soup)) + list(blobs):
        its, n = json_products(blob, base, link_fn)
        found += its
        ex.json_seen += n
    if not (adapter == "amazon" and found):
        found += html_items(soup, url)
        found += html_cards(soup, url)
    ex.mentions = price_mentions(soup.get_text(" ", strip=True))
    items = dedupe_items(found)
    if not TRACK_ALL:
        items = [i for i in items if i.old]
    ex.items = items
    if not ex.items and ex.mentions >= 6:
        ex.hints = price_class_hints(soup)
    return ex


# =============================================================== المتاجر
STORES: List[dict] = [
    {"id": "amazon", "name": "أمازون مصر (Amazon)", "adapter": "amazon", "domain": "https://www.amazon.eg",
     "urls": [
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE&page=2",
         "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE",
     ]},
    {"id": "noon", "name": "نون مصر (Noon)", "adapter": "noon", "domain": "https://www.noon.com",
     "urls": [
         "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc",
     ]},
    {"id": "dubaiphone", "name": "دبي فون (Dubai Phone)", "adapter": "woo", "domain": "https://www.dubaiphone.net",
     "api": [{"url": "https://www.dubaiphone.net/wp-json/wc/store/v1/products?on_sale=true&per_page=100", "kind": "woo"}],
     "urls": ["https://www.dubaiphone.net/"]},
    {"id": "jumia", "name": "جوميا مصر (Jumia)", "adapter": "generic", "domain": "https://www.jumia.com.eg",
     "urls": ["https://www.jumia.com.eg/ar/flash-sales/", "https://www.jumia.com.eg/ar/"]},
    {"id": "raya", "name": "راية شوب (Raya Shop)", "adapter": "generic", "domain": "https://www.rayashop.com",
     "urls": ["https://www.rayashop.com/ar/deals", "https://www.rayashop.com/ar/"]},
    {"id": "btech", "name": "بي تك (B.Tech)", "adapter": "generic", "domain": "https://btech.com",
     "urls": ["https://btech.com/ar/"]},
    {"id": "2b", "name": "2B Egypt", "adapter": "generic", "domain": "https://2b.com.eg",
     "urls": ["https://2b.com.eg/ar/deals.html", "https://2b.com.eg/ar/"]},
    {"id": "raneen", "name": "رنين (Raneen)", "adapter": "generic", "domain": "https://www.raneen.com",
     "urls": ["https://www.raneen.com/ar/deals", "https://www.raneen.com/ar/"]},
    {"id": "dream2000", "name": "دريم 2000 (Dream 2000)", "adapter": "generic", "domain": "https://dream2000.com",
     "urls": ["https://dream2000.com/mobiles.html", "https://dream2000.com/"]},
    {"id": "kenzz", "name": "كنز (Kenzz)", "adapter": "generic", "domain": "https://kenzz.com",
     "urls": ["https://kenzz.com/ar"]},
]
STORE_DOMAIN: Dict[str, str] = {c["id"]: c["domain"] for c in STORES}
STORE_NAME: Dict[str, str] = {c["id"]: c["name"] for c in STORES}


def rel_link(link: str, sid: str) -> str:
    dom = STORE_DOMAIN.get(sid, "")
    return link[len(dom):] if (link and dom and link.startswith(dom)) else link


def abs_link(link: str, sid: str) -> str:
    return STORE_DOMAIN.get(sid, "") + link if (link and link.startswith("/")) else link


# =============================================================== ذاكرة الأسعار + حكم العروض
class History:
    """history.json: لكل منتج نقاط [وقت, سعر] — بتتسجّل لما السعر يتغيّر أو مرة كل ~20 ساعة."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.products: Dict[str, dict] = {}
        self.meta: dict = {}
        self.loaded = False
        self.seen_run: set = set()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("products"), dict):
                self.products = d["products"]
                self.meta = d.get("meta") or {}
                self.loaded = True
        except FileNotFoundError:
            pass
        except Exception as e:
            log("⚠ history.json تالف/مش مقروء (%s) — هبدأ ذاكرة جديدة وأحتفظ بنسخة .bad" % type(e).__name__)
            try:
                os.replace(self.path, self.path + ".bad")
            except Exception:
                pass

    def record(self, it: Item, now: int) -> Optional[dict]:
        pid = it.pid
        if pid in self.seen_run:
            return self.products.get(pid)
        self.seen_run.add(pid)
        link = rel_link(it.link, it.sid)
        rec = self.products.get(pid)
        if rec is None:
            rec = {"s": it.sid, "t": it.title[:110], "l": link, "f": now, "ls": now, "c": 1, "p": [[now, it.price]]}
            self.products[pid] = rec
        else:
            pts = rec["p"]
            last_ts, last_p = pts[-1]
            if it.price == last_p:
                rec["c"] = rec.get("c", 1) + 1
                if now - last_ts >= 20 * 3600:
                    pts.append([now, it.price])
            else:
                pts.append([now, it.price])
                rec["c"] = 1
            rec["ls"] = now
            rec["t"] = it.title[:110]
            if link:
                rec["l"] = link
            if len(pts) > 400:
                del pts[: len(pts) - 400]
        if it.old:
            rec["o"] = it.old
        else:
            rec.pop("o", None)
        return rec

    def prune(self, now: int) -> None:
        keep_after = now - HISTORY_KEEP_DAYS * DAY
        for pid in [p for p, r in self.products.items() if r.get("ls", 0) < now - 45 * DAY]:
            self.products.pop(pid, None)
        for rec in self.products.values():
            pts = rec["p"]
            while len(pts) > 1 and pts[0][0] < keep_after:
                pts.pop(0)
        if len(self.products) > 12000:                 # سقف أمان لحجم الملف
            for pid, _ in sorted(self.products.items(), key=lambda kv: kv[1].get("ls", 0))[: len(self.products) - 12000]:
                self.products.pop(pid, None)

    def save(self, now: int) -> None:
        self.prune(now)
        self.meta.setdefault("since", now)
        self.meta["updated"] = now
        self.meta["runs"] = self.meta.get("runs", 0) + 1
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 2, "meta": self.meta, "products": self.products}, f, ensure_ascii=False,
                          separators=(",", ":"))
            os.replace(tmp, self.path)
        except Exception as e:
            log("⚠ مقدرتش أحفظ history: %s" % e)

    def span_days(self, now: int) -> float:
        since = self.meta.get("since")
        return max(0.0, (now - since) / DAY) if since else 0.0


@dataclass
class Verdict:
    code: str = "NEW"        # REAL | LOW | NEW | MINOR | FAKE | STATIC | PUMP | FLAT
    ref: int = 0             # السعر المعتاد قبل السعر الحالي
    low: int = 0             # أقل سعر سابق
    high: int = 0            # أعلى سعر سابق
    real: int = 0            # نسبة النزول الفعلي %
    conf: int = 0            # ثقة الحكم 0..2
    hist_days: float = 0.0
    at_price_days: float = 0.0
    notes: List[str] = field(default_factory=list)


def assess(it: Item, rec: dict, now: int) -> Verdict:
    """يحكم على العرض من تاريخ السعر الفعلي."""
    v = Verdict()
    pts = list(rec.get("p") or [])
    if not pts:
        return v
    cur = it.price
    if pts[-1][1] != cur:
        pts.append([now, cur])
    adv = it.discount
    v.hist_days = max(0.0, (now - rec.get("f", pts[0][0])) / DAY)
    i = len(pts) - 1
    while i > 0 and pts[i - 1][1] == cur:
        i -= 1
    v.at_price_days = max(0.0, (now - pts[i][0]) / DAY)
    before = pts[:i]
    if not before:                                   # السعر ما اتغيّرش طول فترة المتابعة
        if adv >= MIN_DISCOUNT and v.hist_days >= STATIC_MIN_DAYS:
            v.code = "STATIC"
            v.notes.append("السعر ثابت من %d يوم متابعة — الخصم المعلن شكلي" % round(v.hist_days))
        elif adv:
            v.notes.append("لسه بجمع تاريخ السعر (%d يوم) — الخصم حسب إعلان المتجر بس" % round(v.hist_days))
        return v
    since = now - HISTORY_WINDOW_DAYS * DAY
    daily: Dict[int, int] = {}
    for ts, p in before:                             # آخر سعر في كل يوم
        if ts >= since:
            daily[int(ts // DAY)] = p
    prices = list(daily.values()) or [p for _, p in before[-5:]]
    v.ref = int(statistics.median(prices))
    v.low = min(p for _, p in before)
    v.high = max(p for _, p in before)
    v.real = round((v.ref - cur) / v.ref * 100) if v.ref else 0
    n_days = len(daily)
    v.conf = 2 if (v.hist_days >= 10 and n_days >= 5) else 1 if (v.hist_days >= MIN_HISTORY_DAYS and n_days >= 2) else 0
    prev_price = before[-1][1]
    pumped = bool(v.conf >= 1 and v.ref and prev_price >= v.ref * 1.12 and cur >= v.ref * 0.97)

    if v.real >= MIN_REAL_DROP:
        v.code = "LOW" if (cur <= v.low and v.hist_days >= 7) else "REAL"
        if adv and adv - v.real >= 15 and v.conf >= 1:
            v.notes.append("المتجر معلن خصم %d%% لكن النزول الفعلي %d%% بس" % (adv, v.real))
        if it.old and v.high and it.old > v.high * 1.08 and v.hist_days >= 10:
            v.notes.append("السعر القديم المعلن (%s) أعلى من أعلى سعر اتسجّل (%s)" % (money(it.old), money(v.high)))
        if v.at_price_days >= 10:
            v.notes.append("العرض شغال من %d يوم" % round(v.at_price_days))
        if v.conf == 0:
            v.notes.append("بيانات قليلة (%d يوم متابعة)" % round(v.hist_days))
    elif pumped and adv >= MIN_DISCOUNT:
        v.code = "PUMP"
        v.notes.append("السعر اترفع لـ %s وبعدين رجع للمعتاد %s — الخصم المعلن محسوب من السعر المرفوع"
                       % (money(prev_price), money(v.ref)))
    elif v.conf >= 1 and adv >= MIN_DISCOUNT:
        if v.real < 3:
            v.code = "FAKE"
            v.notes.append("الخصم المعلن %d%% لكن السعر مش أقل من المعتاد (%s)" % (adv, money(v.ref)))
        else:
            v.code = "MINOR"
    elif v.conf >= 1 and v.real >= 3:
        v.code = "MINOR"
    elif adv:
        v.code = "NEW"
        v.notes.append("لسه بجمع تاريخ السعر (%d يوم) — الخصم حسب إعلان المتجر بس" % round(v.hist_days))
    else:
        v.code = "FLAT"
    return v


def deal_score(it: Item) -> float:
    v: Verdict = it.verdict
    base = float(v.real) if v.code in ("REAL", "LOW") else it.discount * 0.5
    if v.code == "LOW":
        base += 12
    saving = (it.old - it.price) if it.old else max(v.ref - it.price, 0)
    base += min(max(saving, 0) / 1000.0, 10)
    if it.cross:
        base += -10 if it.cross[0][0] < it.price * 0.97 else 6
    return base


# =============================================================== مطابقة المنتجات بين المتاجر
_AR_TR = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": ""})
_DIACRITICS = re.compile(r"[\u064B-\u065F]")


def _ar_norm(s: str) -> str:
    return _DIACRITICS.sub("", s.translate(_AR_TR))


_AR_BRANDS_RAW = {
    "سامسونج": "samsung", "سامسونغ": "samsung", "ابل": "apple", "ايفون": "iphone", "شاومي": "xiaomi",
    "ريدمي": "redmi", "هواوي": "huawei", "اوبو": "oppo", "ريلمي": "realme", "انفينكس": "infinix",
    "تكنو": "tecno", "هونر": "honor", "ون بلس": "oneplus", "نوكيا": "nokia", "موتورولا": "motorola",
    "لينوفو": "lenovo", "ديل": "dell", "اسوس": "asus", "ايسر": "acer", "توشيبا": "toshiba", "شارب": "sharp",
    "باناسونيك": "panasonic", "سوني": "sony", "فريش": "fresh", "يونيون اير": "unionaire",
    "يونيونير": "unionaire", "كريازي": "kiriazi", "زانوسي": "zanussi", "بيكو": "beko", "بوش": "bosch",
    "سيمنس": "siemens", "ميديا": "midea", "كاريير": "carrier", "تورنيدو": "tornado", "هيتاشي": "hitachi",
    "هايسنس": "hisense", "فيليبس": "philips", "هاير": "haier", "اريستون": "ariston", "ال جي": "lg",
    "اتش بي": "hp", "تي سي ال": "tcl", "جي بي ال": "jbl", "جالاكسي": "galaxy", "ايباد": "ipad", "ماك بوك": "macbook",
}
_AR_BRANDS = {_ar_norm(k): v for k, v in _AR_BRANDS_RAW.items()}
_AR_BRAND_RE = re.compile(
    r"(?<![\u0600-\u06FF])(" + "|".join(re.escape(k) for k in sorted(_AR_BRANDS, key=len, reverse=True))
    + r")(?![\u0600-\u06FF])")
_BRAND_SET = {"samsung", "apple", "xiaomi", "huawei", "oppo", "realme", "infinix", "tecno", "honor", "oneplus",
              "nokia", "motorola", "lenovo", "dell", "hp", "asus", "acer", "msi", "lg", "toshiba", "sharp",
              "panasonic", "sony", "fresh", "unionaire", "kiriazi", "zanussi", "beko", "bosch", "siemens",
              "midea", "carrier", "tornado", "hitachi", "tcl", "hisense", "philips", "jbl", "haier",
              "ariston", "vivo", "zte", "anker", "canon", "epson", "hyundai", "gree", "electrolux",
              "whirlpool", "candy", "kenwood", "braun", "tefal", "moulinex", "delonghi", "dyson"}
_BRAND_ALIAS = {"iphone": "apple", "ipad": "apple", "macbook": "apple", "airpods": "apple",
                "galaxy": "samsung", "redmi": "xiaomi", "poco": "xiaomi"}
_DIFFS = {"pro", "max", "plus", "ultra", "mini", "fe", "lite", "se", "note", "prime", "neo", "turbo",
          "flip", "fold", "air", "edge", "slim", "gt"}
_AR_WORDS = {"برو": "pro", "ماكس": "max", "بلس": "plus", "الترا": "ultra", "ميني": "mini", "لايت": "lite"}
_MATCH_STOP = {
    "the", "and", "with", "for", "new", "original", "global", "version", "dual", "sim", "smartphone", "mobile",
    "phone", "cell", "android", "unlocked", "warranty", "year", "years", "egypt", "edition", "model", "ram",
    "rom", "storage", "internal", "memory", "by", "of", "in", "or", "black", "white", "blue", "green", "gray",
    "grey", "silver", "gold", "pink", "red", "purple", "titanium", "natural", "midnight", "starlight",
    "graphite", "orange", "yellow", "beige", "brown", "cream", "lavender", "mint", "navy",
    "جديد", "اصلي", "ضمان", "سنه", "سنوات", "عام", "مع", "من", "في", "موبايل", "هاتف", "تليفون", "جوال",
    "ذكي", "اسود", "ابيض", "ازرق", "اخضر", "رمادي", "فضي", "ذهبي", "وردي", "احمر", "بنفسجي", "تيتانيوم",
    "برتقالي", "اصفر", "بيج", "بني", "كحلي", "رام", "مصر", "الاصلي",
}
_NOT_MODEL = {"1080p", "720p", "2160p", "ddr3", "ddr4", "ddr5", "usb3", "usb2", "wifi6", "wifi5"}


class Sig(NamedTuple):
    brand: str
    models: frozenset
    caps: tuple          # (gb, tb, inch)
    diffs: frozenset
    core: frozenset


def _norm_caps(t: str) -> str:
    t = re.sub(r"(\d+)\s*(?:tb|terabyte|تيرا(?:\s*بايت)?|تيرابايت)", r" \1tb ", t)
    t = re.sub(r"(\d+)\s*(?:gb|gigabyte|جيجا(?:\s*بايت)?|جيجابايت|جي\s*بي)", r" \1gb ", t)
    t = re.sub(r"(\d+(?:\.\d)?)\s*(?:inches|inch|in\b|\"|”|بوصه|بوصة|انش|إنش)", r" \1inch ", t)
    t = re.sub(r"(\d+(?:\.\d)?)\s*(?:hp|حصان)", r" \1hp ", t)
    t = re.sub(r"(\d+)\s*(?:mah|ملي\s*امبير)", r" \1mah ", t)
    t = re.sub(r"(\d+)\s*(?:kg|كجم|كيلو)", r" \1kg ", t)
    return t


def title_sig(title: str) -> Sig:
    t = _ar_norm((title or "").lower().translate(_TRANS))
    t = _AR_BRAND_RE.sub(lambda m: " " + _AR_BRANDS[m.group(1)] + " ", t)
    t = _norm_caps(t)
    t = re.sub(r"\b([a-z]{1,4})[-/_](?=[a-z]*\d)", r"\1", t)          # wm-7000 -> wm7000
    toks = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?|[\u0600-\u06FF]+", t)
    brand = ""
    caps: Dict[str, set] = {"gb": set(), "tb": set(), "inch": set()}
    models, diffs, core = set(), set(), set()
    for tok in toks:
        tok = _AR_WORDS.get(tok, tok)
        if tok in _BRAND_SET or tok in _BRAND_ALIAS:
            brand = brand or _BRAND_ALIAS.get(tok, tok)
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(gb|tb|inch)", tok)
        if m:
            caps[m.group(2)].add(m.group(1))
            continue
        if tok in _MATCH_STOP or re.fullmatch(r"20\d\d", tok):
            continue
        if tok in _DIFFS:
            diffs.add(tok)
            continue
        if (len(tok) >= 3 and re.search(r"[a-z]", tok) and re.search(r"\d", tok) and tok not in _NOT_MODEL
                and not re.fullmatch(r"\d+(?:\.\d+)?(?:hp|mah|kg|l|w|hz|mp|ah)", tok)):
            models.add(tok)
        core.add(tok)
    return Sig(brand, frozenset(models), (frozenset(caps["gb"]), frozenset(caps["tb"]), frozenset(caps["inch"])),
               frozenset(diffs), frozenset(core))


def sig_match(a: Sig, b: Sig) -> bool:
    """مطابقة تحفظية: نفس الموديل + مواصفات متوافقة. (تقريبية — مش مضمونة 100%)."""
    if a.brand and b.brand and a.brand != b.brand:
        return False
    if a.diffs != b.diffs:
        return False
    for sa, sb in zip(a.caps, b.caps):
        if sa and sb and not (sa <= sb or sb <= sa):
            return False
    if a.models and b.models:
        return bool(a.models & b.models)
    if a.models or b.models:
        return False
    if len(a.core) < 3 or len(b.core) < 3:
        return False
    return len(a.core & b.core) / len(a.core | b.core) >= 0.7


def annotate_cross(picked: List[Item], hist: History, now: int) -> None:
    index = []
    for rec in hist.products.values():
        if now - rec.get("ls", 0) > CROSS_MAX_AGE_H * 3600:
            continue
        index.append((rec.get("s", ""), rec["p"][-1][1], rec.get("t", ""), rec.get("l", ""), title_sig(rec.get("t", ""))))
    for it in picked:
        sg = title_sig(it.title)
        if not (sg.models or len(sg.core) >= 3):
            continue
        best: Dict[str, Tuple[int, str, str]] = {}
        for sid, price, title, link, sg2 in index:
            if sid == it.sid or not (0.55 * it.price <= price <= 1.8 * it.price):
                continue
            if not sig_match(sg, sg2):
                continue
            cur = best.get(sid)
            if cur is None or price < cur[0]:
                best[sid] = (price, title, link)
        it.cross = sorted((p, sid, t, l) for sid, (p, t, l) in best.items())
        it.score = deal_score(it)


# =============================================================== تليجرام + الذاكرة
def send_telegram(message: str, html: bool = False) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log("⚠ BOT_TOKEN/CHAT_ID مش متضبطين — الرسالة اتطبعت هنا بس:\n" + message)
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN
    payload: Dict[str, Any] = {"chat_id": CHAT_ID, "text": message[:4000], "disable_web_page_preview": False}
    if html:
        payload["parse_mode"] = "HTML"
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                try:
                    wait = int(r.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    wait = 5
                time.sleep(wait + 1)
                continue
            if r.status_code == 400 and html:           # مشكلة في الـ HTML -> ابعت نص عادي
                payload.pop("parse_mode", None)
                payload["text"] = htmllib.unescape(re.sub(r"<[^>]+>", "", message))[:4000]
                html = False
                continue
            log("⚠ تليجرام رجّع %d: %s" % (r.status_code, r.text[:120]))
            return False
        except Exception as e:
            log("خطأ تليجرام: %s" % e)
            time.sleep(2)
    return False


def load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log("⚠ مقدرتش أحفظ %s: %s" % (path, e))


def commit_state() -> None:
    """بديل: حفظ الذاكرة في الريبو نفسه (COMMIT_STATE=1). الأفضل تستخدم الـ data branch في الـ workflow."""
    if not COMMIT_STATE:
        return
    files = [f for f in (HISTORY_FILE, STATE_FILE, HEALTH_FILE) if os.path.exists(f)]
    if not files:
        return

    def run(*a):
        return subprocess.run(a, check=False, capture_output=True, text=True)

    try:
        run("git", "config", "user.name", "deals-radar[bot]")
        run("git", "config", "user.email", "deals-radar@users.noreply.github.com")
        run("git", "add", *files)
        if run("git", "diff", "--cached", "--quiet").returncode == 0:
            return
        run("git", "commit", "-m", "chore: update radar data [skip ci]")
        r = run("git", "push")
        log("حفظ الذاكرة في الريبو: %s" % ("تمام" if r.returncode == 0 else "فشل — " + (r.stderr or "")[:150]))
    except Exception as e:
        log("⚠ commit state: %s" % e)


def should_send(state: Dict[str, dict], pid: str, price: int) -> Tuple[bool, str]:
    rec = state.get(pid)
    if not rec:
        return True, ""
    age_h = (time.time() - rec.get("ts", 0)) / 3600.0
    if age_h > REALERT_HOURS:
        return True, ""
    if price <= rec.get("price", price) * 0.95:
        return True, "📉 نزل أكتر من آخر تنبيه"
    return False, ""


def cross_line(it: Item) -> str:
    if not it.cross:
        return ""
    price, sid, _t, link = it.cross[0]
    name = STORE_NAME.get(sid, sid)
    if price < it.price * 0.97:
        return "🔻 أرخص في %s بـ %s ج.م (سعره %s) — مطابقة تقريبية\n   %s" % (
            esc(name), money(it.price - price), money(price), esc(abs_link(link, sid)))
    n = len({c[1] for c in it.cross})
    return "🏪 ما لقيتش أرخص منه في %d متجر تاني (مطابقة تقريبية) ✅" % n


def deal_message(it: Item, tag: str = "") -> str:
    v: Verdict = it.verdict
    adv = it.discount
    store = esc(it.store)
    if v.code == "LOW":
        head = "🏆 <b>أقل سعر اتسجّل</b> — %s" % store
    elif v.code == "REAL" and it.old and valid_discount(it.price, it.old):
        head = "✅ <b>خصم حقيقي %d%%</b> — %s" % (v.real, store)
    elif v.code == "REAL":
        head = "📉 <b>نزل %d%% عن سعره المعتاد</b> — %s" % (v.real, store)
    elif v.code == "NEW":
        head = "🆕 <b>عرض غير مؤكد</b> (خصم معلن %d%%) — %s" % (adv, store)
    else:
        head = "🚩 <b>عرض مشكوك فيه</b> — %s" % store
    if tag:
        head += "\n" + esc(tag)
    lines = [head, "📦 " + esc(it.title[:95])]
    price = "💰 <b>%s</b> ج.م" % money(it.price)
    if it.old and adv:
        price += "  (المتجر بيعلن إنه كان %s — خصم %d%%)" % (money(it.old), adv)
    lines.append(price)
    if v.code in ("REAL", "LOW") and v.ref:
        lines.append("📊 المعتاد %s | أقل سعر قبل كده %s | متابَع من %d يوم"
                     % (money(v.ref), money(v.low), round(v.hist_days)))
    for n in v.notes:
        lines.append("ℹ️ " + esc(n))
    cl = cross_line(it)
    if cl:
        lines.append(cl)
    lines.append("🔗 " + (abs_link(it.link, it.sid) or STORE_DOMAIN.get(it.sid, "")))
    return "\n".join(lines)


# =============================================================== فحص المتاجر
@dataclass
class StoreReport:
    cfg: dict
    urls_total: int = 0
    urls_ok: int = 0
    urls_skipped: int = 0
    mentions: int = 0
    methods: set = field(default_factory=set)
    reasons: list = field(default_factory=list)
    items: List[Item] = field(default_factory=list)
    adv: int = 0
    real: int = 0
    flagged: int = 0
    new: int = 0
    sent: int = 0
    fails: int = 0

    @property
    def name(self) -> str:
        return self.cfg["name"]

    @property
    def sid(self) -> str:
        return self.cfg["id"]

    @property
    def seen(self) -> int:
        return len(self.items)

    @property
    def status(self) -> str:
        if self.urls_ok == 0:
            return "deferred" if (self.urls_total and self.urls_skipped >= self.urls_total) else "fail"
        if self.items:
            return "ok"
        return "parse_fail" if self.mentions >= 6 else "empty"


def fetch_and_extract(url: str, cfg: dict, fetcher: Fetcher) -> Tuple[Optional[Extract], FetchOutcome, Optional[Fetched]]:
    def accept(res: Fetched) -> Accept:
        ex = extract_page(res.text, url, cfg, res.blobs)
        log("   ✅ %s (%dKB) -> %d منتج | %d بسعر معلن | JSON: %d | أسعار بالصفحة: %d"
            % (res.method, len(res.text) // 1024, len(ex.items), ex.advertised, ex.json_seen, ex.mentions))
        good = bool(ex.items) or ex.mentions >= 6
        shell = not ex.items and ex.mentions < 6
        if shell:
            log("   ↪ الصفحة اتفتحت بس شكلها هيكل JS فاضي -> تصعيد للمتصفح/البوابات")
        return Accept(good=good, shell=shell, score=(len(ex.items), ex.mentions), payload=ex)

    out = fetcher.fetch(url, accept)
    if out.best:
        _score, res, ex = out.best
        return ex, out, res
    return None, out, None


def scan_store(cfg: dict, fetcher: Fetcher, deadline: float, hist: History, now: int) -> StoreReport:
    rep = StoreReport(cfg)
    collected: List[Item] = []
    fails = 0
    base = cfg["domain"]

    def acc_json(res: Fetched) -> Accept:
        try:
            data = json.loads(res.text)
        except Exception:
            return Accept(False, False, (0,), None)
        return Accept(True, False, (1,), data)

    for api in cfg.get("api", []):
        if time.time() > deadline:
            break
        rep.urls_total += 1
        log("🔍 [%s] API: %s" % (cfg["name"], api["url"]))
        out = fetcher.fetch(api["url"], acc_json, expect_json=True)
        if not out.best or out.best[2] is None:
            if out.attempted == 0:
                rep.urls_skipped += 1
            rep.reasons.extend(out.notes[-2:])
            continue
        _s, res, data = out.best
        if api.get("kind") == "shopify":
            its, n = shopify_items(data, base)
        else:
            its, n = json_products(data, base)
        log("   ✅ %s -> %d منتج / %d بسعر معلن" % (res.method, n, sum(1 for i in its if i.old)))
        rep.urls_ok += 1
        rep.methods.add(res.method)
        collected += its

    for url in cfg["urls"]:
        if time.time() > deadline:
            rep.reasons.append("خلص وقت الجولة")
            break
        if fails >= 2 and rep.urls_ok == 0:
            rep.reasons.append("اتخطّيت باقي الروابط بعد فشل متكرر")
            break
        rep.urls_total += 1
        log("🔍 [%s] %s" % (cfg["name"], url))
        ex, out, res = fetch_and_extract(url, cfg, fetcher)
        if ex is not None and res is not None:
            rep.urls_ok += 1
            rep.methods.add(res.method)
            rep.mentions += ex.mentions
            collected += ex.items
            if not ex.items and ex.hints:
                log("   🧭 كلاسات الأسعار في الصفحة (للضبط): %s" % ex.hints)
        elif out.attempted == 0:
            rep.urls_skipped += 1
            rep.reasons.append(short(" | ".join(out.notes[-2:]) or "اتخطّى (طرق الجلب كلها موقوفة مؤقتًا)", 110))
            log("   ⏸ اتخطّيت: " + " | ".join(out.notes[-2:]))
        else:
            fails += 1
            if not out.hard:
                rep.reasons.extend(out.notes[-2:])
        time.sleep(random.uniform(0.8, 1.8))

    items = dedupe_items(collected)
    for it in items:
        it.sid, it.store = cfg["id"], cfg["name"]
        if it.price >= MIN_PRODUCT_PRICE:
            hist.record(it, now)
    rep.items = [it for it in items if it.price >= MIN_PRODUCT_PRICE]
    return rep


# =============================================================== التقييم والإرسال
def evaluate(reports: List[StoreReport], hist: History, now: int) -> List[Item]:
    cands: List[Item] = []
    for rep in reports:
        for it in rep.items:
            rec = hist.products.get(it.pid)
            if not rec:
                continue
            adv_ok = bool(it.old) and valid_discount(it.price, it.old)
            if adv_ok:
                rep.adv += 1
            v = assess(it, rec, now)
            it.verdict = v
            if adv_ok:
                if v.code in ("REAL", "LOW"):
                    rep.real += 1
                    cands.append(it)
                elif v.code in ("FAKE", "STATIC", "PUMP"):
                    rep.flagged += 1
                    if SEND_FLAGGED:
                        cands.append(it)
                elif v.code == "NEW":
                    rep.new += 1
                    if SEND_UNVERIFIED:
                        cands.append(it)
            elif (TRACK_ALL and v.code in ("REAL", "LOW") and v.real >= DROP_ALERT
                  and v.conf >= 1 and rec.get("c", 1) >= 2):
                it.source += "+drop"             # نزل فعلاً عن المعتاد من غير ما المتجر يعلن خصم
                rep.real += 1
                cands.append(it)
    return cands


def select_deals(cands: List[Item]) -> List[Item]:
    by_store: Dict[str, List[Item]] = defaultdict(list)
    for it in cands:
        it.score = deal_score(it)
        by_store[it.sid].append(it)
    picked: List[Item] = []
    for lst in by_store.values():
        lst.sort(key=lambda x: x.score, reverse=True)
        picked += [x for x in lst if x.verdict.code in ("REAL", "LOW")][:MAX_DEALS_PER_STORE]
        picked += [x for x in lst if x.verdict.code == "NEW"][:MAX_UNVERIFIED_PER_STORE]
        picked += [x for x in lst if x.verdict.code in ("FAKE", "STATIC", "PUMP")][:2]
    picked.sort(key=lambda x: (x.verdict.code in ("REAL", "LOW"), x.score), reverse=True)
    return picked[:MAX_DEALS_PER_RUN]


def dispatch(picked: List[Item], state: Dict[str, dict], reports: List[StoreReport]) -> int:
    by_sid = {r.sid: r for r in reports}
    sent = 0
    for it in picked:
        ok, tag = should_send(state, it.pid, it.price)
        if not ok:
            continue
        if send_telegram(deal_message(it, tag), html=True):
            state[it.pid] = {"price": it.price, "ts": int(time.time()), "v": it.verdict.code}
            by_sid[it.sid].sent += 1
            sent += 1
            time.sleep(1.2)
    return sent


def update_health(reports: List[StoreReport], health: dict, now: int) -> List[str]:
    stores = health.setdefault("stores", {})
    events: List[str] = []
    for r in reports:
        h = stores.setdefault(r.sid, {"fails": 0, "ok": 0})
        if r.status in ("ok", "parse_fail", "empty"):
            if h.get("fails", 0) >= 3:
                events.append("🟢 %s رجع يشتغل بعد %d جولة" % (r.name, h["fails"]))
            h["fails"] = 0
            h["ok"] = now
        elif r.status == "fail":
            h["fails"] = h.get("fails", 0) + 1
            h["reason"] = short(" | ".join(r.reasons[-2:]), 120)
            if h["fails"] == 3:
                events.append("🔴 %s محجوب من 3 جولات ورا بعض" % r.name)
        r.fails = h.get("fails", 0)
    return events


def build_summary(reports: List[StoreReport], elapsed: float, hist: History, now: int, sent_total: int,
                  fetcher: Fetcher, events: List[str], persistent: bool) -> str:
    lines = ["📊 ملخص الجولة (%.1f دقيقة)" % (elapsed / 60.0), ""]
    any_fail = False
    for r in reports:
        st = r.status
        if st == "ok":
            lines.append("✅ %s: %d منتج | معلن %d | حقيقي %d | مشكوك %d | غير مؤكد %d | أُرسل %d"
                         % (r.name, r.seen, r.adv, r.real, r.flagged, r.new, r.sent))
        elif st == "parse_fail":
            lines.append("⚠ %s: اتفتح (%d سعر بالصفحة) بس ملقيتش منتجات — شكل الصفحة اتغيّر؟" % (r.name, r.mentions))
            any_fail = True
        elif st == "empty":
            lines.append("⚠ %s: اتفتح بس مفيش أسعار ظاهرة (JS/حماية؟)" % r.name)
            any_fail = True
        elif st == "deferred":
            lines.append("⏸ %s: محجوب (بيتجرّب كل %d ساعة) — %d جولة فاشلة" % (r.name, DEAD_RETRY_H, r.fails))
            any_fail = True
        else:
            lines.append("❌ %s: محجوب من %d جولة -> %s" % (r.name, r.fails, short(" | ".join(r.reasons[-2:]) or "مفيش رد", 100)))
            any_fail = True
    flagged = sum(r.flagged for r in reports)
    lines += ["", "إجمالي المرسل: %d | 🚩 مستبعد (وهمي/مشكوك): %d" % (sent_total, flagged)]
    lines.append("📚 الذاكرة: %d منتج | متابعة من %.0f يوم" % (len(hist.products), hist.span_days(now)))
    if fetcher.gw_calls:
        lines.append("🌐 طلبات بوابات الجولة دي: %d" % fetcher.gw_calls)
    for e in events:
        lines.append(e)
    if not persistent and not hist.loaded:
        lines.append("⚠ الذاكرة مش بتتحفظ بين الجولات! استخدم الـ workflow الجديد (data branch) أو COMMIT_STATE=1.")
    elif hist.span_days(now) < MIN_HISTORY_DAYS:
        lines.append("ℹ️ الذاكرة لسه صغيرة — حكم 'حقيقي/وهمي' بيبقى أدق بعد %d-7 أيام." % MIN_HISTORY_DAYS)
    if any_fail and not fetcher.gateways and not PROXY_URL:
        lines.append("💡 المواقع المحجوبة من IP جيتهاب: ضيف Secret باسم PROXY_URL أو SCRAPERAPI_KEY "
                     "(أو SCRAPINGBEE_KEY / ZENROWS_KEY / SCRAPEDO_TOKEN).")
    return "\n".join(lines)[:3900]


# =============================================================== التشغيل
def main() -> None:
    t0 = time.time()
    now = int(t0)
    deadline = t0 + MAX_RUNTIME_MIN * 60
    log("--- بدء الرادار الذكي الشامل (V8) ---")
    log("curl_cffi=%s cloudscraper=%s browser=%s proxy=%s gateways=%s data=%s"
        % (HAS_CFFI, HAS_CLOUDSCRAPER, USE_BROWSER, bool(PROXY_URL),
           [g["name"] for g in GATEWAYS if os.environ.get(g["env"])] or "-", DATA_DIR))

    os.makedirs(DATA_DIR, exist_ok=True)
    state = load_json(STATE_FILE)
    health = load_json(HEALTH_FILE)
    hist = History(HISTORY_FILE)
    persistent = COMMIT_STATE or DATA_DIR != "."
    first_run = not hist.loaded
    log("📚 الذاكرة: %d منتج محمّل | متابعة من %.0f يوم" % (len(hist.products), hist.span_days(now)))

    stores = [c for c in STORES if not ONLY_STORES or any(o in c["id"].lower() or o in c["name"].lower() for o in ONLY_STORES)]
    fetcher = Fetcher(health)
    reports: List[StoreReport] = []
    sent_total = 0
    events: List[str] = []
    try:
        try:
            for cfg in stores:
                if time.time() > deadline:
                    rep = StoreReport(cfg)
                    rep.reasons.append("خلص وقت الجولة قبل ما أوصله")
                    reports.append(rep)
                    continue
                try:
                    rep = scan_store(cfg, fetcher, deadline, hist, now)
                except Exception as e:
                    log(traceback.format_exc())
                    rep = StoreReport(cfg)
                    rep.reasons.append("خطأ داخلي: %s" % type(e).__name__)
                reports.append(rep)
        finally:
            fetcher.close()

        picked: List[Item] = []
        try:
            cands = evaluate(reports, hist, now)
            picked = select_deals(cands)
            if CROSS_STORE and picked:
                annotate_cross(picked, hist, now)
                picked.sort(key=lambda x: (x.verdict.code in ("REAL", "LOW"), x.score), reverse=True)
            sent_total = dispatch(picked, state, reports)
            log("📨 اتبعت %d عرض من %d مرشّح" % (sent_total, len(picked)))
        except Exception:
            log(traceback.format_exc())
        events = update_health(reports, health, now)
    finally:
        hist.save(now)
        cutoff = time.time() - 14 * DAY
        save_json(STATE_FILE, {k: v for k, v in state.items() if isinstance(v, dict) and v.get("ts", 0) > cutoff})
        save_json(HEALTH_FILE, health)
    commit_state()

    summary = build_summary(reports, time.time() - t0, hist, now, sent_total, fetcher, events, persistent)
    log("--- انتهت الجولة ---\n" + summary)
    last = health.get("last_summary", 0)
    want = (SUMMARY_MODE == "always"
            or (SUMMARY_MODE == "auto" and (sent_total > 0 or events or first_run or now - last > 12 * 3600)))
    if want and SUMMARY_MODE != "never":
        if send_telegram(summary):
            health["last_summary"] = now
            save_json(HEALTH_FILE, health)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        send_telegram("❌ الرادار وقع بخطأ:\n" + tb[-1500:])
        sys.exit(1)
