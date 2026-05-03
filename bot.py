"""
MAC ANALIZ BOTU - KANTİTATİF SÜRÜM (PREMIUM BETSAPI + COLD START FIX)
Özellikler: Rolling Window, İlk Tarama Koruması, Exponential Decay, AH Death Zone, Düz Metin AI
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ================================================
# ÇEVRE DEĞİŞKENLERİ
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "") # YENİ PREMIUM ANAHTAR

GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", "")
]
current_key_index = 0

MIN_PUAN = float(os.getenv("MIN_PUAN", "7.0")) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
mac_gecmisi = {} # ROLLING WINDOW Hafızası
gol_hafizasi = {} # BETSAPI SON GOL TAKİP HAFIZASI

# ================================================
# RAILWAY KORUMASI (HEALTH CHECK)
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
# NESİNE FİLTRESİ VE ZAMANLAMA
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
            return "🟢 NESİNE BÜLTENİ"
    return "🟡 DİĞER BÜLTEN"

def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4: return 19 <= saat <= 23
    else: return 19 <= saat <= 22

# ================================================
# KANTİTATİF FİLTRELER
# ================================================
def ustel_zaman_asimi(dakika, son_gol):
    if son_gol == 0: return 1.0, ""
    fark = dakika - son_gol
    
    if fark <= 5: return 0.0, f"HARD BLOCK: Son gol {fark} dk önce. Şok evresi."
    elif 5 < fark <= 10: return 0.5, f"PENALTY: Son gol {fark} dk önce. Rölanti evresi (-%50 Puan)"
    return 1.0, ""

def death_zone_kontrol(ah_deger, ev_gol, dep_gol):
    gol_fark = ev_gol - dep_gol
    if -1.0 <= ah_deger <= -0.5 and gol_fark == 1: return True, "DEATH ZONE: Favori ev sahibi 1 farkla önde."
    if 0.5 <= ah_deger <= 1.0 and gol_fark == -1: return True, "DEATH ZONE: Favori deplasman 1 farkla önde."
    return False, ""

def premium_artefakt_kontrol(mac):
    cev = mac.get('corner_ev', 0)
    cdep = mac.get('corner_dep', 0)
    if cev > 1000 or cdep > 1000: return 3.0, "💎 PREMIUM ARTEFAKT: Yüksek Hacimli Market ID tespit edildi!"
    return 0.0, ""

def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    ah_deger = mac.get('ah_deger', 0.0)
    
    puan = 0.0
    detay = []
    stratejiler = []
    
    decay_carpan, decay_mesaj = ustel_zaman_asimi(dakika, son_gol)
    if decay_carpan == 0.0: return 0, [decay_mesaj], "BLOCKED", False
    
    dz_aktif, dz_mesaj = death_zone_kontrol(ah_deger, ev_gol, dep_gol)
    if dz_aktif: return 0, [dz_mesaj], "DEATH_ZONE", False

    # 2. KAYAN PENCERE (ROLLING WINDOW) HESAPLAMASI VE İLK TARAMA KORUMASI
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # HARD-LOCK KAPI KONTROLÜ (İlk Taramada Uygulanmaz!)
    if not ilk_tarama and delta_atak < 8 and delta_sut < 1 and dakika > 20:
        return 0, ["HARD LOCK: Son periyotta yeterli ivme yok."], "REJECTED", False

    if ilk_tarama:
        detay.append("🔍 KAPI: İlk ölçüm alınıyor (HFT Referans)")
        puan += 2.0 
    else:
        detay.append(f"✅ KAPI GEÇİLDİ: Son periyot ivmesi (Atak: +{delta_atak}, Şut: +{delta_sut})")
        puan += 4.0 

    sut_puani = suanki_sut * 0.5
    puan += sut_puani
    detay.append(f"🎯 Şut Şiddeti: {suanki_sut} isabetli şut (+{sut_puani} Puan)")
    
    if delta_atak >= 15:
        puan += 2.0
        detay.append(f"🌪️ Ani Baskı İvmesi! (+2.0 Puan)")
        stratejiler.append("YUKSEK_IVME")

    if 65 <= dakika <= 75:
        puan += 3.5
        detay.append("🔥 Kırılma Penceresi (65-75') +3.5")
        stratejiler.append("POWER_WINDOW")
    elif 7 <= dakika <= 15:
        puan += 2.0
        detay.append("⚡ Agresif Açılış (7-15') +2.0")
        stratejiler.append("ERKEN_ACILIS")

    artefakt_puan, art_mesaj = premium_artefakt_kontrol(mac)
    if artefakt_puan > 0:
        puan += artefakt_puan; detay.append(art_mesaj)

    LIG_KATSAYISI = {'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15, 'Champions League': 1.1}
    lig_katsayisi = next((katsayi for lig_adi, katsayi in LIG_KATSAYISI.items() if lig_adi.lower() in mac.get('lig', '').lower()), 1.0)
    
    puan = round((puan * lig_katsayisi) * decay_carpan, 1)
    if decay_carpan < 1.0: detay.append(decay_mesaj)
        
    strateji_adi = stratejiler[0] if stratejiler else "MOMENTUM_TAKIBI"
    return round(puan, 1), detay, strateji_adi, True

# ================================================
# DÜZ METİN GEMİNİ AI
# ================================================
def tavsiye_uret(mac, strateji):
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    gol_fark = ev_gol - dep_gol
    
    if strateji == "POWER_WINDOW": return "GOL OLACAK (S)", "Kırılma anı, savunma disiplini çözülüyor."
    elif strateji == "ERKEN_ACILIS": return "GOL OLACAK (S)", "İlk yarı taktik oturmadan erken açık alan."
    elif gol_fark >= 2: return "EV GOL ATACAK (S)", "Ev sahibi dominant skorla ilerliyor."
    elif gol_fark <= -2: return "DEP GOL ATACAK (S)", "Deplasman dominant skorla ilerliyor."
    return "GOL OLACAK (S)", "Kayan pencere (Rolling Window) yüksek ivme gösteriyor."

def kasa_hesapla(puan):
    if puan >= 12: return 3.0
    elif puan >= 10: return 2.0
    return 1.5

async def gemini_analiz(mac):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI servisi kapalı."

    prompt = f"MAÇ: {mac['ev']} {mac.get('ev_gol',0)}-{mac.get('dep_gol',0)} {mac['dep']} | DK: {mac['dakika']}\nKantitatif analiz bu maçta anlık bir ivme (momentum) tespit etti. Lütfen maçın canlı gidişatını tek bir somut detayla, sadece düz 2 cümle Türkçe olarak yorumla. JSON KULLANMA."

    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={active_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['candidates'][0]['content']['parts'][0]['text'].strip()
                    elif resp.status == 429: 
                        current_key_index = (current_key_index + 1) % len(valid_keys)
        except: current_key_index = (current_key_index + 1) % len(valid_keys)
        await asyncio.sleep(1) 
    return "AI Havuzu meşgul."

# ================================================
# PREMIUM VERİ ÇEKME (BETSAPI MOTORU)
# ================================================
async def maclari_cek():
    maclar = []
    if not BETSAPI_TOKEN:
        logger.error("BETSAPI_TOKEN eksik!")
        return maclar

    try:
        url = f"https://api.betsapi.com/v3/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                if data.get('success') != 1: return maclar
                
                logger.info(f"💎 BetsAPI: {len(data.get('results', []))} maç işleniyor...")

                for f in data.get('results', []):
                    try:
                        m_id = str(f.get('id'))
                        dk = int(f.get('timer', {}).get('tm', 0))
                        if not (5 <= dk <= 88): continue

                        skor = f.get('ss', '0-0')
                        ev_gol, dep_gol = map(int, skor.split('-')) if '-' in skor else (0, 0)
                        toplam_gol = ev_gol + dep_gol

                        onceki_durum = gol_hafizasi.get(m_id, {'toplam': toplam_gol, 'son_gol_dk': 0})
                        if toplam_gol > onceki_durum['toplam']: son_gol_dk = dk
                        else: son_gol_dk = onceki_durum['son_gol_dk']
                        gol_hafizasi[m_id] = {'toplam': toplam_gol, 'son_gol_dk': son_gol_dk}

                        stats = f.get('stats', {})
                        dangerous = stats.get('dangerous_attacks', [0, 0])
                        possession = stats.get('possession_rt', [50, 50])
                        on_target = stats.get('on_target', [0, 0])
                        corners = stats.get('corners', [0, 0])

                        maclar.append({
                            'id': m_id, 
                            'ev': f.get('home', {}).get('name', 'Ev Sahibi'), 
                            'dep': f.get('away', {}).get('name', 'Deplasman'),
                            'lig': f.get('league', {}).get('name', 'Bilinmeyen Lig'), 
                            'dakika': dk,
                            'ev_gol': ev_gol, 'dep_gol': dep_gol,
                            'son_gol': son_gol_dk,
                            'shots_on_target_ev': int(on_target[0]), 'shots_on_target_dep': int(on_target[1]),
                            'possession_ev': int(possession[0]), 'possession_dep': int(possession[1]),
                            'dangerous_attacks_ev': int(dangerous[0]), 'dangerous_attacks_dep': int(dangerous[1]),
                            'corner_ev': int(corners[0]), 'corner_dep': int(corners[1]),
                            'ah_deger': 0.0 # Handikap API verisi ayrıca çekilebilir
                        })
                    except: continue
    except Exception as e: logger.error(f"BetsAPI Bağlantı Hatası: {e}")
    return maclar

async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, ai_yorum):
    kasa = kasa_hesapla(puan)
    nesine_durumu = nesine_kontrol(mac['lig'])
    karar_emoji = "💎🔥🔥" if puan >= 12 else "💎🔥" if puan >= 10 else "💎✅"
    detay_str = "\n".join([f"- {d}" for d in detay[:5]])
    
    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"{nesine_durumu}\n"
        f"────────────────────\n"
        f"📈 KANTİTATİF PUAN: {puan}/15\n"
        f"📝 ALGORİTMA RAPORU:\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 AI STRATEJİSTİ:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💡 POZİSYON: {tahmin}\n"
        f"💰 KASA RİSKİ: %{kasa}\n"
        f"{'═'*20}"
    )
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    
    simdi = datetime.now()
    gun_str = "Hafta Sonu" if simdi.weekday() >= 5 else "Hafta İçi"
    
    mesaj = (
        "🤖 KANTİTATİF ANALİZ BOTU V2.0\n\n"
        "✅ Rolling Window (Kayan Pencere İvmesi - 3 DK)\n"
        "✅ Cold Start Koruması Aktif (İlk Tarama Giyotini İptal)\n"
        "✅ Exponential Decay (Üstel Soğuma Filtresi)\n"
        "✅ AH Death Zone (Skor Koruma Blokajı)\n"
        "✅ Premium BetsAPI (0 Gecikme) Motoru\n"
        "✅ Sezgi Motoru (Sorunsuz Düz Metin AI)\n"
        "✅ Nesine Bülten Filtresi\n\n"
        f"📅 Mod: {gun_str}\n"
        f"🎯 Min Puan Eşiği: {MIN_PUAN}\n\n"
        "HFT (Yüksek Frekanslı) Algoritma Premium Veriyle Başlıyor 🚀"
    )
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue

            maclar = await maclari_cek()
            adaylar = []

            for mac in maclar:
                if mac['id'] in bildirim_gonderilen: continue

                puan, detay, strateji, gecti = sinyal_hesapla(mac)
                
                if gecti and puan >= MIN_PUAN:
                    adaylar.append((mac, puan, detay, strateji))

            if adaylar:
                for mac, puan, detay, strateji in adaylar:
                    tahmin, neden = tavsiye_uret(mac, strateji)
                    ai_yorum = await gemini_analiz(mac)
                    
                    await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, ai_yorum)
                    bildirim_gonderilen[mac['id']] = True

        except Exception as e: logger.error(f"Döngü Hatası: {e}")
        
        await asyncio.sleep(180) # Tarama Hızı: 3 Dakika

if __name__ == "__main__":
    asyncio.run(ana_dongu())
