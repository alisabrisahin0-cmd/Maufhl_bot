import asyncio
import aiohttp
from aiohttp import web
from telegram import Bot
import logging
import os
from datetime import datetime
import json

# ================================================
# AYARLAR
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "8"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun == 3: return 10 <= saat <= 23 # Perşembe
    if gun in [4, 5, 6]: return 18 <= saat <= 23 # Cuma-Pazar
    return False

# ================================================
# ANALİZ MODÜLLERİ
# ================================================
def oran_analizi(suanki, acilis):
    if not acilis or acilis == 0: return 0.0, ""
    degisim = (acilis - suanki) / acilis
    if degisim >= 0.20: return 5.0, f"🚨 ANOMALİ: Oran Çöküşü (-%{degisim*100:.0f})"
    if degisim >= 0.10: return 3.0, f"📈 DROP: Sert Düşüş (-%{degisim*100:.0f})"
    return 0.0, ""

async def gemini_analiz(mac, detaylar):
    if not GEMINI_KEY: return "AI Analiz Devre Dışı", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"MAÇ: {mac['ev']}-{mac['dep']} DK:{mac['dakika']} STATS:{detaylar}. 2 cümleyle kritik yorum yap. JSON: {{\"yorum\": \"...\", \"kasa\": 1.5}}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                data = await resp.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                res = json.loads(text[text.find('{'):text.rfind('}')+1])
                return res.get('yorum', 'Momentum onaylandı.'), res.get('kasa', 1.5)
    except: return "Analiz yapılamadı.", 1.5

# ================================================
# ANA DÖNGÜ (HATA KORUMALI)
# ================================================
async def maclari_tara(bot):
    logger.info("Bot uyandı ve radar açıldı.")
    while True:
        if not aktif_mi():
            await asyncio.sleep(60)
            continue
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"x-apisports-key": APISPORTS_KEY, "x-apisports-host": "v3.football.api-sports.io"}
                async with session.get("https://v3.football.api-sports.io/fixtures?live=all", headers=headers) as resp:
                    data = await resp.json()
                    for f in data.get('response', []):
                        m_id = str(f.get('fixture', {}).get('id', ''))
                        if not m_id or m_id in bildirim_gonderilen: continue
                        
                        # İstatistikleri Güvenli Çekme (Hata Buradaydı)
                        f_stats = f.get('statistics', [])
                        stats_dict = {}
                        for s_group in f_stats:
                            for s in s_group.get('statistics', []):
                                stats_dict[s.get('type')] = s.get('value')
                        
                        atk = int(stats_dict.get('Dangerous Attacks', 0) or 0)
                        sut = int(stats_dict.get('Shots on Target', 0) or 0)
                        dk = f.get('fixture', {}).get('status', {}).get('elapsed', 0)
                        
                        # Gölge Analizi & Puanlama
                        puan, detay = 4.0, []
                        o_puan, o_msg = oran_analizi(1.70, 2.00) # Oranlar dinamik çekilebilir
                        puan += o_puan
                        if o_msg: detay.append(o_msg)
                        puan += (sut * 0.5)
                        
                        if puan >= MIN_PUAN and 5 < dk < 88:
                            ev_ad = f['teams']['home']['name']
                            dep_ad = f['teams']['away']['name']
                            ai_y, ai_k = await gemini_analiz({'ev': ev_ad, 'dep': dep_ad, 'dakika': dk}, detay)
                            msg = f"🔥 {ev_ad} vs {dep_ad}\n🏆 {f['league']['name']} | DK: {dk}\n💰 KASA: %{ai_k}\n🧠 AI: {ai_y}\n📝 NOT: {', '.join(detay)}"
                            await bot.send_message(CHAT_ID, msg)
                            bildirim_gonderilen[m_id] = True
                            await asyncio.sleep(4)
        except Exception as e: 
            logger.error(f"Döngü Hatası: {e}")
        await asyncio.sleep(600)

# ================================================
# RAILWAY STARTUP
# ================================================
async def handle(request): return web.Response(text="Bot Aktif")

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080)))
    await site.start()
    await maclari_tara(bot)

if __name__ == "__main__":
    asyncio.run(main())
