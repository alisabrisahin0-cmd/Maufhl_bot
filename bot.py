"""
MAC ANALIZ BOTU - V7.1 THE NESINE HYBRID
Özellikler: 
1- BetsAPI Veri Motoru (40 Maç Limit Korumalı)
2- Nesine Canlı Bahis Terminolojisi
3- 3x Gemini API Rotasyonu (Rate Limit Koruması)
4- Kasa Yönetimi İptali (Sadeleştirilmiş Sistem)
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
import random
from datetime import datetime, timedelta
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ================================================
# AYARLAR
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# 3 Adet Gemini API Anahtarı
GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1", ""),
    os.getenv("GEMINI_KEY_2", ""),
    os.getenv("GEMINI_KEY_3", "")
]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]

MIN_PUAN = float(os.getenv("MIN_PUAN", "6.0"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
mac_gecmisi = {}
db_pool = None

# Health Check (Railway için)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot Aktif")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(('0.0.0.0', port), HealthCheckHandler).serve_forever()

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4:  return saat >= 19 or saat == 0
    else:         return 19 <= saat <= 23
    return False

def sonraki_aktif():
    return "19:00 (Hafta ici)" if datetime.now().weekday() <= 4 else "19:00 (Hafta sonu)"

# ================================================
# VERİTABANI
# ================================================
async def db_baglant():
    global db_pool
    try:
        if DATABASE_URL:
            db_pool = await asyncpg.create_pool(DATABASE_URL)
            await db_pool.execute("""
                CREATE TABLE IF NOT EXISTS sinyaller (
                    id SERIAL PRIMARY KEY, mac_id TEXT, ev TEXT, dep TEXT, lig TEXT,
                    dakika INTEGER, ev_gol INTEGER, dep_gol INTEGER, puan REAL,
                    strateji TEXT, tahmin TEXT, ai_yorum TEXT, bildirim_zamani TIMESTAMP DEFAULT NOW(),
                    sonuc TEXT DEFAULT 'BEKLIYOR', final_ev_gol INTEGER DEFAULT 0, final_dep_gol INTEGER DEFAULT 0
                )
            """)
            logger.info("✅ Veritabanı bağlandı!")
    except Exception as e:
        logger.error(f"DB Hatası: {e}")

async def sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum):
    try:
        if db_pool:
            await db_pool.execute("""
                INSERT INTO sinyaller (mac_id, ev, dep, lig, dakika, ev_gol, dep_gol, puan, strateji, tahmin, ai_yorum)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """, mac['id'], mac['ev'], mac['dep'], mac['lig'], mac['dakika'], mac['ev_gol'], mac['dep_gol'], float(puan), strateji, tahmin, ai_yorum)
    except: pass

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3 WHERE mac_id=$4 AND sonuc='BEKLIYOR'", sonuc, final_ev, final_dep, mac_id)
    except: pass

# ================================================
# VERİ MOTORU (BETSAPI LIMIT KORUMALI)
# ================================================
async def mac_detay_cek(session, fixture_id):
    try:
        url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}"
        async with session.get(url, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success') == 1 and data.get('results'): return data['results'][0]
            elif resp.status == 429: return "LIMIT"
    except: return None
    return None

async def maclari_cek():
    maclar = []
    async with aiohttp.ClientSession() as session:
        list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
        try:
            async with session.get(list_url, timeout=20) as resp:
                data = await resp.json()
                raw_results = data.get('results', [])
                if raw_results and isinstance(raw_results[0], list): raw_results = raw_results[0]
                
                adaylar = raw_results[:40] # LIMIT KORUMASI
                for f in adaylar:
                    m_id = str(f.get('ID', f.get('id', f.get('FI', ''))))
                    detay = await mac_detay_cek(session, m_id)
                    if detay == "LIMIT": break
                    
                    if detay and isinstance(detay, list):
                        try:
                            ev_isim, dep_isim = "Ev Sahibi", "Deplasman"
                            dk = 0; ev_gol = 0; dep_gol = 0; ev_korner = 0; dep_korner = 0; ev_kirmizi = 0; dep_kirmizi = 0
                            
                            for i, item in enumerate(detay):
                                t = item.get('type')
                                if t == 'EV':
                                    ev_isim = item.get('NA', '').split(' v ')[0] if ' v ' in item.get('NA', '') else 'Ev'
                                    dep_isim = item.get('NA', '').split(' v ')[1] if ' v ' in item.get('NA', '') else 'Dep'
                                    dk = int(item.get('TM', 0))
                                    skor = item.get('SS', '0-0')
                                    ev_gol = int(skor.split('-')[0]) if '-' in skor else 0
                                    dep_gol = int(skor.split('-')[1]) if '-' in skor else 0
                                elif t == 'SC':
                                    isim = item.get('NA')
                                    if isim == 'ICorner':
                                        ev_korner = int(detay[i+1].get('D1', 0)) if i+1 < len(detay) and str(detay[i+1].get('D1', '')).isdigit() else 0
                                        dep_korner = int(detay[i+2].get('D1', 0)) if i+2 < len(detay) and str(detay[i+2].get('D1', '')).isdigit() else 0
                                    elif isim == 'IRedCard':
                                        ev_kirmizi = int(detay[i+1].get('D1', 0)) if i+1 < len(detay) and str(detay[i+1].get('D1', '')).isdigit() else 0
                                        dep_kirmizi = int(detay[i+2].get('D1', 0)) if i+2 < len(detay) and str(detay[i+2].get('D1', '')).isdigit() else 0

                            maclar.append({'id': m_id, 'ev': ev_isim, 'dep': dep_isim, 'lig': 'Bilinmiyor', 'dakika': dk, 'ev_gol': ev_gol, 'dep_gol': dep_gol, 'ev_korner': ev_korner, 'dep_korner': dep_korner, 'ev_kirmizi': ev_kirmizi, 'dep_kirmizi': dep_kirmizi})
                            await asyncio.sleep(1.5) # LIMIT KORUMASI NEFESİ
                        except: continue
        except Exception as e: logger.error(f"Veri çekme hatası: {e}")
    return maclar

# ================================================
# SİNYAL HESAPLAMA (BETSAPI VERİSİNE UYGUN)
# ================================================
def zaman_bonusu(dakika):
    if 54 <= dakika <= 62: return 3.0, "Altın Pencere (54-62') +3.0"
    elif 24 <= dakika <= 36: return 2.0, "Erken Baskı (24-36') +2.0"
    elif 45 <= dakika <= 49: return 2.0, "Uzatma Volatilite (45-49') +2.0"
    return 0, ""

def sinyal_hesapla(mac):
    mac_id = mac['id']
    suanki_korner = mac['ev_korner'] + mac['dep_korner']
    
    ilk_tarama = mac_id not in mac_gecmisi 
    gecmis = mac_gecmisi.get(mac_id, {'korner': suanki_korner})
    delta_korner = max(0, suanki_korner - gecmis['korner'])
    mac_gecmisi[mac_id] = {'korner': suanki_korner}

    puan = 0.0
    detay = []
    strateji_adi = "GENEL"

    esit_skor = mac['ev_gol'] == mac['dep_gol']
    toplam_gol = mac['ev_gol'] + mac['dep_gol']
    kirmizi = mac['ev_kirmizi'] + mac['dep_kirmizi']

    # Eğer yeni bir korner yoksa ve ilk tarama değilse, ivme yoktur
    if not ilk_tarama and delta_korner == 0:
        return 0, [], ""

    if delta_korner >= 1:
        puan += 3.0 + (delta_korner * 0.5)
        detay.append(f"🔥 YENİ KORNER: İvme Artışı (+{delta_korner}) Toplam: {suanki_korner}")
        strateji_adi = "KORNER_BASKISI"

    if esit_skor:
        puan += 1.5
        detay.append(f"🤝 Skor Dengede +1.5")
    
    if toplam_gol >= 3:
        puan += 1.0
        detay.append(f"⚽ Maç Çok Açık ({toplam_gol} Gol) +1.0")
        strateji_adi = "GOL_PATLAMASI"

    if kirmizi >= 1:
        puan += 2.0
        detay.append(f"🟥 Kırmızı Kart - Savunma Zaafı! +2.0")
        strateji_adi = "KIRMIZI_KART"

    z_bonus, z_label = zaman_bonusu(mac['dakika'])
    if z_bonus > 0:
        puan += z_bonus
        detay.append(f"⏱️ {z_label}")

    return round(puan, 1), detay, strateji_adi

# ================================================
# NESİNE TAVSİYESİ & GEMİNİ AI
# ================================================
def tavsiye_uret(mac, strateji):
    gol_fark = mac['ev_gol'] - mac['dep_gol']
    if strateji == "KORNER_BASKISI":
        if mac['ev_korner'] > mac['dep_korner']: return "Nesine: Sıradaki Gol 1", "Ev sahibi köşe vuruşlarıyla baskı kuruyor."
        elif mac['dep_korner'] > mac['ev_korner']: return "Nesine: Sıradaki Gol 2", "Deplasman takımı köşe vuruşlarıyla baskıda."
        return "Nesine: Toplam Gol ÜST", "Karşılıklı ataklar ve yüksek köşe vuruşu temposu."
    elif strateji == "KIRMIZI_KART": return "Nesine: Sıradaki Gol / ÜST", "Kırmızı kart nedeniyle sahada boşluklar var."
    elif strateji == "GOL_PATLAMASI": return "Nesine: Toplam Gol ÜST", "Takımlar savunmayı bırakmış, tempo yüksek."
    
    if gol_fark == 0: return "Nesine: Sıradaki Gol (Karşılıklı Atak)", "Skor eşitliği bozulmaya çok yakın."
    return "Nesine: MS 1" if gol_fark > 0 else "Nesine: MS 2", "Önde olan takımın temposu düşmemiş."

async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEYS: return "AI API anahtarı tanımlanmadı.", True
    secilen_key = random.choice(GEMINI_KEYS)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={secilen_key}"

    prompt = f"""Sen Nesine.com formatına hakim canlı bahis analistisin.
MAÇ: {mac['ev']} {mac['ev_gol']} - {mac['dep_gol']} {mac['dep']} | {mac['dakika']}. Dk
KORNER: {mac['ev']}: {mac['ev_korner']} | {mac['dep']}: {mac['dep_korner']}
KART: Kırmızı: {mac['ev_kirmizi']} - {mac['dep_kirmizi']}
BOT TAHMİNİ: {tahmin} (Gerekçe: {neden})

SADECE bu verilere dayanarak (Korner baskısı, kart durumu ve skor), botun tahmini mantıklı mı? Girmemek için somut bir risk var mı? 
JSON yanıtı ver: {{"yorum": "3 cümlelik analiz", "gir": true}}"""

    try:
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.05, "maxOutputTokens": 300}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if "
http://googleusercontent.com/immersive_entry_chip/0

Tüm o karmaşayı geride bıraktık. Eğer ortam değişkenlerinde (`Environment Variables`) BetsAPI, Telegram, Database ve 3 adet Gemini API (GEMINI_KEY_1 vb.) hazırsa, bu kod Railway'de anında yeşil ışık yakacak. Ne dersiniz, "Deploy" tuşuna basıyor muyuz? 🚀⚽💰
