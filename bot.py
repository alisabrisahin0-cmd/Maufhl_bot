"""
MAC ANALIZ BOTU - MUKEMMEL SISTEM (BET365 & NESINE EDITION)
Zamanlama: Türkiye Saati 13:00 - 00:00 (Her Gün)
Format: İstenen görünüm + Derin Gemini analizi + Nesine Kontrol
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ================================================
# ÇEVRE DEĞİŞKENLERİ
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "") # Bet365 Motoru
DATABASE_URL = os.getenv("DATABASE_URL", "") # Opsiyonel DB
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]
MIN_PUAN = int(os.getenv("MIN_PUAN", "6"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
db_pool = None

# ================================================
# SAĞLIK KONTROLÜ (RAILWAY UYKU ENGELLEYİCİ)
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
# ZAMAN VE NESİNE FİLTRESİ
# ================================================
NESINE_LIGLERI = ['Super Lig', '1. Lig', 'Premier League', 'Championship', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1', 'Eredivisie']

def nesine_kontrol(lig_adi):
    return "🟢 NESİNE BÜLTENİ" if any(lig.lower() in lig_adi.lower() for lig in NESINE_LIGLERI) else "🟡 DİĞER BÜLTEN"

def aktif_mi():
    # Türkiye Saati (UTC+3) ile 13:00 - 23:59 arası her gün aktif
    simdi_tr = datetime.utcnow() + timedelta(hours=3)
    return 13 <= simdi_tr.hour <= 23

def sonraki_aktif():
    return "Yarın 13:00 (TR Saati)"

# ================================================
# VERİTABANI (OPSİYONEL - ÇÖKMEYİ ENGELLER)
# ================================================
async def db_baglant():
    global db_pool
    if not DATABASE_URL:
        logger.warning("DATABASE_URL bulunamadı. Veritabanı kaydı atlanacak, RAM üzerinden çalışılacak.")
        return
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
        logger.info("Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB Bağlantı Hatası: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum, kasa):
    if not db_pool: return
    try:
        await db_pool.execute("""
            INSERT INTO sinyaller
            (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol,
             puan, strateji, tahmin, ai_yorum, kasa_yuzde)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        """, mac['id'], mac['ev'], mac['dep'], mac['lig'],
            mac['dakika'], mac['ev_gol'], mac['dep_gol'],
            puan, strateji, tahmin, ai_yorum, kasa)
    except Exception as e: logger.error(f"Kayıt Hatası: {e}")

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
        'VU_val': 1 if VU else 0, 'TUM_val': 1 if TUM else 0,
        'MA_val': 0 if MA else 1, 'DIYI_val': 0 if DIYI else 1,
        'detay': "Eksik parametreler"
    }

# ================================================
# ALTIN PENCERE & COOLING OFF
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
        'Serie A': 1.0, 'Super Lig': 1.1, 'Serie B': 0.9, 'Ligue 2': 0.9,
    }
    lig = mac.get('lig', '')
    lig_katsayisi = next((katsayi for lig_adi, katsayi in LIG_KATSAYISI.items() if lig_adi.lower() in lig.lower()), 1.0)

    wc = winning_code_kontrol(mac)

    puan = 0.0
    detay = []
    stratejiler = []

    dakika = max(mac.get('dakika', 1), 1)
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    shots_ev, shots_dep = mac.get('shots_on_target_ev', 0), mac.get('shots_on_target_dep', 0)
    possession_ev, possession_dep = mac.get('possession_ev', 50), mac.get('possession_dep', 50)
    dangerous_ev, dangerous_dep = mac.get('dangerous_attacks_ev', 0), mac.get('dangerous_attacks_dep', 0)
    kirmizi = mac.get('kirmizi_kart', 0)
    corner_ev, corner_dep = mac.get('corner_ev', 0), mac.get('corner_dep', 0)
    ah_deger = mac.get('ah_deger', 0.0)

    toplam_gol = ev_gol + dep_gol
    gol_fark = abs(ev_gol - dep_gol)
    shots_toplam = shots_ev + shots_dep
    dangerous_toplam = dangerous_ev + dangerous_dep
    corner_toplam = corner_ev + corner_dep

    dapm_ev = round(dangerous_ev / dakika, 2)
    dapm_dep = round(dangerous_dep / dakika, 2)
    spm_toplam = round(shots_toplam / dakika, 3)

    extreme_value = (shots_toplam >= 12 or possession_ev >= 65 or dapm_ev >= 1.5 or (toplam_gol == 0 and shots_toplam >= 10))

    if not wc['gecti']:
        if extreme_value:
            puan += 2
            detay.append(f"⚠️ WC Kısmi ama EXTREME VALUE +2.0")
            stratejiler.append("EXTREME_VALUE")
        else:
            return 0, [], "", wc 
    else:
        puan += 4
        detay.append(f"✅ Winning Code Onayı (VU:{wc['VU_val']} TÜM:{wc['TUM_val']} MA:{wc['MA_val']} DİYİ:{wc['DIYI_val']})")

    # Çeşitli İstatistik Puanlamaları
    if dapm_ev >= 1.5: puan += 2.0; detay.append(f"🌪️ Ev Ağır Baskı ({dapm_ev}/Dk) +2.0"); stratejiler.append("AGIR_BASKI_EV")
    elif dapm_ev >= 1.2: puan += 1.5; detay.append(f"🌪️ Ev Yüksek Baskı ({dapm_ev}/Dk) +1.5"); stratejiler.append("AGIR_BASKI_EV")
    if spm_toplam >= 0.25: puan += 1.5; detay.append(f"🎯 Yüksek Şut Hızı ({spm_toplam}/Dk) +1.5"); stratejiler.append("YUKSEK_SUT_HIZI")
    
    if ev_gol == dep_gol: puan += 1.5; detay.append(f"🤝 Skor Dengede +1.5"); stratejiler.append("BERABERLIK")
    if toplam_gol >= 4: puan += 2; detay.append(f"⚽ {toplam_gol} Gol (Yüksek Tempo) +2.0"); stratejiler.append("GOL_PATLAMASI")
    if gol_fark >= 3: puan += 2; detay.append(f"📊 Gol Farkı {gol_fark} (Dominant) +2.0"); stratejiler.append("BUYUK_FARK")

    if shots_toplam >= 12: puan += 2; detay.append(f"🎯 {shots_toplam} İsabetli Şut +2.0"); stratejiler.append("YUKSEK_SUT")
    elif shots_toplam >= 8: puan += 1; detay.append(f"🎯 {shots_toplam} İsabetli Şut +1.0")

    if abs(possession_ev - possession_dep) >= 25:
        puan += 2; detay.append(f"⚽ Top Dom (%{max(possession_ev,possession_dep)}) +2.0"); stratejiler.append("POSSESSION_DOM")

    if dangerous_toplam >= 100: puan += 2; detay.append(f"🔥 {dangerous_toplam} Tehlikeli Atak +2.0"); stratejiler.append("YUKSEK_ATAK")
    if corner_toplam >= 12: puan += 2; detay.append(f"🚩 {corner_toplam} Corner (Elite) +2.0"); stratejiler.append("YUKSEK_CORNER")
    
    if kirmizi >= 1: puan += 1.5; detay.append(f"🟥 Kırmızı Kart Etkisi +1.5"); stratejiler.append("KIRMIZI_KAOS")

    z_bonus, z_label, z_strateji = zaman_bonusu(dakika)
    if z_bonus > 0:
        puan += z_bonus; detay.append(f"🔥 {z_label}"); stratejiler.append(z_strateji)

    if lig_katsayisi != 1.0:
        puan = round(puan * lig_katsayisi, 1)

    strateji_adi = stratejiler[0] if stratejiler else "GENEL"
    return round(puan, 1), detay, strateji_adi, wc

# ================================================
# NET TAHMİN & KASA
# ================================================
def tavsiye_uret(mac, strateji):
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    shots_ev, shots_dep = mac.get('shots_on_target_ev', 0), mac.get('shots_on_target_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    
    if strateji == "VALUE_GIRISI": return "EV KAZANIR VEYA BERABERE", f"Ev sahibi geride ama sahayı domine ediyor"
    elif strateji == "GOLSUZ_AKTIF": return "GOL OLACAK (S)", f"0-0 ama çok aktif maç — toplam {shots_ev+shots_dep} isabetli şut"
    elif strateji == "BERABERLIK": return "GOL OLACAK (S)", f"Beraberlik, her iki taraf gol peşinde"
    elif strateji == "POWER_WINDOW": return "GOL OLACAK (S)", "54-60 altın pencere, en yüksek gol yoğunluğu dakikaları"
    return "GOL OLACAK (S)", f"Maç aktif — {shots_ev+shots_dep} isabetli şut"

def sonraki_gol_tahmini(mac, strateji):
    ev, dep = mac.get('ev', ''), mac.get('dep', '')
    ev_skor = (mac.get('possession_ev', 50) * 0.3) + (mac.get('shots_on_target_ev', 0) * 5)
    dep_skor = ((100 - mac.get('possession_ev', 50)) * 0.3) + (mac.get('shots_on_target_dep', 0) * 5)
    
    if ev_skor > dep_skor * 1.3: return f"Sıradaki Gol: {ev[:15]}"
    elif dep_skor > ev_skor * 1.3: return f"Sıradaki Gol: {dep[:15]}"
    return "Sıradaki Gol: Her İki Taraf"

def kasa_hesapla(puan, dakika, ah_deger):
    if puan >= 12: return 4.0
    elif puan >= 10: return 3.0
    elif puan >= 8: return 2.0
    elif puan >= 6: return 1.5
    return 1.0

# ================================================
# GEMİNİ AI — DERİN GERÇEK ANALİZ (MULTI-KEY)
# ================================================
async def gemini_analiz(session, mac, puan, strateji, tahmin, neden, wc):
    keys = [k for k in GEMINI_KEYS if k]
    if not keys: return "AI Analizi devre dışı.", 1.5

    prompt = f"""Sen çok deneyimli bir canlı bahis analistsin.
MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
LİG: {mac['lig']} | DAKİKA: {mac['dakika']} 

İSTATİSTİKLER:
- Şut: {mac['ev']}={mac['shots_on_target_ev']} vs {mac['dep']}={mac['shots_on_target_dep']}
- Top: %{mac['possession_ev']} vs %{100-mac['possession_ev']}
- Atak: {mac['dangerous_attacks_ev']} vs {mac['dangerous_attacks_dep']}

BOT KARARI: {tahmin} | Puan: {puan}/12

KATMAN 1: İstatistiklerin söylediği nedir?
KATMAN 2: İstatistiklerin SÖYLEMEDIĞI ve maça özgü somut riskler nelerdir?
Maksimum 3 cümle. JSON formatında dön.

JSON: {{"yorum": "iki_katmanli_ozgun_analiz", "gir": true, "kasa": 1.5}}"""

    for key in keys:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if "
```" in text: text = text.split("```")[1].replace("json", "").strip()
                    result = json.loads(text)
                    return result.get('yorum', ''), float(result.get('kasa', 1.5))
        except: continue
    return "AI Limit veya Hata.", 1.0

# ================================================
# BET365 / BETSAPI VERİ ÇEKME MOTORU
# ================================================
async def maclari_cek(session):
    maclar = []
    try:
        url = f"https://api.betsapi.com/v3/bet365/inplay?token={BETSAPI_TOKEN}"
        async with session.get(url, timeout=15) as resp:
            data = await resp.json()
            if data.get('success') != 1: return maclar
            
            results = data.get("results", [])[0] if data.get("results") else []
            logger.info(f"💎 Bet365 Premium: {len(results)} maç taranıyor...")
            
            for f in results:
                try:
                    dk = int(f.get("timer", {}).get("tm", 0))
                    if not (5 <= dk <= 88): continue
                    
                    ev_g, dep_g = map(int, f.get("ss", "0-0").split("-"))
                    stats = f.get("stats", {})
                    
                    def gs(k, i): 
                        v = stats.get(k, [0, 0])
                        return int(v[i]) if isinstance(v, list) else 0

                    maclar.append({
                        'id': str(f["id"]), 'ev': f["home"]["name"], 'dep': f["away"]["name"],
                        'lig': f["league"]["name"], 'dakika': dk, 'ev_gol': ev_g, 'dep_gol': dep_g,
                        'shots_on_target_ev': gs("on_target", 0), 'shots_on_target_dep': gs("on_target", 1),
                        'dangerous_attacks_ev': gs("dangerous_attacks", 0), 'dangerous_attacks_dep': gs("dangerous_attacks", 1),
                        'possession_ev': gs("possession", 0) or 50, 'possession_dep': gs("possession", 1) or 50,
                        'corner_ev': gs("corners", 0), 'corner_dep': gs("corners", 1),
                        'kirmizi_kart': gs("redcards", 0) + gs("redcards", 1),
                        'son_gol': 0, 'ah_deger': 0.0 # Hız için sabit tutuldu
                    })
                except: continue
    except Exception as e: logger.error(f"BetsAPI Hata: {e}")
    return maclar

# ================================================
# BİLDİRİM Gönderici
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa):
    kasa = ai_kasa if ai_kasa is not None else kasa_hesapla(puan, mac['dakika'], 0)
    nesine = nesine_kontrol(mac['lig'])
    sonraki = sonraki_gol_tahmini(mac, strateji)

    if puan >= 10: karar_emoji, karar = "🔥🔥", "KESİN GİR"
    elif puan >= 8: karar_emoji, karar = "🔥", "GÜÇLÜ SİNYAL"
    else: karar_emoji, karar = "✅", "POTANSİYEL VAR"

    istat_satirlari = (
        f"⚽ Şut: {mac['shots_on_target_ev']}/{mac['shots_on_target_dep']} "
        f"| 🏃 Top: %{mac['possession_ev']}/%{mac.get('possession_dep',50)}\n"
        f"💥 Atak: {mac['dangerous_attacks_ev']}/{mac['dangerous_attacks_dep']} "
        f"| 🚩 Corner: {mac.get('corner_ev',0)}/{mac.get('corner_dep',0)}"
    )
    detay_str = "\n".join([f"- {d}" for d in detay[:4]])

    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"{nesine}\n"
        f"────────────────────\n"
        f"📈 SİNYAL PUANI: {puan}/12\n"
        f"🎯 STRATEJİ: {sonraki}\n"
        f"────────────────────\n"
        f"📝 SİSTEM ANALİZİ:\n{detay_str}\n"
        f"────────────────────\n"
        f"📊 İSTATİSTİKLER:\n{istat_satirlari}\n"
        f"────────────────────\n"
        f"🧠 AI YORUMU:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💡 TAHMİN: {tahmin}\n"
        f"💰 KASA RİSKİ: %{kasa}\n"
        f"{'═'*20}\n"
        f"{karar_emoji} {karar}\n"
        f"{'═'*20}"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum or "", kasa)
    except Exception as e: logger.error(f"Bildirim: {e}")

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()

    async with aiohttp.ClientSession() as session:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🤖 MAÇ ANALİZ BOTU — YENİ NESİL AKTİF\n\n"
                "✅ Bet365 Premium Hız Motoru\n"
                "✅ Winning Code (VU/TÜM/MA/DİYİ)\n"
                "✅ Nesine Bülten Kontrolü\n"
                "✅ 3 Anahtarlı Gemini Yapay Zeka\n\n"
                "⏰ Zamanlama: Türkiye Saati 13:00 — 23:59\n"
                f"🎯 Min Puan Barajı: {MIN_PUAN}/12\n\n"
                "Sinyaller için pusudayız 🚀"
            )
        )

        uyku_bildirimi = False
        while True:
            try:
                if not aktif_mi():
                    if not uyku_bildirimi:
                        logger.info("💤 Mesai dışı, uyku modu.")
                        uyku_bildirimi = True
                    await asyncio.sleep(600)
                    continue
                else: uyku_bildirimi = False

                maclar = await maclari_cek(session)
                
                for mac in maclar:
                    if mac['id'] in bildirim_gonderilen: continue

                    puan, detay, strateji, wc = sinyal_hesapla(mac)

                    if puan >= MIN_PUAN:
                        cooling, cooling_msg = cooling_off(mac)
                        if cooling: continue

                        tahmin, neden = tavsiye_uret(mac, strateji)
                        ai_yorum, ai_kasa = await gemini_analiz(session, mac, puan, strateji, tahmin, neden, wc)

                        await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, neden, ai_yorum, ai_kasa)
                        bildirim_gonderilen[mac['id']] = {'puan': puan, 'tahmin': tahmin}

            except Exception as e: logger.error(f"Ana döngü: {e}")
            await asyncio.sleep(180) 

if __name__ == "__main__":
    asyncio.run(ana_dongu())
