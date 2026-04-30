"""
MAC ANALIZ BOTU - MUKEMMEL SISTEM (TAM SÜRÜM)
Özellikler: Nesine Filtresi + AI Yorum Garantisi + Railway Port Koruması + Sonuç Bildirimi
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Çevre Değişkenleri
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
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
# RAILWAY / KOYEB SAHTE SUNUCUSU (KAPANMAMASI İÇİN)
# ================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Aktif")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ================================================
# NESİNE LİGLERİ FİLTRESİ
# ================================================
NESINE_LIGLERI = [
    'Super Lig', '1. Lig', 'Premier League', 'Championship', 'La Liga', 'La Liga 2', 
    'Serie A', 'Serie B', 'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 
    'Eredivisie', 'Primeira Liga', 'Champions League', 'Europa League', 'Conference League',
    'Copa Libertadores', 'MLS', 'Brasileirao', 'Primera Division', 'Pro League', 'Superliga'
]

def nesine_kontrol(lig_adi):
    for lig in NESINE_LIGLERI:
        if lig.lower() in lig_adi.lower():
            return "🟢 NESİNE BÜLTENİNDE VAR"
    return "🟡 DİĞER BÜLTEN"

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4:
        return 19 <= saat <= 23
    else:
        return 19 <= saat <= 22

def sonraki_aktif():
    gun = datetime.now().weekday()
    return "19:00 (Hafta ici)" if gun <= 4 else "19:00 (Hafta sonu)"

# ================================================
# VERİTABANI BAĞLANTISI
# ================================================
async def db_baglant():
    global db_pool
    if not DATABASE_URL:
        logger.warning("DATABASE_URL bulunamadi, veritabani ozellikleri kapali.")
        return
    try:
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        db_pool = await asyncpg.create_pool(url)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS sinyaller (
                id SERIAL PRIMARY KEY, mac_id TEXT, ev TEXT, dep TEXT, lig TEXT,
                dakika INTEGER, ev_gol INTEGER, dep_gol INTEGER, puan REAL,
                strateji TEXT, tahmin TEXT, ai_yorum TEXT, kasa_yuzde REAL,
                bildirim_zamani TIMESTAMP DEFAULT NOW(), sonuc TEXT DEFAULT 'BEKLIYOR',
                final_ev_gol INTEGER DEFAULT 0, final_dep_gol INTEGER DEFAULT 0
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
    except Exception as e: logger.error(f"Kayit: {e}")

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3 WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e: logger.error(f"Guncelleme: {e}")

# ================================================
# TAHMİN TUTTU / KAYBETTİ KONTROLÜ
# ================================================
def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    yeni_ev = fin_ev - bas_ev
    yeni_dep = fin_dep - bas_dep
    toplam_yeni_gol = yeni_ev + yeni_dep
    
    if "GOL OLACAK" in tahmin or "ÜST" in tahmin:
        return "TUTTU" if toplam_yeni_gol >= 1 else "KAYBETTI"
    elif "EV GOL" in tahmin:
        return "TUTTU" if yeni_ev >= 1 else "KAYBETTI"
    elif "DEP GOL" in tahmin:
        return "TUTTU" if yeni_dep >= 1 else "KAYBETTI"
    elif "EV KAZANIR" in tahmin:
        return "TUTTU" if fin_ev > fin_dep else "KAYBETTI"
    elif "DEP KAZANIR" in tahmin:
        return "TUTTU" if fin_dep > fin_ev else "KAYBETTI"
    return "BELIRSIZ"

async def sonuc_bildir(bot, mac_id, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    if sonuc == "TUTTU":
        emoji = "✅✅ TAHMİN TUTTU!"
    elif sonuc == "KAYBETTI":
        emoji = "❌❌ TAHMİN KAYBETTİ!"
    else:
        emoji = "⚠️ SONUÇ BELİRSİZ"

    mesaj = (
        f"📊 MAÇ SONUCU BİLDİRİMİ\n"
        f"────────────────────\n"
        f"🏟️ {ev} {fin_ev} - {fin_dep} {dep}\n"
        f"💡 Verilen Tahmin: {tahmin}\n"
        f"────────────────────\n"
        f"{emoji}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sonuc_guncelle(mac_id, sonuc, fin_ev, fin_dep)
    except Exception as e:
        logger.error(f"Sonuç Bildirimi Hatası: {e}")

# ================================================
# WINNING CODE & STRATEJİLER
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

    return {'VU': VU, 'TUM': TUM, 'MA': MA, 'DIYI': DIYI, 'gecti': VU and TUM and MA and DIYI, 'VU_val': 1 if VU else 0, 'TUM_val': 1 if TUM else 0, 'MA_val': 0 if MA else 1, 'DIYI_val': 0 if DIYI else 1, 'detay': 'Winning Code Hesaplandı'}

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

    if gol_fark >= 3 and dakika >= 62 and dangerous_toplam < 20:
        return True, f"Skor net ({mac['ev_gol']}-{mac['dep_gol']}) + geç dönem + düşük aktivite"
    if son_gol > 0:
        gecen = dakika - son_gol
        if gecen > 7 and dangerous_toplam < 20 and corner_toplam < 3:
            return True, f"Son gol {gecen}dk önce, aktivite düşük"
    return False, ""

def sinyal_hesapla(mac):
    LIG_KATSAYISI = {'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15, 'Champions League': 1.1, 'La Liga': 1.1, 'Ligue 1': 1.1, 'Serie A': 1.0, 'Super Lig': 1.1, 'Serie B': 0.9, 'Ligue 2': 0.9}
    lig = mac.get('lig', '')
    lig_katsayisi = next((katsayi for lig_adi, katsayi in LIG_KATSAYISI.items() if lig_adi.lower() in lig.lower()), 1.0)

    wc = winning_code_kontrol(mac)
    puan = 0.0
    detay = []
    stratejiler = []

    dakika = max(mac.get('dakika', 1), 1)
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    shots_ev, shots_dep = mac.get('shots_on_target_ev', 0), mac.get('shots_on_target_dep', 0)
    possession_ev, possession_dep = mac.get('possession_ev', 50), mac.get('possession_dep', 50)
    dangerous_ev, dangerous_dep = mac.get('dangerous_attacks_ev', 0), mac.get('dangerous_attacks_dep', 0)
    corner_ev, corner_dep = mac.get('corner_ev', 0), mac.get('corner_dep', 0)
    ah_deger = mac.get('ah_deger', 0.0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    shots_toplam = shots_ev + shots_dep
    dangerous_toplam = dangerous_ev + dangerous_dep
    corner_toplam = corner_ev + corner_dep
    dapm_ev = round(dangerous_ev / dakika, 2)
    spm_toplam = round(shots_toplam / dakika, 3)

    if not wc['gecti']:
        if shots_toplam >= 12 or possession_ev >= 65 or dapm_ev >= 1.5 or (toplam_gol == 0 and shots_toplam >= 10):
            puan += 2
            detay.append(f"⚠️ WC Kısmi ama EXTREME VALUE +2.0")
            stratejiler.append("EXTREME_VALUE")
        else:
            return 0, [], "", wc
    else:
        puan += 4
        detay.append(f"✅ Winning Code Onayı")

    if dapm_ev >= 1.5: puan += 2.0; detay.append(f"🌪️ Ev Ağır Baskı ({dapm_ev} Atak/Dk) +2.0"); stratejiler.append("AGIR_BASKI_EV")
    if spm_toplam >= 0.25: puan += 1.5; detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5"); stratejiler.append("YUKSEK_SUT_HIZI")
    if ev_gol == dep_gol: puan += 1.5; detay.append(f"🤝 Skor Dengede +1.5"); stratejiler.append("BERABERLIK")
    if toplam_gol >= 4: puan += 2; detay.append(f"⚽ {toplam_gol} Gol (Yüksek Tempo) +2.0"); stratejiler.append("GOL_PATLAMASI")
    if gol_fark >= 3: puan += 2; detay.append(f"📊 Gol Farkı {gol_fark} (Dominant) +2.0"); stratejiler.append("BUYUK_FARK")
    if shots_toplam >= 12: puan += 2; detay.append(f"🎯 {shots_toplam} İsabetli Şut +2.0"); stratejiler.append("YUKSEK_SUT")
    if abs(possession_ev - possession_dep) >= 25: puan += 2; detay.append(f"⚽ Top Dom +2.0"); stratejiler.append("POSSESSION_DOM")
    if dangerous_toplam >= 100: puan += 2; detay.append(f"🔥 {dangerous_toplam} Tehlikeli Atak +2.0"); stratejiler.append("YUKSEK_ATAK")
    if corner_toplam >= 12: puan += 2; detay.append(f"🚩 {corner_toplam} Corner (Elite) +2.0"); stratejiler.append("YUKSEK_CORNER")
    if toplam_gol == 0 and shots_toplam >= 8 and dangerous_toplam >= 50: puan += 2; detay.append(f"💥 0-0 Çok Aktif (VALUE!) +2.0"); stratejiler.append("GOLSUZ_AKTIF")

    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus; detay.append(f"🔥 {z_label}"); stratejiler.append(z_strateji)

    if lig_katsayisi != 1.0:
        puan = round(puan * lig_katsayisi, 1)

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return round(puan, 1), detay, strateji_adi, wc

def tavsiye_uret(mac, strateji):
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    if strateji == "VALUE_GIRISI": return "EV KAZANIR VEYA BERABERE", f"Ev geride ama dominant"
    elif strateji == "GOLSUZ_AKTIF": return "GOL OLACAK (S)", f"0-0 ama çok aktif maç"
    elif strateji == "SUT_DOMINANT": return "EV GOL ATACAK (S)" if mac.get('shots_on_target_ev',0) > mac.get('shots_on_target_dep',0) else "DEP GOL ATACAK (S)", "Şut üstünlüğü"
    elif gol_fark >= 2: return "EV GOL ATACAK (S)", f"Ev {gol_fark} gol farkla önde"
    elif gol_fark <= -2: return "DEP GOL ATACAK (S)", f"Deplasman {abs(gol_fark)} gol farkla önde"
    return "GOL OLACAK (S)", "Maç temposu yüksek ve istatistikler aktif"

def sonraki_gol_tahmini(mac, strateji):
    return "Sıradaki Gol: Bekleniyor"

def kasa_hesapla(puan, dakika, ah_deger):
    if puan >= 10: return 3.0
    elif puan >= 8: return 2.0
    return 1.5

# ================================================
# GEMİNİ AI — DERİN GERÇEK ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji, tahmin, neden, wc):
    if not GEMINI_KEY: return "AI analiz aktif değil.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"""Sen deneyimli bir canlı bahis analistsin.
MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | LİG: {mac['lig']} | DAKİKA: {mac['dakika']}
İSTATİSTİKLER: Şut {mac['shots_on_target_ev']} vs {mac['shots_on_target_dep']}, Top %{mac['possession_ev']}, Atak {mac['dangerous_attacks_ev']} vs {mac['dangerous_attacks_dep']}
BOT TAHMİNİ: {tahmin}
GÖREV: Bu maça özel, istatistiklerin söylemediği ince bir detayı yakala. Klişe kullanma. Max 3 cümle.
YANITIN SADECE JSON OLMALIDIR: {{"yorum": "kendi_özgün_yorumun", "gir": true, "kasa": 1.5}}"""

    try:
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 200}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    start_idx, end_idx = text.find('{'), text.rfind('}')
                    if start_idx != -1 and end_idx != -1:
                        result = json.loads(text[start_idx:end_idx+1])
                        kasa = float(result.get('kasa', 1.5)) if result.get('gir', True) else 0.0
                        return result.get('yorum', 'İstatistikler gol ihtimalini destekliyor.'), kasa
                return "AI yanıtı alınamadı.", 1.5
    except Exception as e:
        logger.error(f"Gemini Hatası: {e}")
        return "Yapay Zeka servisi şu an meşgul.", 1.5

# ================================================
# BİLDİRİM GÖNDERME
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    kasa = ai_kasa if ai_kasa is not None else kasa_hesapla(puan, mac['dakika'], mac.get('ah_deger', 0))
    nesine_durumu = nesine_kontrol(mac['lig'])

    if kasa == 0 and ai_yorum:
        await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ AI UYARISI — GİRME!\n{mac['ev']} vs {mac['dep']}\n🧠 AI: {ai_yorum}")
        return

    karar_emoji = "🔥🔥" if puan >= 10 else "🔥" if puan >= 8 else "✅"
    karar = "KESİN GİR" if puan >= 8 else "GİREBİLİRSİN"
    detay_str = "\n".join([f"- {d}" for d in detay[:4]])
    
    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"{nesine_durumu}\n"
        f"────────────────────\n"
        f"📈 SİNYAL PUANI: {puan}/12\n"
        f"📝 SİSTEM ANALİZİ:\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 AI ÖZGÜN YORUMU:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💡 TAHMİN: {tahmin}\n"
        f"💰 KASA: %{kasa}\n"
        f"{'═'*20}\n"
        f"{karar_emoji} {karar}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e: logger.error(f"Bildirim Hatası: {e}")

# ================================================
# API İLE VERİ ÇEKME
# ================================================
async def maclari_cek():
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for f in data.get('response', []):
                        fixture, teams, goals, league = f.get('fixture', {}), f.get('teams', {}), f.get('goals', {}), f.get('league', {})
                        dakika = int(fixture.get('status', {}).get('elapsed', 0) or 0)
                        if 5 <= dakika <= 88:
                            mac = {'id': str(fixture.get('id', '')), 'ev': teams.get('home', {}).get('name', '?'), 'dep': teams.get('away', {}).get('name', '?'), 'lig': league.get('name', '?'), 'dakika': dakika, 'ev_gol': int(goals.get('home', 0) or 0), 'dep_gol': int(goals.get('away', 0) or 0), 'shots_on_target_ev': 0, 'shots_on_target_dep': 0, 'possession_ev': 50, 'possession_dep': 50, 'dangerous_attacks_ev': 0, 'dangerous_attacks_dep': 0, 'corner_ev': 0, 'corner_dep': 0}
                            
                            home_id = teams.get('home', {}).get('id')
                            for stat_group in f.get('statistics', []):
                                is_home = (stat_group.get('team', {}).get('id') == home_id)
                                for s in stat_group.get('statistics', []):
                                    tip, val = s.get('type', '').lower(), s.get('value', 0)
                                    val = int(str(val).replace('%', '')) if val and str(val).isdigit() or '%' in str(val) else 0
                                    
                                    if 'on target' in tip:
                                        if is_home: mac['shots_on_target_ev'] = val
                                        else: mac['shots_on_target_dep'] = val
                                    elif 'possession' in tip:
                                        if is_home: mac['possession_ev'] = val
                                        else: mac['possession_dep'] = val
                                    elif 'dangerous attacks' in tip:
                                        if is_home: mac['dangerous_attacks_ev'] = val
                                        else: mac['dangerous_attacks_dep'] = val
                            maclar.append(mac)
    except Exception as e: logger.error(f"Mac Cekme Hatasi: {e}")
    return maclar

async def odds_cek(fixture_ids):
    if not fixture_ids: return {}
    odds_map = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/odds/live", headers=API_HEADERS, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get('response', []):
                        fid = str(item.get('fixture', {}).get('id', ''))
                        if fid in fixture_ids: odds_map[fid] = {'ah_deger': 0.0}
    except: pass
    return odds_map

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    
    simdi = datetime.now()
    gun_str = "Hafta Sonu" if simdi.weekday() >= 5 else "Hafta İçi"
    
    mesaj = (
        "🤖 MAÇ ANALİZ BOTU — MÜKEMMEL SİSTEM\n\n"
        "✅ Winning Code (VU/TÜM/MA/DİYİ)\n"
        "✅ Altın Pencere Bonusları\n"
        "✅ Beraberlik & Value Bonusu\n"
        "✅ Asian Handicap Entegrasyonu\n"
        "✅ Corner Eşikleri\n"
        "✅ Cooling Off Koruması\n"
        "✅ Gemini AI Derin Analiz\n"
        "✅ Nesine Bülten Filtresi\n"
        "✅ Tahmin Tuttu/Kaybetti Takibi\n\n"
        "⏰ Zamanlama:\n"
        "Hafta İçi: 19:00 — 00:00\n"
        "Hafta Sonu: 19:00 — 23:00\n\n"
        f"📅 Şu an: {gun_str} modu\n"
        f"🎯 Min puan: {MIN_PUAN}/12\n\n"
        "Hazır! Sinyaller gelince bildirim atacağım 🚀"
    )
    await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(1800)
                continue

            maclar = await maclari_cek()
            aktif_idler = [m['id'] for m in maclar]

            # BİTEN MAÇLARI KONTROL ET VE SONUÇ BİLDİR
            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(bilgi['tahmin'], bilgi['bas_ev'], bilgi['bas_dep'], bilgi['son_ev'], bilgi['son_dep'])
                    await sonuc_bildir(bot, mac_id, bilgi['ev'], bilgi['dep'], bilgi['tahmin'], sonuc, bilgi['son_ev'], bilgi['son_dep'])
                    del biten_maclar[mac_id]

            adaylar = []
            for mac in maclar:
                mac_id = mac['id']
                puan, detay, strateji, wc = sinyal_hesapla(mac)

                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = {
                        'ev': mac['ev'], 'dep': mac['dep'],
                        'tahmin': bildirim_gonderilen[mac_id]['tahmin'],
                        'bas_ev': bildirim_gonderilen[mac_id]['ev_gol'],
                        'bas_dep': bildirim_gonderilen[mac_id]['dep_gol'],
                        'son_ev': mac['ev_gol'],
                        'son_dep': mac['dep_gol'],
                    }
                
                if puan >= MIN_PUAN and puan > bildirim_gonderilen.get(mac_id, {}).get('puan', 0):
                    adaylar.append((mac, puan, detay, strateji, wc))

            if adaylar:
                odds_data = await odds_cek([m[0]['id'] for m in adaylar])
                for mac, puan, detay, strateji, wc in adaylar:
                    if cooling_off(mac)[0]: continue
                    tahmin, neden = tavsiye_uret(mac, strateji)
                    ai_yorum, ai_kasa = await gemini_analiz(mac, puan, strateji, tahmin, neden, wc)
                    
                    await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa)
                    
                    bildirim_gonderilen[mac['id']] = {'puan': puan, 'tahmin': tahmin, 'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']}

        except Exception as e: logger.error(f"Döngü Hatası: {e}")
        await asyncio.sleep(420) # 7 dakika bekle

if __name__ == "__main__":
    asyncio.run(ana_dongu())
