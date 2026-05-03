# MAC ANALIZ BOTU - V8.0 THE TIME KEEPER (ZAMAN MUHAFIZI)
# Özellikler: 
# 1- Saat Senkronizasyonu (45'te takılı kalan maçları tespit eder)
# 2- Genişletilmiş Nesine Alt Lig/Amatör Filtresi
# 3- Çökmeyen Düz Metin AI Motoru
# 4- Ghost Match (Donuk/Bitik Maç) Dedektörü

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
import random
import re
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
# NESİNE BÜLTEN KONTROLÜ (GÜNCELLENDİ)
# ================================================
def nesine_uygunluk(lig, ev, dep):
    metin = (lig + " " + ev + " " + dep).lower()
    riskli_kelimeler = [
        'u19', 'u20', 'u21', 'u23', 'reserve', 'amateur', 'amatör', 
        'women', 'kadınlar', 'youth', 'liga 3', 'liga 4', 
        'iii liga', 'iv liga', 'regional', 'bilinmiyor'
    ]
    
    if any(kelime in metin for kelime in riskli_kelimeler):
        return "⚠️ Nesine'de Olmayabilir (Alt Lig/Gençler)"
    return "✅ Büyük İhtimalle Nesine Bülteninde Var"

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
# VERİ MOTORU (BETSAPI) & ZAMAN SENKRONİZATÖRÜ
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
                            lig_isim = "Bilinmiyor"
                            dk = 0
                            tt = '1'
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
                                    lig_isim = item.get('CT') or item.get('L3') or item.get('CC') or 'Bilinmiyor'
                                    dk = int(item.get('TM', 0))
                                    tt = str(item.get('TT', '1'))
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
                                elif t == 'ST':
                                    # YENİ: ZAMAN SENKRONİZATÖRÜ (STUCK AT 45 FİX)
                                    # Olay metinlerini ("85' - Sarı Kart") tarar ve en büyük dakikayı alır
                                    la_metin = str(item.get('LA', ''))
                                    match = re.search(r'^(\d+)(?:\+\d+)?\'', la_metin)
                                    if match:
                                        olay_dakikasi = int(match.group(1))
                                        if olay_dakikasi > dk:
                                            dk = olay_dakikasi

                            # Aşırı uzun (buga girmiş) maçları engelle
                            if dk > 105:
                                continue

                            maclar.append({
                                'id': m_id, 'ev': ev_isim, 'dep': dep_isim, 'lig': lig_isim, 
                                'dakika': dk, 'devre_arasi': (dk == 45 and tt == '0'),
                                'ev_gol': ev_gol, 'dep_gol': dep_gol, 
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
# SİNYAL HESAPLAMA & GHOST MATCH DEDEKTÖRÜ
# ================================================
def sinyal_hesapla(mac):
    mac_id = mac['id']
    suanki_korner = mac['ev_korner'] + mac['dep_korner']
    dakika = mac['dakika']
    simdi = datetime.now()
    
    if mac_id not in mac_gecmisi:
        mac_gecmisi[mac_id] = {'korner': suanki_korner, 'dakika': dakika, 'son_hareket': simdi}
        return 0, [], "GENEL"
        
    gecmis = mac_gecmisi[mac_id]
    delta_korner = max(0, suanki_korner - gecmis['korner'])
    
    # 👻 DONUK MAÇ (GHOST BUSTER) KONTROLÜ
    if gecmis['dakika'] == dakika:
        gecen_sure = (simdi - gecmis['son_hareket']).total_seconds() / 60
        if gecen_sure > 15 and not mac.get('devre_arasi', False):
            return 0, [], "GHOST"
    else:
        gecmis['dakika'] = dakika
        gecmis['son_hareket'] = simdi

    gecmis['korner'] = suanki_korner

    puan = 0.0
    detay = []
    strateji_adi = "GENEL"

    esit_skor = mac['ev_gol'] == mac['dep_gol']
    toplam_gol = mac['ev_gol'] + mac['dep_gol']
    kirmizi = mac['ev_kirmizi'] + mac['dep_kirmizi']

    if delta_korner == 0:
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

    if 54 <= dakika <= 62:
        puan += 3.0
        detay.append("⏱️ Altın Pencere (54-62') +3.0")
    elif 24 <= dakika <= 36:
        puan += 2.0
        detay.append("⏱️ Erken Baskı (24-36') +2.0")
    elif 45 <= dakika <= 49 and not mac.get('devre_arasi', False):
        puan += 2.0
        detay.append("⏱️ Uzatma Volatilite (45-49') +2.0")

    return round(puan, 1), detay, strateji_adi

# ================================================
# NET TAHMİNLER (AÇIK TÜRKÇE)
# ================================================
def tavsiye_uret(mac, strateji):
    gol_fark = mac['ev_gol'] - mac['dep_gol']
    ev_korner = mac['ev_korner']
    dep_korner = mac['dep_korner']
    korner_farki = abs(ev_korner - dep_korner)

    if strateji == "KORNER_BASKISI":
        if ev_korner > dep_korner and korner_farki >= 2: 
            return "🎯 KESİN TAHMİN: SIRADAKİ GOLÜ EV SAHİBİ ATAR", "Ev sahibi kornerlerle oyunu rakip sahaya yıktı."
        elif dep_korner > ev_korner and korner_farki >= 2: 
            return "🎯 KESİN TAHMİN: SIRADAKİ GOLÜ DEPLASMAN ATAR", "Deplasman ekibi baskıyı artırdı, savunma bunaldı."
        return "🎯 KESİN TAHMİN: SIRADAKİ GOL OLUR (ÜST)", "İki takım da karşılıklı korner üretiyor, maç git-gelli."
    
    elif strateji == "KIRMIZI_KART": 
        return "🎯 KESİN TAHMİN: SIRADAKİ GOL OLUR (ÜST)", "Kırmızı kart oyunu tamamen açtı, savunma dengesi kayboldu."
    
    elif strateji == "GOL_PATLAMASI": 
        return "🎯 KESİN TAHMİN: MAÇ SONU ÜST BİTER", "Maçta gol düellosu var, takımlar savunmayı bırakmış."
    
    if gol_fark == 0: 
        if ev_korner > dep_korner:
            return "🎯 KESİN TAHMİN: SIRADAKİ GOLÜ EV SAHİBİ ATAR", "Skor eşit ama ev sahibi galibiyete daha yakın."
        elif dep_korner > ev_korner:
            return "🎯 KESİN TAHMİN: SIRADAKİ GOLÜ DEPLASMAN ATAR", "Skor eşit ama deplasman ciddi tehlike yaratıyor."
        return "🎯 KESİN TAHMİN: 0.5 ÜST (Maçta Gol Olur)", "Saha içi aksiyon yüksek, takımlar beraberliğe razı değil."
    
    if gol_fark > 0:
        return "🎯 KESİN TAHMİN: MAÇ SONU EV SAHİBİ KAZANIR", "Ev sahibi önde ve momentumu hala koruyor."
    else:
        return "🎯 KESİN TAHMİN: MAÇ SONU DEPLASMAN KAZANIR", "Deplasman ekibi üstünlüğü ele aldı, rakibi çıkartmıyor."

# ================================================
# GEMİNİ AI (ÇÖKMEYEN DÜZ METİN MOTORU)
# ================================================
async def gemini_analiz(mac, tahmin, neden):
    if not GEMINI_KEYS: 
        return "AI API anahtarı tanımlanmadı.", True
    
    secilen_key = random.choice(GEMINI_KEYS)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={secilen_key}"

    prompt = f"""Sen saha içini okuyan ve çok iddialı, özgün cümleler kuran usta bir canlı bahis analistisin.
KURAL 1: Bana ASLA maçtaki korneri, skoru veya istatistiği tekrar etme!
KURAL 2: Her maç için YEPYENİ benzetmeler kullan. Robotik olma.
KURAL 3: SADECE YORUMU YAZ. Asla JSON, markdown veya gereksiz sembol kullanma.

GİZLİ MAÇ VERİSİ (Sadece referansın için):
{mac['ev']} {mac['ev_gol']} - {mac['dep_gol']} {mac['dep']} | {mac['dakika']}. Dk
Kornerler: Ev: {mac['ev_korner']} - Dep: {mac['dep_korner']}
Sistem Tahmini: {tahmin}

GÖREV:
Rakamların arkasındaki "Kırılma Anını" oku. (Örnek: 'Ceza sahasındaki yoğun abluka, savunmanın her an teslim olacağının sinyalini veriyor.')
Yazdığın 2 cümlelik yorumun EN SONUNA, eğer bu tahmine girmek mantıklıysa "[UYGUN]", maç kilitli ve riskliyse "[RİSKLİ]" yaz.
Başka hiçbir şey ekleme."""

    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}], 
            "generationConfig": {"temperature": 0.9} 
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    
                    gir = True
                    if "[RİSKLİ]" in text.upper():
                        gir = False
                        
                    text = text.replace("[UYGUN]", "").replace("[RİSKLİ]", "").replace("[uygun]", "").replace("[riskli]", "").strip()
                    return text, gir
                else:
                    logger.error(f"Gemini API Hatası: {resp.status}")
    except Exception as e: 
        pass
    
    alternatifler = [
        "Saha içindeki baskı iyice arttı, savunmanın her an hata yapma ihtimali yüksek.",
        "Karşılıklı ataklarla tempo tavan yapmış, bu dakikalarda bir kırılma yaşanması sürpriz olmaz.",
        "Takımların oyunu geniş alana yaymasıyla ceza sahası aksiyonları tehlikeli boyutlara ulaştı."
    ]
    return random.choice(alternatifler), True

# ================================================
# BİLDİRİM & SONUÇ
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, tahmin, neden, ai_yorum, ai_onay, strateji):
    nesine_durum = nesine_uygunluk(mac['lig'], mac['ev'], mac['dep'])
    
    dk_gosterimi = f"{mac['dakika']}. DK"
    if mac.get('devre_arasi'):
        dk_gosterimi = "45. DK (Devre Arası)"

    if not ai_onay:
        mesaj = (
            f"⚠️ NESİNE RİSK UYARISI — İŞLEME GİRME!\n"
            f"{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | {dk_gosterimi}\n"
            f"📺 BÜLTEN: {nesine_durum}\n"
            f"🕵️‍♂️ ÜSTAD AI: {ai_yorum}"
        )
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
        await sinyal_kaydet(mac, puan, strateji, tahmin, ai_yorum)
        return

    karar_emoji, karar = ("🔥🔥", "YÜKSEK GÜVEN") if puan >= 10 else ("🔥", "İDEAL FIRSAT") if puan >= 8 else ("✅", "DEĞERLENDİRİLEBİLİR")
    
    detay_metni = "\n".join([f"- {d}" for d in detay[:3]]) if detay else "- İvme dengeli seyrediyor."
    
    mesaj = (
        f"{karar_emoji} {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}\n"
        f"🏆 {mac['lig']} | ⏱️ {dk_gosterimi}\n"
        f"📺 BÜLTEN: {nesine_durum}\n"
        f"────────────────────\n"
        f"📈 SİNYAL PUANI: {puan}/12\n"
        f"🧮 MATEMATİKSEL PUANLAMA:\n{detay_metni}\n"
        f"────────────────────\n"
        f"📊 CANLI İSTATİSTİKLER:\n"
        f"🚩 Corner: {mac['ev_korner']}/{mac['dep_korner']} | 🟥 Kırmızı: {mac['ev_kirmizi']}/{mac['dep_kirmizi']}\n"
        f"────────────────────\n"
        f"🕵️‍♂️ ÜSTAD AI (GRİ ALAN) YORUMU:\n{ai_yorum}\n"
        f"────────────────────\n"
        f"💡 {tahmin}\n"
        f"📌 GEREKÇE: {neden}\n"
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
    
    tahmin_upper = tahmin.upper()
    
    if "ÜST" in tahmin_upper or "SONRAKİ GOL" in tahmin_upper or "GOL OLUR" in tahmin_upper:
        if toplam >= 1: return "TUTTU"
        else: return "DSTU"
    elif "EV SAHİBİ ATAR" in tahmin_upper or "EV SAHİBİ KAZANIR" in tahmin_upper:
        if yeni_ev >= 1 or fin_ev >= fin_dep: return "TUTTU"
        else: return "DSTU"
    elif "DEPLASMAN ATAR" in tahmin_upper or "DEPLASMAN KAZANIR" in tahmin_upper:
        if yeni_dep >= 1 or fin_dep > fin_ev: return "TUTTU"
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
            text="🤖 V8.0 THE TIME KEEPER — AKTİF\n✅ Zaman Senkronizatörü Devrede (Alt Liglerde Saati Doğrular)\n✅ Amatör/Alt Lig Filtreleri Genişletildi\n\nGözlem Başlıyor..."
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
                        msg = f"📊 SONUÇ: {bilgi['ev']} {bilgi['son_ev']}-{bilgi['son_dep']} {bilgi['dep']}\n{emoji}\n💡 Hedef: {bilgi['tahmin']}"
                        await bot.send_message(chat_id=CHAT_ID, text=msg)
                    
                    await sonuc_guncelle(mac_id, sonuc, bilgi['son_ev'], bilgi['son_dep'])
                    del biten_maclar[mac_id]

            for mac in maclar:
                puan, detay, strateji = sinyal_hesapla(mac)
                mac_id = mac['id']

                if strateji == "GHOST" or strateji == "GENEL":
                    continue

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
