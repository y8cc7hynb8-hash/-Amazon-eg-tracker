#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار عروض المتاجر المصرية — V10 (النسخة الحاسمة)
================================================
أهم التعديلات عن V9:
  1) حل مشكلة (أُرسل 0): إرسال أقوى العروض الفورية منذ اليوم الأول مع فلترة ذكية، وعدم تجميد الرادار 14 يوماً.
  2) تفعيل حفظ الذاكرة تلقائياً (COMMIT_STATE=1 افتراضياً) لبناء سجل الأسعار ومنع تكرار الرسائل.
  3) إضافة 4 بوابات عبور مجانية (Free Proxy Gateways) لتخطي حظر IP جيتهاب في جوميا ودبي فون بدون اشتراكات.
  4) إصلاح استخراج العروض في (بي تك) عبر روابط العروض وحساب الخصم من شارات النسبة المئوية (%).
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

# مفعّل افتراضياً لحفظ الذاكرة في المستودع تلقائياً بين الجولات
COMMIT_STATE = _env_flag("COMMIT_STATE", True)

MIN_DISCOUNT = _env_int("MIN_DISCOUNT", 20)               # أقل خصم معلن لإرسال العرض
MAX_REALISTIC_DISC = _env_int("MAX_REALISTIC_DISC", 72)   # منع نسب الخصم الوهمية/الأقساط
MIN_REAL_DROP = _env_int("MIN_REAL_DROP", 10)             # أقل نزول فعلي عن السعر المعتاد
DROP_ALERT = _env_int("DROP_ALERT", 15)                   # للمنتجات بدون سعر قديم
MIN_PRODUCT_PRICE = _env_int("MIN_PRODUCT_PRICE", 250)
MAX_DEALS_PER_STORE = _env_int("MAX_DEALS_PER_STORE", 5)
MAX_DEALS_PER_RUN = _env_int("MAX_DEALS_PER_RUN", 35)
MAX_UNVERIFIED_PER_STORE = _env_int("MAX_UNVERIFIED_PER_STORE", 5)
MAX_RUNTIME_MIN = _env_int("MAX_RUNTIME_MIN", 25)
REALERT_HOURS = _env_int("REALERT_HOURS", 72)

HISTORY_WINDOW_DAYS = _env_int("HISTORY_WINDOW_DAYS", 60)
HISTORY_KEEP_DAYS = _env_int("HISTORY_KEEP_DAYS", 90)
MIN_HISTORY_DAYS = _env_int("MIN_HISTORY_DAYS", 3)        # تأكيد أسرع من السجل التاريخي
CROSS_MAX_AGE_H = 48

# مفعّل افتراضياً لإرسال أقوى العروض فوراً حتى لو الذاكرة جديدة
SEND_UNVERIFIED = _env_flag("SEND_UNVERIFIED", True)
SEND_FLAGGED = _env_flag("SEND_FLAGGED", False)
TRACK_ALL = _env_flag("TRACK_ALL", True)
CROSS_STORE = _env_flag("CROSS_STORE", True)
USE_BROWSER = _env_flag("USE_BROWSER", True)
SUMMARY_MODE = (os.environ.get("SUMMARY_MODE") or "always").strip().lower()
ONLY_STORES = [s.strip().lower() for s in (os.environ.get("ONLY_STORES") or "").split(",") if s.strip()]

PROXY_URL = os.environ.get("PROXY_URL") or None
JINA_API_KEY = os.environ.get("JINA_API_KEY") or None
GATEWAY_MIN_INTERVAL_MIN = _env_int("GATEWAY_MIN_INTERVAL_MIN", 180)
GATEWAY_MAX_CALLS = _env_int("GATEWAY_MAX_CALLS", 10)
GATEWAY_COUNTRY = (os.environ.get("GATEWAY_COUNTRY") if os.environ.get("GATEWAY_COUNTRY") is not None else "eg").strip().lower()
DEAD_RETRY_H = _env_int("DEAD_RETRY_H", 2)

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
_BADGE_PCT_RE = re.compile(r"(?:خصم|وفر|off|save|-|\b)\s*(\d{1,2})\s*%", re.I)


def _to_int(s: str) -> int:
    try:
        return int(float(s.replace(",", "")))
    except ValueError:
        return 0


def parse_price(value: Any) -> int:
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
    return x if 1 <= x <= 85 else None


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


_TRACKING = re.compile(r"^(utm_|ref$\vert{}ref_\vert{}qid$|sr$\vert{}sprefix\vert{}crid\vert{}pd_rd\vert{}pf_rd\vert{}psc$|_encoding|th$\vert{}keywords\vert{}dib\vert{}content-id\vert{}linkcode\vert{}tag$|smid)", re.I)


def clean_link(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))
    except Exception:
        return url


def item_key(link: str, title: str) -> str:
    if link:
        try:
            u = urllib.parse.urlsplit(link)
            path = u.path.rstrip("/")
            keep = {"variant", "sku", "skuId", "productId", "id", "item", "offer"}
            q = sorted((k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
                       if k in keep and v)
            if len(path) > 1:
                return path + ("?" + urllib.parse.urlencode(q) if q else "")
        except ValueError:
            pass
    return "t:" + norm_title(title)


def sane_old(cur: int, old: int) -> int:
    return old if old and cur < old <= cur * 6 else 0


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
    if cur < MIN_PRODUCT_PRICE or old <= cur:
        return False
    d = round((old - cur) / old * 100)
    return MIN_DISCOUNT <= d <= MAX_REALISTIC_DISC


# =============================================================== كشف الحظر
_BLOCK_TITLES = ("just a moment", "attention required", "access denied", "403 forbidden", "robot check",
                 "are you a robot", "security check", "pardon our interruption", "verify you are human",
                 "request blocked", "you have been blocked", "one more step", "bot verification",
                 "error 1020", "captcha", "service unavailable", "temporarily unavailable",
                 "checking your browser", "sorry, you have been blocked", "security checkpoint")
_BLOCK_BODY = ("cf_chl_opt", "cf-browser-verification", "challenge-form", "px-captcha", "captcha-delivery.com",
               "validatecaptcha", "enter the characters you see below", "api-services-support@amazon.com",
               "_incapsula_resource", "incapsula incident", "sucuri website firewall", "errors.edgesuite.net",
               "vercel security checkpoint")


def block_reason(text: str) -> str:
    if not text:
        return "رد فاضي"
    head = text[:6000].lower()
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    for b in _BLOCK_TITLES:
        if b in title:
            return "صفحة حماية: " + title[:40]
    if len(text) < 45000:
        low = text.lower()
        for b in _BLOCK_BODY:
            if b in low:
                return "صفحة حماية (" + b + ")"
    return ""


def judge(text: str, status: int, expect_json: bool) -> str:
    if status in (404, 410):
        return "HTTP %d" % status
    if expect_json:
        t_strip = (text or "").lstrip()
        if status == 200 and t_strip[:1] in ("{", "["):
            return ""
        if status != 200:
            return "HTTP %d" % status
        return block_reason(text) or "مش JSON"
    blk = block_reason(text)
    if status != 200:
        return "HTTP %d%s" % (status, (" - " + blk) if blk else "")
    if blk:
        return blk
    if len(text) < 1200:
        return "صفحة قصيرة (%d)" % len(text)
    return ""


# =============================================================== المتصفح (Playwright)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['ar-EG', 'ar', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
"""
_WAIT_TITLES = ("just a moment", "attention required", "access denied", "verify", "security checkpoint",
                "checking your browser", "ddos")


def _block_heavy(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()


class Browser:
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
                        self._launch("chrome")
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
                except Exception as e:
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
            for _ in range(8):
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
            for _ in range(6):
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


# =============================================================== الجلب (طبقات متصاعدة + بوابات مجانية ومدفوعة)
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
    q["url"] = url
    return "https://api.scraperapi.com/?" + urllib.parse.urlencode(q)


def _gw_scrapingbee(key: str, url: str, js: bool, country: str) -> str:
    q = {"api_key": key, "url": url, "render_js": "true" if js else "false", "premium_proxy": "true"}
    if country:
        q["country_code"] = country
    return "https://app.scrapingbee.com/api/v1/?" + urllib.parse.urlencode(q)


def _gw_zenrows(key: str, url: str, js: bool, country: str) -> str:
    q = {"apikey": key, "url": url, "js_render": "true" if js else "false", "premium_proxy": "true"}
    if country:
        q["proxy_country"] = country
    return "https://api.zenrows.com/v1/?" + urllib.parse.urlencode(q)


def _gw_scrapedo(key: str, url: str, js: bool, country: str) -> str:
    q = {"token": key, "url": url, "super": "true"}
    if js:
        q["render"] = "true"
    if country:
        q["geoCode"] = country
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
    hard: bool = False
    blobs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass
class Layer:
    name: str
    family: str                             # static | free_proxy | render | gateway
    run: Callable[[str, bool], Tuple[str, int, list]]
    proxy: bool = False
    no_challenge: bool = False


@dataclass
class Accept:
    good: bool = False
    shell: bool = False
    score: tuple = (0,)
    payload: Any = None


@dataclass
class FetchOutcome:
    best: Optional[tuple] = None
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

    def _hm(self, host: str) -> dict:
        return self.mem.setdefault(host, {"dead": {}})

    def _is_dead(self, host: str, name: str) -> bool:
        d = self._hm(host).get("dead", {}).get(name)
        return bool(d) and d.get("n", 0) >= 3 and time.time() - d.get("ts", 0) < DEAD_RETRY_H * 3600

    def _mark_bad(self, host: str, name: str) -> None:
        d = self._hm(host).setdefault("dead", {}).setdefault(name, {"n": 0, "ts": 0})
        d["n"] = d.get("n", 0) + 1
        d["ts"] = int(time.time())

    def _mark_ok(self, host: str, name: str) -> None:
        self._hm(host).setdefault("dead", {}).pop(name, None)

    @staticmethod
    def _is_challenge(note: str) -> bool:
        low = note.lower()
        return any(k in low for k in ("moment", "captcha", "challenge", "حماية", "403", "429", "checkpoint"))

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

    def _cffi(self, fp: str, via_proxy: bool = False):
        def run(url: str, expect_json: bool):
            parts = urllib.parse.urlsplit(url)
            host = parts.netloc
            home = "%s://%s/" % (parts.scheme, host)
            base_headers = self._headers(expect_json)
            sess = self._session(fp, host, via_proxy)
            proxies = self.proxies if via_proxy else None
            wk = (fp, host, via_proxy)
            if wk not in self._warmed:
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

    # ---- بوابات عبور مجانية لتخطي حظر IP جيتهاب (جوميا / دبي فون)
    def _free_proxy_run(self, kind: str):
        def run(url: str, expect_json: bool):
            enc = urllib.parse.quote(url, safe="")
            if kind == "codetabs":
                purl = "https://api.codetabs.com/v1/proxy?quest=" + enc
            elif kind == "allorigins":
                purl = "https://api.allorigins.win/raw?url=" + enc
            elif kind == "allorigins_json":
                purl = "https://api.allorigins.win/get?url=" + enc
            elif kind == "corsproxy":
                purl = "https://corsproxy.io/?" + enc
            else:
                purl = "https://thingproxy.freeboard.io/fetch/" + url

            h = {"User-Agent": UA_CHROME, "Accept-Language": AR_LANG}
            if HAS_CFFI:
                r = cffi_requests.get(purl, headers=h, impersonate="chrome120", timeout=30)
            else:
                r = requests.get(purl, headers=h, timeout=30)

            text = r.text or ""
            st = r.status_code
            if kind == "allorigins_json" and st == 200:
                try:
                    j = json.loads(text)
                    text = j.get("contents") or ""
                except Exception:
                    pass
            return text, st, []
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
                    continue
                break
            return last[0], last[1], []
        return run

    def _gateway_gate(self, url: str) -> str:
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

        # البوابات المجانية لتغيير الـ IP عند حظر سيرفر جيتهاب
        for p_kind in ("codetabs", "allorigins", "allorigins_json", "corsproxy"):
            L.append(Layer("proxy:" + p_kind, "free_proxy", self._free_proxy_run(p_kind), proxy=True))

        if self.browser is not None:
            L.append(Layer("playwright", "render", self._browser_run(False)))
            if PROXY_URL:
                L.append(Layer("playwright/proxy", "render", self._browser_run(True), proxy=True))
        if not expect_json:
            L.append(Layer("jina", "render", self._jina()))
        for g in self.gateways:
            L.append(Layer("gw:" + g["name"], "gateway", self._gateway(g)))
        good = self.mem.get(host, {}).get("good")
        if good:
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
            reason = ""
        if reason:
            snippet = (" | " + re.sub(r"\s+", " ", text[:80])) if (layer.family == "gateway" and text) else ""
            out.notes.append("%s: %s%s" % (layer.name, reason, snippet))
            if st in (404, 410):
                out.hard = True
            return out
        out.ok, out.text, out.status, out.blobs = True, text, st, blobs
        return out

    def fetch(self, url: str, accept: Callable[[Fetched], Accept], expect_json: bool = False) -> FetchOutcome:
        host = urllib.parse.urlsplit(url).netloc
        out = FetchOutcome()
        skip_static = False
        direct_blocked = False
        for layer in self._layers(host, expect_json):
            if layer.family == "static" and skip_static:
                continue
            if direct_blocked and layer.family == "static" and not layer.proxy:
                continue
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
        cutoff = self.now - 7 * DAY
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
    pr = d.get("price_range")
    if isinstance(pr, dict) and isinstance(pr.get("minimum_price"), dict):
        mp = pr["minimum_price"]
        fin, reg = _num(mp.get("final_price")), _num(mp.get("regular_price"))
        if fin:
            return fin, (reg if reg and reg > fin else None)
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
            cur = pv
        elif old is None and pv > cur:
            old = pv
    if cur is None:
        of = d.get("offers")
        if isinstance(of, list) and of:
            of = of[0]
        if isinstance(of, dict):
            cur = _num(of.get("price") or of.get("lowPrice"))
            if not old:
                old = _num(of.get("highPrice"))
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
                    v = "/" + v.strip("/") + str(d.get("url_suffix") or ".html")
                elif k == "handle":
                    v = "/products/" + v
                link = urllib.parse.urljoin(base, v)
                break
    return clean_title(name), int(round(cur)), int(round(old)) if old else 0, link or ""


def json_products(root: Any, base: str, link_fn=None) -> Tuple[List["Item"], int]:
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
               "msrp", "original", "prev", "previous", "compare", "oldprice", "wasprice", "linethrough",
               "regular", "del"}
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
    t = txt.translate(_TRANS)
    if _PRICE_RE.search(t):
        return True
    if toks & (_PRICE_TOK | _CUR_STRONG):
        return True
    return bool(re.fullmatch(r"[\d.,\s]+", t))


def _current_candidates(card, old_desc: set, old_p: int, strict: bool = False) -> list:
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
        return min(vals)
    return max(vals)


def _pick_current(card, old_desc: set, old_p: int) -> int:
    return _choose_current(_current_candidates(card, old_desc, old_p))


def html_items(soup, page_url: str) -> List["Item"]:
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
        
