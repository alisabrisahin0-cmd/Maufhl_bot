""
MAC ANALIZ BOTU - V7.2 THE NESINE HYBRID (SYNTAX FIX)
Özellikler: 
1- BetsAPI Veri Motoru (40 Maç Limit Korumalı)
2- Nesine Canlı Bahis Terminolojisi
3- 3x Gemini API Rotasyonu
4- Güvenli Kod Formatı (Copy-Paste Korumalı)
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

# ================================================
# HEALTH CHECK (RAILWAY)
# ================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Aktif")

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
    if gun <= 4:
        return saat >= 19 or saat == 0
    else:
        return 19 <= saat <= 23
    return False

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
                    id SERIAL PRIMARY KEY, 
                    mac_id TEXT, ev TEXT, dep TEXT, lig TEXT,
                    dakika INTEGER, ev_gol INTEGER, dep_gol INTEGER, 
                    puan REAL, strateji TEXT, tahmin TEXT, ai_yorum TEXT, 
                    bildirim_zamani TIMESTAMP DEFAULT NOW(),
                    sonuc TEXT DEFAULT 'BEKLIYOR', 
                    final_ev_gol INTEGER DEFAULT 0, 
                    final_dep_gol INTEGER DEFAULT 0
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
    except Exception as e:
        pass

async def sonuc_guncelle(mac_id, sonuc, final_ev, final_dep):
    try:
        if db_pool:
            await db_pool.execute("""
                UPDATE sinyaller SET sonuc=$1, final_ev_gol=$2, final_dep_gol=$3 
                WHERE mac_id=$4 AND sonuc='BEKLIYOR'
            """, sonuc, final_ev, final_dep, mac_id)
    except Exception as e:
        pass

# ================================================
# VERİ MOTORU (BETSAPI)
# ================================================
async def mac_detay_cek(session, fixture_id):
    try:
        url = f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={fixture_id}"
        async with session.get(url, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success') == 1 and data.get('results'): 
                    return data['results'][0]
            elif resp.status == 429: 
                return "LIMIT"
    except Exception as e: 
        return None
    return None

async def maclari_cek():
    maclar = []
    async with aiohttp.ClientSession() as session:
        list_url = f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
        try:
            async with session.get(list_url, timeout=20) as resp:
                data = await resp.json()
                raw_results = data.get('results', [])
                if raw_results and isinstance(raw_results[0], list): 
                    raw_results = raw_results[0]
                
                adaylar = raw_results[:40]
                for f in adaylar:
                    m_id = str(f.get('ID', f.get('id', f.get('FI', ''))))
                    detay = await mac_detay_cek(session, m_id)
                    
                    if detay == "LIMIT": 
                        break
                    
                    if detay and isinstance(detay, list):
                        try:
                            ev_isim = "Ev Sahibi"
                            dep_isim = "Deplasman"
                            dk = 0
                            ev_gol = 0
                            dep_gol = 0
                            ev_korner = 0
                            dep_korner = 0
                            ev_kirmizi = 0
                            dep_kirmizi = 0
                            
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

                            maclar.append({
                                'id': m_id, 'ev': ev_isim, 'dep': dep_isim, 'lig': 'Bilinmiyor', 
                                'dakika': dk, 'ev_gol': ev_gol, 'dep_gol': dep_gol, 
                                'ev_korner': ev_korner, 'dep_korner': dep_korner, 
                                'ev_kirmizi': ev_kirmizi, 'dep_kirmizi': dep_kirmizi
                            })
                            await asyncio.sleep(1.5)
                        except Exception as e: 
                            continue
        except Exception as e: 
            logger.error(f"Veri çekme hatası: {e}")
    return maclar

# ================================================
# SİNYAL HESAPLAMA
# ================================================
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

    dakika = mac['dakika']
    if 54 <= dakika <= 62:
        puan += 3.0
        detay.append("⏱️ Altın Pencere (54-62') +3.0")
    elif 24 <= dakika <= 36:
        puan += 2.0
        detay.append("⏱️ Erken Baskı (24-36') +2.0")
    elif 45 <= dakika <= 49:
        puan += 2.0
        detay.append("⏱️ Uzatma Volatilite (45-49') +2.0")

    return round(puan, 1), detay, strateji_adi

# ================================================
# NESİNE TAVSİYESİ & GEMİNİ AI
# ================================================
def tavsiye_uret(mac, strateji):
    gol_fark = mac['ev_gol'] - mac['dep_gol']
    if strateji == "KORNER_BASKISI":
        if mac['ev_korner'] > mac['dep_korner']: 
            return "Nesine: Sıradaki Gol 1", "Ev sahibi köşe vuruşlarıyla baskı kuruyor."
        elif mac['dep_korner'] > mac['ev_korner']: 
            return "Nesine: Sıradaki Gol 2", "Deplasman takımı köşe vuruşlarıyla baskıda."
        return "Nesine: Toplam Gol ÜST", "Karşılıklı ataklar ve yüksek köşe vuruşu temposu."
    elif strateji == "KIRMIZI_KART": 
        return "Nesine: Sıradaki Gol / ÜST", "Kırmızı kart nedeniyle sahada boşluklar var."
    elif strateji == "GOL_PATLAMASI": 
        return "Nesine: Toplam Gol ÜST", "Takımlar savunmayı bırakmış, tempo yüksek."
    
    if gol_fark == 0: 
        return "Nesine: Sıradaki Gol", "Skor eşitliği bozulmaya çok yakın."
    
    if gol_fark > 0:
        return "Nesine: MS 1", "Ev sahibi önde ve tempo devam ediyor."
    else:
        return "Nesine: MS 2", "Deplasman önde ve tempo devam ediyor."

async def gemini_analiz(mac, tahmin, neden):
    if not GEMINI_KEYS: 
        return "AI API anahtarı tanımlanmadı.", True
    
    secilen_key = random.choice(GEMINI_KEYS)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={secilen_key}"

    prompt = f"""Sen Nesine.com formatına hakim canlı bahis analistisin.
MAÇ: {mac['ev']} {mac['ev_gol']} - {mac['dep_gol']} {mac['dep']} | {mac['dakika']}. Dk
KORNER: {mac['ev']}: {mac['ev_korner']} | {mac['dep']}: {mac['dep_korner']}
KART: Kırmızı: {mac['ev_kirmizi']} - {mac['dep_kirmizi']}
BOT TAHMİNİ: {tahmin} (Gerekçe: {neden})

SADECE bu verilere dayanarak (Korner baskısı, kart durumu ve skor), botun tahmini mantıklı mı? Girmemek için somut bir risk var mı? 
JSON yanıtı ver: {{"yorum": "Kısa analiz", "gir": true}}"""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}], 
            "generationConfig": {"temperature": 0.05, "maxOutputTokens": 300}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    if "```" in text: 
                        text = [p for p in text.split("```") if p.startswith("json") or p.startswith("{")][0].replace("json", "").strip()
                    res = json.loads(text)
                    return res.get('yorum', ''), res.get('gir', True)
    except Exception as e: 
        pass
    return "AI şu an yorum yapamıyor.", True

# ================================================
# BİLDİRİM & SONUÇ
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, tahmin, neden, ai_yorum, ai_onay, strateji):
    if not ai_onay:
        mesaj = (
            f"⚠️ NESİNE RİSK UYARISI — İŞLEME GİRME!\n"
            f"{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | {mac['dakika']}. Dk\n"
            f"🧠 AI Tespiti: {ai_yorum}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum)
        return

    karar_emoji, karar = ("🔥🔥", "YÜKSEK GÜVEN") if puan >= 10 else ("🔥", "İDEAL FIRSAT") if puan >= 8 else ("✅", "DEĞERLENDİRİLEBİLİR")
    
    detay_metni = "\n".join([f"- {d}" for d in detay[:3]])
    
    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {mac['dakika']}. DK\n"
        f"────────────────────\n"
        f"📈 SİNYAL PUANI: {puan}/12\n"
        f"📝 SİSTEM ANALİZİ:\n{detay_metni}\n"
        f"────────────────────\n"
        f"📊 İSTATİSTİKLER:\n"
        f"🚩 Corner: {mac['ev_korner']}/{mac['dep_korner']} | 🟥 Kırmızı: {mac['ev_kirmizi']}/{mac['dep_kirmizi']}\n"
        f"────────────────────\n"
        f"🧠 NESİNE AI YORUMU:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💡 TAHMİN: {tahmin}\n"
        f"📌 NEDEN: {neden}\n"
        f"{'═'*20}\n{karar_emoji} {karar}\n{'═'*20}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum)
    except Exception as e: 
        pass

def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    yeni_ev = fin_ev - bas_ev
    yeni_dep = fin_dep - bas_dep
    toplam = yeni_ev + yeni_dep
    
    # HATA DÜZELTİLEN YER - Çoklu satırlı güvenli format
    if "ÜST" in tahmin or "Sıradaki Gol (" in tahmin:
        if toplam >= 1: return "TUTTU"
        else: return "DSTU"
    elif "Sıradaki Gol 1" in tahmin or "Ev Sahibi" in tahmin:
        if yeni_ev >= 1: return "TUTTU"
        else: return "DSTU"
    elif "Sıradaki Gol 2" in tahmin or "Deplasman" in tahmin:
        if yeni_dep >= 1: return "TUTTU"
        else: return "DSTU"
    elif "MS 1" in tahmin or "1-X" in tahmin:
        if fin_ev >= fin_dep: return "TUTTU"
        else: return "DSTU"
    elif "MS 2" in tahmin:
        if fin_dep > fin_ev: return "TUTTU"
        else: return "DSTU"
        
    return "BELIRSIZ"

# ================================================
# ANA DÖNGÜ
# ================================================
async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    await db_baglant()
    
    try: 
        await bot.send_message(
            chat_id=CHAT_ID, 
            text="🤖 V7.2 NESINE HYBRID — AKTİF\n✅ Syntax/Format Hatası Giderildi\n✅ 3x Gemini Rotasyonu Devrede\n\nGözlem Başlıyor..."
        )
    except Exception as e: 
        pass

    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(1800)
                continue

            maclar = await maclari_cek()
            aktif_idler = [m['id'] for m in maclar]

            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(
                        bilgi['tahmin'], bilgi['bas_ev'], bilgi['bas_dep'], 
                        bilgi['son_ev'], bilgi['son_dep']
                    )
                    if sonuc != "BELIRSIZ":
                        emoji = "✅ TUTTU!" if sonuc == "TUTTU" else "❌ DÜŞTÜ!"
                        msg = f"📊 SONUÇ: {bilgi['ev']} {bilgi['son_ev']}-{bilgi['son_dep']} {bilgi['dep']}\n{emoji}\n💡 Nesine: {bilgi['tahmin']}"
                        await bot.send_message(chat_id=CHAT_ID, text=msg)
                    
                    await sonuc_guncelle(mac_id, sonuc, bilgi['son_ev'], bilgi['son_dep'])
                    del biten_maclar[mac_id]

            for mac in maclar:
                puan, detay, strateji = sinyal_hesapla(mac)
                mac_id = mac['id']

                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = { 
                        'ev': mac['ev'], 'dep': mac['dep'], 
                        'tahmin': bildirim_gonderilen[mac_id]['tahmin'], 
                        'bas_ev': bildirim_gonderilen[mac_id]['ev_gol'], 
                        'bas_dep': bildirim_gonderilen[mac_id]['dep_gol'], 
                        'son_ev': mac['ev_gol'], 'son_dep': mac['dep_gol'] 
                    }
                
                if puan >= MIN_PUAN:
                    onceki = bildirim_gonderilen.get(mac_id, {}).get('puan', 0)
                    if puan > onceki:
                        tahmin, neden = tavsiye_uret(mac, strateji)
                        ai_yorum, ai_onay = await gemini_analiz(mac, tahmin, neden)

                        await bildirim_gonder(bot, mac, puan, detay, tahmin, neden, ai_yorum, ai_onay, strateji)
                        bildirim_gonderilen[mac_id] = {
                            'puan': puan, 'tahmin': tahmin, 
                            'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']
                        }

        except Exception as e: 
            logger.error(f"Döngü hatası: {e}")
            
        await asyncio.sleep(180)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
