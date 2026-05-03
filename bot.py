"""
MAC ANALIZ BOTU - V4.0 QUANT MASTER SÜRÜMÜ
Özellikler: Altın Pencere (55-60'), Hücum Epilasyonu (SOT Trap), Aşırı Isınma Engeli, Kaos Sınırı
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
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

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
mac_gecmisi = {} 
gol_hafizasi = {} 

# ================================================
# RAILWAY KORUMASI
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

def nesine_kontrol(lig_adi):
    NESINE_LIGLERI = ['Super Lig', '1. Lig', 'Premier League', 'Championship', 'La Liga', 'La Liga 2', 
                      'Serie A', 'Serie B', 'Bundesliga', '2. Bundesliga', 'Ligue 1', 'Ligue 2', 
                      'Eredivisie', 'Primeira Liga', 'Champions League', 'Europa League', 'Conference League',
                      'Copa Libertadores', 'MLS', 'Brasileirao', 'Primera Division', 'Pro League', 'Superliga']
    for lig in NESINE_LIGLERI:
        if lig.lower() in lig_adi.lower():
            return "🟢 NESİNE BÜLTENİ"
    return "🟡 DİĞER BÜLTEN"

def aktif_mi():
    simdi = datetime.now()
    return 14 <= simdi.hour <= 23

# ================================================
# V4.0 YENİ KANTİTATİF DEKONSTRÜKSİYON FİLTRELERİ
# ================================================
def ustel_zaman_asimi(dakika, son_gol):
    if son_gol == 0: return 1.0, ""
    fark = dakika - son_gol
    
    if fark <= 5: return 0.0, f"HARD BLOCK: Son gol {fark} dk önce. Şok evresi."
    elif 5 < fark <= 10: return 0.5, f"PENALTY: Son gol {fark} dk önce. Rölanti evresi (-%50 Puan)"
    return 1.0, ""

def death_zone_kontrol(ah_deger, ev_gol, dep_gol):
    gol_fark = ev_gol - dep_gol
    # V4.0 Güncellemesi: 3 farklı skorlarda (3-0, 4-1) rölanti (coasting) engeli
    if abs(gol_fark) >= 3: return True, "DEATH ZONE: 3+ Farklı Skor (Rölanti/Coasting Evresi)."
    
    if -1.0 <= ah_deger <= -0.5 and gol_fark == 1: return True, "DEATH ZONE: Favori ev sahibi 1 farkla önde."
    if 0.5 <= ah_deger <= 1.0 and gol_fark == -1: return True, "DEATH ZONE: Favori deplasman 1 farkla önde."
    return False, ""

def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    son_gol = mac.get('son_gol', 0)
    ah_deger = mac.get('ah_deger', 0.0)
    
    puan = 0.0
    detay = []
    stratejiler = []

    # 1. SKOR DOYUMU (Kaos Bölgesi Engelleyicisi)
    if toplam_gol >= 5:
        return 0, ["OVER_LIMIT: Toplam gol 5 sınırında (Random Walk/Kaos)."], "REJECTED", False
    
    # 2. ÜSTEL SOĞUMA
    decay_carpan, decay_mesaj = ustel_zaman_asimi(dakika, son_gol)
    if decay_carpan == 0.0: return 0, [decay_mesaj], "BLOCKED", False
    
    # 3. DEATH ZONE (Rölanti Koruması)
    dz_aktif, dz_mesaj = death_zone_kontrol(ah_deger, ev_gol, dep_gol)
    if dz_aktif: return 0, [dz_mesaj], "DEATH_ZONE", False

    # 4. ROLLING WINDOW & İLK TARAMA
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}

    if ilk_tarama:
        detay.append("🔍 KAPI: İlk ölçüm alınıyor (HFT Referans)")
        puan += 2.0 
    else:
        detay.append(f"✅ KAPI GEÇİLDİ: Son periyot ivmesi (Atak: +{delta_atak}, Şut: +{delta_sut})")
        puan += 4.0 

    # 5. DEVRE ODAKLI ASİMETRİK İVME KONTROLÜ
    if not ilk_tarama:
        if dakika < 45:
            if delta_atak >= 15:
                return 0, ["HARD BLOCK: İlk yarı 15+ ivme (Aşırı Isınma/Overheating)."], "REJECTED", False
            if delta_atak < 8 and delta_sut < 1 and dakika > 20:
                return 0, ["HARD LOCK: İlk yarı yeterli ivme yok."], "REJECTED", False
        else:
            if delta_atak < 10:
                return 0, ["HARD LOCK: İkinci yarı momentum tabanı (10) aşılamadı."], "REJECTED", False

    # 6. SOT TRAP (İsabetli Şut Lojistik Cezası)
    if suanki_sut <= 8:
        sut_puani = suanki_sut * 0.25
        puan += sut_puani
        detay.append(f"🎯 Şut Verimliliği: {suanki_sut} isabetli şut (+{sut_puani} Puan)")
    else:
        puan -= 1.0
        detay.append("⚠️ SOT TRAP: Şut sayısı 8'i aştı. Hücum Epilasyonu cezası! (-1.0 Puan)")

    # 7. ZAMAN AĞIRLIKLI KAPILAR (ALTIN PENCERE)
    if 55 <= dakika <= 60:
        puan += 4.0
        detay.append("💎 ALTIN PENCERE (55-60'): Geçiş oyunu zirvesi (+4.0 Puan)")
        stratejiler.append("ALTIN_FIRSAT")
    elif 60 < dakika <= 75:
        puan += 2.0
        detay.append("🔥 Geçiş Oyunu Evresi (60-75') (+2.0 Puan)")
        stratejiler.append("GECIS_OYUNU")

    # 8. ANOMALİ TEMİZLİĞİ (Korner Tuzağı)
    korner_toplam = mac.get('corner_ev', 0) + mac.get('corner_dep', 0)
    if korner_toplam > 1000:
        return 0, ["API ANOMALİSİ: 1000+ Korner Reddedildi."], "REJECTED", False
    if korner_toplam > 12:
        puan -= 1.0
        detay.append(f"⚠️ KORNER TUZAĞI: {korner_toplam} Korner, Etkisiz Baskı (-1.0 Puan)")

    # Lig Katsayısı ve Son Puan
    LIG_KATSAYISI = {'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15, 'Champions League': 1.1}
    lig_katsayisi = next((katsayi for lig_adi, katsayi in LIG_KATSAYISI.items() if lig_adi.lower() in mac.get('lig', '').lower()), 1.0)
    
    puan = round((puan * lig_katsayisi) * decay_carpan, 1)
    if decay_carpan < 1.0: detay.append(decay_mesaj)
        
    strateji_adi = stratejiler[0] if stratejiler else "MOMENTUM_TAKIBI"
    return round(puan, 1), detay, strateji_adi, True

# ================================================
# TAVSİYE VE AI MOTORU
# ================================================
def tavsiye_uret(mac, strateji):
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    dakika = mac.get('dakika', 1)
    skor_fark = ev_gol - dep_gol
    toplam_gol = ev_gol + dep_gol
    
    # Raporun İstediği Kesin Altın Fırsat Yönergesi
    if strateji == "ALTIN_FIRSAT" and 55 <= dakika <= 60 and abs(skor_fark) in [1, 2] and toplam_gol <= 4:
        return "ALTIN FIRSAT: SIRADAKİ GOL (S)", "Rasyonel geçiş oyunu evresi, istatistiksel zirve."

    if strateji == "GECIS_OYUNU": return "GOL OLACAK (S)", "İkinci yarı kırılma evresi, fiziksel yorgunluk boşlukları."
    if skor_fark >= 2: return "EV GOL ATACAK (S)", "Ev sahibi dominant skorla ilerliyor."
    elif skor_fark <= -2: return "DEP GOL ATACAK (S)", "Deplasman dominant skorla ilerliyor."
    return "GOL OLACAK (S)", "Kayan pencere (Rolling Window) yüksek ivme gösteriyor."

def kasa_hesapla(puan):
    if puan >= 12: return 3.0
    elif puan >= 10: return 2.0
    return 1.5

async def gemini_analiz(mac):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI servisi kapalı."

    prompt = f"MAÇ: {mac['ev']} {mac.get('ev_gol',0)}-{mac.get('dep_gol',0)} {mac['dep']} | DK: {mac['dakika']}\nKantitatif analiz bu maçta anlık bir ivme tespit etti. Lütfen maçı tek bir somut detayla, düz 2 cümle Türkçe olarak yorumla. JSON KULLANMA."

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
                            'ah_deger': 0.0 
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
    
    mesaj = (
        "🤖 V4.0 QUANT MASTER BOT BAŞLADI\n\n"
        "✅ Altın Pencere (55-60') Aktif\n"
        "✅ Hücum Epilasyonu (SOT Trap) Engeli\n"
        "✅ İlk Yarı Aşırı Isınma (Overheating) Kilidi\n"
        "✅ Kaos Sınırı (5+ Gol) Bloğu\n"
        "✅ İkinci Yarı Momentum Tabanı (10 Atak)\n"
        "✅ Premium BetsAPI (0 Gecikme) Motoru\n\n"
        "🕒 Mesai: 14:00 - 23:59\n"
        f"🎯 Min Puan Eşiği: {MIN_PUAN}\n\n"
        "Profesyonel Algoritma Ava Çıktı! 🚀"
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
        
        await asyncio.sleep(180) 

if __name__ == "__main__":
    asyncio.run(ana_dongu())
