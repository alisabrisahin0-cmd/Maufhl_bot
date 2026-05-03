"""
V5.0 QUANT MASTER - THE SHIELD (BETSAPI v3 COMPLIANT)
Özellikler: List-Object Flattening Fix, Two-Step Fetch, Rolling Window, 3.0 Barajı
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

GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]
current_key_index = 0
MIN_PUAN = float(os.getenv("MIN_PUAN", "3.0")) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
mac_gecmisi = {} 
gol_hafizasi = {} 

# ================================================
# RAILWAY KORUMASI & MESAİ KONTROLÜ
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

def aktif_mi():
    saat = datetime.now().hour
    return 13 <= saat <= 23

# ================================================
# KANTİTATİF ANALİZ MOTORU
# ================================================
def ustel_zaman_asimi(dakika, son_gol):
    if son_gol == 0: return 1.0, ""
    fark = dakika - son_gol
    if fark <= 5: return 0.0, f"HARD BLOCK: Son gol {fark} dk önce."
    elif 5 < fark <= 10: return 0.5, f"PENALTY: Son gol {fark} dk önce (-%50 Puan)"
    return 1.0, ""

def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    
    decay_carpan, decay_mesaj = ustel_zaman_asimi(dakika, son_gol)
    if decay_carpan == 0.0: return 0, [decay_mesaj], False
    
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    if not ilk_tarama and delta_atak < 7 and delta_sut < 1 and dakika > 20:
        return 0, ["HARD LOCK: Yetersiz ivme."], False

    puan = 4.0 if not ilk_tarama else 2.0
    puan += (suanki_sut * 0.5)
    if 65 <= dakika <= 75: puan += 3.5
    elif 7 <= dakika <= 15: puan += 2.0

    return round(puan * decay_carpan, 1), [f"Atak: +{delta_atak}", f"Şut: +{delta_sut}"], True

# ================================================
# GEMINI AI ANALİZİ
# ================================================
async def gemini_analiz(mac):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "Momentum devam ediyor."
    
    prompt = f"MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | DK: {mac['dakika']}\nİvme saptandı. 2 cümle Türkçe yorumla."
    
    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={active_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['candidates'][0]['content']['parts'][0]['text'].strip()
        except: pass
        current_key_index = (current_key_index + 1) % len(valid_keys)
    return "Momentum yüksek."

async def bildirim_gonder(bot, mac, puan, detay, ai_yorum):
    kasa = 3.0 if puan >= 12 else 2.0 if puan >= 10 else 1.5
    mesaj = (
        f"💎✅ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n📈 PUAN: {puan}/15\n"
        f"📝 ANALİZ: {', '.join(detay)}\n"
        f"────────────────────\n🧠 AI:\n{ai_yorum}\n"
        f"────────────────────\n💡 POZİSYON: GOL OLACAK (S)\n💰 KASA: %{kasa}"
    )
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass

# ================================================
# VERİ ÇEKME MOTORU (V5.0 - FLATTENING FIX)
# ================================================
async def mac_detay_cek(session, fixture_id):
    try:
        url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}"
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            if data.get('success') == 1 and data.get('results'):
                res = data['results']
                # Eğer sonuç bir liste içindeyse ilkini al
                return res[0] if isinstance(res, list) and len(res) > 0 else res
    except: pass
    return None

async def maclari_cek():
    maclar = []
    if not BETSAPI_TOKEN: return maclar
    try:
        async with aiohttp.ClientSession() as session:
            list_url = f"https://api.betsapi.com/v3/bet365/inplay?token={BETSAPI_TOKEN}&sport_id=1"
            async with session.get(list_url, timeout=10) as resp:
                data = await resp.json()
                if data.get('success') != 1: return maclar
                
                raw_results = data.get('results', [])
                # FIX: Eğer sonuçlar liste içinde liste olarak geliyorsa düzleştir
                if raw_results and isinstance(raw_results[0], list):
                    raw_results = raw_results[0]
                
                logger.info(f"💎 Sahada Toplam {len(raw_results)} maç taramaya uygun.")

                for f in raw_results:
                    m_id = "Bilinmiyor"
                    try:
                        # f'in bir sözlük olduğundan emin ol
                        if not isinstance(f, dict): continue
                        
                        m_id = str(f.get('id', f.get('FI', '')))
                        if not m_id or m_id == "Bilinmiyor": continue
                        
                        detay = await mac_detay_cek(session, m_id)
                        if not detay or not isinstance(detay, dict): continue

                        timer = detay.get('timer', {})
                        dk = int(timer.get('tm', 0)) if isinstance(timer, dict) else 0
                        if not (5 <= dk <= 88): continue

                        stats = detay.get('stats', {})
                        skor = detay.get('ss', '0-0')
                        ev_gol, dep_gol = map(int, skor.split('-')) if '-' in skor else (0, 0)
                        
                        toplam_gol = ev_gol + dep_gol
                        onceki = gol_hafizasi.get(m_id, {'toplam': toplam_gol, 'son_gol_dk': 0})
                        son_gol_dk = dk if toplam_gol > onceki['toplam'] else onceki['son_gol_dk']
                        gol_hafizasi[m_id] = {'toplam': toplam_gol, 'son_gol_dk': son_gol_dk}

                        def gs(key, idx):
                            v = stats.get(key, [0, 0])
                            return int(v[idx]) if isinstance(v, list) and len(v) > idx else 0

                        maclar.append({
                            'id': m_id, 'ev': detay.get('home', {}).get('name'), 'dep': detay.get('away', {}).get('name'),
                            'lig': detay.get('league', {}).get('name'), 'dakika': dk,
                            'ev_gol': ev_gol, 'dep_gol': dep_gol, 'son_gol': son_gol_dk,
                            'shots_on_target_ev': gs('on_target', 0), 'shots_on_target_dep': gs('on_target', 1),
                            'dangerous_attacks_ev': gs('dangerous_attacks', 0), 'dangerous_attacks_dep': gs('dangerous_attacks', 1)
                        })
                    except Exception as e:
                        logger.error(f"Döngü hatası (ID: {m_id}): {e}")
                        continue
    except Exception as e: 
        logger.error(f"Motor Hatası: {e}")
    return maclar

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=f"🤖 V5.0 QUANT MASTER AKTİF\n\n🛡️ Veri Yapısı Koruması Devrede\n🎯 Min Puan: {MIN_PUAN}")
    while True:
        try:
            if aktif_mi():
                maclar = await maclari_cek()
                for mac in maclar:
                    if mac['id'] in bildirim_gonderilen: continue
                    puan, detay_list, gecti = sinyal_hesapla(mac)
                    if gecti and puan >= MIN_PUAN:
                        ai_yorum = await gemini_analiz(mac)
                        await bildirim_gonder(bot, mac, puan, detay_list, ai_yorum)
                        bildirim_gonderilen[mac['id']] = True
        except Exception as e: logger.error(f"Döngü Hatası: {e}")
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
