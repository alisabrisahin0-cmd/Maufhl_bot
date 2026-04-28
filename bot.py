import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json
from pytz import timezone # Adana/TR saat dilimi için

# Ayarlar
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))
TR_TZ = timezone('Europe/Istanbul') # Yerel saat dilimi

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
momentum_takip = {} # Momentum verisi için yeni sözlük
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# ZAMAN YÖNETİMİ (TR SAATİNE SABİTLENDİ)
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
# YENİ: MOMENTUM VE KARAKTER ANALİZİ
# ================================================
def momentum_analizi(mac_id, dangerous_attacks_toplam):
    simdi = datetime.now(TR_TZ)
    if mac_id not in momentum_takip:
        momentum_takip[mac_id] = []
    
    momentum_takip[mac_id].append((simdi, dangerous_attacks_toplam))
    
    # Sadece son 15 dakikayı tut
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
        return "KISIR_BASKI", "Ev sahibi topu tutuyor ama üretemiyor, kontra riski yüksek."
    if poss < 40 and shots_fark > 2:
        return "KATİL_KONTRA", "Deplasman topu bıraktı ama kaleyi mermi gibi dövüyor."
    return "STANDART", ""

# ================================================
# VERİTABANI (MEVCUT YAPI KORUNDU)
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
    except Exception as e:
        logger.error(f"Kayit: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        logger.error(f"Guncelleme: {e}")

# ================================================
# WINNING CODE — (ORİJİNAL KORUNDU)
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
    if son_gol > 0:
        gecen = dakika - son_gol
        MA = not (gecen > 8 and (dangerous_ev + dangerous_dep) < 20)
    else:
        MA = not (dakika > 15 and dangerous_ev < 8)
    DIYI = dangerous_dep <= dangerous_ev * 0.65 and shots_dep <= shots_ev + 3

    return {
        'VU': VU, 'TUM': TUM, 'MA': MA, 'DIYI': DIYI,
        'gecti': VU and TUM and MA and DIYI,
        'VU_val': 1 if VU else 0,
        'TUM_val': 1 if TUM else 0,
        'MA_val': 0 if MA else 1,
        'DIYI_val': 0 if DIYI else 1,
    }

# ================================================
# ALTIN PENCERE (ORİJİNAL KORUNDU)
# ================================================
def zaman_bonusu(dakika):
    if 54 <= dakika <= 60:
        return 3.5, "Altın Pencere (54-62') +3.5", "POWER_WINDOW"
    elif 24 <= dakika <= 36:
        return 2.0, "Erken Baskı (24-36') +2.0", "ERKEN_BASKISI"
    elif 45 <= dakika <= 49:
        return 2.0, "Uzatma Volatilite (45-49') +2.0", "UZATMA"
    elif 7 <= dakika <= 15:
        return 1.0, "Erken Açılış (7-15') +1.0", "ERKEN_ACILIS"
    return 0, "", ""

# ================================================
# COOLING OFF (ORİJİNAL KORUNDU)
# ================================================
def cooling_off(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_toplam = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    corner_toplam = mac.get('corner_ev', 0) + mac.get('corner_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))

    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20:
        return True, "Skor net + düşük aktivite"
    if son_gol > 0:
        gecen = dakika - son_gol
        if gecen > 7 and dangerous_toplam < 20 and corner_toplam < 3:
            return True, "Momentum kaybı"
    return False, ""

# ================================================
# SİNYAL SİSTEMİ (MOMENTUM BONUSU EKLENDİ)
# ================================================
def sinyal_hesapla(mac):
    wc = winning_code_kontrol(mac)
    if not wc['gecti']:
        return 0, [], "", wc

    puan = 0.0
    detay = []
    stratejiler = []

    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    kirmizi = mac.get('kirmizi_kart', 0)
    corner_toplam = mac.get('corner_ev', 0) + mac.get('corner_dep', 0)
    ah_deger = mac.get('ah_deger', 0.0)

    toplam_gol = ev_gol + dep_gol
    shots_toplam = shots_ev + shots_dep
    dangerous_toplam = dangerous_ev + dangerous_dep

    # WC onayı (Temel)
    puan += 4
    detay.append("✅ Winning Code Onayı")

    # YENİ: Momentum Ivme Bonusu
    ivme = momentum_analizi(mac['id'], dangerous_toplam)
    if ivme >= 2.0:
        puan += 2.0
        detay.append(f"🚀 AGRESİF MOMENTUM (+{ivme} atak/dk) +2.0")
    elif ivme >= 1.0:
        puan += 1.0
        detay.append(f"⚡ YÜKSELEN MOMENTUM (+{ivme} atak/dk) +1.0")

    # Diğer İstatistikler
    if ev_gol == dep_gol: puan += 1.5; detay.append("🤝 Skor Dengede +1.5")
    if shots_toplam >= 12: puan += 2; detay.append("🎯 İsabetli Şut Elite +2.0")
    if dangerous_toplam >= 100: puan += 2; detay.append("🔥 Tehlikeli Atak Tavan +2.0")
    if corner_toplam >= 10: puan += 1.5; detay.append("🚩 Yoğun Korner +1.5")
    
    # AH Değerlendirmesi
    if ah_deger <= -0.75: puan += 2; detay.append(f"📈 AH {ah_deger} Favori Baskısı +2.0")

    # Altın pencere
    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus
        detay.append(f"🔥 {z_label}")
        if z_strateji: stratejiler.append(z_strateji)

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return round(puan, 1), detay, strateji_adi, wc

# ================================================
# NET TAHMİN (ORİJİNAL KORUNDU)
# ================================================
def tavsiye_uret(mac, strateji):
    ev_gol, dep_gol = mac['ev_gol'], mac['dep_gol']
    if ev_gol == dep_gol: return "GOL OLACAK (S)", "Skor dengede, baskı gol getirebilir."
    if ev_gol > dep_gol: return "EV GOL ATACAK (S)", "Ev sahibi üstünlüğünü koruyor."
    return "DEP GOL ATACAK (S)", "Deplasman kontra/baskı ile gol arıyor."

def sonraki_gol_tahmini(mac, strateji):
    ev_skor = (mac['possession_ev'] * 0.3) + (mac['shots_on_target_ev'] * 5)
    dep_skor = ((100 - mac['possession_ev']) * 0.3) + (mac['shots_on_target_dep'] * 5)
    if ev_skor > dep_skor * 1.3: return f"Sıradaki Gol: {mac['ev'][:15]}"
    if dep_skor > ev_skor * 1.3: return f"Sıradaki Gol: {mac['dep'][:15]}"
    return "Sıradaki Gol: Her İki Taraf"

def kasa_hesapla(puan, dakika, ah_deger):
    if puan >= 10: return 3.5
    if puan >= 8: return 2.5
    return 1.5

# ================================================
# GEMİNİ AI — GELİŞTİRİLMİŞ ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    if not GEMINI_KEY: return "AI aktif değil.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    
    karakter_tipi, karakter_notu = oyun_karakteri(mac)
    ivme = momentum_analizi(mac['id'], mac['dangerous_attacks_ev'] + mac['dangerous_attacks_dep'])

    prompt = f"""Uzman bahis analisti olarak şu maçı yorumla:
MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
DAKİKA: {mac['dakika']} | LİG: {mac['lig']}
OYUN KARAKTERİ: {karakter_tipi} ({karakter_notu})
MOMENTUM İVMESİ: {ivme} atak/dk (Son 15 dk)
İSTATİSTİKLER: Şut {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']}, Korner {mac['corner_ev']}/{mac['corner_dep']}, AH {mac['ah_deger']}

GÖREV:
- İstatistiklerin söylemediği 'saha psikolojisi' üzerine 3 kısa cümle kur.
- Atak sürekliliği gibi genel laflardan kaçın.
- Bu ligin ve dakikanın riskini belirt.
- Çıktıyı JSON formatında ver.

JSON: {{"yorum": "analiz", "gir": true, "kasa": 1.5}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = json.loads(data['candidates'][0]['content']['parts'][0]['text'])
                    return res.get('yorum', ''), float(res.get('kasa', 1.5))
    except:
        return "Analiz hatası.", 1.5
    return "Analiz yapılamadı.", 1.5

# ================================================
# VERİ ÇEKME VE BİLDİRİM (KORUNDU & DÜZENLENDİ)
# ================================================
async def macları_cek():
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS, timeout=15) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                for f in data.get('response', []):
                    try:
                        fix, teams, goals, stats_raw = f['fixture'], f['teams'], f['goals'], f.get('statistics', [])
                        mac_id = str(fix['id'])
                        dakika = fix['status']['elapsed'] or 0
                        if dakika < 5 or dakika > 88: continue

                        stats = {'shots_on_target_ev':0, 'shots_on_target_dep':0, 'possession_ev':50, 
                                 'dangerous_attacks_ev':0, 'dangerous_attacks_dep':0, 'kirmizi_kart':0, 
                                 'corner_ev':0, 'corner_dep':0, 'ah_deger':0.0, 'son_gol':0}
                        
                        for sg in stats_raw:
                            is_home = (sg['team']['id'] == teams['home']['id'])
                            for s in sg['statistics']:
                                tip, val = s['type'].lower(), s['value'] or 0
                                if 'target' in tip: stats['shots_on_target_ev' if is_home else 'shots_on_target_dep'] = int(val)
                                elif 'possession' in tip: stats['possession_ev'] = int(str(val).replace('%','')) if is_home else stats['possession_ev']
                                elif 'dangerous' in tip: stats['dangerous_attacks_ev' if is_home else 'dangerous_attacks_dep'] = int(val)
                                elif 'corner' in tip: stats['corner_ev' if is_home else 'corner_dep'] = int(val)
                                elif 'red' in tip: stats['kirmizi_kart'] += int(val)

                        maclar.append({'id': mac_id, 'ev': teams['home']['name'], 'dep': teams['away']['name'], 
                                       'lig': f['league']['name'], 'dakika': dakika, 'ev_gol': goals['home'] or 0, 
                                       'dep_gol': goals['away'] or 0, **stats})
                    except: continue
    except: pass
    return maclar

async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    kasa = ai_kasa if ai_kasa else kasa_hesapla(puan, mac['dakika'], mac['ah_deger'])
    sonraki = sonraki_gol_tahmini(mac, strateji)
    
    mesaj = (
        f"🔥 {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 PUAN: {puan}/12 | 🎯 {sonraki}\n"
        f"────────────────────\n"
        f"📝 SİSTEM ANALİZİ:\n" + "\n".join([f"- {d}" for d in detay[:4]]) + "\n"
        f"────────────────────\n"
        f"🧠 AI YORUMU:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💰 KASA: %{kasa} | 💡 {tahmin}\n"
    )
    await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa)

# ================================================
# ANA DÖNGÜ (ADANA SAATİ VE UYKU DÜZENİ)
# ================================================
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    logger.info("Bot basladi!")

    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600) # 10 dk uyu
                continue

            maclar = await macları_cek()
            for mac in maclar:
                puan, detay, strateji, wc = sinyal_hesapla(mac)
                if puan >= MIN_PUAN:
                    onceki = bildirim_gonderilen.get(mac['id'], 0)
                    if puan > onceki:
                        if cooling_off(mac)[0]: continue
                        tahmin, neden = tavsiye_uret(mac, strateji)
                        ai_yorum, ai_kasa = await gemini_analiz(mac, puan, strateji, tahmin, neden, wc)
                        await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa)
                        bildirim_gonderilen[mac['id']] = puan
            
            await asyncio.sleep(300) # 5 dakikada bir tara
        except Exception as e:
            logger.error(f"Döngü hatası: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
