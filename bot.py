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

def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun == 3: return 10 <= saat <= 23 # Perşembe
    if gun in [4, 5, 6]: return 18 <= saat <= 23 # C-C-P
    return False

# ================================================
# ORAN ANALİZİ (GÖLGE)
# ================================================
def oran_analizi(suanki, acilis):
    if not acilis or acilis == 0: return 0.0, ""
    degisim = (acilis - suanki) / acilis
    if degisim >= 0.15: return 4.5, f"🚨 ANOMALİ: Oran Çöküşü (-%{degisim*100:.0f})"
    if degisim >= 0.10: return 3.0, f"📈 DROP: Sert Düşüş (-%{degisim*100:.0f})"
    return 0.0, ""

# ================================================
# AI ANALİZ MOTORU (RETRY MEKANİZMALI)
# ================================================
async def gemini_analiz(mac_data):
    if not GEMINI_KEY: return "AI Servisi Tanımsız.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""ANALİZ GÖREVİ:
    MAÇ: {mac_data['ev']} {mac_data['skor']} {mac_data['dep']}
    DK: {mac_data['dakika']} | LİG: {mac_data['lig']}
    İSTATİSTİK: Şut:{mac_data['sut']} | Atak:{mac_data['atak']}
    DURUM: {mac_data['detaylar']}
    
    Kritik bir yorum yap ve risk oranını belirle. Yanıtın SADECE şu JSON formatında olsun:
    {{"yorum": "kısa ve öz yorumun", "kasa": 1.5}}"""

    for i in range(3): # 3 kere deneme yapacak
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data['candidates'][0]['content']['parts'][0]['text']
                        res = json.loads(text[text.find('{'):text.rfind('}')+1])
                        return res.get('yorum'), res.get('kasa', 1.5)
            await asyncio.sleep(2) # Hata olursa 2 sn bekle ve tekrar dene
        except: continue
    return "AI şu an meşgul, istatistikler golü işaret ediyor.", 1.5

# ================================================
# ANA DÖNGÜ
# ================================================
async def maclari_tara(bot):
    logger.info("Bot uyandı, radarlar açıldı.")
    while True:
        if not aktif_mi():
            await asyncio.sleep(60)
            continue
        try:
            async with aiohttp.ClientSession() as session:
                h = {"x-apisports-key": APISPORTS_KEY, "x-apisports-host": "v3.football.api-sports.io"}
                async with session.get("https://v3.football.api-sports.io/fixtures?live=all", headers=h) as resp:
                    data = await resp.json()
                    for f in data.get('response', []):
                        m_id = str(f['fixture']['id'])
                        if m_id in bildirim_gonderilen: continue
                        
                        # Veri Toplama
                        dk = f['fixture']['status']['elapsed']
                        skor_ev = f['goals']['home'] or 0
                        skor_dep = f['goals']['away'] or 0
                        
                        f_stats = f.get('statistics', [])
                        s_dict = {s['type']: s['value'] for g in f_stats for s in g.get('statistics', [])}
                        sut = int(s_dict.get('Shots on Target', 0) or 0)
                        
                        # Puanlama
                        puan, detaylar = 4.0, []
                        o_puan, o_msg = oran_analizi(1.65, 2.00) # Dinamikleştirilebilir
                        puan += o_puan
                        if o_msg: detaylar.append(o_msg)
                        puan += (sut * 0.7)
                        
                        if puan >= MIN_PUAN and 5 < dk < 88:
                            mac_info = {
                                'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                                'skor': f"{skor_ev}-{skor_dep}", 'dakika': dk, 'lig': f['league']['name'],
                                'sut': sut, 'atak': s_dict.get('Dangerous Attacks', 0), 'detaylar': detaylar
                            }
                            
                            ai_yorum, ai_kasa = await gemini_analiz(mac_info)
                            
                            bildirim = (
                                f"⚽️ {mac_info['ev']} {mac_info['skor']} {mac_info['dep']}\n"
                                f"🏆 {mac_info['lig']} | DK: {dk}'\n"
                                f"──────────────────\n"
                                f"💡 TAHMİN: SIRADAKİ GOL / ÜST\n"
                                f"📈 PUAN: {puan} | KASA: %{ai_kasa}\n"
                                f"──────────────────\n"
                                f"🧠 AI ANALİZİ:\n{ai_yorum}\n"
                                f"──────────────────\n"
                                f"📝 NOT: {', '.join(detaylar)}"
                            )
                            await bot.send_message(CHAT_ID, bildirim)
                            bildirim_gonderilen[m_id] = True
                            await asyncio.sleep(4)
        except Exception as e: logger.error(f"Hata: {e}")
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
