"""
MAC ANALIZ BOTU - MUKEMMEL SISTEM
Zamanlama:
- Hafta ici (Pzt-Cuma): 19:00 - 00:00
- Hafta sonu (Cmt-Pzr): 19:00 - 23:00
Format: Istenen gorunum + Derin Gemini analizi
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

# 3 ANAHTARLI AI HAVUZU (BURASI GÜNCELLENDİ)
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", "")
]
current_key_index = 0

MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

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
    gun = simdi.weekday()  # 0=Pzt, 6=Pzr
    if gun <= 4:  # Hafta ici Pzt-Cuma
        return 19 <= saat <= 23  # 19:00 - 23:59
    else:  # Hafta sonu Cmt-Pzr
        return 19 <= saat <= 22  # 19:00 - 22:59
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
    # ---- LİG KATSAYILARI (Volatility Index) ----
    LIG_KATSAYISI = {
        # Yüksek gol ligleri
        'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15,
        'Champions League': 1.1, 'La Liga': 1.1, 'Ligue 1': 1.1,
        'Serie A': 1.0, 'Super Lig': 1.1,
        # Düşük gol ligleri
        'Serie B': 0.9, 'Ligue 2': 0.9,
    }
    lig = mac.get('lig', '')
    lig_katsayisi = 1.0
    for lig_adi, katsayi in LIG_KATSAYISI.items():
        if lig_adi.lower() in lig.lower():
            lig_katsayisi = katsayi
            break

    wc = winning_code_kontrol(mac)

    # ---- WC DARBOĞAZ ESNETME ----
    # WC geçmese bile ekstrem değerlerde puanlamaya al
    puan = 0.0
    detay = []
    stratejiler = []

    dakika = max(mac.get('dakika', 1), 1)  # Sıfıra bölme önlemi
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
    kirmizi_taraf = mac.get('kirmizi_taraf', '')  # 'home' veya 'away'
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

    # DAPM ve SPM (Dakika başına hız)
    dapm_ev = round(dangerous_ev / dakika, 2)
    dapm_dep = round(dangerous_dep / dakika, 2)
    spm_ev = round(shots_ev / dakika, 3)
    spm_toplam = round(shots_toplam / dakika, 3)

    # Kontra atak tespiti: Az top ama çok şut = kontr
    kontra_ev = shots_ev >= 3 and possession_ev < 40
    kontra_dep = shots_dep >= 3 and possession_ev > 60

    # Extreme Value: WC geçmese bile ekstrem durumlar
    extreme_value = (
        shots_toplam >= 12 or
        possession_ev >= 65 or
        dapm_ev >= 1.5 or
        (toplam_gol == 0 and shots_toplam >= 10)
    )

    if not wc['gecti']:
        if extreme_value:
            # Extreme value durumunda 2 puan ver, devam et
            puan += 2
            detay.append(f"⚠️ WC Kısmi ({wc['detay']}) ama EXTREME VALUE +2.0")
            stratejiler.append("EXTREME_VALUE")
        else:
            return 0, [], "", wc  # Sinyal üretilmez
    else:
        # WC tam geçti
        puan += 4
        detay.append(
            f"✅ Winning Code Onayı (VU:{wc['VU_val']} TÜM:{wc['TUM_val']} "
            f"MA:{wc['MA_val']} DİYİ:{wc['DIYI_val']})"
        )

    # ---- DAPM — Dakika Başına Tehlikeli Atak ----
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

    # ---- SPM — Dakika Başına Şut ----
    if spm_toplam >= 0.25:  # 4 dk'da 1 şut
        puan += 1.5
        detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5")
        stratejiler.append("YUKSEK_SUT_HIZI")

    # ---- KONTRA ATAK TESPİTİ ----
    if kontra_dep:
        puan += 1.5
        detay.append(f"⚡ Dep Kontra Atak! (%{possession_ev} top ama {shots_dep} şut) +1.5")
        stratejiler.append("KONTRA_ATAK_DEP")
    if kontra_ev:
        puan += 1.5
        detay.append(f"⚡ Ev Kontra Atak! ({shots_ev} şut ama düşük top) +1.5")
        stratejiler.append("KONTRA_ATAK_EV")

    # ---- BERABERLIK BONUSU ----
    if esit_skor:
        puan += 1.5
        detay.append(f"🤝 Skor Dengede +1.5")
        stratejiler.append("BERABERLIK")

    # Value: favori geri ama dominant
    if dep_gol > ev_gol and possession_ev >= 55 and shots_ev > shots_dep:
        puan += 2
        detay.append(f"💎 VALUE: Ev geride ama dominant +2.0")
        stratejiler.append("VALUE_GIRISI")

    # ---- GOL BAZLI ----
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

    # ---- SHOTS ----
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

    # ---- POSSESSION ----
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

    # ---- DANGEROUS ATTACKS (Kümülatif) ----
    if dangerous_toplam >= 100:
        puan += 2
        detay.append(f"🔥 {dangerous_toplam} Tehlikeli Atak +2.0")
        stratejiler.append("YUKSEK_ATAK")
    elif dangerous_toplam >= 60:
        puan += 1
        detay.append(f"🔥 {dangerous_toplam} Tehlikeli Atak +1.0")

    # ---- CORNER ----
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

    # ---- ASIAN HANDICAP ----
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

    # ---- KIRMIZI KART — Derin Mantık ----
    if kirmizi >= 1:
        # Favori mi, underdog mu kırmızı gördü?
        ev_skoru = possession_ev + shots_ev * 3
        dep_skoru = (100 - possession_ev) + shots_dep * 3
        ev_favori = ev_skoru > dep_skoru

        if kirmizi_taraf == 'home' or (not kirmizi_taraf and ev_favori):
            # Güçlü takım kırmızı gördü → açık alan, kaos
            puan += 2
            detay.append(f"🟥 Güçlü Taraf Kırmızı Kart! Kaos + Açık Alan +2.0")
            stratejiler.append("KIRMIZI_KAOS")
        else:
            # Zayıf takım kırmızı gördü → park the bus, maç kilitlenir
            puan += 0.5
            detay.append(f"🟥 Zayıf Taraf Kırmızı (Savunma Kapanabilir) +0.5")
            stratejiler.append("KIRMIZI_KILITLI")

    # ---- 0-0 AKTİF ----
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50:
        puan += 2
        detay.append(f"💥 0-0 Çok Aktif (VALUE!) +2.0")
        stratejiler.append("GOLSUZ_AKTIF")

    # ---- 2. YARI BAŞI ----
    if 45 <= dakika <= 60 and shots_toplam >= 6:
        puan += 1
        detay.append(f"⏱️ 2. Yarı + {shots_toplam} Şut +1.0")
        stratejiler.append("2Y_BASLANGIC")

    # Son gol taze
    if son_gol >= 70:
        puan += 1
        detay.append(f"⚡ Son Gol {son_gol}dk (Taze) +1.0")

    # ---- ALTIN PENCERE ----
    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus
        detay.append(f"🔥 {z_label}")
        if z_strateji:
            stratejiler.append(z_strateji)

    # ---- LİG KATSAYISI UYGULA ----
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
    # Genel
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
# GEMİNİ AI — DERİN GERÇEK ANALİZ (BURASI GÜNCELLENDİ)
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys:
        return "AI aktif değil.", 1.5

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

    sut_fark = abs(shots_ev - shots_dep)
    atak_fark = abs(dangerous_ev - dangerous_dep)
    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)

    # Maçın durumunu yorumla
    durum = ""
    if toplam_gol == 0:
        durum = "GOLsuz maç, her iki takım da henüz gol bulamamış"
    elif ev_gol == dep_gol:
        durum = f"Beraberlik ({ev_gol}-{dep_gol}), her iki taraf eşit"
    elif ev_gol > dep_gol:
        durum = f"{mac['ev']} {gol_fark} gol önde"
    else:
        durum = f"{mac['dep']} {gol_fark} gol önde"

    prompt = f"""Sen çok deneyimli bir canlı bahis analistsin. Görülmüşsün, çok maç izlemişsin.

MAÇ: {mac['ev']} {ev_gol}-{dep_gol} {mac['dep']}
LİG: {mac['lig']} | DAKİKA: {dakika} | DURUM: {durum}

İSTATİSTİKLER:
- Şut (isabetli): {mac['ev']}={shots_ev} vs {mac['dep']}={shots_dep}
- Top: {mac['ev']}=%{possession_ev} vs {mac['dep']}=%{100-possession_ev}
- Tehlikeli Atak: {mac['ev']}={dangerous_ev} vs {mac['dep']}={dangerous_dep}
- Corner: {mac['ev']}={corner_ev} vs {mac['dep']}={corner_dep}
- Son gol: {son_gol}. dk ({gecen} dk önce)
- Kırmızı kart: {kirmizi}
- Asian Handicap: {ah if ah != 0 else 'yok'}

BOT KARARI: {tahmin} | Puan: {puan}/12

GÖREV — İKİ KATMANLI ANALİZ YAP:

KATMAN 1 — İstatistiklerin söylediği:
{mac['ev']} {shots_ev} şutla daha mı tehlikeli yoksa {shots_dep} şutlu {mac['dep']} mi? Son gol {gecen}dk önce — momentum hala var mı?

KATMAN 2 — İstatistiklerin SÖYLEMEDIĞI (en önemli kısım):
Şu soruları düşün ve maça özgü cevapla:
- Bu skor durumunda ({ev_gol}-{dep_gol}) öndeki takım kasıtlı yavaşlıyor olabilir mi?
- {dakika}. dakikada oyuncular fiziksel olarak yorulmuş olabilir mi? Tempo düşmüş mü?
- Deplasman takımı kontr atak için bekliyorsa istatistikler yanıltıcı olabilir mi?
- {mac['lig']} liginde bu tür maçlar genelde nasıl biter? Gol geç mi gelir?
- İstatistikler güçlü görünse de gol GELMEYEBİLİR mi? Neden?

KURAL: "Atak sürekliliği", "Winning Code onaylı", "gol ihtimalini güçlendiriyor" gibi kalıp cümleler KESİNLİKLE YASAK.
Sadece bu maça özel, somut, keskin gözlem yap.
Maksimum 3 cümle. Türkçe.

JSON: {{"yorum": "iki_katmanli_ozgun_analiz", "gir": true, "kasa": 1.5}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 250}
    }

    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={active_key}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        if "```" in text:
                            parts = text.split("```")
                            for part in parts:
                                if part.startswith("json") or part.startswith("{"):
                                    text = part.replace("json", "").strip()
                                    break
                        result = json.loads(text)
                        yorum = result.get('yorum', '')
                        kasa = float(result.get('kasa', 1.5))
                        if not result.get('gir', True):
                            kasa = 0.0
                        logger.info(f"Gemini OK: gir={result.get('gir')} kasa={kasa}")
                        return yorum, kasa
                    elif resp.status == 429:
                        logger.warning(f"Gemini limit doldu, diger key'e geciliyor...")
                        current_key_index = (current_key_index + 1) % len(valid_keys)
                    else:
                        logger.error(f"Gemini hata: {resp.status}")
                        current_key_index = (current_key_index + 1) % len(valid_keys)
        except Exception as e:
            logger.error(f"Gemini baglanti hatasi: {e}")
            current_key_index = (current_key_index + 1) % len(valid_keys)
        
        await asyncio.sleep(1)

    return None, None


# ================================================
# RAPORLAR
# ================================================
async def haftalik_rapor(bot):
    try:
        rows = await db_pool.fetch(
            "SELECT * FROM sinyaller WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'",
            datetime.now() - timedelta(days=7)
        )
        if not rows:
            return
        toplam = len(rows)
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        oran = round(kazanan / toplam * 100, 1)
        strateji_stats = {}
        for r in rows:
            s = r.get('strateji') or 'Diger'
            if s not in strateji_stats:
                strateji_stats[s] = {'k': 0, 't': 0}
            strateji_stats[s]['t'] += 1
            if r['sonuc'] == 'TUTTU':
                strateji_stats[s]['k'] += 1
        en_iyi = max(
            [(k, v) for k, v in strateji_stats.items() if v['t'] >= 2],
            key=lambda x: x[1]['k'] / x[1]['t'],
            default=('Yok', {'k': 0, 't': 1})
        )
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"📊 HAFTALIK RAPOR\n\nToplam: {toplam} | Kazanan: {kazanan}\nBaşarı: %{oran}\nEn iyi: {en_iyi[0]}"
        )
    except Exception as e:
        logger.error(f"Haftalik: {e}")


async def aylik_rapor(bot):
    try:
        rows = await db_pool.fetch(
            "SELECT * FROM sinyaller WHERE bildirim_zamani > $1 AND sonuc != 'BEKLIYOR'",
            datetime.now() - timedelta(days=30)
        )
        if not rows:
            return
        toplam = len(rows)
        kazanan = len([r for r in rows if r['sonuc'] == 'TUTTU'])
        oran = round(kazanan / toplam * 100, 1)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"📊 AYLIK RAPOR\nToplam: {toplam} | Kazanan: {kazanan}\nBaşarı: %{oran}"
        )
    except Exception as e:
        logger.error(f"Aylik: {e}")


# ================================================
# SONUÇ KONTROLÜ
# ================================================
def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    yeni_ev = fin_ev - bas_ev
    yeni_dep = fin_dep - bas_dep
    toplam = yeni_ev + yeni_dep
    if "GOL OLACAK" in tahmin or "ÜST" in tahmin:
        return "TUTTU" if toplam >= 1 else "DSTU"
    elif "EV GOL" in tahmin:
        return "TUTTU" if yeni_ev >= 1 else "DSTU"
    elif "DEP GOL" in tahmin:
        return "TUTTU" if yeni_dep >= 1 else "DSTU"
    elif "EV KAZANIR" in tahmin:
        return "TUTTU" if fin_ev >= fin_dep else "DSTU"
    elif "DEP KAZANIR" in tahmin:
        return "TUTTU" if fin_dep >= fin_ev else "DSTU"
    return "BELIRSIZ"


# ================================================
# ODDS ÇEK — SADECE ADAY MAÇLARA
# ================================================
async def odds_cek(fixture_ids: list):
    """
    Sadece sinyal geçen maçlara odds sor.
    API hak tasarrufu: Her döngüde max 5-10 istek.
    """
    if not fixture_ids:
        return {}

    odds_map = {}
    try:
        async with aiohttp.ClientSession() as session:
            # Tüm canlı odds tek seferde çek
            url = f"{BASE_URL}/odds/live"
            async with session.get(
                url, headers=API_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get('response', [])
                    logger.info(f"Odds API: {len(results)} sonuç")

                    for item in results:
                        fid = str(item.get('fixture', {}).get('id', ''))
                        if fid not in [str(x) for x in fixture_ids]:
                            continue

                        ah_deger = 0.0
                        ev_oran = 0.0
                        dep_oran = 0.0

                        for bet in item.get('bets', []):
                            bet_name = bet.get('name', '').lower()
                            if 'asian handicap' in bet_name:
                                for v in bet.get('values', []):
                                    val_str = v.get('value', '')
                                    odd_val = float(v.get('odd', 0) or 0)
                                    if 'home' in val_str.lower():
                                        ev_oran = odd_val
                                        # AH değerini parse et: "Home -1.5" → -1.5
                                        for part in val_str.split():
                                            try:
                                                ah_deger = float(part)
                                                break
                                            except:
                                                pass
                                    elif 'away' in val_str.lower():
                                        dep_oran = odd_val

                        odds_map[fid] = {
                            'ah_deger': ah_deger,
                            'ev_oran': ev_oran,
                            'dep_oran': dep_oran,
                        }
                else:
                    logger.error(f"Odds API hata: {resp.status}")
    except Exception as e:
        logger.error(f"Odds cekme: {e}")

    return odds_map


# ================================================
# VERİ ÇEKME
# ================================================
async def macları_cek():
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/fixtures?live=all",
                headers=API_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"API hata: {resp.status}")
                    return maclar
                data = await resp.json()
                if data.get('errors'):
                    logger.error(f"API errors: {data['errors']}")
                    return maclar
                fixtures = data.get('response', [])
                logger.info(f"{len(fixtures)} canli mac")

                for f in fixtures:
                    try:
                        fixture = f.get('fixture', {})
                        teams = f.get('teams', {})
                        goals = f.get('goals', {})
                        league = f.get('league', {})

                        mac_id = str(fixture.get('id', ''))
                        ev = teams.get('home', {}).get('name', '?')
                        dep = teams.get('away', {}).get('name', '?')
                        lig = league.get('name', '?')
                        dakika = int(fixture.get('status', {}).get('elapsed', 0) or 0)

                        if dakika < 5 or dakika > 88:
                            continue

                        ev_gol = int(goals.get('home', 0) or 0)
                        dep_gol = int(goals.get('away', 0) or 0)
                        home_id = teams.get('home', {}).get('id')

                        stats = {
                            'shots_on_target_ev': 0, 'shots_on_target_dep': 0,
                            'possession_ev': 50, 'possession_dep': 50,
                            'dangerous_attacks_ev': 0, 'dangerous_attacks_dep': 0,
                            'sari_kart_ev': 0, 'sari_kart_dep': 0,
                            'kirmizi_kart': 0, 'son_gol': 0,
                            'corner_ev': 0, 'corner_dep': 0, 'corner_toplam': 0,
                            'ah_deger': 0.0,
                        }

                        for stat_group in f.get('statistics', []):
                            team_id = stat_group.get('team', {}).get('id')
                            is_home = (team_id == home_id)
                            for s in stat_group.get('statistics', []):
                                tip = s.get('type', '').lower()
                                val = s.get('value', 0)
                                if val is None:
                                    val = 0
                                if isinstance(val, str) and '%' in val:
                                    try:
                                        val = int(val.replace('%', '').strip())
                                    except:
                                        val = 0
                                else:
                                    try:
                                        val = int(val)
                                    except:
                                        val = 0

                                if 'shots on goal' in tip or 'on target' in tip:
                                    if is_home:
                                        stats['shots_on_target_ev'] = val
                                    else:
                                        stats['shots_on_target_dep'] = val
                                elif 'ball possession' in tip:
                                    if is_home:
                                        stats['possession_ev'] = val
                                    else:
                                        stats['possession_dep'] = val
                                elif 'dangerous attacks' in tip:
                                    if is_home:
                                        stats['dangerous_attacks_ev'] = val
                                    else:
                                        stats['dangerous_attacks_dep'] = val
                                elif 'yellow cards' in tip:
                                    if is_home:
                                        stats['sari_kart_ev'] = val
                                    else:
                                        stats['sari_kart_dep'] = val
                                elif 'red cards' in tip:
                                    stats['kirmizi_kart'] += val
                                elif 'corner kicks' in tip or 'corners' in tip:
                                    if is_home:
                                        stats['corner_ev'] = val
                                    else:
                                        stats['corner_dep'] = val
                                    stats['corner_toplam'] = stats['corner_ev'] + stats['corner_dep']

                        # Odds
                        for bet in f.get('odds', {}).get('bets', []):
                            if 'asian handicap' in bet.get('name', '').lower():
                                for v in bet.get('values', []):
                                    if 'home' in v.get('value', '').lower():
                                        for p in v.get('value', '').split():
                                            try:
                                                stats['ah_deger'] = float(p)
                                                break
                                            except:
                                                pass

                        # Son gol
                        son_gol = 0
                        for event in f.get('events', []):
                            if event.get('type') == 'Goal':
                                gdk = int(event.get('time', {}).get('elapsed', 0) or 0)
                                if gdk > son_gol:
                                    son_gol = gdk
                        stats['son_gol'] = son_gol

                        maclar.append({
                            'id': mac_id, 'ev': ev, 'dep': dep, 'lig': lig,
                            'dakika': dakika, 'ev_gol': ev_gol, 'dep_gol': dep_gol,
                            **stats
                        })
                    except Exception as e:
                        logger.error(f"Mac parse: {e}")
    except Exception as e:
        logger.error(f"API: {e}")
    return maclar


# ================================================
# BİLDİRİM — İSTENEN FORMAT
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    kasa = ai_kasa if ai_kasa is not None else kasa_hesapla(
        puan, mac['dakika'], mac.get('ah_deger', 0)
    )

    # AI iptal mi?
    if kasa == 0 and ai_yorum:
        mesaj = (
            f"⚠️ AI UYARISI — GİRME!\n"
            f"{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
            f"{mac['lig']} | {mac['dakika']}. Dk\n\n"
            f"🧠 AI: {ai_yorum}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, 0)
        return

    # Emoji ve karar
    if puan >= 10:
        karar_emoji = "🔥🔥"
        karar = "KESİN GİR"
    elif puan >= 8:
        karar_emoji = "🔥"
        karar = "KESİN GİR"
    elif puan >= 6:
        karar_emoji = "✅"
        karar = "GİREBİLİRSİN"
    else:
        karar_emoji = "⚠️"
        karar = "DİKKATLİ OL"

    # Sonraki gol tahmini
    sonraki = sonraki_gol_tahmini(mac, strateji)

    # İstatistik satırı
    ah = mac.get('ah_deger', 0)
    corner_toplam = mac.get('corner_toplam', 0)
    istat_satirlari = (
        f"⚽ Şut: {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} "
        f"| 🏃 Top: %{mac['possession_ev']}/%{mac.get('possession_dep',50)}\n"
        f"💥 Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']} "
        f"| 🚩 Corner: {mac.get('corner_ev',0)}/{mac.get('corner_dep',0)}"
        + (f" | 📈 AH: {ah}" if ah != 0 else "")
    )

    # Detay listesi (max 4)
    detay_str = "\n".join([f"- {d}" for d in detay[:4]])

    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 SİNYAL PUANI: {puan}/12\n"
        f"🎯 STRATEJİ: {sonraki}\n"
        f"────────────────────\n"
        f"📝 SİSTEM ANALİZİ:\n"
        f"{detay_str}\n"
        f"────────────────────\n"
        f"📊 İSTATİSTİKLER:\n"
        f"{istat_satirlari}\n"
        f"────────────────────\n"
        f"🧠 AI ÖZGÜN YORUMU:\n"
        f"{ai_yorum if ai_yorum else 'Analiz yapılamadı.'}\n"
        f"────────────────────\n"
        f"💡 TAHMİN: {tahmin}\n"
        f"📌 NEDEN: {neden}\n"
        f"────────────────────\n"
        f"💰 KASA: %{kasa}\n"
        f"{'═'*20}\n"
        f"{karar_emoji} {karar}\n"
        f"{'═'*20}"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum or "", kasa)
        logger.info(f"✅ Bildirim: {mac['ev']} vs {mac['dep']} | {puan}p | {tahmin} | %{kasa}")
    except Exception as e:
        logger.error(f"Bildirim: {e}")


async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "✅ TUTTU!" if sonuc == "TUTTU" else "❌ DÜŞTÜ!"
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"📊 SONUÇ: {ev} {fin_ev}-{fin_dep} {dep}\n{emoji}\n💡 Tahmin: {tahmin}"
    )
    await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)


# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    try:
        simdi = datetime.now()
        gun_str = "Hafta Sonu" if simdi.weekday() >= 5 else "Hafta İçi"
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🤖 MAÇ ANALİZ BOTU — AKTİF\n\n"
                "✅ Winning Code (VU/TÜM/MA/DİYİ)\n"
                "✅ Altın Pencere Bonusları\n"
                "✅ Beraberlik & Value Bonusu\n"
                "✅ Asian Handicap Entegrasyonu\n"
                "✅ Corner Eşikleri\n"
                "✅ Cooling Off Koruması\n"
                "✅ Gemini AI Derin Analiz\n"
                "✅ Net Tahmin + Neden\n\n"
                "⏰ Zamanlama:\n"
                "Hafta İçi: 19:00 — 00:00\n"
                "Hafta Sonu: 19:00 — 23:00\n\n"
                f"📅 Şu an: {gun_str} modu\n"
                f"🎯 Min puan: {MIN_PUAN}/12\n\n"
                "Hazır! Sinyaller gelince bildirim atacağım 🚀"
            )
        )
        logger.info("Bot basladi!")
    except Exception as e:
        logger.error(f"Baslangic: {e}")

    uyku_bildirimi = False
    son_haftalik = None
    son_aylik = None

    while True:
        try:
            simdi = datetime.now()
            bugun = simdi.date()

            if simdi.weekday() == 0 and simdi.hour == 9 and son_haftalik != bugun:
                await haftalik_rapor(bot)
                son_haftalik = bugun

            if simdi.day == 1 and simdi.hour == 9 and son_aylik != bugun:
                await aylik_rapor(bot)
                son_aylik = bugun

            if not aktif_mi():
                if not uyku_bildirimi:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"😴 UYKU MODU\nAPI hakkı korunuyor.\n⏰ Sonraki: {sonraki_aktif()}"
                    )
                    uyku_bildirimi = True
                await asyncio.sleep(1800)
                continue
            else:
                if uyku_bildirimi:
                    gun = simdi.weekday()
                    gun_str = "Hafta Sonu" if gun >= 5 else "Hafta İçi"
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"⚡ UYANDIM! {gun_str} modu aktif\n🔍 Maç taraması başlıyor..."
                    )
                    uyku_bildirimi = False

            maclar = await macları_cek()
            aktif_idler = [m['id'] for m in maclar]

            # Biten maçlar
            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(
                        bilgi['tahmin'],
                        bilgi['bas_ev'], bilgi['bas_dep'],
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    await sonuc_bildir(
                        bot, mac_id, bilgi['ev'], bilgi['dep'],
                        bilgi['tahmin'], sonuc,
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    del biten_maclar[mac_id]
                    await asyncio.sleep(1)

            # ADIM 1: Tüm maçları tara, takip listesini güncelle
            adaylar = []
            for mac in maclar:
                puan, detay, strateji, wc = sinyal_hesapla(mac)
                mac_id = mac['id']

                # Takip listesini güncelle
                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = {
                        'ev': mac['ev'], 'dep': mac['dep'],
                        'tahmin': bildirim_gonderilen[mac_id]['tahmin'],
                        'bas_ev': bildirim_gonderilen[mac_id]['ev_gol'],
                        'bas_dep': bildirim_gonderilen[mac_id]['dep_gol'],
                        'son_ev': mac['ev_gol'],
                        'son_dep': mac['dep_gol'],
                    }

                if puan == 0:
                    continue

                if puan >= MIN_PUAN:
                    onceki = bildirim_gonderilen.get(mac_id, {}).get('puan', 0)
                    if puan > onceki:
                        adaylar.append((mac, puan, detay, strateji, wc))

            # ADIM 2: Sadece aday maçlara odds sor (API hak tasarrufu!)
            if adaylar:
                aday_idler = [m[0]['id'] for m in adaylar]
                logger.info(f"{len(adaylar)} aday mac icin odds sorgulanıyor...")
                odds_data = await odds_cek(aday_idler)

                # Odds'u maçlara ekle ve puanı yeniden hesapla
                yeni_adaylar = []
                for mac, puan, detay, strateji, wc in adaylar:
                    if mac['id'] in odds_data:
                        mac['ah_deger'] = odds_data[mac['id']].get('ah_deger', 0.0)
                        mac['ev_oran'] = odds_data[mac['id']].get('ev_oran', 0.0)
                        mac['dep_oran'] = odds_data[mac['id']].get('dep_oran', 0.0)
                        # AH geldi, puanı yeniden hesapla
                        puan, detay, strateji, wc = sinyal_hesapla(mac)
                        logger.info(f"Odds OK: {mac['ev']} AH={mac['ah_deger']}")
                    yeni_adaylar.append((mac, puan, detay, strateji, wc))
                adaylar = yeni_adaylar

            # ADIM 3: Aday maçları bildir
            for mac, puan, detay, strateji, wc in adaylar:
                mac_id = mac['id']

                # Cooling Off
                cooling, cooling_msg = cooling_off(mac)
                if cooling:
                    logger.info(f"Cooling Off: {mac['ev']} - {cooling_msg}")
                    continue

                tahmin, neden = tavsiye_uret(mac, strateji)

                # Gemini AI
                ai_yorum, ai_kasa = await gemini_analiz(
                    mac, puan, strateji, tahmin, neden, wc
                )
                if ai_yorum is None:
                    ai_yorum = "AI analiz geçici olarak kullanılamıyor."

                await bildirim_gonder(
                    bot, mac, puan, detay, strateji,
                    tahmin, neden, ai_yorum, ai_kasa
                )

                bildirim_gonderilen[mac_id] = {
                    'puan': puan, 'tahmin': tahmin,
                    'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']
                }
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Ana dongu: {e}")

        await asyncio.sleep(420)  # 7 dakika


if __name__ == "__main__":
    logger.info("BOT STARTED")
    asyncio.run(ana_dongu())
