import asyncio
import aiohttp
from aiohttp import web
from telegram import Bot
import logging
import os
from datetime import datetime
import json

# ================================================
# AYARLAR VE DEĞİŞKENLER
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")

# 3 ANAHTARLI AI HAVUZU (Sadece bu eklendi)
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", "")
]

MIN_PUAN = float(os.getenv("MIN_PUAN", "8.0")) # Kendi standart barajın
current_key_index = 0 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
mac_gecmisi = {} # Eski sistemdeki ivmeyi ölçmek için hafıza

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
# AI ANALİZ MOTORU (HAVUZ SİSTEMİ)
# ================================================
async def gemini_analiz_havuzu(mac_data, rapor):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI Servisi kapalı.", 1.5

    prompt = f"""MAÇ: {mac_data['ev']} {mac_data['skor']} {mac_data['dep']}
    DK: {mac_data['dakika']} | LİG: {mac_data['lig']}
    ŞUT: {mac_data['sut']} | ATAK: {mac_data['atak']}
    DURUM: {rapor}
    Kısa ve net bir gol yorumu yap. JSON: {{"yorum": "...", "kasa": 1.5}}"""

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
                        return res.get('yorum', 'İstatistikler baskıyı doğruluyor.'), res.get('kasa', 1.5)
                    elif resp.status == 429: 
                        current_key_index = (current_key_index + 1) % len(valid_keys)
        except Exception:
            current_key_index = (current_key_index + 1) % len(valid_keys)
        await asyncio.sleep(1) 
    
    return "Momentum yüksek, AI havuzu yoğun.", 1.5

# ================================================
# ORİJİNAL İSTATİSTİK VE İVME HESAPLAMA (ESKİ SİSTEM)
# ================================================
def sinyal_hesapla(m_id, dk, sut, atak):
    puan = 0.0
    detaylar = []
    
    # Geçmiş veriyi al (İvme ölçümü için)
    gecmis = mac_gecmisi.get(m_id, {'atak': atak, 'sut': sut})
    delta_atak = max(0, atak - gecmis['atak'])
    delta_sut = max(0, sut - gecmis['sut'])
    
    # Hafızayı güncelle
    mac_gecmisi[m_id] = {'atak': atak, 'sut': sut}
    
    # Eğer veri yetersizse direkt ele
    if delta_atak < 5 and delta_sut < 1 and dk > 20:
        return 0, ["Yetersiz Veri"]

    puan += 4.0 # Kapı geçiş (Taban) puanın
    puan += (sut * 0.5) # Şut başına 0.5
    
    if delta_atak >= 8:
        puan += 1.5
        detaylar.append(f"🔥 Sert Atak İvmesi (+{delta_atak})")
    
    if delta_sut >= 2:
        puan += 1.0
        detaylar.append("🎯 Üst Üste Şutlar")
        
    if 65 <= dk <= 75:
        puan += 3.5
        detaylar.append("⏱ POWER WINDOW (65-75')")

    return round(puan, 1), detaylar

# ================================================
# ANA DÖNGÜ
# ================================================
async def maclari_tara(bot):
    try:
        await bot.send_message(CHAT_ID, "🟢 ASIL SİSTEM AKTİF! Klasik istatistik takibi ve 3 AI Motoru devrede.")
    except Exception: pass

    while True:
        if not aktif_mi():
            await asyncio.sleep(60)
            continue
            
        try:
            async with aiohttp.ClientSession() as session:
                h = {"x-apisports-key": APISPORTS_KEY, "x-apisports-host": "v3.football.api-sports.io"}
                async with session.get("https://v3.football.api-sports.io/fixtures?live=all", headers=h) as resp:
                    
                    if resp.status == 200:
                        data = await resp.json()
                        maclar = data.get('response', [])
                        
                        for f in maclar:
                            m_id = str(f['fixture']['id'])
                            if m_id in bildirim_gonderilen: continue
                            
                            dk = f['fixture']['status']['elapsed']
                            if not (5 < dk < 88): continue

                            f_stats = f.get('statistics', [])
                            s_dict = {s['type']: s['value'] for g in f_stats for s in g.get('statistics', [])}
                            
                            sut = int(s_dict.get('Shots on Target', 0) or 0)
                            atak = int(s_dict.get('Dangerous Attacks', 0) or 0)
                            
                            # Senin Orijinal Hesaplama Fonksiyonun
                            puan, notlar = sinyal_hesapla(m_id, dk, sut, atak)
                            
                            if puan >= MIN_PUAN and "Yetersiz Veri" not in notlar:
                                mac_info = {
                                    'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                                    'skor': f"{f['goals']['home'] or 0}-{f['goals']['away'] or 0}", 
                                    'dakika': dk, 'lig': f['league']['name'], 
                                    'sut': sut, 'atak': atak
                                }
                                
                                ai_y, ai_k = await gemini_analiz_havuzu(mac_info, ", ".join(notlar))
                                
                                msg = (
                                    f"⚽️ {mac_info['ev']} {mac_info['skor']} {mac_info['dep']}\n"
                                    f"🏆 {mac_info['lig']} | DK: {dk}'\n"
                                    f"──────────────────\n"
                                    f"💡 TAHMİN: SIRADAKİ GOL\n"
                                    f"🎯 PUAN: {puan} | KASA: %{ai_k}\n"
                                    f"──────────────────\n"
                                    f"🧠 AI: {ai_y}\n"
                                    f"📝 {', '.join(notlar) if notlar else 'Düzenli Baskı'}"
                                )
                                await bot.send_message(CHAT_ID, msg)
                                bildirim_gonderilen[m_id] = True
                                await asyncio.sleep(4)
        except Exception as e: logger.error(f"Hata: {e}")
        
        await asyncio.sleep(600)

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Asıl Sistem Aktif"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 8080))).start()
    await maclari_tara(bot)

if __name__ == "__main__":
    asyncio.run(main())
