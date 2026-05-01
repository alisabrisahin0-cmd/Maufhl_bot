"""
MAC ANALIZ BOTU - V3.5 (Tam Açık Şanzıman + Radar Logları)
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# 3 ANAHTARLI AI HAVUZU
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", "")
]
current_key_index = 0

# BARAJI 4 YAPARAK TEST EDİYORUZ
MIN_PUAN = float(os.getenv("MIN_PUAN", "4.0")) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4:  return 19 <= saat <= 23  # Hafta içi
    else:         return 19 <= saat <= 22  # Hafta sonu
    return False

# ================================================
# VERİTABANI KOPYASI (BOŞ GEÇİLDİ)
# ================================================
async def db_baglant(): pass
async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa): pass
async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep): pass

# ================================================
# WINNING CODE VE PUANLAMA (KİLİTLER KIRILDI)
# ================================================
def winning_code_kontrol(mac):
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    son_gol = mac.get('son_gol', 0)
    dakika = mac.get('dakika', 0)

    VU = shots_ev >= 2 and possession_ev >= 42 and dangerous_ev >= 15
    TUM = (dangerous_ev + dangerous_dep) >= 25
    if son_gol > 0: MA = not ((dakika - son_gol) > 8 and (dangerous_ev + dangerous_dep) < 20)
    else:           MA = not (dakika > 15 and dangerous_ev < 8)
    DIYI = dangerous_dep <= dangerous_ev * 0.65 and shots_dep <= shots_ev + 3

    return {
        'gecti': VU and TUM and MA and DIYI,
        'detay': "Eksik" if not (VU and TUM and MA and DIYI) else "Tam"
    }

def zaman_bonusu(dakika):
    if 54 <= dakika <= 60: return 3.5, "Altın Pencere (54-62') +3.5", "POWER_WINDOW"
    elif 24 <= dakika <= 36: return 2.0, "Erken Baskı (24-36') +2.0", "ERKEN_BASKISI"
    elif 45 <= dakika <= 49: return 2.0, "Uzatma Volatilite (45-49') +2.0", "UZATMA"
    elif 7 <= dakika <= 15: return 1.0, "Erken Açılış (7-15') +1.0", "ERKEN_ACILIS"
    return 0, "", ""

def cooling_off(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_toplam = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    corner_toplam = mac.get('corner_ev', 0) + mac.get('corner_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))

    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20: return True, "Skor net + düşük aktivite"
    if son_gol > 0 and (dakika - son_gol) > 7 and dangerous_toplam < 20 and corner_toplam < 3: return True, "Aktivite düştü"
    return False, ""

def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    puan = 0.0
    detay = []
    stratejiler = []

    dakika = max(mac.get('dakika', 1), 1)
    toplam_gol = mac.get('ev_gol', 0) + mac.get('dep_gol', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))
    shots_toplam = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_toplam = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    
    dapm_ev = round(mac.get('dangerous_attacks_ev', 0) / dakika, 2)
    dapm_dep = round(mac.get('dangerous_attacks_dep', 0) / dakika, 2)
    spm_toplam = round(shots_toplam / dakika, 3)

    # GİYOTİN KALDIRILDI! SIFIR VERMİYORUZ ARTIK.
    extreme_value = (shots_toplam >= 12 or possession_ev >= 65 or dapm_ev >= 1.5 or (toplam_gol == 0 and shots_toplam >= 10))
    
    if wc['gecti']:
        puan += 4.0; detay.append("✅ Winning Code Onayı +4.0")
    elif extreme_value:
        puan += 2.0; detay.append("⚠️ EXTREME VALUE tespit edildi +2.0")
    else:
        puan += 1.0; detay.append("ℹ️ Standart İstatistik Taraması +1.0")

    # DİĞER İSTATİSTİKLER (Bunlar artık direkt puan ekleyecek)
    if dapm_ev >= 1.5:uan += 2.0; detay.append(f"🌪️ Ev Ağır Baskı ({dapm_ev} Atak/Dk) +2.0")
    if dapm_dep >= 1.5: puan += 1.5; detay.append(f"🌪️ Dep Ağır Baskı ({dapm_dep} Atak/Dk) +1.5")
    if spm_toplam >= 0.25: puan += 1.5; detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5")
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50: puan += 2; detay.append("💥 0-0 Çok Aktif (VALUE!) +2.0")
    if gol_fark >= 2: puan += 1; detay.append(f"📊 Gol Farkı {gol_fark} +1.0")
    if shots_toplam >= 8: puan += 1; detay.append(f"🎯 {shots_toplam} İsabetli Şut +1.0")
    
    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus; detay.append(f"🔥 {z_label}")

    return round(puan, 1), detay, "GENEL", wc

# ================================================
# GEMİNİ AI — DERİN GERÇEK ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI servisi kapalı.", 1.5

    prompt = f"""MAÇ: {mac['ev']} {mac.get('ev_gol',0)}-{mac.get('dep_gol',0)} {mac['dep']} | DK: {mac['dakika']}
    Çok kısa (2 cümle) maç gidişatı yorumu yap. JSON dön: {{"yorum": "...", "gir": true, "kasa": 1.5}}"""

    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={active_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data['candidates'][0]['content']['parts'][0]['text']
                        res = json.loads(text[text.find('{'):text.rfind('}')+1])
                        return res.get('yorum', 'Baskı var.'), res.get('kasa', 1.5)
                    elif resp.status == 429: current_key_index = (current_key_index + 1) % len(valid_keys)
        except Exception: current_key_index = (current_key_index + 1) % len(valid_keys)
        await asyncio.sleep(1) 
    return "AI Havuzu meşgul.", 1.5

# ================================================
# VERİ ÇEKME (LOGLAR AÇILDI)
# ================================================
async def macları_cek():
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS, timeout=10) as resp:
                data = await resp.json()
                
                # API HATA KONTROLÜ
                if resp.status != 200 or data.get('errors'):
                    logger.error(f"❌ API Hatası: {data.get('errors')}")
                    return maclar
                
                response_data = data.get('response', [])
                logger.info(f"🔍 API OK! Şu an {len(response_data)} canlı maç işleniyor...")

                for f in response_data:
                    try:
                        dk = f['fixture']['status']['elapsed']
                        if not (5 < dk < 88): continue

                        stats = {'shots_on_target_ev': 0, 'shots_on_target_dep': 0, 'possession_ev': 50, 'dangerous_attacks_ev': 0, 'dangerous_attacks_dep': 0}
                        home_id = f['teams']['home']['id']
                        
                        for stat_group in f.get('statistics', []):
                            is_home = (stat_group['team']['id'] == home_id)
                            for s in stat_group.get('statistics', []):
                                val = s['value']
                                if val is None: val = 0
                                if isinstance(val, str) and '%' in val: val = int(val.replace('%',''))
                                else: val = int(val)
                                
                                tip = s['type'].lower()
                                if 'on target' in tip:
                                    if is_home: stats['shots_on_target_ev'] = val
                                    else: stats['shots_on_target_dep'] = val
                                elif 'possession' in tip:
                                    if is_home: stats['possession_ev'] = val
                                elif 'dangerous attacks' in tip:
                                    if is_home: stats['dangerous_attacks_ev'] = val
                                    else: stats['dangerous_attacks_dep'] = val

                        maclar.append({
                            'id': str(f['fixture']['id']), 'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                            'lig': f['league']['name'], 'dakika': dk,
                            'ev_gol': f['goals']['home'] or 0, 'dep_gol': f['goals']['away'] or 0, **stats
                        })
                    except Exception: pass
    except Exception as e: 
        logger.error(f"❌ Veri Çekme Hatası: {e}")
    return maclar

# ================================================
# BİLDİRİM GÖNDER
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    kasa = ai_kasa if ai_kasa is not None else 1.5
    karar = "🔥🔥 KESİN GİR" if puan >= 10 else "✅ GİREBİLİRSİN" if puan >= 7 else "⚠️ DİKKATLİ OL"
    
    detay_str = "\n".join([f"- {d}" for d in detay[:4]])
    
    mesaj = (
        f"⚽️ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 PUAN: {puan} | KASA: %{kasa}\n"
        f"────────────────────\n"
        f"📝 ANALİZ:\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 AI: {ai_yorum}\n"
        f"{'═'*20}\n"
        f"{karar}"
    )
    try: await bot.send_message(CHAT_ID, mesaj)
    except Exception: pass

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    try: await bot.send_message(CHAT_ID, f"🟢 V3.5 SİSTEM AKTİF! Minimum Puan Barajı: {MIN_PUAN} - Kilitler Tamamen Kırıldı!")
    except Exception: pass

    while True:
        if not aktif_mi():
            await asyncio.sleep(600)
            continue

        maclar = await macları_cek()
        for mac in maclar:
            puan, detay, strateji, wc = sinyal_hesapla(mac)
            m_id = mac['id']

            if m_id in bildirim_gonderilen: continue
            
            cooling, c_msg = cooling_off(mac)
            if cooling: continue

            if puan >= MIN_PUAN:
                ai_yorum, ai_kasa = await gemini_analiz(mac, puan, strateji, "Bekleniyor", "Bekleniyor", wc)
                
                await bildirim_gonder(bot, mac, puan, detay, strateji, "Taraf Gol", "İstatistik", ai_yorum, ai_kasa)
                bildirim_gonderilen[m_id] = True
                await asyncio.sleep(2)
        
        await asyncio.sleep(300) # Tarama Hızı: 5 Dakika (300sn)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
