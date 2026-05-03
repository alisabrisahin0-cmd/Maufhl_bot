"""
V6.2 SNIPER QUANT MASTER - RELAXED ENGINE
Özellikler: Genişletilmiş Skor Filtresi (1-1, 2-0 vb.), Esnek Zaman Penceresi (25-65), Yumuşatılmış İvme (Delta 7)
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

# Keskin nişancı barajı
MIN_PUAN = float(os.getenv("MIN_PUAN", "7.0"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ================================================
# BELLEK YÖNETİMİ (TTL: 120 DK)
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
# SAĞLIK KONTROLÜ (RAILWAY)
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
    # YENİ KURAL: Esnetilmiş Altın Fırsat Penceresi ve Skorları
    if 25 <= dk <= 65 and skor in [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (3,1), (1,3)]:
        return "ALTIN FIRSAT: SIRADAKİ GOL (S)"
    return "GOL OLACAK (S)"

# ================================================
# KURAL SETİ: V6.2 RELAXED ANALİZ MOTORU
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

    gecmis = mac_gecmisi.get(mac_id, {"atak": su_atak, "sut": su_sut})
    delta_atak = max(0, su_atak - gecmis["atak"])
    mac_gecmisi[mac_id] = {"atak": su_atak, "sut": su_sut, "time": datetime.now()}

    # --- KESİN RED (HARD BLOCK) FİLTRELERİ ---
    
    if toplam_gol >= 5: return 0, False, "🚫 KAOS BÖLGESİ"
    if abs(ev_gol - dep_gol) >= 3: return 0, False, "🚫 DEATH ZONE"
    
    # YENİ KURAL 1: Skor Filtresi Genişletildi (1-1, 2-2, 0-1, 2-0 eklendi)
    if skor not in [(1,1), (2,2), (0,1), (2,0), (2,1), (1,2), (3,1), (1,3)]: 
        return 0, False, f"🚫 SKOR STATE DIŞI ({ev_gol}-{dep_gol})"
    
    # YENİ KURAL 3: İvme Eşiği Yumuşatıldı (10 yerine 7 yapıldı)
    if delta_atak < 7: return 0, False, f"🚫 YETERSİZ İVME (<7) | Güncel: {delta_atak}"

    # YENİ KURAL 2: Zaman Penceresi Genişletildi (25 ile 65 arası)
    if not (25 <= dk <= 65): return 0, False, "🚫 ZAMAN PENCERESİ DIŞI"

    # --- PUANLAMA (CEZA VE BONUSLAR) ---
    puan = 0.0

    # Altın pencere puanı
    if 25 <= dk <= 65:
        puan += 4.0

    # Lojistik Ceza Modeli (Şut)
    if su_sut <= 8:
        puan += (su_sut * 0.25)
    else:
        puan -= 1.0

    # Korner Anomali Temizliği
    if korner_toplam > 12:
        puan -= 1.0

    # Geçerli İvme (Delta >= 7) Puanı
    if delta_atak >= 7:
        puan += 2.0

    return round(puan, 1), True, f"✅ POTANSİYEL | İvme: +{delta_atak}"

# ================================================
# GEMINI AI VE BET365 BAĞLANTILARI
# ================================================
async def gemini_analiz(session, mac):
    keys = [k for k in GEMINI_KEYS if k]
    if not keys: return "AI Analizi devre dışı."
    prompt = f"MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} (DK: {mac['dakika']}). Futbol veri analizi algoritması bu maçta kırılma anı (gol) tespit etti. Bu skoru ve dakikayı baz alarak tek cümlelik profesyonel bir yorum yap. JSON kullanma."
    
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
            
            for f in results:
                try:
                    dk = int(f.get("timer", {}).get("tm", 0))
                    if not (5 <= dk <= 88): continue
                    
                    skor = f.get("ss", "0-0")
                    ev_g, dep_g = map(int, skor.split("-"))
                    m_id = str(f["id"])
                    
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
            text="🚀 V6.2 DATA-DRIVEN QUANT BAŞLADI\n\n🎯 Mod: Genişletilmiş State Avcısı\n🕒 Mesai: 13:00 - 00:00\n⚡ Esneklik: 25-65 Dk | Skor: 1-1, 2-0 eklendi | Delta: 7\n🔓 Overfitting kilidi açıldı."
        )
        
        while True:
            try:
                cleanup_memory()
                
                if not aktif_mi():
                    await asyncio.sleep(600)
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
                            f"📈 QUANT PUAN: {puan}/15\n"
                            f"📝 {rapor}\n"
                            f"🧠 AI: {yorum}\n"
                            f"────────────────────\n"
                            f"💰 KASA RİSKİ: %3.0"
                        )
                        
                        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
                        bildirim_gonderilen[mac["id"]] = {"time": datetime.now()}

            except Exception as e: logger.error(f"Ana Döngü Hatası: {e}")
            
            await asyncio.sleep(180) 

if __name__ == "__main__":
    asyncio.run(main())
