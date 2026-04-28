import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta, timezone as dt_timezone # Standart kütüphane eklendi
import json

# Ayarlar
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

# Adana/Türkiye için UTC+3 sabitlendi (pytz gerektirmez)
TR_TZ = dt_timezone(timedelta(hours=3))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
momentum_takip = {}
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
    simdi = datetime.now(TR_TZ)
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4:  # Hafta ici
        return saat >= 19 or saat == 0
    else:  # Hafta sonu
        return 19 <= saat <= 22

def sonraki_aktif():
    gun = datetime.now(TR_TZ).weekday()
    return "19:00 (Hafta ici)" if gun <= 4 else "19:00 (Hafta sonu)"

# ================================================
# MOMENTUM VE KARAKTER ANALİZİ
# ================================================
def momentum_analizi(mac_id, dangerous_attacks_toplam):
    simdi = datetime.now(TR_TZ)
    if mac_id not in momentum_takip:
        momentum_takip[mac_id] = []
    
    momentum_takip[mac_id].append((simdi, dangerous_attacks_toplam))
    momentum_takip[mac_id] = [x for x in momentum_takip[mac_id] if x[0] > simdi - timedelta(minutes=15)]
    
    if len(momentum_takip[mac_id]) > 1:
        ilk_veri = momentum_takip[mac_id][0]
        son_veri = momentum_takip[mac_id][-1]
        sure = (son_veri[0] - ilk_veri[0]).seconds / 60
        if sure > 0:
            ivme = (son_veri[1] - ilk_veri[1]) / sure
            return round(ivme, 2)
    return 0.0

def oyun_karakteri(mac):
    poss = mac.get('possession_ev', 50)
    shots_fark = mac.get('shots_on_target_ev', 0) - mac.get('shots_on_target_dep', 0)
    if poss > 65 and shots_fark < -1:
        return "KISIR_BASKI", "Ev sahibi topu tutuyor ama üretemiyor."
    if poss < 40 and shots_fark > 2:
        return "KATİL_KONTRA", "Deplasman topu bıraktı ama kaleyi dövüyor."
    return "STANDART", ""

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
                mac_id TEXT,
                ev TEXT,
                dep TEXT,
                lig TEXT,
                dakika INTEGER,
                ev_gol INTEGER,
                dep_gol INTEGER,
                puan REAL,
                strateji TEXT,
                tahmin TEXT,
                ai_yorum TEXT,
                kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(),
                sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0,
                final_dep_gol INTEGER DEFAULT 0
            )
        """)
        logger.info("Veritabani baglandi!")
    except Exception as e:
        logger.error(f"DB: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, strateji, tahmin, ai_yorum, kasa_yuzde)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'], mac['dakika'], mac['ev_gol'], mac['dep_gol'], puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e:
        logger.error(f"Kayit: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3 WHERE mac_id=$4 AND sonuc='BEKLIYOR'", sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme: {e}")

# ================================================
# WINNING CODE
# ================================================
def winning_code_kontrol(mac):
    s_ev, s_dep = mac.get('shots_on_target_ev', 0), mac.get('shots_on_target_dep', 0)
    p_ev = mac.get('possession_ev', 50)
    d_ev, d_dep = mac.get('dangerous_attacks_ev', 0), mac.get('dangerous_attacks_dep', 0)
    dak, sg = mac.get('dakika', 0), mac.get('son_gol', 0)

    VU = s_ev >= 2 and p_ev >= 42 and d_ev >= 15
    TUM = (d_ev + d_dep) >= 25
    MA = not (dak - sg > 8 and (d_ev + d_dep) < 20) if sg > 0 else not (dak > 15 and d_ev < 8)
    DIYI = d_dep <= d_ev * 0.65 and s_dep <= s_ev + 3
    return {'gecti': VU and TUM and MA and DIYI}

def zaman_bonusu(dakika):
    if 54 <= dakika <= 60: return 3.5, "Altın Pencere +3.5", "POWER_WINDOW"
    if 24 <= dakika <= 36: return 2.0, "Erken Baskı +2.0", "ERKEN_BASKISI"
    return 0, "", ""

def cooling_off(mac):
    dak, sg = mac.get('dakika', 0), mac.get('son_gol', 0)
    d_top = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    if abs(mac['ev_gol'] - mac['dep_gol']) >= 3 and dak >= 62 and d_top < 20: return True
    if sg > 0 and (dak - sg) > 7 and d_top < 20: return True
    return False

# ================================================
# SİNYAL HESAPLA
# ================================================
def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']: return 0, [], "", wc

    puan, detay, stratejiler = 4.0, ["✅ Winning Code Onayı"], []
    d_top = mac['dangerous_attacks_ev'] + mac['dangerous_attacks_dep']
    
    ivme = momentum_analizi(mac['id'], d_top)
    if ivme >= 2.0: puan += 2.0; detay.append(f"🚀 AGRESİF MOMENTUM (+{ivme})")
    elif ivme >= 1.0: puan += 1.0; detay.append(f"⚡ YÜKSELEN MOMENTUM (+{ivme})")

    if mac['ev_gol'] == mac['dep_gol']: puan += 1.5; detay.append("🤝 Beraberlik Bonusu")
    if (mac['shots_on_target_ev'] + mac['shots_on_target_dep']) >= 12: puan += 2; detay.append("🎯 Şut Elite")
    
    z_bonus, z_label, z_strat = zaman_bonusu(mac['dakika'])
    if z_bonus > 0: puan += z_bonus; detay.append(f"🔥 {z_label}"); stratejiler.append(z_strat)

    return round(puan, 1), detay, (stratejiler[0] if stratejiler else "GENEL"), wc

# ================================================
# TAHMİN VE GEMINI
# ================================================
def tavsiye_uret(mac, strateji):
    if mac['ev_gol'] == mac['dep_gol']: return "GOL OLACAK (S)", "Skor dengede, baskı artıyor."
    return "EV GOL ATACAK (S)" if mac['ev_gol'] > mac['dep_gol'] else "DEP GOL ATACAK (S)", "Baskı süregelen tarafta."

async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEY: return "AI aktif değil.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    k_tip, k_not = oyun_karakteri(mac)
    ivme = momentum_analizi(mac['id'], mac['dangerous_attacks_ev'] + mac['dangerous_attacks_dep'])
    
    prompt = f"Maç: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\nDakika: {mac['dakika']}\nKarakter: {k_tip}\nMomentum: {ivme}\nTahmin: {tahmin}\n3 kısa cümleyle saha analizi yap ve JSON dön: {{\"yorum\": \"...\", \"kasa\": 1.5}}"
    try:
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = json.loads(data['candidates'][0]['content']['parts'][0]['text'])
                    return res.get('yorum', ''), float(res.get('kasa', 1.5))
    except: pass
    return "Analiz yapılamadı.", 1.5

# ================================================
# API VE ANA DÖNGÜ
# ================================================
async def macları_cek():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS, timeout=15) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                maclar = []
                for f in data.get('response', []):
                    try:
                        fix, teams, goals = f['fixture'], f['teams'], f['goals']
                        dak = fix['status']['elapsed'] or 0
                        if dak < 5 or dak > 88: continue
                        s = {'shots_on_target_ev':0, 'shots_on_target_dep':0, 'possession_ev':50, 'dangerous_attacks_ev':0, 'dangerous_attacks_dep':0, 'corner_ev':0, 'corner_dep':0}
                        for sg in f.get('statistics', []):
                            is_h = sg['team']['id'] == teams['home']['id']
                            for st in sg['statistics']:
                                t, v = st['type'].lower(), st['value'] or 0
                                if 'target' in t: s['shots_on_target_ev' if is_h else 'shots_on_target_dep'] = int(v)
                                elif 'possession' in t: s['possession_ev'] = int(str(v).replace('%','')) if is_h else s['possession_ev']
                                elif 'dangerous' in t: s['dangerous_attacks_ev' if is_h else 'dangerous_attacks_dep'] = int(v)
                        maclar.append({'id': str(fix['id']), 'ev': teams['home']['name'], 'dep': teams['away']['name'], 'lig': f['league']['name'], 'dakika': dak, 'ev_gol': goals['home'] or 0, 'dep_gol': goals['away'] or 0, **s})
                    except: continue
                return maclar
    except: return []

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue
            maclar = await macları_cek()
            for mac in maclar:
                puan, detay, strateji, wc = sinyal_hesapla(mac)
                if puan >= MIN_PUAN and puan > bildirim_gonderilen.get(mac['id'], 0):
                    if cooling_off(mac): continue
                    tahmin, neden = tavsiye_uret(mac, strateji)
                    yorum, kasa = await gemini_analiz(mac, puan, strateji, tahmin, neden)
                    msg = f"🔥 {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n📈 PUAN: {puan}/12\n📝 ANALİZ:\n" + "\n".join([f"- {d}" for d in detay]) + f"\n🧠 AI: {yorum}\n💰 KASA: %{kasa} | 💡 {tahmin}"
                    await bot.send_message(chat_id=CHAT_ID, text=msg)
                    await sinyal_kaydet(mac, puan, strateji, tahmin, yorum, kasa)
                    bildirim_gonderilen[mac['id']] = puan
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
