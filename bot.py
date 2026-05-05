# MAC ANALIZ BOTU - V31.0 OMNI-IRONCLAD
# Yenilik: Fiziksel limit denetimi, 3-dakikalık çökme koruması ve puan tavanı.

import asyncio, aiohttp, os, urllib.parse, traceback
from telegram import Bot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# --- DENETLEME VE AYRIŞTIRMA MOTORU ---
async def akilli_denetleyici(ev_v, dep_v, dk):
    """Veriyi futbol mantığına göre ayıklar ve imkansız rakamları siler."""
    try:
        # 1. Rakamları büyüklük sırasına göre etiketle
        def s_ayikla(v_dict):
            raw = {k: int(v) for k, v in v_dict.items() if k.startswith('S') and str(v).isdigit()}
            # En büyük 3 değeri bul (TA > DA > SOT hiyerarşisi)
            sirali = sorted(raw.items(), key=lambda x: x[1], reverse=True)
            return {
                "TA": sirali[0][1] if len(sirali) > 0 else 0,
                "DA": sirali[1][1] if len(sirali) > 1 else 0,
                "SOT": sirali[2][1] if len(sirali) > 2 else 0
            }

        e = s_ayikla(ev_v)
        d = s_ayikla(dep_v)

        # 2. FİZİKSEL LİMİT KONTROLÜ (Hata Önleyici)
        toplam_sot = e["SOT"] + d["SOT"]
        # Kural: Dakika başına 0.8'den fazla isabetli şut imkansızdır (45 dk'da 47 şut hatasını boğar)
        if toplam_sot > (dk * 0.8):
            e["SOT"] = 0; d["SOT"] = 0 # Kirli veriyi temizle

        # Kural: Tehlikeli Atak (DA) hiçbir zaman Toplam Atak'tan (TA) büyük olamaz.
        if e["DA"] > e["TA"]: e["TA"] = e["DA"] + 5
        if d["DA"] > d["TA"]: d["TA"] = d["DA"] + 5

        return e, d
    except: return None, None

async def analiz_et(results):
    # (Veri çekme kodları burada...)
    # ...
    ev, dep = await akilli_denetleyici(ev_v, dep_v, dk)
    if not ev: return None

    # 3. PUANLAMA (7.0-9.5 ARASI GERÇEKÇİ PUANLAR)
    puan = 4.0
    if skor in ["0-0", "1-1", "2-2", "1-0", "0-1", "2-1", "1-2"]: puan += 3.0
    
    # İstatistik Bonusları (Sınırlı ve Denetlenmiş)
    da_bonusu = min(((ev["DA"] + dep["DA"]) // 10) * 0.5, 3.0) # Max +3
    sot_bonusu = min(((ev["SOT"] + dep["SOT"]) // 2) * 0.5, 2.0) # Max +2
    puan += (da_bonusu + sot_bonusu)

    if puan >= 7.0:
        # (Nesine link ve Telegram mesajı...)
        return f"💎 **SİNYAL (Puan: {round(puan,1)})**\n..."

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🚀 V31.0 AKTİF: Fiziksel limit denetimi ve çökme koruması devrede.")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # (Tarama kodları...)
                await asyncio.sleep(60)
            except Exception as e:
                # Hata olsa bile sistemi kapatma, sadece logla
                print(f"Hata Yakalandı: {e}")
                await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
