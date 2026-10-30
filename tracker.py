import os
import time
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# رابط صفحة عروض اليوم في أمازون مصر
DEALS_URL = "https://www.amazon.eg/deals"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

alerted_asins = set()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": False}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

def parse_number(text):
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def check_deals():
    print("جاري فحص صفحة عروض أمازون...", flush=True)
    try:
        r = requests.get(DEALS_URL, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"فشل الاتصال بصفحة العروض، كود الاستجابة: {r.status_code}")
            return
        
        soup = BeautifulSoup(r.text, "html.parser")
        # البحث عن عروض المنتجات في الصفحة الرئيسية للعروض
        deals = soup.find_all("div", {"data-testid": "grid-deals-container"}) or soup.find_all("div", {"class": "DealGridItem-module__dealItemContent_1vnt0fuGt53pxYkuezpMRV"})
        
        # لو لم يتم العثور على الحاويات بالشكل التقليدي، نبحث عن روابط المنتجات مباشرة
        if not deals:
            deals = soup.find_all("div", class_=re.compile("dealCard", re.I))

        print(f"تم العثور على {len(deals)} عرض حالي في الصفحة.", flush=True)

        for deal in deals[:20]: # فحص أول 20 عرض قوي
            # محاولة استخراج الرابط أو الـ ASIN
            link_elem = deal.find("a", href=True)
            if not link_elem:
                continue
            
            href = link_elem["href"]
            asin_match = re.search(r"/dp/([A-Z0-9]{10})", href)
            if not asin_match:
                asin_match = re.search(r"g/([A-Z0-9]{10})/", href)
            
            if not asin_match:
                continue
                
            asin = asin_match.group(1)
            if asin in alerted_asins:
                continue

            # استخراج عنوان المنتج والخصم إن وجد
            title = link_elem.get_text().strip() or "عرض مميز على أمازون"
            
            # استخراج نسبة الخصم لو متوفرة في النص
            deal_text = deal.get_text()
            discount_match = re.search(r"(\d+)%\s*خصم|خصم\s*(\d+)%", deal_text)
            
            alerted_asins.add(asin)
            msg = (
                🔥 لقطة جديدة من صفحة عروض أمازون مصر!\n\n"
                f"📦 {title[:80]}...\n"
                f"🔗 رابط العرض مباشرة: https://www.amazon.eg/dp/{asin}"
            )
            send_telegram(msg)
            time.sleep(1)

    except Exception as e:
        print(f"Error checking deals: {e}", flush=True)

def main():
    send_telegram("🚀 بدأ رادار صفحة العروض المباشرة لأمازون مصر...")
    
    # فحص العروض لمدة ساعتين (12 جولة كل 10 دقائق)
    for round_num in range(12):
        print(f"--- الجولة رقم {round_num + 1} ---", flush=True)
        check_deals()
        if round_num < 11:
            time.sleep(600) # انتظار 10 دقائق

    send_telegram("✅ انتهت جلسة رصد عروض أمازون.")

if __name__ == "__main__":
    main()
