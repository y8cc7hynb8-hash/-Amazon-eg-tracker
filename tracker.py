#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رادار عروض المتاجر المصرية — V7
================================
اللي اتغيّر عن V6:
  1) كشف الحظر: V6 كان بيعتبر أي صفحة 200 "نجاح" حتى لو صفحة Cloudflare/Captcha.
     دلوقتي بيكشفها وبيصعّد تلقائياً:
     curl_cffi -> cloudscraper -> متصفح حقيقي (Playwright) -> Jina Reader
     -> ScraperAPI (لو فيه مفتاح) -> بروكسيات عامة.
  2) شلت User-Agent العشوائي: كان بيتبعت (آيفون/ماك) مع بصمة TLS بتاعة Chrome120،
     والتعارض ده بيتكشف كبوت. curl_cffi دلوقتي بيحط الهيدرز المتسقة لوحده.
  3) استخراج عام: JSON (Next.js / Magento GraphQL / WooCommerce / Algolia / JSON-LD)
     + كروت HTML معتمدة على "السعر القديم المشطوب" بدل أسماء كلاسات ثابتة.
  4) إصلاح نون (price = القديم و sale_price = الحالي) وإصلاح WooCommerce (الأسعار ×100).
  5) تقرير تليجرام لكل متجر (اشتغل / فاضي / محجوب) + ذاكرة بين الجولات لمنع التكرار.

متغيرات اختيارية (Secrets أو env):
  BOT_TOKEN, CHAT_ID            تليجرام (زي ما هما)
  PROXY_URL                     بروكسي http://user:pass@host:port (يحل حظر IP بتاع GitHub)
  SCRAPERAPI_KEY                مفتاح ScraperAPI كحل أخير للمواقع المحجوبة
  JINA_API_KEY                  مفتاح Jina Reader (اختياري، بيرفع حد الطلبات)
  MIN_DISCOUNT / MAX_REALISTIC_DISC / MIN_PRODUCT_PRICE / MAX_DEALS_PER_STORE
  USE_BROWSER=0                 يقفل تحميل المتصفح
  COMMIT_STATE=1                يحفظ sent_deals.json في الريبو (محتاج permissions: contents: write)
  SEND_START_MSG=1              يبعت رسالة "بدأ الفحص" في أول الجولة
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


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


BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

MIN_DISCOUNT = _env_int("MIN_DISCOUNT", 20)
MAX_REALISTIC_DISC = _env_int("MAX_REALISTIC_DISC", 70)   # منع نسب الخصم الوهمية
MIN_PRODUCT_PRICE = _env_int("MIN_PRODUCT_PRICE", 200)
MAX_DEALS_PER_STORE = _env_int("MAX_DEALS_PER_STORE", 8)
MAX_RUNTIME_MIN = _env_int("MAX_RUNTIME_MIN", 25)
REALERT_HOURS = _env_int("REALERT_HOURS", 72)

USE_BROWSER = os.environ.get("USE_BROWSER", "1") != "0"
PROXY_URL = os.environ.get("PROXY_URL") or None
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY") or None
JINA_API_KEY = os.environ.get("JINA_API_KEY") or None
STATE_FILE = os.environ.get("STATE_FILE", "sent_deals.json")
COMMIT_STATE = os.environ.get("COMMIT_STATE") == "1"
SEND_START_MSG = os.environ.get("SEND_START_MSG") == "1"

AR_LANG = "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7"
UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
IMPERSONATE = ("chrome120", "safari17_0")

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


@dataclass
class Item:
    title: str
    price: int
    old: int
    link: str = ""
    source: str = ""

    @property
    def discount(self) -> int:
        return round((self.old - self.price) / self.old * 100) if self.old else 0


def valid_discount(cur: int, old: int) -> bool:
    if cur < MIN_PRODUCT_PRICE or old <= cur:
        return False
    d = round((old - cur) / old * 100)
    return MIN_DISCOUNT <= d <= MAX_REALISTIC_DISC


_STOP = {"عرض", "خصم", "جديد", "أصلي", "ضمان", "مصر", "سنة", "new", "original", "warranty", "egypt", "with", "and"}


def norm_title(title: str) -> str:
    clean = re.sub(r"[^\w\s]", " ", (title or "").lower())
    words = [w for w in clean.split() if len(w) > 1 and w not in _STOP]
    return " ".join(words[:8])


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


# =============================================================== كشف الحظر
_BLOCK_TITLES = ("just a moment", "attention required", "access denied", "403 forbidden", "robot check",
                 "are you a robot", "security check", "pardon our interruption", "verify you are human",
                 "request blocked", "you have been blocked", "one more step", "bot verification",
                 "error 1020", "captcha", "service unavailable", "temporarily unavailable")
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


def _block_heavy(route) -> None:
    if route.request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()


class Browser:
    """متصفح Chromium بيتحمّل مرة واحدة وبس لو احتجناه."""

    def __init__(self) -> None:
        self.state = "new"
        self._pw = None
        self._browser = None
        self._ctx = None

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

    def _launch(self) -> None:
        from playwright.sync_api import sync_playwright  # type: ignore
        self._pw = sync_playwright().start()
        kw: Dict[str, Any] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        }
        pc = self._proxy_conf()
        if pc:
            kw["proxy"] = pc
        self._browser = self._pw.chromium.launch(**kw)
        self._ctx = self._browser.new_context(
            locale="ar-EG", timezone_id="Africa/Cairo", user_agent=UA_CHROME,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": AR_LANG},
        )
        self._ctx.add_init_script(_STEALTH_JS)
        self._ctx.route("**/*", _block_heavy)

    def _reset(self) -> None:
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._ctx = None

    def start(self) -> bool:
        if self.state != "new":
            return self.state == "ready"
        try:
            if not _ensure("playwright"):
                raise RuntimeError("تعذر تثبيت playwright")
            try:
                self._launch()
            except Exception:
                self._reset()
                log("🌐 بحمّل متصفح Chromium (مرة واحدة في الجولة)...")
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                               check=False, timeout=420, capture_output=True)
                try:
                    self._launch()
                except Exception:
                    self._reset()
                    subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                                   check=False, timeout=600, capture_output=True)
                    self._launch()
            self.state = "ready"
        except Exception as e:
            log("⚠ المتصفح مش متاح: %s" % str(e)[:120])
            self._reset()
            self.state = "failed"
        return self.state == "ready"

    def render(self, url: str, raw: bool = False) -> Tuple[str, list, int, str]:
        """يرجّع (html, json_blobs, status, note). raw=True لنقاط JSON."""
        blobs: list = []
        status = 0
        page = None
        try:
            page = self._ctx.new_page()

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
                status = status or 0
                note = "goto: " + type(e).__name__
                if page.url in ("about:blank", ""):
                    return "", blobs, 0, note
            for _ in range(8):   # استنى لحد ما تعدّي صفحة "Just a moment"
                t = (page.title() or "").lower()
                if any(b in t for b in ("just a moment", "attention required", "access denied", "verify")):
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
        try:
            if self._ctx:
                self._ctx.close()
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        self._reset()


# =============================================================== الجلب (طبقات متصاعدة)
@dataclass
class Fetched:
    ok: bool = False
    text: str = ""
    status: int = 0
    method: str = ""
    hard: bool = False                      # 404/410: مفيش فايدة من التصعيد
    blobs: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Fetcher:
    def __init__(self) -> None:
        self.proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        self._sessions: Dict[Tuple[str, str], Any] = {}
        self._warmed: set = set()
        self._scraper = None
        self.browser = Browser() if USE_BROWSER else None

    def _session(self, fp: str, host: str):
        key = (fp, host)
        if key not in self._sessions:
            self._sessions[key] = cffi_requests.Session(impersonate=fp)
        return self._sessions[key]

    @staticmethod
    def _attempt(out: Fetched, name: str, fn: Callable[[], Tuple[str, int]], expect_json: bool) -> bool:
        try:
            text, st = fn()
        except Exception as e:
            out.notes.append("%s: %s" % (name, type(e).__name__))
            return False
        reason = judge(text, st, expect_json)
        if reason:
            out.notes.append("%s: %s" % (name, reason))
            if st in (404, 410):
                out.hard = True
            return False
        out.ok, out.text, out.status, out.method = True, text, st, name
        return True

    @staticmethod
    def _is_challenge(note: str) -> bool:
        low = note.lower()
        return any(k in low for k in ("moment", "captcha", "challenge", "حماية"))

    # ---- طبقة 1+2: سريعة وخفيفة
    def static(self, url: str, expect_json: bool = False) -> Fetched:
        out = Fetched()
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc
        home = "%s://%s/" % (parts.scheme, host)
        accept = ("application/json,text/plain,*/*" if expect_json else
                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
        base_headers = {"Accept-Language": AR_LANG, "Accept": accept}
        challenge = False

        if HAS_CFFI:
            for fp in IMPERSONATE:
                def go(fp=fp):
                    sess = self._session(fp, host)
                    if (fp, host) not in self._warmed:     # زيارة الرئيسية الأول عشان الكوكيز
                        self._warmed.add((fp, host))
                        try:
                            sess.get(home, headers=base_headers, timeout=15, proxies=self.proxies)
                        except Exception:
                            pass
                        time.sleep(random.uniform(0.5, 1.2))
                    h = dict(base_headers)
                    h["Referer"] = home
                    r = sess.get(url, headers=h, timeout=25, proxies=self.proxies, allow_redirects=True)
                    return (r.text or ""), r.status_code

                if self._attempt(out, "curl_cffi/" + fp, go, expect_json):
                    return out
                if out.hard:
                    return out
                if self._is_challenge(out.notes[-1]):
                    challenge = True
                    break
        else:
            def go_plain():
                h = dict(base_headers)
                h["User-Agent"] = UA_CHROME
                r = requests.get(url, headers=h, timeout=25, proxies=self.proxies)
                return r.text or "", r.status_code

            if self._attempt(out, "requests", go_plain, expect_json):
                return out
            if out.hard:
                return out

        if HAS_CLOUDSCRAPER and not challenge and not expect_json:
            def go_cs():
                if self._scraper is None:
                    self._scraper = _cloudscraper_mod.create_scraper(
                        browser={"browser": "chrome", "platform": "windows", "mobile": False})
                r = self._scraper.get(url, headers={"Accept-Language": AR_LANG}, timeout=25, proxies=self.proxies)
                return r.text or "", r.status_code

            if self._attempt(out, "cloudscraper", go_cs, expect_json):
                return out
        return out

    # ---- طبقة 3+: متصفح حقيقي وبوابات
    def rendered(self, url: str, expect_json: bool = False) -> Fetched:
        out = Fetched()
        if self.browser is not None and self.browser.start():
            html, blobs, st, note = self.browser.render(url, raw=expect_json)
            reason = judge(html, st, expect_json) if html else (note or "فاضي")
            if not reason or (blobs and st == 200):
                out.ok, out.text, out.status, out.method, out.blobs = True, html, st, "playwright", blobs
                return out
            out.notes.append("playwright: %s" % reason)
            if st in (404, 410):
                out.hard = True
                return out

        if not expect_json:
            def go_jina():
                h = {"X-Return-Format": "html", "Accept": "text/html", "X-Timeout": "40"}
                if JINA_API_KEY:
                    h["Authorization"] = "Bearer " + JINA_API_KEY
                r = requests.get("https://r.jina.ai/" + url, headers=h, timeout=60)
                return r.text or "", r.status_code

            if self._attempt(out, "jina", go_jina, expect_json):
                return out

        if SCRAPERAPI_KEY:
            def go_sapi():
                q = urllib.parse.urlencode({"api_key": SCRAPERAPI_KEY, "url": url,
                                            "render": "false" if expect_json else "true"})
                r = requests.get("https://api.scraperapi.com/?" + q, timeout=90)
                return r.text or "", r.status_code

            if self._attempt(out, "scraperapi", go_sapi, expect_json):
                return out

        enc = urllib.parse.quote(url, safe="")
        for name, purl in (("codetabs", "https://api.codetabs.com/v1/proxy?quest=" + enc),
                           ("allorigins", "https://api.allorigins.win/raw?url=" + enc)):
            def go_proxy(purl=purl):
                r = requests.get(purl, headers={"User-Agent": UA_CHROME, "Accept-Language": AR_LANG}, timeout=25)
                return r.text or "", r.status_code

            if self._attempt(out, name, go_proxy, expect_json):
                return out
        return out

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()


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
        if isinstance(v, str) and len(v.strip()) >= 6 and not v.strip().isdigit() and v.strip().lower() not in _JUNK_NAMES:
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


def json_products(root: Any, base: str, link_fn=None) -> Tuple[List[Item], int]:
    """يمشي على أي JSON ويلقط المنتجات. يرجّع (العروض, عدد المنتجات اللي شافها)."""
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
                if old and valid_discount(cur, old):
                    k = (norm_title(title), cur)
                    if k not in keys:
                        keys.add(k)
                        items.append(Item(title, cur, old, link, "json"))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return items, seen_n


def shopify_items(data: Any, base: str) -> Tuple[List[Item], int]:
    items: List[Item] = []
    seen_n = 0
    prods = data.get("products") if isinstance(data, dict) else None
    for p in prods or []:
        if not isinstance(p, dict):
            continue
        for v in p.get("variants") or []:
            cur, old = _num(v.get("price")), _num(v.get("compare_at_price"))
            if not cur:
                continue
            seen_n += 1
            if old and valid_discount(int(cur), int(old)):
                items.append(Item(clean_title(str(p.get("title", ""))), int(cur), int(old),
                                  "%s/products/%s" % (base.rstrip("/"), p.get("handle", "")), "shopify"))
                break
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
    return max(cands, key=lambda c: c[0])[1] if cands else ""


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


def _pick_current(card, old_desc: set, old_p: int) -> int:
    floor = old_p * (1 - MAX_REALISTIC_DISC / 100.0)
    cands = []
    for order, el in enumerate(card.find_all(True)):
        if id(el) in old_desc:
            continue
        txt = el.get_text(" ", strip=True)
        if not txt or len(txt) > 45 or not _has_digit(txt):
            continue
        low = txt.lower()
        if any(w in low for w in COUPON_WORDS) or any(w in low for w in INSTALLMENT_WORDS):
            continue
        par = el.parent
        if par is not None and par is not card:
            ptxt = par.get_text(" ", strip=True).lower()
            if len(ptxt) <= 80 and (any(w in ptxt for w in COUPON_WORDS) or any(w in ptxt for w in INSTALLMENT_WORDS)):
                continue
        attrs = el.attrs or {}
        v = 0
        amt = attrs.get("data-price-amount")
        if amt:
            n = _num(amt)
            v = int(round(n)) if n else 0
        if not v:
            v = parse_price(txt)
        if v < MIN_PRODUCT_PRICE or v >= old_p or v < floor:
            continue
        toks = class_tokens(attrs.get("class"))
        score = 1
        if str(attrs.get("data-price-type", "")).lower() == "finalprice" or el.name == "ins":
            score = 4
        elif toks & _CUR_STRONG:
            score = 3
        elif toks & _PRICE_TOK:
            score = 2
        cands.append((score, v, order))
    if not cands:
        return 0
    top = max(c[0] for c in cands)
    # لو فيه أكتر من سعر بنفس الأولوية ناخد الأقرب للقديم (بيستبعد سعر الكوبون/الباندل)
    return max((c for c in cands if c[0] == top), key=lambda c: c[1])[1]


def html_items(soup, page_url: str) -> List[Item]:
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
        if cur and valid_discount(cur, old_p):
            items.append(Item(title, cur, old_p, _pick_link(card, page_url) or page_url, "html"))
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


def extract_amazon(soup) -> Tuple[List[Item], int]:
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
        if old and valid_discount(cur, old):
            items.append(Item(title, cur, old, "https://www.amazon.eg/dp/" + asin, "amazon"))
    return items, seen_n


@dataclass
class Extract:
    items: List[Item] = field(default_factory=list)
    json_seen: int = 0
    mentions: int = 0
    amazon_seen: int = 0
    hints: list = field(default_factory=list)

    @property
    def good(self) -> bool:
        return bool(self.items) or self.json_seen >= 3 or self.mentions >= 6 or self.amazon_seen > 0

    @property
    def seen(self) -> int:
        return max(self.json_seen, self.mentions, self.amazon_seen)

    @property
    def score(self) -> Tuple[int, int, int]:
        return (len(self.items), self.seen, self.mentions)


def dedupe_items(items: List[Item]) -> List[Item]:
    out: List[Item] = []
    seen_keys = set()
    for it in items:
        k1 = (norm_title(it.title), it.price)
        k2 = clean_link(it.link) if it.link else None
        if k1 in seen_keys or (k2 and k2 in seen_keys):
            continue
        seen_keys.add(k1)
        if k2:
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
        its, n = extract_amazon(soup)
        found += its
        ex.amazon_seen = n
    link_fn = LINK_BUILDERS.get(adapter)
    for blob in list(script_json_blobs(soup)) + list(blobs):
        its, n = json_products(blob, base, link_fn)
        found += its
        ex.json_seen += n
    if not (adapter == "amazon" and found):
        found += html_items(soup, url)
    ex.mentions = price_mentions(soup.get_text(" ", strip=True))
    ex.items = dedupe_items(found)
    if not ex.items and ex.mentions >= 6:
        ex.hints = price_class_hints(soup)
    return ex


# =============================================================== المتاجر
STORES: List[dict] = [
    {"name": "أمازون مصر (Amazon)", "adapter": "amazon", "domain": "https://www.amazon.eg",
     "urls": [
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901098031&language=ar_AE&page=2",
         "https://www.amazon.eg/s?i=kitchen&rh=p_n_deal_type%3A26901098031&language=ar_AE",
         "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901098031&language=ar_AE",
     ]},
    {"name": "نون مصر (Noon)", "adapter": "noon", "domain": "https://www.noon.com",
     "urls": [
         "https://www.noon.com/egypt-ar/electronics/?sort[by]=discount&sort[dir]=desc",
         "https://www.noon.com/egypt-ar/electronics-and-mobiles/mobiles-and-accessories/mobiles-20905/?sort[by]=discount&sort[dir]=desc",
     ]},
    {"name": "دبي فون (Dubai Phone)", "adapter": "woo", "domain": "https://www.dubaiphone.net",
     "api": [{"url": "https://www.dubaiphone.net/wp-json/wc/store/v1/products?on_sale=true&per_page=100", "kind": "woo"}],
     "urls": ["https://www.dubaiphone.net/"]},
    {"name": "جوميا مصر (Jumia)", "adapter": "generic", "domain": "https://www.jumia.com.eg",
     "urls": ["https://www.jumia.com.eg/ar/flash-sales/", "https://www.jumia.com.eg/ar/"]},
    {"name": "راية شوب (Raya Shop)", "adapter": "generic", "domain": "https://www.rayashop.com",
     "urls": ["https://www.rayashop.com/ar/deals", "https://www.rayashop.com/ar/"]},
    {"name": "بي تك (B.Tech)", "adapter": "generic", "domain": "https://btech.com",
     "urls": ["https://btech.com/ar/"]},
    {"name": "2B Egypt", "adapter": "generic", "domain": "https://2b.com.eg",
     "urls": ["https://2b.com.eg/ar/deals.html", "https://2b.com.eg/ar/"]},
    {"name": "رنين (Raneen)", "adapter": "generic", "domain": "https://www.raneen.com",
     "urls": ["https://www.raneen.com/ar/deals", "https://www.raneen.com/ar/"]},
    {"name": "دريم 2000 (Dream 2000)", "adapter": "generic", "domain": "https://dream2000.com",
     "urls": ["https://dream2000.com/mobiles.html", "https://dream2000.com/"]},
    {"name": "كنز (Kenzz)", "adapter": "generic", "domain": "https://kenzz.com",
     "urls": ["https://kenzz.com/ar"]},
]


# =============================================================== فحص متجر
@dataclass
class StoreReport:
    name: str
    urls_total: int = 0
    urls_ok: int = 0
    seen: int = 0
    found: int = 0
    sent: int = 0
    methods: set = field(default_factory=set)
    reasons: list = field(default_factory=list)
    items: List[Item] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.urls_ok == 0:
            return "fail"
        return "ok" if self.seen > 0 else "empty"


def fetch_and_extract(url: str, cfg: dict, fetcher: Fetcher) -> Tuple[Extract, Fetched]:
    res = fetcher.static(url)
    ex: Optional[Extract] = None
    if res.ok:
        ex = extract_page(res.text, url, cfg)
        log("   ✅ %s (%dKB) -> %d عرض | منتجات JSON: %d | أسعار بالصفحة: %d"
            % (res.method, len(res.text) // 1024, len(ex.items), ex.json_seen, ex.mentions))
        if ex.good:
            return ex, res
        log("   ↪ الصفحة اتفتحت بس شكلها هيكل JS فاضي -> تصعيد للمتصفح")
    else:
        log("   ⛔ " + " | ".join(res.notes[-3:]))
        if res.hard:
            return Extract(), res
        log("   ↪ تصعيد للمتصفح/البوابات...")
    res2 = fetcher.rendered(url)
    if res2.ok:
        ex2 = extract_page(res2.text, url, cfg, res2.blobs)
        log("   ✅ %s (%dKB, JSON متلقط: %d) -> %d عرض | أسعار بالصفحة: %d"
            % (res2.method, len(res2.text) // 1024, len(res2.blobs), len(ex2.items), ex2.mentions))
        if ex is None or ex2.score > ex.score:
            return ex2, res2
        return ex, res
    log("   ⛔ " + " | ".join(res2.notes[-4:]))
    if ex is not None:
        return ex, res
    res.notes.extend(res2.notes)
    return Extract(), res


def scan_store(cfg: dict, fetcher: Fetcher, deadline: float) -> StoreReport:
    rep = StoreReport(cfg["name"])
    collected: List[Item] = []
    fails = 0
    base = cfg["domain"]

    for api in cfg.get("api", []):
        if time.time() > deadline:
            break
        rep.urls_total += 1
        log("🔍 [%s] API: %s" % (cfg["name"], api["url"]))
        res = fetcher.static(api["url"], expect_json=True)
        if not res.ok and not res.hard:
            res = fetcher.rendered(api["url"], expect_json=True)
        if not res.ok:
            log("   ⛔ " + " | ".join(res.notes[-3:]))
            rep.reasons.extend(res.notes[-2:])
            continue
        try:
            data = json.loads(res.text)
        except Exception:
            rep.reasons.append("API رجّع مش JSON")
            continue
        if api.get("kind") == "shopify":
            its, n = shopify_items(data, base)
        else:
            its, n = json_products(data, base)
        log("   ✅ %s -> %d منتج / %d عرض" % (res.method, n, len(its)))
        rep.urls_ok += 1
        rep.seen += n
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
        ex, res = fetch_and_extract(url, cfg, fetcher)
        if res.ok:
            rep.urls_ok += 1
            rep.methods.add(res.method)
            rep.seen += ex.seen
            collected += ex.items
            if not ex.items and ex.hints:
                log("   🧭 كلاسات الأسعار في الصفحة (للضبط): %s" % ex.hints)
        else:
            fails += 1
            if not res.hard:
                rep.reasons.extend(res.notes[-2:])
        time.sleep(random.uniform(1.0, 2.2))

    rep.items = dedupe_items(collected)
    rep.found = len(rep.items)
    return rep


# =============================================================== تليجرام + الذاكرة
def send_telegram(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log("⚠ BOT_TOKEN/CHAT_ID مش متضبطين — الرسالة اتطبعت هنا بس:\n" + message)
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN
    payload = {"chat_id": CHAT_ID, "text": message[:4000], "disable_web_page_preview": False}
    for _ in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            log("حالة إرسال تليجرام: %d" % r.status_code)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                try:
                    wait = int(r.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    wait = 5
                time.sleep(wait + 1)
                continue
            return False
        except Exception as e:
            log("خطأ تليجرام: %s" % e)
            time.sleep(2)
    return False


def load_state() -> Dict[str, dict]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: Dict[str, dict]) -> None:
    cutoff = time.time() - 14 * 86400
    state = {k: v for k, v in state.items() if isinstance(v, dict) and v.get("ts", 0) > cutoff}
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0)
    except Exception as e:
        log("⚠ مقدرتش أحفظ الذاكرة: %s" % e)


def commit_state() -> None:
    if not COMMIT_STATE or not os.path.exists(STATE_FILE):
        return

    def run(*a):
        return subprocess.run(a, check=False, capture_output=True, text=True)

    try:
        run("git", "config", "user.name", "deals-radar[bot]")
        run("git", "config", "user.email", "deals-radar@users.noreply.github.com")
        run("git", "add", STATE_FILE)
        if run("git", "diff", "--cached", "--quiet").returncode == 0:
            return
        run("git", "commit", "-m", "chore: update sent deals [skip ci]")
        r = run("git", "push")
        log("حفظ الذاكرة في الريبو: %s" % ("تمام" if r.returncode == 0 else "فشل — " + (r.stderr or "")[:150]))
    except Exception as e:
        log("⚠ commit state: %s" % e)


def state_key(store: str, it: Item) -> str:
    if it.link and urllib.parse.urlsplit(it.link).path.strip("/"):
        return clean_link(it.link)
    return store + "|" + norm_title(it.title)


def should_send(state: Dict[str, dict], key: str, price: int) -> Tuple[bool, str]:
    rec = state.get(key)
    if not rec:
        return True, ""
    age_h = (time.time() - rec.get("ts", 0)) / 3600.0
    if age_h > REALERT_HOURS:
        return True, ""
    if price <= rec.get("price", price) * 0.95:
        return True, "📉 نزل أكتر من آخر تنبيه\n"
    return False, ""


def dispatch(store: str, items: List[Item], state: Dict[str, dict]) -> int:
    if not items:
        return 0
    ordered = sorted(items, key=lambda x: (x.discount, x.old - x.price), reverse=True)
    sent = 0
    for it in ordered:
        if sent >= MAX_DEALS_PER_STORE:
            break
        key = state_key(store, it)
        ok, tag = should_send(state, key, it.price)
        if not ok:
            continue
        msg = (
            "🔥 عرض من %s (خصم %d%%)\n%s\n"
            "📦 %s\n"
            "💰 السعر الحالي: %s ج.م\n"
            "🏷 السعر السابق: %s ج.م (توفير %s ج.م)\n"
            "🔗 %s"
        ) % (store, it.discount, tag, it.title[:95], format(it.price, ","), format(it.old, ","),
             format(it.old - it.price, ","), it.link)
        if send_telegram(msg):
            state[key] = {"price": it.price, "ts": time.time()}
            sent += 1
            time.sleep(1.3)
    return sent


def short(s: str, n: int = 90) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def build_summary(reports: List[StoreReport], elapsed: float) -> str:
    lines = ["📊 ملخص الجولة (%.1f دقيقة)" % (elapsed / 60.0), ""]
    total = 0
    any_fail = False
    for r in reports:
        total += r.sent
        if r.status == "ok":
            lines.append("✅ %s: %d منتج | %d عرض | أُرسل %d" % (r.name, r.seen, r.found, r.sent))
        elif r.status == "empty":
            lines.append("⚠ %s: اتفتح بس مفيش أسعار ظاهرة (JS/حماية؟)" % r.name)
            any_fail = True
        else:
            why = short(" | ".join(r.reasons[-2:]) or "مفيش رد", 110)
            lines.append("❌ %s: محجوب -> %s" % (r.name, why))
            any_fail = True
    lines += ["", "إجمالي الصفقات المرسلة: %d" % total]
    if any_fail:
        lines.append("💡 المواقع المحجوبة من IP جيتهاب: ضيف Secret باسم PROXY_URL أو SCRAPERAPI_KEY.")
    return "\n".join(lines)


# =============================================================== التشغيل
def main() -> None:
    t0 = time.time()
    deadline = t0 + MAX_RUNTIME_MIN * 60
    log("--- بدء الرادار الذكي الشامل (V7) ---")
    log("curl_cffi=%s cloudscraper=%s browser=%s proxy=%s"
        % (HAS_CFFI, HAS_CLOUDSCRAPER, USE_BROWSER, bool(PROXY_URL)))
    if SEND_START_MSG:
        send_telegram("🚀 بدأ الفحص التلقائي لكل المتاجر المصرية (خصومات %d%% فأكثر)..." % MIN_DISCOUNT)

    state = load_state()
    fetcher = Fetcher()
    reports: List[StoreReport] = []
    try:
        for cfg in STORES:
            if time.time() > deadline:
                rep = StoreReport(cfg["name"])
                rep.reasons.append("خلص وقت الجولة قبل ما أوصله")
                reports.append(rep)
                continue
            try:
                rep = scan_store(cfg, fetcher, deadline)
                rep.sent = dispatch(cfg["name"], rep.items, state)
            except Exception as e:
                log(traceback.format_exc())
                rep = StoreReport(cfg["name"])
                rep.reasons.append("خطأ داخلي: %s" % type(e).__name__)
            reports.append(rep)
    finally:
        fetcher.close()
        save_state(state)
    commit_state()

    summary = build_summary(reports, time.time() - t0)
    log("--- انتهت الجولة ---\n" + summary)
    send_telegram(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log(tb)
        send_telegram("❌ الرادار وقع بخطأ:\n" + tb[-1500:])
        sys.exit(1)
