"""
MAC ANALIZ BOTU - MUKEMMEL SISTEM (V3.4 - Kilitler Açıldı)
Zamanlama:
- Hafta ici (Pzt-Cuma): 19:00 - 00:00
- Hafta sonu (Cmt-Pzr): 19:00 - 23:00
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

MIN_PUAN = float(os.getenv("MIN_PUAN", "7.0")) # Daha agresif maçlar için 7.0 iyi bir başlangıç

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

def sonraki_aktif():
    gun = datetime.now().weekday()
    return "19:00 (Hafta ici)" if gun <= 4 else "19:00 (Hafta sonu)"

# ================================================
# VERİTABANI
# ================================================
async def db_baglant():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS sinyaller (
                id SERIAL PRIMARY KEY,
                mac_id TEXT, ev TEXT, dep TEXT, lig TEXT, dakika INTEGER,
                ev_gol INTEGER, dep_gol INTEGER, puan REAL, strateji TEXT,
                tahmin TEXT, ai_yorum TEXT, kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0, final_dep_gol INTEGER DEFAULT 0
            )
        """)
    except Exception as e:
        logger.error(f"DB: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller
                (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
                 puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
                mac['dakika'], mac['ev_gol'], mac['dep_gol'],
                puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e: pass

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e: pass

# ================================================
# WINNING CODE — VU/TÜM/MA/DİYİ
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
        'VU': VU, 'TUM': TUM, 'MA': MA, 'DIYI': DIYI,
        'gecti': VU and TUM and MA and DIYI,
        'VU_val': 1 if VU else 0, 'TUM_val': 1 if TUM else 0,
        'MA_val': 0 if MA else 1, 'DIYI_val': 0 if DIYI else 1,
        'detay': "Eksik" if not (VU and TUM and MA and DIYI) else "Tam"
    }

# ================================================
# ALTIN PENCERE VE COOLING OFF
# ================================================
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

# ================================================
# SİNYAL SİSTEMİ (Orijinal Sistem - Mükemmel Uyum)
# ================================================
def sinyal_hesapla(mac):
    lig = mac.get('lig', '')
    lig_katsayisi = 1.0 # Basitleştirildi
    if any(l in lig.lower() for l in ['eredivisie', 'bundesliga']): lig_katsayisi = 1.2

    wc = winning_code_kontrol(mac)
    puan = 0.0
    detay = []
    stratejiler = []

    dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    shots_toplam = shots_ev + shots_dep
    
    possession_ev = mac.get('possession_ev', 50)
    
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    dangerous_toplam = dangerous_ev + dangerous_dep
    
    dapm_ev = round(dangerous_ev / dakika, 2)
    dapm_dep = round(dangerous_dep / dakika, 2)
    spm_toplam = round(shots_toplam / dakika, 3)

    # 1. EXTREME VALUE VEYA WC ONAYI
    extreme_value = (shots_toplam >= 12 or possession_ev >= 65 or dapm_ev >= 1.5 or (toplam_gol == 0 and shots_toplam >= 10))
    if not wc['gecti']:
        if extreme_value:
            puan += 2
            detay.append("⚠️ WC Kısmi ama EXTREME VALUE +2.0")
            stratejiler.append("EXTREME_VALUE")
        else:
            return 0, [], "", wc # İki testten de geçemezse sinyal yok
    else:
        puan += 4
        detay.append("✅ Winning Code Onayı Tamam +4.0")

    # 2. İSTATİSTİK PUANLARI
    if dapm_ev >= 1.5: puan += 2.0; detay.append(f"🌪️ Ev Ağır Baskı ({dapm_ev} Atak/Dk) +2.0")
    if dapm_dep >= 1.5: puan += 1.5; detay.append(f"🌪️ Dep Ağır Baskı ({dapm_dep} Atak/Dk) +1.5")
    if spm_toplam >= 0.25: puan += 1.5; detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5")
    
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50:
        puan += 2; detay.append("💥 0-0 Çok Aktif (VALUE!) +2.0")
    
    if gol_fark >= 2: puan += 1; detay.append(f"📊 Gol Farkı {gol_fark} +1.0")
    if shots_toplam >= 8: puan += 1; detay.append(f"🎯 {shots_toplam} İsabetli Şut +1.0")
    
    poss_fark = abs(possession_ev - (100-possession_ev))
    if poss_fark >= 15: puan += 1; detay.append(f"⚽ Top Hakimiyeti Üstünlüğü +1.0")

    # 3. ZAMAN BONUSU
    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus; detay.append(f"🔥 {z_label}")
        if z_strateji: stratejiler.append(z_strateji)

    # LİG KATSAYISI
    puan = round(puan * lig_katsayisi, 1)
    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    
    return round(puan, 1), detay, strateji_adi, wc

# ================================================
# TAVSİYE ÜRET
# ================================================
def tavsiye_uret(mac, strateji):
    # Basitleştirildi, her zaman GOL OLACAK (S) veya taraf golü tavsiyesi verir.
    toplam_gol = mac.get('ev_gol',0) + mac.get('dep_gol',0)
    return "GOL OLACAK (S)", f"Maç çok aktif, toplam {mac.get('shots_on_target_ev',0) + mac.get('shots_on_target_dep',0)} şut var."

def sonraki_gol_tahmini(mac, strateji): return "Sıradaki Gol: Bekleniyor"
def kasa_hesapla(puan, dakika, ah_deger): return 1.5 if puan >= 6 else 1.0

# ================================================
# GEMİNİ AI — DERİN GERÇEK ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI servisi kapalı.", 1.5

    prompt = f"""MAÇ: {mac['ev']} {mac.get('ev_gol',0)}-{mac.get('dep_gol',0)} {mac['dep']} | DK: {mac['dakika']}
    Lütfen çok kısa (2 cümle) maç gidişatı hakkında yorum yap. JSON olarak dön: {{"yorum": "...", "gir": true, "kasa": 1.5}}"""

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
                        return res.get('yorum', 'Baskı hissediliyor.'), res.get('kasa', 1.5)
                    elif resp.status == 429: 
                        current_key_index = (current_key_index + 1) % len(valid_keys)
        except Exception:
            current_key_index = (current_key_index + 1) % len(valid_keys)
        await asyncio.sleep(1) 
    return "Tüm AI anahtarları meşgul.", 1.5

# ================================================
# VERİ ÇEKME
# ================================================
async def macları_cek():
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for f in data.get('response', []):
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
    except Exception: pass
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
    try: await bot.send_message(CHAT_ID, "🟢 SİSTEM AKTİF! Kilitler Kırıldı. VIP Maçlar Aranıyor...")
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
                tahmin, neden = tavsiye_uret(mac, strateji)
                ai_yorum, ai_kasa = await gemini_analiz(mac, puan, strateji, tahmin, neden, wc)
                
                await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa)
                bildirim_gonderilen[m_id] = True
                await asyncio.sleep(2)
        
        await asyncio.sleep(300) # Tarama Hızı: 5 Dakika (300sn)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
