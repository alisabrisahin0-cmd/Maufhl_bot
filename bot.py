"""
MAUFHL_BOT V4.0 QUANT MASTER - FULL EDITION
Gelişmiş HFT Algoritması + BetsAPI Hata İfşası + Timezone Fix
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
# ÇEVRE DEĞİŞKENLERİ (RAILWAY VARIABLES)
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
mac_gecmisi = {} 
gol_hafizasi = {} 

# ================================================
# RAILWAY HEALTH CHECK (SUNUCU AYAKTA TUTMA)
# ================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"V4.0 Active")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ================================================
# ZAMAN VE BÜLTEN KONTROLÜ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    # TZ Değişkeni Europe/Istanbul ise yerel saati baz alır
    return 13 <= simdi.hour <= 23

# ================================================
# V4.0 QUANT MASTER FİLTRELERİ (HFT MANTIĞI)
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    toplam_gol = ev_gol + dep_gol
    son_gol = mac.get('son_gol', 0)
    
    puan = 0.0
    detay = []
    strateji = "MOMENTUM_TAKIBI"

    # 1. KAOS SINIRI (5+ GOL) BLOĞU
    if toplam_gol >= 5:
        return 0, ["🚫 KAOS SINIRI: 5+ gol olmuş maçlarda algoritma güvenliği azalır."], "BLOCKED", False

    # 2. ÜSTEL SOĞUMA (OVERHEATING) KİLİDİ
    fark = dakika - son_gol
    if son_gol > 0 and fark <= 5:
        return 0, [f"🚫 AŞIRI ISINMA: Son gol {fark} dk önce. Bekleme evresi."], "BLOCKED", False

    # 3. KAYAN PENCERE (ROLLING WINDOW) & İLK TARAMA KORUMASI
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # 4. İKİNCİ YARI MOMENTUM TABANI (10 ATAK)
    if not ilk_tarama and dakika > 45 and delta_atak < 10 and delta_sut < 1:
        return 0, ["🚫 MOMENTUM TABANI: İkinci yarı ivme 10 atak/3dk altında."], "REJECTED", False

    if ilk_tarama:
        detay.append("🔍 İLK ÖLÇÜM: Referans noktası oluşturuldu.")
        puan += 2.0
    else:
        # 5. HÜCUM EPİLASYONU (SOT TRAP) ENGELİ
        # Sadece atak var ama şut hiç yoksa puan kırılır
        if delta_atak > 12 and delta_sut == 0:
            puan -= 2.0
            detay.append("⚠️ HÜCUM EPİLASYONU: Kısır baskı tespit edildi.")
        else:
            puan += 4.0
            detay.append(f"✅ İVME: Son periyot (Atak: +{delta_atak}, Şut: +{delta_sut})")

    # 6. ALTIN PENCERE (55-60') BONUSU
    if 55 <= dakika <= 60:
        puan += 3.0
        detay.append("🌟 ALTIN PENCERE: 55-60 arası yüksek verimlilik.")
        strateji = "GOLDEN_WINDOW"

    puan += (suanki_sut * 0.4)
    return round(puan, 1), detay, strateji, True

# ================================================
# GEMINI AI SEZGİ MOTORU
# ================================================
async def gemini_analiz(mac):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI Servisi Devre Dışı."

    prompt = f"{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} (Dk:{mac['dakika']}). Maçın ivmesi yüksek. Gidişatı 2 kısa Türkçe cümleyle yorumla. JSON kullanma."

    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={active_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=8) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['candidates'][0]['content']['parts'][0]['text'].strip()
                    current_key_index = (current_key_index + 1) % len(valid_keys)
        except: current_key_index = (current_key_index + 1) % len(valid_keys)
    return "Yapay zeka şu an meşgul."

# ================================================
# PREMIUM VERİ ÇEKME (BETSAPI MOTORU + HATA İFŞASI)
# ================================================
async def maclari_cek():
    maclar = []
    try:
        url = f"https://api.betsapi.com/v3/events/inplay?sport_id=1&token={BETSAPI_TOKEN}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                
                # SESSİZLİĞİ BOZAN HATA KONTROLÜ
                if data.get('success') != 1:
                    logger.error(f"🔴 BetsAPI Bizi Reddetti! Gelen Cevap: {data}")
                    return maclar
                
                results = data.get('results', [])
                logger.info(f"💎 BetsAPI: {len(results)} maç işleniyor...")

                for f in results:
                    m_id = str(f.get('id'))
                    dk = int(f.get('timer', {}).get('tm', 0))
                    if not (5 <= dk <= 88): continue

                    skor = f.get('ss', '0-0')
                    ev_gol, dep_gol = map(int, skor.split('-')) if '-' in skor else (0, 0)
                    
                    # Gol Zamanı Takibi
                    onceki = gol_hafizasi.get(m_id, {'toplam': 0, 'dk': 0})
                    if (ev_gol + dep_gol) > onceki['toplam']: son_gol = dk
                    else: son_gol = onceki['dk']
                    gol_hafizasi[m_id] = {'toplam': ev_gol + dep_gol, 'dk': son_gol}

                    stats = f.get('stats', {})
                    maclar.append({
                        'id': m_id, 'ev': f.get('home', {}).get('name'), 'dep': f.get('away', {}).get('name'),
                        'lig': f.get('league', {}).get('name'), 'dakika': dk,
                        'ev_gol': ev_gol, 'dep_gol': dep_gol, 'son_gol': son_gol,
                        'dangerous_attacks_ev': int(stats.get('dangerous_attacks', [0,0])[0]),
                        'dangerous_attacks_dep': int(stats.get('dangerous_attacks', [0,0])[1]),
                        'shots_on_target_ev': int(stats.get('on_target', [0,0])[0]),
                        'shots_on_target_dep': int(stats.get('on_target', [0,0])[1])
                    })
    except Exception as e:
        logger.error(f"⚠️ Bağlantı Hatası: {e}")
    return maclar

# ================================================
# TELEGRAM BİLDİRİM SİSTEMİ
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, ai_yorum):
    karar = "💎🔥🔥" if puan >= 12 else "💎🔥" if puan >= 9 else "💎✅"
    mesaj = (
        f"{karar} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 QUANT PUAN: {puan}/15\n"
        f"📝 ANALİZ: {detay[-1]}\n"
        f"────────────────────\n"
        f"🧠 AI SEZGİSİ:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💡 POZİSYON: GOL OLACAK (S)\n"
        f"💰 RİSK: %{2.0 if puan >= 10 else 1.5}\n"
    )
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # Karşılama Mesajı
    start_msg = "🤖 V4.0 QUANT MASTER BAŞLADI\n\n✅ Premium BetsAPI (0 Gecikme)\n✅ Timezone Fix (Istanbul)\n✅ Rolling Window (3 DK)\n✅ Hata İfşası Aktif"
    try: await bot.send_message(chat_id=CHAT_ID, text=start_msg)
    except: pass
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue

            maclar = await maclari_cek()
            for mac in maclar:
                if mac['id'] in bildirim_gonderilen: continue

                puan, detay, strateji, gecti = sinyal_hesapla(mac)
                if gecti and puan >= MIN_PUAN:
                    ai_yorum = await gemini_analiz(mac)
                    await bildirim_gonder(bot, mac, puan, detay, strateji, ai_yorum)
                    bildirim_gonderilen[mac['id']] = True

        except Exception as e: logger.error(f"Ana Döngü Hatası: {e}")
        await asyncio.sleep(180) # 3 Dakika Tarama Aralığı

if __name__ == "__main__":
    asyncio.run(ana_dongu())
