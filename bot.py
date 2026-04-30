import asyncio
import aiohttp
from aiohttp import web
from telegram import Bot
import logging
import os
import json

# ================================================
# AYARLAR VE API HAVUZU
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")

# 3 ANAHTARI BURAYA LİSTE OLARAK EKLEDİK
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", "")
]

# TEST İÇİN BARAJI GEÇİCİ OLARAK 5'E DÜŞÜRDÜK (Telegram'a mesaj düşsün diye)
MIN_PUAN = 5 
current_key_index = 0 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}

# ================================================
# AI ANALİZ MOTORU (HAVUZ SİSTEMİ)
# ================================================
async def gemini_analiz_havuzu(mac_data):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI Anahtarı bulunamadı.", 1.5

    prompt = f"""MAÇ: {mac_data['ev']} {mac_data['skor']} {mac_data['dep']}
    DK: {mac_data['dakika']} | LİG: {mac_data['lig']}
    Şut:{mac_data['sut']} | Atak:{mac_data['atak']}
    Kısa yorum yap. JSON: {{"yorum": "...", "kasa": 1.5}}"""

    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={active_key}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data['candidates'][0]['content']['parts'][0]['text']
                        res = json.loads(text[text.find('{'):text.rfind('}')+1])
                        return res.get('yorum', 'Sistem onayladı.'), res.get('kasa', 1.5)
                    elif resp.status == 429: 
                        logger.warning(f"AI Key {current_key_index+1} limiti doldu, havuzdaki diğer key'e geçiliyor...")
                        current_key_index = (current_key_index + 1) % len(valid_keys)
        except Exception as e:
            current_key_index = (current_key_index + 1) % len(valid_keys)
        await asyncio.sleep(1) 
    
    return "Tüm AI kanalları denendi, yanıt alınamadı.", 1.5

# ================================================
# PUANLAMA MANTIĞI
# ================================================
def sinyal_hesapla(mac_stats):
    puan = 2.0
    sut = int(mac_stats.get('Shots on Target', 0) or 0)
    atak = int(mac_stats.get('Dangerous Attacks', 0) or 0)
    
    puan += (sut * 1.5)
    if atak > 30: puan += 1.5
    puan += 3.0 # Standart oran baskısı
    
    return round(puan, 1)

# ================================================
# ANA DÖNGÜ (LOGLAMALI)
# ================================================
async def maclari_tara(bot):
    logger.info("🟢 BOT BAŞARIYLA UYANDI! 3 AI Motoru Aktif. Telegram test ediliyor...")
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                h = {"x-apisports-key": APISPORTS_KEY, "x-apisports-host": "v3.football.api-sports.io"}
                async with session.get("https://v3.football.api-sports.io/fixtures?live=all", headers=h) as resp:
                    data = await resp.json()
                    maclar = data.get('response', [])
                    
                    logger.info(f"🔍 API'den canlı veri çekildi. Şu an {len(maclar)} maç oynanıyor. Tarama yapılıyor...")
                    
                    for f in maclar:
                        m_id = str(f['fixture']['id'])
                        if m_id in bildirim_gonderilen: continue
                        
                        dk = f['fixture']['status']['elapsed']
                        if not (5 < dk < 85): continue

                        f_stats = f.get('statistics', [])
                        s_dict = {s['type']: s['value'] for g in f_stats for s in g.get('statistics', [])}
                        
                        puan = sinyal_hesapla(s_dict)
                        
                        if puan >= MIN_PUAN:
                            mac_info = {
                                'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                                'skor': f"{f['goals']['home']}-{f['goals']['away']}", 'dakika': dk,
                                'lig': f['league']['name'], 'sut': s_dict.get('Shots on Target', 0),
                                'atak': s_dict.get('Dangerous Attacks', 0)
                            }
                            
                            ai_y, ai_k = await gemini_analiz_havuzu(mac_info)
                            
                            msg = (
                                f"⚽️ {mac_info['ev']} {mac_info['skor']} {mac_info['dep']}\n"
                                f"🏆 {mac_info['lig']} | DK: {dk}'\n"
                                f"──────────────────\n"
                                f"🎯 PUAN: {puan} | KASA: %{ai_k}\n"
                                f"🧠 AI: {ai_y}"
                            )
                            await bot.send_message(CHAT_ID, msg)
                            logger.info(f"✅ Telegram'a mesaj başarıyla gönderildi: {mac_info['ev']} maçı.")
                            bildirim_gonderilen[m_id] = True
                            await asyncio.sleep(4)
        except Exception as e: logger.error(f"Hata Döngüsü: {e}")
        
        logger.info("⏳ 10 dakika bekleniyor...")
        await asyncio.sleep(600)

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot Aktif"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080))).start()
    await maclari_tara(bot)

if __name__ == "__main__":
    asyncio.run(main())
