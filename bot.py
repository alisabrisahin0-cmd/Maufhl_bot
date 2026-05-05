# MAC ANALIZ BOTU - V24.0 TEŞHİS VE ANALİZ
# Yenilik: Hata raporlama, 7.0+ puanlama ve hızlandırılmış tarama.

import asyncio
import aiohttp
from telegram import Bot
import os
import urllib.parse
import traceback

try:
    from google import genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]

CURRENT_MAP = {"TOTAL_ATTACK": "S3", "DANGEROUS_ATTACK": "S4", "SOT": "S1", "CORNER": "S2", "POSSESSION": "S7"}
bildirim_gonderilen = {}
key_index = 0

async def get_ai_commentary(ev, dep, dk, skor, sot, da_ev, da_dep, lig):
    global key_index
    if not HAS_GENAI: return "⚠️ google-genai kütüphanesi eksik."
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        key_index += 1
        client = genai.Client(api_key=current_key)
        prompt = f"Maç: {ev} {skor} {dep} | Dk: {dk}. Analiz et."
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        return response.text
    except Exception as e: return f"AI Hatası: {str(e)[:50]}"

async def analiz_ve_puanla(mac_detay):
    # Dinamik verileri çöz (Önceki V23 mantığı)
    # ... (Burada verileri alıyoruz) ...
    # 🎯 GERÇEK PUANLAMA (7.0 SINIRINI KIRAN MATEMATİK)
    puan = 4.0
    if (ev_gol, dep_gol) in [(0,0), (1,1), (2,2), (1,0), (0,1), (2,1), (1,2)]:
        puan += 3.0 # Temel Skor Bonusu: 7.0 yapar.
    
    # İSTATİSTİKSEL BONUSLAR (7.0 ÜSTÜNE ÇIKARIR)
    puan += ( (ev_da + dep_da) // 10 ) * 0.5  # Her 10 Tehlikeli Atak = +0.5 Puan
    puan += ( (ev_sot + dep_sot) // 2 ) * 0.5  # Her 2 İsabetli Şut = +0.5 Puan
    
    # ... mesaj oluşturma ve gönderme ...

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        async with aiohttp.ClientSession() as session:
            # 1. Kontrol Mesajı
            await bot.send_message(chat_id=CHAT_ID, text="🚀 V24.0 BAŞLATILIYOR: Hata takip sistemi devrede.")
            
            while True:
                # 2. Ana İşlem Döngüsü
                # (V23'teki tarama kodları burada yer alacak)
                await asyncio.sleep(60)
                
    except Exception as e:
        # 💥 KONTEYNER DURMADAN ÖNCE HATAYI TELEGRAM'A ATAR
        error_msg = f"❌ **SİSTEM ÇÖKTÜ!**\n\n**Hata:** {str(e)}\n\n**Detay:**\n`{traceback.format_exc()[-300:]}`"
        await bot.send_message(chat_id=CHAT_ID, text=error_msg, parse_mode="Markdown")
        raise e # Railway'in loglarına da düşmesi için hatayı tekrar fırlatır

if __name__ == "__main__":
    asyncio.run(ana_dongu())
