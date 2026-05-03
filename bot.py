"""
V6.1 SNIPER QUANT MASTER - CORE ENGINE
Özellikler: State & Timing Bot, Lojistik Ceza Modeli, Optimum Skor Filtresi, Bet365 Premium
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# ================================================
# ÇEVRE DEĞİŞKENLERİ VE YAPILANDIRMA
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv("GEMINI_KEY_1", ""), os.getenv("GEMINI_KEY_2", ""), os.getenv("GEMINI_KEY_3", "")]

# Keskin nişancı barajı (Altın vuruş için 7.0 yeterli)
MIN_PUAN = float(os.getenv("MIN_PUAN", "7.0"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ================================================
# BELLEK YÖNETİMİ (TTL: 120 DK - MEMORY LEAK KORUMASI)
# ================================================
mac_gecmisi = {}
gol_hafizasi = {}
bildirim_gonderilen = {}
TTL_MINUTES = 120

def cleanup_memory():
    now = datetime.now()
    for store in [mac_gecmisi, gol_hafizasi, bildirim_gonderilen]:
        silinecek = [k for k, v in store.items() if isinstance(v, dict) and "time" in v and now - v["time"] > timedelta(minutes=TTL_MINUTES)]
        for k in silinecek: del store[k]

# ================================================
# SAĞLIK KONTROLÜ (RAILWAY İÇİN)
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
# FİLTRELER VE MESAİ
# ================================================
NESINE_LIGLERI = ['Super Lig', '1. Lig', 'Premier League', 'Championship', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1', 'Eredivisie']

def nesine_kontrol(lig_adi):
    return "🟢 NESİNE BÜLTENİ" if any(lig.lower() in lig_adi.lower() for lig in NESINE_LIGLERI) else "🟡 DİĞER BÜLTEN"

def aktif_mi():
    saat = datetime.now().hour
    return 13 <= saat <= 23

def tavsiye_uret(mac):
    dk = mac["dakika"]
    skor = (mac["ev_gol"], mac["dep_gol"])
    # Kural 8: Tavsiye Sistemi Revizyonu
    if 55 <= dk <= 60 and skor in [(2,1), (3,1), (1,2), (1,3)]:
        return "ALTIN FIRSAT: SIRADAKİ GOL (S)"
    return "GOL OLACAK (S)"

# ================================================
# KURAL SETİ: V6 SNIPER ANALİZ MOTORU
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac["id"]
    dk = max(mac["dakika"], 1)
    ev_gol, dep_gol = mac["ev_gol"], mac["dep_gol"]
    toplam_gol = ev_gol + dep_gol
    skor = (ev_gol, dep_gol)
    
    su_atak = mac["dangerous_attacks_ev"] + mac["dangerous_attacks_dep"]
    su_sut = mac["shots_on_target_ev"] + mac["shots_on_target_dep"]
    korner_toplam = mac["corner_ev"] + mac["corner_dep"]

    # Rolling Window (Kayan Pencere Güncellemesi)
    gecmis = mac_gecmisi.get(mac_id, {"atak": su_atak, "sut": su_sut})
    delta_atak = max(0, su_atak - gecmis["atak"])
    mac_gecmisi[mac_id] = {"atak": su_atak, "sut": su_sut, "time": datetime.now()}

    # --- KESİN RED (HARD BLOCK) FİLTRELERİ ---
    
    # Kural 2: Kaos Bölgesi
    if toplam_gol >= 5: return 0, False, "🚫 KAOS BÖLGESİ"
    
    # Kural 6: Death Zone (3 Fark)
    if abs(ev_gol - dep_gol) >= 3: return 0, False, "🚫 DEATH ZONE"
    
    # Kural 7: Optimum Skor State
    if skor not in [(2,1), (1,2), (3,1), (1,3)]: return 0, False, f"🚫 SKOR STATE DIŞI ({ev_gol}-{dep_gol})"
    
    # Kural 4: Devreye Göre Momentum (Aşırı Isınma & İvme)
    if dk < 45 and delta_atak >= 15: return 0, False, "🚫 İLK YARI AŞIRI ISINMA"
    if dk >= 45 and delta_atak < 10: return 0, False, "🚫 İKİNCİ YARI YETERSİZ İVME"

    # Sadece 55-75 arası maçlara izin ver (Kural 1 & 9 Gereği)
    if not (55 <= dk <= 75): return 0, False, "🚫 ZAMAN PENCERESİ DIŞI"

    # --- PUANLAMA (CEZA VE BONUSLAR) ---
    puan = 0.0

    # Kural 1: Zaman Penceresi Puanı
    if 55 <= dk <= 60:
        puan += 4.0
    elif 60 < dk <= 75:
        puan += 2.0

    # Kural 3: Lojistik Ceza Modeli (Şut)
    if su_sut <= 8:
        puan += (su_sut * 0.25)
    else:
        puan -= 1.0

    # Kural 5: Korner Anomali Temizliği
    if korner_toplam > 12:
        puan -= 1.0

    # Ekstra: Geçerli İvme (Delta >= 10) puan barajını aşmasını garantiler
    if delta_atak >= 10:
        puan += 2.0

    # Core Engine Sinyali (Kural 9 Güvencesi)
    if 55 <= dk <= 60 and toplam_gol < 5 and abs(ev_gol - dep_gol) < 3 and skor in [(2,1),(1,2),(3,1),(1,3)] and su_sut <= 8 and delta_atak >= 10:
        return round(puan, 1), True, f"🎯 CORE ENGINE (SNIPER VURUŞU) | İvme: +{delta_atak}"

    return round(puan, 1), True, f"✅ POTANSİYEL | İvme: +{delta_atak}"

# ================================================
# GEMINI AI VE BET365 BAĞLANTILARI
# ================================================
async def gemini_analiz(session, mac):
    keys = [k for k in GEMINI_KEYS if k]
    if not keys: return "AI Analizi devre dışı."
    prompt = f"MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} (DK: {mac['dakika']}). Algoritma altın fırsat penceresi yakaladı. 'State' (Durum) analizi için tek cümlelik, keskin bir yorum yap. JSON kullanma."
    
    for key in keys:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=8) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['candidates'][0]['content']['parts'][0]['text'].strip()
        except: continue
    return "AI Limit."

async def maclari_cek(session):
    maclar = []
    try:
        url = f"https://api.betsapi.com/v3/bet365/inplay?token={BETSAPI_TOKEN}"
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            if data.get('success') != 1: return maclar
            
            results = data.get("results", [])[0] if data.get("results") else []
            logger.info(f"💎 Bet365 Premium: {len(results)} maç HFT filtreden geçiyor...")
            
            for f in results:
                try:
                    dk = int(f.get("timer", {}).get("tm", 0))
                    if not (5 <= dk <= 88): continue
                    
                    skor = f.get("ss", "0-0")
                    ev_g, dep_g = map(int, skor.split("-"))
                    m_id = str(f["id"])
                    
                    # Gol Hafızası
                    onceki = gol_hafizasi.get(m_id, {"toplam": ev_g + dep_g, "son": 0})
                    son_g = dk if (ev_g + dep_g) > onceki["toplam"] else onceki["son"]
                    gol_hafizasi[m_id] = {"toplam": ev_g + dep_g, "son": son_g, "time": datetime.now()}

                    stats = f.get("stats", {})
                    def gs(k, i):
                        v = stats.get(k, [0, 0])
                        return int(v[i]) if isinstance(v, list) else 0

                    maclar.append({
                        "id": m_id, "ev": f["home"]["name"], "dep": f["away"]["name"],
                        "lig": f["league"]["name"], "dakika": dk, "ev_gol": ev_g, "dep_gol": dep_g,
                        "son_gol": son_g, "shots_on_target_ev": gs("on_target", 0), "shots_on_target_dep": gs("on_target", 1),
                        "dangerous_attacks_ev": gs("dangerous_attacks", 0), "dangerous_attacks_dep": gs("dangerous_attacks", 1),
                        "corner_ev": gs("corners", 0), "corner_dep": gs("corners", 1)
                    })
                except: continue
    except Exception as e: logger.error(f"API Hata: {e}")
    return maclar

# ================================================
# ANA DÖNGÜ (MAIN LOOP)
# ================================================
async def main():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    
    async with aiohttp.ClientSession() as session:
        await bot.send_message(
            chat_id=CHAT_ID, 
            text="🚀 V6.1 SNIPER QUANT MASTER BAŞLADI\n\n🎯 Mod: State + Timing\n🕒 Mesai: 13:00 - 00:00\n⚡ Filtre: Lojistik Ceza Model v1\n🔒 Sadece kesin fırsatlar onaylanacak."
        )
        
        while True:
            try:
                cleanup_memory() # Her döngüde bellek temizliği
                
                if not aktif_mi():
                    await asyncio.sleep(600) # Mesai dışı 10 dk uyu
                    continue

                maclar = await maclari_cek(session)
                
                for mac in maclar:
                    if mac["id"] in bildirim_gonderilen: continue
                    
                    puan, gecti, rapor = sinyal_hesapla(mac)

                    if gecti and puan >= MIN_PUAN:
                        tavsiye = tavsiye_uret(mac)
                        yorum = await gemini_analiz(session, mac)
                        nesine = nesine_kontrol(mac['lig'])
                        
                        karar_emoji = "💎🔥🔥" if "ALTIN" in tavsiye else "💎✅"
                        
                        mesaj = (
                            f"{karar_emoji} {tavsiye}\n"
                            f"────────────────────\n"
                            f"⚽ {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
                            f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
                            f"{nesine}\n"
                            f"────────────────────\n"
                            f"📈 SNIPER PUAN: {puan}/15\n"
                            f"📝 {rapor}\n"
                            f"🧠 AI: {yorum}\n"
                            f"────────────────────\n"
                            f"💰 KASA RİSKİ: %3.0"
                        )
                        
                        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
                        # Maçı tekrar bildirmemek için hafızaya al
                        bildirim_gonderilen[mac["id"]] = {"time": datetime.now()}

            except Exception as e: logger.error(f"Ana Döngü Hatası: {e}")
            
            # Tarama Hızı: 3 Dakika (180 saniye)
            await asyncio.sleep(180) 

if __name__ == "__main__":
    asyncio.run(main())
