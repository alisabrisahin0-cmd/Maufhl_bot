"""
V4.0 QUANT MASTER BOT - PREMIUM BET365 EDITION
Özellikler: Bet365 Inplay Motoru, Rolling Window, Cold Start Fix, Exponential Decay, Düz Metin AI
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
mac_gecmisi = {} 
gol_hafizasi = {} 

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
# NESİNE FİLTRESİ VE ZAMANLAMA (13:00 - 00:00)
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
    # Mesai: Öğlen 13'ten gece 24'e kadar
    return 13 <= saat <= 23

# ================================================
# KANTİTATİF ANALİZ MOTORU
# ================================================
def ustel_zaman_asimi(dakika, son_gol):
    if son_gol == 0: return 1.0, ""
    fark = dakika - son_gol
    if fark <= 5: return 0.0, f"HARD BLOCK: Son gol {fark} dk önce. Şok evresi."
    elif 5 < fark <= 10: return 0.5, f"PENALTY: Son gol {fark} dk önce. Rölanti evresi (-%50 Puan)"
    return 1.0, ""

def sinyal_hesapla(mac):
    mac_id = mac['id']
    dakika = max(mac.get('dakika', 1), 1)
    ev_gol, dep_gol = mac.get('ev_gol', 0), mac.get('dep_gol', 0)
    son_gol = mac.get('son_gol', 0)
    
    puan = 0.0
    detay = []
    
    # 1. GOLDEN SONRAKİ SOĞUMA
    decay_carpan, decay_mesaj = ustel_zaman_asimi(dakika, son_gol)
    if decay_carpan == 0.0: return 0, [decay_mesaj], False
    
    # 2. KAYAN PENCERE (ROLLING WINDOW) & COLD START FIX
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # HARD-LOCK (İvme yoksa kes - İlk taramada uygulanmaz)
    if not ilk_tarama and delta_atak < 8 and delta_sut < 1 and dakika > 20:
        return 0, ["HARD LOCK: Yetersiz ivme."], False

    if ilk_tarama:
        detay.append("🔍 KAPI: İlk ölçüm alınıyor (HFT Referans)")
        puan += 2.0 
    else:
        detay.append(f"✅ KAPI GEÇİLDİ: Son periyot (Atak: +{delta_atak}, Şut: +{delta_sut})")
        puan += 4.0 

    # 3. İSTATİSTİK PUANLAMA
    puan += (suanki_sut * 0.5)
    detay.append(f"🎯 Toplam Şut: {suanki_sut} (+{suanki_sut * 0.5})")
    
    if 65 <= dakika <= 75:
        puan += 3.5
        detay.append("🔥 Kırılma Penceresi (65-75') +3.5")
    elif 7 <= dakika <= 15:
        puan += 2.0
        detay.append("⚡ Agresif Açılış (7-15') +2.0")

    # 4. LİG VE DECAY ÇARPANI
    LIG_KATSAYISI = {'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15}
    katsayi = next((k for lig, k in LIG_KATSAYISI.items() if lig.lower() in mac['lig'].lower()), 1.0)
    
    final_puan = round((puan * katsayi) * decay_carpan, 1)
    if decay_carpan < 1.0: detay.append(decay_mesaj)
        
    return final_puan, detay, True

# ================================================
# GEMINI AI & BİLDİRİM
# ================================================
async def gemini_analiz(mac):
    global current_key_index
    valid_keys = [k for k in GEMINI_KEYS if k]
    if not valid_keys: return "AI servisi kapalı."
    prompt = f"MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | DK: {mac['dakika']}\nİvme saptandı. Maçın gidişatını 2 cümle Türkçe yorumla. JSON kullanma."
    for _ in range(len(valid_keys)):
        active_key = valid_keys[current_key_index]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={active_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['candidates'][0]['content']['parts'][0]['text'].strip()
                    current_key_index = (current_key_index + 1) % len(valid_keys)
        except: current_key_index = (current_key_index + 1) % len(valid_keys)
    return "AI Limit."

async def bildirim_gonder(bot, mac, puan, detay, ai_yorum):
    kasa = 3.0 if puan >= 12 else 2.0 if puan >= 10 else 1.5
    nesine = nesine_kontrol(mac['lig'])
    karar = "💎🔥🔥" if puan >= 11 else "💎✅"
    mesaj = (
        f"{karar} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n{nesine}\n"
        f"────────────────────\n📈 PUAN: {puan}/15\n"
        f"📝 ANALİZ:\n" + "\n".join([f"- {d}" for d in detay[:5]]) + "\n"
        f"────────────────────\n🧠 AI:\n{ai_yorum}\n"
        f"────────────────────\n💡 POZİSYON: GOL OLACAK (S)\n💰 KASA: %{kasa}"
    )
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass

# ================================================
# PREMIUM BET365 VERİ ÇEKME MOTORU
# ================================================
async def maclari_cek():
    maclar = []
    if not BETSAPI_TOKEN: return maclar
    try:
        # Yeni /bet365/inplay kapısı
        url = f"https://api.betsapi.com/v3/bet365/inplay?token={BETSAPI_TOKEN}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                if data.get('success') != 1: return maclar
                
                results = data.get('results', [])[0] if data.get('results') else []
                logger.info(f"💎 Bet365 Premium: {len(results)} maç taranıyor...")

                for f in results:
                    try:
                        m_id = str(f.get('id'))
                        dk = int(f.get('timer', {}).get('tm', 0))
                        if not (5 <= dk <= 88): continue

                        skor = f.get('ss', '0-0')
                        ev_gol, dep_gol = map(int, skor.split('-')) if '-' in skor else (0, 0)
                        toplam_gol = ev_gol + dep_gol

                        onceki = gol_hafizasi.get(m_id, {'toplam': toplam_gol, 'son_gol_dk': 0})
                        son_gol_dk = dk if toplam_gol > onceki['toplam'] else onceki['son_gol_dk']
                        gol_hafizasi[m_id] = {'toplam': toplam_gol, 'son_gol_dk': son_gol_dk}

                        stats = f.get('stats', {})
                        def gs(key, idx):
                            v = stats.get(key, [0, 0])
                            return int(v[idx]) if isinstance(v, list) and len(v) > idx else 0

                        maclar.append({
                            'id': m_id, 'ev': f.get('home', {}).get('name'), 'dep': f.get('away', {}).get('name'),
                            'lig': f.get('league', {}).get('name'), 'dakika': dk,
                            'ev_gol': ev_gol, 'dep_gol': dep_gol, 'son_gol': son_gol_dk,
                            'shots_on_target_ev': gs('on_target', 0), 'shots_on_target_dep': gs('on_target', 1),
                            'dangerous_attacks_ev': gs('dangerous_attacks', 0), 'dangerous_attacks_dep': gs('dangerous_attacks', 1)
                        })
                    except: continue
    except Exception as e: logger.error(f"Hata: {e}")
    return maclar

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🤖 V4.0 QUANT MASTER BOT BAŞLADI\n\n✅ Premium Bet365 Motoru\n✅ Rolling Window (3 DK)\n✅ Cold Start Koruması\n\n🎯 Min Puan: 7.0\n🕒 Mesai: 13:00 - 00:00\n\nAva Çıkıyoruz! 🚀")
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue

            maclar = await maclari_cek()
            for mac in maclar:
                if mac['id'] in bildirim_gonderilen: continue
                puan, detay, gecti = sinyal_hesapla(mac)
                if gecti and puan >= MIN_PUAN:
                    ai_yorum = await gemini_analiz(mac)
                    await bildirim_gonder(bot, mac, puan, detay, ai_yorum)
                    bildirim_gonderilen[mac['id']] = True
        except Exception as e: logger.error(f"Döngü: {e}")
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
