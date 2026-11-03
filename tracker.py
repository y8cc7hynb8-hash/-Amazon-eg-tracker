#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, re, random, html as htmllib, subprocess, urllib.parse, traceback, logging
from collections import defaultdict

log = logging.getLogger("radar")
# ... (باقي إعدادات التسجيل والاستيراد كما هي) ...

# ... (الإعدادات والمتغيرات العامة كما هي) ...

# ================= أدوات مساعدة محسنة =================

def extract_json_ld_price(html_text: str) -> int:
    """دالة جديدة: استخراج السعر من بيانات JSON-LD (الأكثر دقة وثباتاً)."""
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list): data = data[0]
            if data.get("@type") == "Product":
                offers = data.get("offers", {})
                if isinstance(offers, list): offers = offers[0]
                price = offers.get("price")
                if price:
                    return int(float(price))
        except Exception:
            continue
    return 0

def validate_deal(d: dict) -> bool:
    """تحقق نهائي صارم يمنع الخصومات الوهمية بناءً على السعر الفعلي."""
    if not d.get("title") or not d.get("link"):
        return False
    if d["old"] <= d["price"] or d["price"] < MIN_PRODUCT_PRICE:
        return False
    # حساب الخصم الحقيقي وليس النص المعلن
    computed = round((d["old"] - d["price"]) / d["old"] * 100)
    d["discount"] = computed
    return MIN_DISCOUNT <= computed <= MAX_REALISTIC_DISC

# ================= دوال الاستخراج المحدثة =================

def extract_amazon_deals(html_text: str) -> list:
    """أمازون: الاعتماد على المحددات الدقيقة للأسعار."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for div in soup.select('div[data-component-type="s-search-result"]'):
        asin = (div.get("data-asin") or "").strip()
        if not asin: continue
        h2 = div.find("h2")
        title = clean_title(h2.get_text(" ", strip=True)) if h2 else ""
        
        cur_el = div.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
        old_el = div.select_one("span.a-text-price span.a-offscreen, span[data-a-strike='true'] span.a-offscreen")
        
        cur = parse_price(cur_el.get_text()) if cur_el else 0
        old = parse_price(old_el.get_text()) if old_el else 0
        
        if title and old > cur >= MIN_PRODUCT_PRICE:
            deals.append({"title": title, "price": cur, "old": old, "link": f"https://www.amazon.eg/dp/{asin}"})
    return deals

def extract_noon_deals(html_text: str) -> list:
    """نون: استخدام JSON-LD أولاً، ثم المحددات الاحتياطية."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/p/" not in href or "/egypt-ar/" not in href: continue
        
        # محاولة استخراج السعر من JSON-LD أولاً
        cur = extract_json_ld_price(str(a))
        old = 0 # السعر القديم قد لا يكون موجوداً في JSON-LD
        
        if cur == 0:
            # Fallback لمحددات CSS
            p_now = a.find(attrs={"data-qa": re.compile(r"selling-price|price-now", re.I)}) or a.find(class_=re.compile(r"priceNow|amount", re.I))
            cur = parse_price(p_now.get_text()) if p_now else 0
        
        title_el = a.find(attrs={"data-qa": re.compile(r"product-name|title", re.I)}) or a.find(["h2", "h3"])
        title = clean_title(title_el.get_text(" ", strip=True) if title_el else a.get("title") or "")
        
        if title and cur >= MIN_PRODUCT_PRICE:
            # نون لا تعرض السعر القديم دائماً، لذا نعتمد على النص المئوي كـ fallback
            m_pct = _BADGE_PCT_RE.search(a.get_text(" ", strip=True).translate(_TRANS))
            if m_pct:
                pct = int(m_pct.group(1))
                if MIN_DISCOUNT <= pct <= MAX_REALISTIC_DISC:
                    old = int(round(cur / (1.0 - pct / 100.0)))
            if old > cur:
                deals.append({"title": title, "price": cur, "old": old, "link": urllib.parse.urljoin("https://www.noon.com", href)})
    return deals

def extract_general_deals(html_text: str, base_domain: str) -> list:
    """بقية المتاجر: محددات أكثر تحديداً + JSON-LD."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.find_all(["li", "div", "article"], class_=re.compile(r"product-item|product-card|productCard|prd|product-layout|plp-product", re.I))
    
    for card in cards:
        # حذف عناصر التقسيط
        for inst in card.find_all(class_=re.compile(r"installment|minicash|monthly|tabby|tamara", re.I)):
            inst.decompose()
            
        a = card.find("a", href=True)
        if not a: continue
        
        # 1. محاولة JSON-LD داخل الكارت
        cur = extract_json_ld_price(str(card))
        if cur == 0:
            # 2. محددات CSS الأكثر تحديداً
            sp_el = card.find(attrs={"data-price-type": "finalPrice"}) or card.select_one("[class*='price-now' i], [class*='sale-price' i], [class*='current-price' i]") or card.find("ins")
            cur = parse_price(sp_el.get_text()) if sp_el else 0
            
        old_el = card.find(attrs={"data-price-type": "oldPrice"}) or card.select_one("[class*='old-price' i], [class*='regular-price' i], [class*='was-price' i]") or card.find(["del", "s", "strike"])
        old = parse_price(old_el.get_text()) if old_el else 0
        
        title_el = card.find(["h2", "h3", "h4", "strong"], class_=re.compile(r"name|title|link", re.I)) or a
        title = clean_title(title_el.get_text(" ", strip=True) or a.get("title") or "")
        
        if title and old > cur >= MIN_PRODUCT_PRICE:
            deals.append({"title": title, "price": cur, "old": old, "link": urllib.parse.urljoin(base_domain, a["href"].strip())})
    return deals

def extract_jumia_deals(html_text: str) -> list:
    """جوميا: استخراج محسن باستخدام JSON-LD."""
    deals = []
    soup = BeautifulSoup(html_text, "html.parser")
    # جوميا تستخدم كارت article مع كلاس prd
    for card in soup.select("article.prd, .sku"):
        # 1. محاولة JSON-LD
        cur = extract_json_ld_price(str(card))
        if cur == 0:
            # 2. Fallback: السعر في كلاس .prc
            price_el = card.select_one(".prc")
            cur = parse_price(price_el.get_text()) if price_el else 0
            
        old_el = card.select_one(".old")
        old = parse_price(old_el.get_text()) if old_el else 0
        
        title_el = card.select_one(".name") or card.find("h3")
        title = clean_title(title_el.get_text(" ", strip=True)) if title_el else ""
        
        a = card.find("a", href=True)
        if title and a and old > cur >= MIN_PRODUCT_PRICE:
            deals.append({"title": title, "price": cur, "old": old, "link": urllib.parse.urljoin("https://www.jumia.com.eg", a["href"])})
    return deals

# ================= تحديث خريطة المتاجر =================
STORES = [
    # ... (أمازون، نون، دريم 2000، 2B، رنين، كنز، راية، بي تك كما هي) ...
    {"id": "jumia", "name": "جوميا مصر (Jumia)", "domain": "https://www.jumia.com.eg", "type": "jumia",
     "urls": ["https://www.jumia.com.eg/ar/flash-sales/", "https://www.jumia.com.eg/ar/phones-tablets/?sort=discount"]},
    # ... (دبي فون كما هي) ...
]

# ================= تحديث دالة main =================
def main():
    # ...
    for st in STORES:
        # ...
        for u in st["urls"]:
            html_txt = smart_fetch(u, sname)
            if html_txt:
                # استخدام الدوال المحدثة
                if stype == "amazon": extracted = extract_amazon_deals(html_txt)
                elif stype == "noon": extracted = extract_noon_deals(html_txt)
                elif stype == "dream2000": extracted = extract_dream2000_deals(html_txt) # يمكن تحسينها بنفس الطريقة
                elif stype == "jumia": extracted = extract_jumia_deals(html_txt)
                else: extracted = extract_general_deals(html_txt, st["domain"])
                # ...
    # ...
    # استخدام validate_deal الجديدة
    for d in ordered:
        if sent_store >= MAX_DEALS_PER_STORE: break
        if not validate_deal(d): continue # التحقق من صحة العرض
        # ...
