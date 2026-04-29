"""
MAC ANALIZ BOTU - MUKEMMEL SISTEM
Zamanlama:
- Hafta ici (Pzt-Cuma): 19:00 - 00:00
- Hafta sonu (Cmt-Pzr): 19:00 - 23:00
Format: Istenen gorunum + Derin Gemini analizi
"""

import asyncio
import aiohttp
from aiohttp import web  # Railway için eklendi
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
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

# Logların anında görünmesi için ayar
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


# --- RAILWAY'İ KANDIRMAK İÇİN SAHTE WEB SUNUCUSU ---
async def handle_ping(request):
    return web.Response(text="Bot aktif!")

async def web_server_baslat():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Railway için port {port} açıldı.")

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()  # 0=Pzt, 6=Pzr
    if gun <= 4:  # Hafta ici Pzt-Cuma
        return 19 <= saat <= 23  # 19:00 - 23:59
    else:  # Hafta sonu Cmt-Pzr
        return 19 <= saat <= 22  # 19:00 - 22:59


def sonraki_aktif():
    gun = datetime.now().weekday()
    return "19:00 (Hafta ici)" if gun <= 4 else "19:00 (Hafta sonu)"


# ================================================
# VERİTABANI
# ================================================
async def db_baglanti():
    global db_pool
    try:
        # Postgres URL düzeltmesi (asyncpg için)
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)

        db_pool = await asyncpg.create_pool(url)
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
        for kolon, tip in [
            ("ai_yorum", "TEXT"), ("kasa_yuzde", "REAL"), ("strateji", "TEXT")
        ]:
            try:
                await db_pool.execute(
                    f"ALTER TABLE sinyaller ADD COLUMN IF NOT EXISTS {kolon} {tip}"
                )
            except:
                pass
        logger.info("Veritabani baglandi!")
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
        'detay': "Winning Code parametreleri"
    }


# ================================================
# ALTIN PENCERE
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
# COOLING OFF
# ================================================
def cooling_off(mac):
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    dangerous_toplam = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    corner_toplam = mac.get('corner_ev', 0) + mac.get('corner_dep', 0)
    gol_fark = abs(mac.get('ev_gol', 0) - mac.get('dep_gol', 0))

    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20:
        return True, f"Skor net ({mac['ev_gol']}-{mac['dep_gol']}) + geç dönem + düşük aktivite"
    if son_gol > 0:
        gecen = dakika - son_gol
        if gecen > 7 and dangerous_toplam < 20 and corner_toplam < 3:
            return True, f"Son gol {gecen}dk önce, aktivite düşük"
    return False, ""


# ================================================
# SİNYAL SİSTEMİ
# ================================================
def sinyal_hesapla(mac):
    LIG_KATSAYISI = {
        'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15,
        'Champions League': 1.1, 'La Liga': 1.1, 'Ligue 1': 1.1,
        'Serie A': 1.0, 'Super Lig': 1.1,
        'Serie B': 0.9, 'Ligue 2': 0.9,
    }
    lig = mac.get('lig', '')
    lig_katsayisi = 1.0
    for lig_adi, katsayi in LIG_KATSAYISI.items():
        if lig_adi.lower() in lig.lower():
            lig_katsayisi = katsayi
            break

    wc = winning_code_kontrol(mac)
    puan = 0.0
    detay = []
    stratejiler = []

    dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    possession_dep = mac.get('possession_dep', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    kirmizi = mac.get('kirmizi_kart', 0)
    kirmizi_taraf = mac.get('kirmizi_taraf', '')
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    corner_toplam = corner_ev + corner_dep
    ah_deger = mac.get('ah_deger', 0.0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    kg_var = ev_gol > 0 and dep_gol > 0
    esit_skor = ev_gol == dep_gol
    gol_hizi = round(toplam_gol / dakika, 3)
    shots_toplam = shots_ev + shots_dep
    dangerous_toplam = dangerous_ev + dangerous_dep

    dapm_ev = round(dangerous_ev / dakika, 2)
    dapm_dep = round(dangerous_dep / dakika, 2)
    spm_ev = round(shots_ev / dakika, 3)
    spm_toplam = round(shots_toplam / dakika, 3)

    kontra_ev = shots_ev >= 3 and possession_ev < 40
    kontra_dep = shots_dep >= 3 and possession_ev > 60

    extreme_value = (
        shots_toplam >= 12 or
        possession_ev >= 65 or
        dapm_ev >= 1.5 or
        (toplam_gol == 0 and shots_toplam >= 10)
    )

    if not wc['gecti']:
        if extreme_value:
            puan += 2
            detay.append(f"⚠️ WC Kısmi ({wc['detay']}) ama EXTREME VALUE +2.0")
            stratejiler.append("EXTREME_VALUE")
        else:
            return 0, [], "", wc
    else:
        puan += 4
        detay.append(
            f"✅ Winning Code Onayı (VU:{wc['VU_val']} TÜM:{wc['TUM_val']} "
            f"MA:{wc['MA_val']} DİYİ:{wc['DIYI_val']})"
        )

    if dapm_ev >= 1.5:
        puan += 2.0
        detay.append(f"🌪️ Ev Ağır Baskı ({dapm_ev} Atak/Dk) +2.0")
        stratejiler.append("AGIR_BASKI_EV")
    elif dapm_ev >= 1.2:
        puan += 1.5
        detay.append(f"🌪️ Ev Yüksek Baskı ({dapm_ev} Atak/Dk) +1.5")
        stratejiler.append("AGIR_BASKI_EV")

    if dapm_dep >= 1.5:
        puan += 1.5
        detay.append(f"🌪️ Dep Ağır Baskı ({dapm_dep} Atak/Dk) +1.5")
        stratejiler.append("AGIR_BASKI_DEP")

    if spm_toplam >= 0.25:
        puan += 1.5
        detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5")
        stratejiler.append("YUKSEK_SUT_HIZI")

    if kontra_dep:
        puan += 1.5
        detay.append(f"⚡ Dep Kontra Atak! (%{possession_ev} top ama {shots_dep} şut) +1.5")
        stratejiler.append("KONTRA_ATAK_DEP")
    if kontra_ev:
        puan += 1.5
        detay.append(f"⚡ Ev Kontra Atak! ({shots_ev} şut ama düşük top) +1.5")
        stratejiler.append("KONTRA_ATAK_EV")

    if esit_skor:
        puan += 1.5
        detay.append(f"🤝 Skor Dengede +1.5")
        stratejiler.append("BERABERLIK")

    if dep_gol > ev_gol and possession_ev >= 55 and shots_ev > shots_dep:
        puan += 2
        detay.append(f"💎 VALUE: Ev geride ama dominant +2.0")
        stratejiler.append("VALUE_GIRISI")

    if toplam_gol >= 4:
        puan += 2
        detay.append(f"⚽ {toplam_gol} Gol (Yüksek Tempo) +2.0")
        stratejiler.append("GOL_PATLAMASI")
    elif toplam_gol >= 3:
        puan += 1
        detay.append(f"⚽ {toplam_gol} Gol +1.0")

    if kg_var:
        puan += 1
        detay.append(f"🔄 KG Var +1.0")

    if gol_fark >= 3:
        puan += 2
        detay.append(f"📊 Gol Farkı {gol_fark} (Dominant) +2.0")
        stratejiler.append("BUYUK_FARK")
    elif gol_fark >= 2:
        puan += 1
        detay.append(f"📊 Gol Farkı {gol_fark} +1.0")

    if gol_hizi >= 0.15:
        puan += 1
        detay.append(f"⚡ Gol Hızı {gol_hizi}/dk +1.0")

    if shots_toplam >= 12:
        puan += 2
        detay.append(f"🎯 {shots_toplam} İsabetli Şut +2.0")
        stratejiler.append("YUKSEK_SUT")
    elif shots_toplam >= 8:
        puan += 1
        detay.append(f"🎯 {shots_toplam} İsabetli Şut +1.0")

    shots_fark = abs(shots_ev - shots_dep)
    if shots_fark >= 5:
        puan += 1
        d = mac['ev'] if shots_ev > shots_dep else mac['dep']
        detay.append(f"🎯 {d[:12]} Şut Dom ({shots_ev}/{shots_dep}) +1.0")
        stratejiler.append("SUT_DOMINANT")

    poss_fark = abs(possession_ev - possession_dep)
    if poss_fark >= 25:
        puan += 2
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        detay.append(f"⚽ {d[:12]} Top Dom (%{max(possession_ev,possession_dep)}) +2.0")
        stratejiler.append("POSSESSION_DOM")
    elif poss_fark >= 15:
        puan += 1
        d = mac['ev'] if possession_ev > possession_dep else mac['dep']
        detay.append(f"⚽ {d[:12]} Top Üst (%{max(possession_ev,possession_dep)}) +1.0")

    if dangerous_toplam >= 100:
        puan += 2
        detay.append(f"🔥 {dangerous_toplam} Tehlikeli Atak +2.0")
        stratejiler.append("YUKSEK_ATAK")
    elif dangerous_toplam >= 60:
        puan += 1
        detay.append(f"🔥 {dangerous_toplam} Tehlikeli Atak +1.0")

    if corner_toplam >= 12:
        puan += 2
        detay.append(f"🚩 {corner_toplam} Corner (Elite) +2.0")
        stratejiler.append("YUKSEK_CORNER")
    elif corner_toplam >= 8:
        puan += 1
        detay.append(f"🚩 {corner_toplam} Corner +1.0")

    corner_fark = abs(corner_ev - corner_dep)
    if corner_fark >= 5:
        puan += 1
        d = mac['ev'] if corner_ev > corner_dep else mac['dep']
        detay.append(f"🚩 {d[:12]} Corner Dom ({corner_ev}/{corner_dep}) +1.0")

    if ah_deger != 0:
        if -1.5 <= ah_deger <= -0.75:
            puan += 2
            detay.append(f"📈 AH {ah_deger} Ev Güçlü Favori +2.0")
            stratejiler.append("AH_FAVORI_EV")
        elif -0.75 < ah_deger < 0:
            puan += 1
            detay.append(f"📈 AH {ah_deger} Hafif Ev Fav +1.0")
        elif 0 < ah_deger <= 1.25:
            puan += 1
            detay.append(f"📈 AH {ah_deger} Dengeli +1.0")
            stratejiler.append("AH_DENGELI")

    if kirmizi >= 1:
        ev_skoru = possession_ev + shots_ev * 3
        dep_skoru = (100 - possession_ev) + shots_dep * 3
        ev_favori = ev_skoru > dep_skoru

        if kirmizi_taraf == 'home' or (not kirmizi_taraf and ev_favori):
            puan += 2
            detay.append(f"🟥 Güçlü Taraf Kırmızı Kart! Kaos + Açık Alan +2.0")
            stratejiler.append("KIRMIZI_KAOS")
        else:
            puan += 0.5
            detay.append(f"🟥 Zayıf Taraf Kırmızı (Savunma Kapanabilir) +0.5")
            stratejiler.append("KIRMIZI_KILITLI")

    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50:
        puan += 2
        detay.append(f"💥 0-0 Çok Aktif (VALUE!) +2.0")
        stratejiler.append("GOLSUZ_AKTIF")

    if 45 <= dakika <= 60 and shots_toplam >= 6:
        puan += 1
        detay.append(f"⏱️ 2. Yarı + {shots_toplam} Şut +1.0")
        stratejiler.append("2Y_BASLANGIC")

    if son_gol >= 70:
        puan += 1
        detay.append(f"⚡ Son Gol {son_gol}dk (Taze) +1.0")

    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus
        detay.append(f"🔥 {z_label}")
        if z_strateji:
            stratejiler.append(z_strateji)

    if lig_katsayisi != 1.0:
        onceki_puan = puan
        puan = round(puan * lig_katsayisi, 1)
        detay.append(f"🏆 Lig Katsayısı x{lig_katsayisi} ({onceki_puan}→{puan})")

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return round(puan, 1), detay, strateji_adi, wc


# ================================================
# NET TAHMİN
# ================================================
def tavsiye_uret(mac, strateji):
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    toplam_gol = ev_gol + dep_gol
    possession_ev = mac.get('possession_ev', 50)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    ah_deger = mac.get('ah_deger', 0.0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)

    if strateji == "VALUE_GIRISI":
        return "EV KAZANIR VEYA BERABERE", f"Ev sahibi {abs(gol_fark)} gol geride ama sahayı domine ediyor (%{possession_ev} top, {shots_ev} şut)"
    elif strateji == "GOLSUZ_AKTIF":
        if shots_ev > shots_dep and possession_ev >= 55:
            return "EV GOL ATACAK (S)", f"0-0 ama ev sahibi baskıda ({shots_ev} şut, %{possession_ev} top)"
        elif shots_dep > shots_ev:
            return "DEP GOL ATACAK (S)", f"0-0 ama deplasman daha aktif ({shots_dep} şut)"
        return "GOL OLACAK (S)", f"0-0 ama çok aktif maç — toplam {shots_ev+shots_dep} isabetli şut"
    elif strateji == "BERABERLIK":
        if possession_ev >= 60 and dangerous_ev > dangerous_dep:
            return "EV GOL ATACAK (S)", f"Beraberlik ama ev sahibi dominant (%{possession_ev} top, {dangerous_ev} atak)"
        elif possession_ev < 42:
            return "DEP GOL ATACAK (S)", f"Beraberlik ama deplasman sahayı kontrol ediyor (%{100-possession_ev} top)"
        return "GOL OLACAK (S)", f"Beraberlik ({ev_gol}-{dep_gol}), her iki taraf gol peşinde, {shots_ev+shots_dep} toplam şut"
    elif strateji == "AH_FAVORI_EV":
        return "EV GOL ATACAK (S)", f"Piyasa ev sahibini güçlü favori görüyor (AH {ah_deger})"
    elif strateji == "AH_DENGELI":
        return "GOL OLACAK (S)", f"Dengeli maç (AH {ah_deger}), karşılıklı gol beklentisi yüksek"
    elif strateji == "SUT_DOMINANT":
        if shots_ev > shots_dep:
            return "EV GOL ATACAK (S)", f"Ev sahibi şut üstünlüğü ({shots_ev} vs {shots_dep})"
        return "DEP GOL ATACAK (S)", f"Deplasman şut üstünlüğü ({shots_dep} vs {shots_ev})"
    elif strateji == "POSSESSION_DOM":
        if possession_ev >= 60:
            return "EV GOL ATACAK (S)", f"Ev sahibi topu domine ediyor (%{possession_ev})"
        return "DEP GOL ATACAK (S)", f"Deplasman top hakimiyetinde (%{100-possession_ev})"
    elif strateji == "GOL_PATLAMASI":
        if gol_fark >= 2:
            return "EV GOL ATACAK (S)", f"Ev sahibi {gol_fark} gol farkla önde ve tempo düşmemiş"
        elif gol_fark <= -2:
            return "DEP GOL ATACAK (S)", f"Deplasman {abs(gol_fark)} gol farkla önde"
        return "GOL OLACAK (S)", f"Toplam {toplam_gol} gol, maç çok açık"
    elif strateji == "YUKSEK_CORNER":
        if corner_ev > corner_dep:
            return "EV GOL ATACAK (S)", f"Ev sahibi corner dominant ({corner_ev}-{corner_dep})"
        return "GOL OLACAK (S)", f"Toplam {corner_ev+corner_dep} corner, maç çok aktif"
    elif strateji == "KIRMIZI_KART":
        return "GOL OLACAK (S)", "Kırmızı kart sonrası açık alan, gol beklentisi yüksek"
    elif strateji == "POWER_WINDOW":
        if possession_ev >= 55:
            return "EV GOL ATACAK (S)", f"54-60 altın pencere + ev sahibi dominant (%{possession_ev} top)"
        return "GOL OLACAK (S)", "54-60 altın pencere, en yüksek gol yoğunluğu dakikaları"
    
    if gol_fark >= 2 and possession_ev >= 50:
        return "EV GOL ATACAK (S)", f"Ev sahibi {gol_fark} gol farkla önde ve sahaya hakim"
    elif gol_fark <= -2:
        return "DEP GOL ATACAK (S)", f"Deplasman {abs(gol_fark)} gol farkla önde"
    elif toplam_gol >= 3 and ev_gol > 0 and dep_gol > 0:
        return "GOL OLACAK (S)", f"Maç çok açık, {toplam_gol} gol ve tempo yüksek"
    elif possession_ev >= 65:
        return "EV GOL ATACAK (S)", f"Ev sahibi topu hükmediyor (%{possession_ev})"
    return "GOL OLACAK (S)", f"Maç aktif — {shots_ev+shots_dep} isabetli şut, {dangerous_ev+dangerous_dep} tehlikeli atak"


# ================================================
# SONRAKI GOL KİM ATAR
# ================================================
def sonraki_gol_tahmini(mac, strateji):
    ev = mac.get('ev', '')
    dep = mac.get('dep', '')
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    possession_ev = mac.get('possession_ev', 50)
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)

    ev_skor = (possession_ev * 0.3) + (shots_ev * 5) + (dangerous_ev * 0.4)
    dep_skor = ((100 - possession_ev) * 0.3) + (shots_dep * 5) + (dangerous_dep * 0.4)

    if ev_skor > dep_skor * 1.3:
        return f"Sıradaki Gol: {ev[:15]}"
    elif dep_skor > ev_skor * 1.3:
        return f"Sıradaki Gol: {dep[:15]}"
    return "Sıradaki Gol: Her İki Taraf"


# ================================================
# KASA YÖNETİMİ
# ================================================
def kasa_hesapla(puan, dakika, ah_deger):
    ah_bonus = 0.5 if -1.5 <= ah_deger <= -0.75 else 0
    if puan >= 12:
        return 4.0
    elif puan >= 10:
        return 3.0 + ah_bonus
    elif puan >= 8:
        return 2.0 + ah_bonus
    elif puan >= 6:
        return 1.5
    return 1.0


# ================================================
# GEMİNİ AI — DERİN GERÇEK ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    if not GEMINI_KEY:
        return "AI aktif değil.", None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    dakika = mac.get('dakika', 0)
    son_gol = mac.get('son_gol', 0)
    gecen = dakika - son_gol if son_gol > 0 else dakika
    shots_ev = mac.get('shots_on_target_ev', 0)
    shots_dep = mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    corner_ev = mac.get('corner_ev', 0)
    corner_dep = mac.get('corner_dep', 0)
    ah = mac.get('ah_deger', 0)
    kirmizi = mac.get('kirmizi_kart', 0)
    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)

    if toplam_gol == 0:
        durum = "GOLsuz maç, her iki takım da henüz gol bulamamış"
    elif ev_gol == dep_gol:
        durum = f"Beraberlik ({ev_gol}-{dep_gol}), her iki taraf eşit"
    elif ev_gol > dep_gol:
        durum = f"{mac['ev']} {gol_fark} gol önde"
    else:
        durum = f"{mac['dep']} {gol_fark} gol önde"

    prompt = f"""Sen üst düzey bir futbol veri analisti ve risk uzmanısın.

MAÇ: {mac['ev']} {ev_gol}-{dep_gol} {mac['dep']} ({mac['lig']}) | DK: {dakika} | DURUM: {durum}

İSTATİSTİKLER:
- Şut: {shots_ev} vs {shots_dep} | Top: %{possession_ev} vs %{100-possession_ev}
- Tehlikeli Atak: {dangerous_ev} vs {dangerous_dep} | Corner: {corner_ev} vs {corner_dep}
- Son gol: {son_gol}. dk | Kırmızı: {kirmizi} | AH: {ah if ah != 0 else 'yok'}

GÖREV: Sadece bu verilere odaklanarak 3 cümleyi geçmeyen, "Winning Code onaylı" gibi kalıplar içermeyen keskin bir analiz yap.

JSON FORMATI:
{{
  "yorum": "analiziniz",
  "gir": true,
  "kasa": 1
