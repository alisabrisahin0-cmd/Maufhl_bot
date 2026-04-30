import asyncio
import aiohttp
from aiohttp import web
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime
import json

# ================================================
# ÇEVRE DEĞİŞKENLERİ
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "8")) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
mac_gecmisi = {} 
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# ZAMAN YÖNETİMİ (PERŞEMBE: 10-00 | C-C-P: 18-00)
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday() # 3: Perşembe, 4: Cuma, 5: Cts, 6: Pazar
    
    if gun == 3: # PERŞEMBE
        return 10 <= saat <= 23
    elif gun in [4, 5, 6]: # CUMA, CUMARTESİ, PAZAR
        return 18 <= saat <= 23
    return False

# ================================================
# ORAN KIRILMASI (ODD DROP) MODÜLÜ
# ================================================
def oran_analizi(suanki_oran, acilis_orani):
    if not acilis_orani or acilis_orani == 0 or suanki_oran == 0: 
        return 0.0, ""
    
    # Oran düşüş yüzdesi hesaplama (Örn: 2.00 -> 1.60 = %20 Drop)
    degisim = (acilis_orani - suanki_oran) / acilis_orani
    
    if degisim >= 0.20:
        return 5.0, f"🚨 ANOMALİ: Oranlarda sert çöküş! (-%{degisim*100:.0f})"
    elif degisim >= 0.10:
        return 3.0, f"📈 DROP: Piyasada sert düşüş, baskı yüksek! (-%{degisim*100:.0f})"
    elif degisim >= 0.05:
        return 1.5, f"🔍 TREND: Oranlarda düşüş eğilimi (-%{degisim*100:.0f})"
    
    return 0.0, ""

# ================================================
# SİNYAL MOTORU (HFT & GÖLGE ANALİZİ)
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    
    puan = 0.0
    detay = []
    
    # 1. ORAN ANALİZİ (GÖLGE TAKİBİ)
    # Varsayılan değerler üzerinden oran analizi yapılır
    suanki = mac.get('suanki_ah_oran', 1.80)
    acilis = mac.get('acilis_ah_oran', 2.00)
    oran_puan, oran_mesaj = oran_analizi(suanki, acilis)
    puan += oran_puan
    if oran_mesaj: detay.append(oran_mesaj)

    # 2. İVME VE İSTATİSTİK
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # Veri gelmiyorsa ama oranlar çökmüşse (Gölge Analizi) kapıdan geçebilir
    if delta_atak < 8 and delta_sut < 1 and dakika > 20 and oran_puan < 3.0:
        return 0, ["HARD LOCK: Veri yetersiz ve oran baskısı yok."], "REJECTED", False

    puan += 4.0 # Kapı geçiş puanı
    puan += (suanki_sut * 0.5)
    
    if 65 <= dakika <= 75:
        puan += 3.5
        detay.append("🔥 POWER WINDOW (65-75') +3.5")

    return round(puan, 1), detay, "MOMENTUM_TAKIBI", True

# ================================================
# GEMİNİ AI - SEZGİ MOTORU
# ================================================
async def gemini_analiz(mac, puan, detay_listesi):
    if not GEMINI_KEY: return "AI analiz devre dışı.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    sistem_raporu = " | ".join(detay_listesi)
    
    prompt = f"""ANALİZ GÖREVİ:
    MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | DAKİKA: {mac['dakika']}
    İSTATİSTİKLER: Şut {mac['shots_on_target_ev']}-{mac['shots_on_target_dep']} | Atak {mac['dangerous_attacks_ev']}-{mac['dangerous_attacks_dep']}
    ALGORİTMA NOTLARI: {sistem_raporu}

    Sayıların göremediği 'Görünmez Gerçeği' 2 cümlede söyle. JSON formatında dön: {{"yorum": "...", "kasa": 1.5}}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    res = json.loads(text[text.find('{'):text.rfind('}')+1])
                    return res.get('yorum', 'Momentum onaylandı.'), res.get('kasa', 1.5)
    except: return "Analiz yapılamadı.", 1.5

# ================================================
# ANA DÖNGÜ VE VERİ ÇEKME (ÖZETLİ)
# ================================================
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Bot uyandı ve radar açıldı.")
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(60)
                continue

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for f in data.get('response', []):
                            fixture = f['fixture']
                            teams = f['teams']
                            stats = f['statistics']
                            
                            mac = {
                                'id': str(fixture['id']),
                                'ev': teams['home']['name'],
                                'dep': teams['away']['name'],
                                'dakika': fixture['status']['elapsed'],
                                'ev_gol': f['goals']['home'] or 0,
                                'dep_gol': f['goals']['away'] or 0,
                                'lig': f['league']['name'],
                                'dangerous_attacks_ev': 0,
                                'dangerous_attacks_dep': 0,
                                'shots_on_target_ev': 0,
                                'shots_on_target_dep': 0,
                                'suanki_ah_oran': 1.70, # Örnek (Odds endpointinden çekilebilir)
                                'acilis_ah_oran': 2.00  # Örnek
                            }
                            
                            # İstatistikleri yerleştir
                            for s_group in stats:
                                is_home = s_group['team']['id'] == teams['home']['id']
                                for s in s_group['statistics']:
                                    if s['type'] == 'Dangerous Attacks':
                                        if is_home: mac['dangerous_attacks_ev'] = s['value'] or 0
                                        else: mac['dangerous_attacks_dep'] = s['value'] or 0
                                    if s['type'] == 'Shots on Target':
                                        if is_home: mac['shots_on_target_ev'] = s['value'] or 0
                                        else: mac['shots_on_target_dep'] = s['value'] or 0

                            puan, detay, strat, gecti = sinyal_hesapla(mac)
                            
                            if gecti and puan >= MIN_PUAN and mac['id'] not in bildirim_gonderilen:
                                ai_yorum, ai_kasa = await gemini_analiz(mac, puan, detay)
                                
                                mesaj = (
                                    f"🔥 {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
                                    f"📈 PUAN: {puan} | DK: {mac['dakika']}\n"
                                    f"--------------------\n"
                                    f"🧠 AI: {ai_yorum}\n"
                                    f"💰 KASA: %{ai_kasa}\n"
                                    f"--------------------\n"
                                    f"📝 RAPOR: {', '.join(detay)}"
                                )
                                await bot.send_message(chat_id=CHAT_ID, text=mesaj)
                                bildirim_gonderilen[mac['id']] = True
                                await asyncio.sleep(4) # Nefes Payı

        except Exception as e:
            logger.error(f"Hata: {e}")
        
        await asyncio.sleep(600) # 10 Dakikada bir tara

if __name__ == "__main__":
    asyncio.run(ana_dongu())
