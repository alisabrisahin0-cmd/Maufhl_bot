# MAC ANALIZ BOTU - V13.6-PULSE
# Hedef: Botun yaşadığını her 5 dakikada bir Telegram'a raporlamak.

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import urllib.parse

# AI Modülü Güvenli Kontrol
try:
    from google import genai
    HAS_GENAI = True
except:
    HAS_GENAI = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
key_index = 0

async def get_ai_commentary(ev, dep, dk, skor):
    global key_index
    if not HAS_GENAI: return "AI Modülü yüklenemedi."
    try:
        current_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
        key_index += 1
        client = genai.Client(api_key=current_key)
        response = client.models.generate_content(model="gemini-2.0-flash", contents=f"{ev} {skor} {dk}. dk. Kısa yorum yap.")
        return response.text
    except: return "AI meşgul."

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    # Başlangıç Mesajı
    await bot.send_message(chat_id=CHAT_ID, text="💓 V13.6 PULSE BAŞLADI: Her taramada size rapor vereceğim.")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay?token={BETSAPI_TOKEN}") as r:
                    data = await r.json()
                    res = data.get('results', [])
                
                # HAYATTA KALMA RAPORU
                await bot.send_message(chat_id=CHAT_ID, text=f"📡 Tarama Tamamlandı: Şu an bültende {len(res)} aktif maç var.")

                for m in res:
                    m_id = str(m.get('FI') or m.get('ID') or m)
                    if m_id in bildirim_gonderilen: continue
                    
                    # Zaman Kontrolü
                    timer = m.get('timer', {})
                    dk = int(timer.get('tm', 0))
                    
                    if 20 <= dk <= 85:
                        ss = m.get('ss', '0-0')
                        ev = m.get('home', {}).get('name', 'Ev')
                        dep = m.get('away', {}).get('name', 'Dep')
                        
                        # Basit Puanlama (4.0 barajı için sadece zaman yeterli)
                        ai_y = await get_ai_commentary(ev, dep, dk, ss)
                        msg = f"💎 SİNYAL\n⚽ {ev} {ss} {dep}\n⏱ Dk: {dk}\n🤖 AI: {ai_y}"
                        await bot.send_message(chat_id=CHAT_ID, text=msg)
                        bildirim_gonderilen[m_id] = True
            except Exception as e:
                logger.error(f"Hata: {e}")
            await asyncio.sleep(300) # 5 dakikada bir tara ve rapor ver

if __name__ == "__main__":
    asyncio.run(ana_dongu())
