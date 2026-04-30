"""
MAC ANALIZ BOTU - KANTİTATİF SÜRÜM (V2.0 HFT MODELİ)
Özellikler: Rolling Window, Exponential Decay, AH Death Zone Filter, Artifact Exploit, Gemini AI
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
import asyncpg
from datetime import datetime, timedelta
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ================================================
# ÇEVRE DEĞİŞKENLERİ VE KURULUM
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")
MIN_PUAN = int(os.getenv("MIN_PUAN", "8")) # Filtreler güçlendiği için min puan eşiği artırıldı

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}
biten_maclar = {}
mac_gecmisi = {} # ROLLING WINDOW (Kayan Pencere) Hafızası
db_pool = None

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

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
# NESİNE FİLTRESİ
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

# ================================================
# ZAMAN YÖNETİMİ
# ================================================
def aktif_mi():
    simdi = datetime.now()
    saat = simdi.hour
    gun = simdi.weekday()
    if gun <= 4:
        return 19 <= saat <= 23
    else:
        return 19 <= saat <= 22

# ================================================
# KANTİTATİF FİLTRELER (YENİ NESİL)
# ================================================

def ustel_zaman_asimi(dakika, son_gol):
    """ Golden sonraki şok dalgasını filtreler (Exponential Decay) """
    if son_gol == 0: return 1.0, ""
    fark = dakika - son_gol
    
    if fark <= 5:
        return 0.0, f"HARD BLOCK: Son gol {fark} dk önce. Şok evresi."
    elif 5 < fark <= 10:
        return 0.5, f"PENALTY: Son gol {fark} dk önce. Rölanti evresi (-%50 Puan)"
    return 1.0, ""

def death_zone_kontrol(ah_deger, ev_gol, dep_gol):
    """ -0.75 ve -0.50 Ölüm Bölgesinde (Skora yatma) filtreleme """
    gol_fark = ev_gol - dep_gol
    if -1.0 <= ah_deger <= -0.5 and gol_fark == 1:
        return True, "DEATH ZONE: Favori ev sahibi 1 farkla önde, oyun kilitlenebilir."
    if 0.5 <= ah_deger <= 1.0 and gol_fark == -1:
        return True, "DEATH ZONE: Favori deplasman 1 farkla önde, oyun kilitlenebilir."
    return False, ""

def premium_artefakt_kontrol(mac):
    """ API'deki 4578X metadata hatasını (Market ID) Likidite göstergesi olarak kullanır """
    cev = mac.get('corner_ev', 0)
    cdep = mac.get('corner_dep', 0)
    if cev > 1000 or cdep > 1000:
        return 3.0, "💎 PREMIUM ARTEFAKT: Yüksek Hacimli Market ID tespit edildi!"
    return 0.0, ""

# ================================================
# SİNYAL VE PUANLAMA MOTORU
# ================================================
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
    
    # 1. KANTİTATİF KONTROLLER
    decay_carpan, decay_mesaj = ustel_zaman_asimi(dakika, son_gol)
    if decay_carpan == 0.0:
        return 0, [decay_mesaj], "BLOCKED", False
    
    dz_aktif, dz_mesaj = death_zone_kontrol(ah_deger, ev_gol, dep_gol)
    if dz_aktif:
        return 0, [dz_mesaj], "DEATH_ZONE", False

    # 2. KAYAN PENCERE (ROLLING WINDOW) HESAPLAMASI
    suanki_tehlikeli = mac.get('dangerous_attacks_ev', 0) + mac.get('dangerous_attacks_dep', 0)
    suanki_sut = mac.get('shots_on_target_ev', 0) + mac.get('shots_on_target_dep', 0)
    
    gecmis = mac_gecmisi.get(mac_id, {'atak': suanki_tehlikeli, 'sut': suanki_sut})
    delta_atak = max(0, suanki_tehlikeli - gecmis['atak'])
    delta_sut = max(0, suanki_sut - gecmis['sut'])
    
    # Geçmişi Güncelle
    mac_gecmisi[mac_id] = {'atak': suanki_tehlikeli, 'sut': suanki_sut}
    
    # HARD-LOCK KAPI KONTROLÜ (İvme yoksa maçı reddet)
    # Son periyotta (yaklaşık 7 dk) en az 8 atak veya 1 isabetli şut yoksa kapıdan geçemez.
    if delta_atak < 8 and delta_sut < 1 and dakika > 20:
        return 0, ["HARD LOCK: Son periyotta yeterli ivme yok."], "REJECTED", False

    detay.append(f"✅ KAPI GEÇİLDİ: Son periyot ivmesi (Atak: +{delta_atak}, Şut: +{delta_sut})")
    puan += 4.0 # Kapı geçiş taban puanı

    # 3. BÜYÜKLÜK ÖLÇEKLENDİRMESİ (Magnitude)
    sut_puani = suanki_sut * 0.5
    puan += sut_puani
    detay.append(f"🎯 Şut Şiddeti: {suanki_sut} isabetli şut (+{sut_puani} Puan)")
    
    if delta_atak >= 15:
        puan += 2.0
        detay.append(f"🌪️ Ani Baskı İvmesi! (+2.0 Puan)")
        stratejiler.append("YUKSEK_IVME")

    # 4. YENİ ZAMAN PENCERELERİ
    if 65 <= dakika <= 75:
        puan += 3.5
        detay.append("🔥 Kırılma Penceresi (65-75') +3.5")
        stratejiler.append("POWER_WINDOW")
    elif 7 <= dakika <= 15:
        puan += 2.0
        detay.append("⚡ Agresif Açılış (7-15') +2.0")
        stratejiler.append("ERKEN_ACILIS")

    # 5. ARTEFAKT SÖMÜRÜSÜ
    artefakt_puan, art_mesaj = premium_artefakt_kontrol(mac)
    if artefakt_puan > 0:
        puan += artefakt_puan
        detay.append(art_mesaj)

    # Lig Katsayısı ve Decay Çarpanı Uygulama
    LIG_KATSAYISI = {'Eredivisie': 1.3, 'Bundesliga': 1.2, 'Premier League': 1.15, 'Champions League': 1.1}
    lig_katsayisi = next((katsayi for lig_adi, katsayi in LIG_KATSAYISI.items() if lig_adi.lower() in mac.get('lig', '').lower()), 1.0)
    
    puan = round((puan * lig_katsayisi) * decay_carpan, 1)
    if decay_carpan < 1.0:
        detay.append(decay_mesaj)
        
    strateji_adi = stratejiler[0] if stratejiler else "MOMENTUM_TAKIBI"
    return puan, detay, strateji_adi, True

# ================================================
# GEMİNİ AI, TAVSİYE VE SONUÇ KONTROLLERİ (AYNI KALDI)
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

async def gemini_analiz(mac, puan, strateji, tahmin, detay_listesi):
    if not GEMINI_KEY: return "AI analiz aktif değil.", 1.5
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"""Sen bir Kantitatif Spor Analistisin. 
MAÇ: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} | LİG: {mac['lig']} | DAKİKA: {mac['dakika']}
BOT GÖZLEMİ: Son periyotta ölçülen ivme yüksek. Handikap Death Zone tehlikesi yok.
GÖREV: Bu maçın istatistiksel yığılmasını değil, CANLI DİNAMİĞİNİ tek bir somut detayla yorumla. Klişe yasak.
YANITIN JSON OLMALI: {{"yorum": "kendi_özgün_yorumun", "gir": true, "kasa": 1.5}}"""

    try:
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 200}}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    start_idx, end_idx = text.find('{'), text.rfind('}')
                    if start_idx != -1 and end_idx != -1:
                        result = json.loads(text[start_idx:end_idx+1])
                        kasa = float(result.get('kasa', 1.5)) if result.get('gir', True) else 0.0
                        return result.get('yorum', 'Kantitatif ivme devam ediyor.'), kasa
                return "AI yanıtı alınamadı.", 1.5
    except Exception as e:
        logger.error(f"Gemini Hatası: {e}")
        return "Yapay Zeka servisi şu an meşgul.", 1.5

# ================================================
# VERİ ÇEKME, BİLDİRİM VE DÖNGÜ (OPTİMİZE EDİLDİ)
# ================================================
async def bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, ai_yorum, ai_kasa):
    kasa = ai_kasa if ai_kasa is not None else kasa_hesapla(puan)
    nesine_durumu = nesine_kontrol(mac['lig'])

    if kasa == 0 and ai_yorum:
        await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ AI UYARISI — GİRME!\n{mac['ev']} vs {mac['dep']}\n🧠 AI: {ai_yorum}")
        return

    karar_emoji = "🔥🔥" if puan >= 12 else "🔥" if puan >= 10 else "✅"
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
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except Exception as e: logger.error(f"Bildirim Hatası: {e}")

async def maclari_cek():
    maclar = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/fixtures?live=all", headers=API_HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for f in data.get('response', []):
                        fixture, teams, goals, league = f.get('fixture', {}), f.get('teams', {}), f.get('goals', {}), f.get('league', {})
                        dakika = int(fixture.get('status', {}).get('elapsed', 0) or 0)
                        if 5 <= dakika <= 88:
                            mac = {'id': str(fixture.get('id', '')), 'ev': teams.get('home', {}).get('name', '?'), 'dep': teams.get('away', {}).get('name', '?'), 'lig': league.get('name', '?'), 'dakika': dakika, 'ev_gol': int(goals.get('home', 0) or 0), 'dep_gol': int(goals.get('away', 0) or 0), 'shots_on_target_ev': 0, 'shots_on_target_dep': 0, 'possession_ev': 50, 'possession_dep': 50, 'dangerous_attacks_ev': 0, 'dangerous_attacks_dep': 0, 'corner_ev': 0, 'corner_dep': 0, 'son_gol': 0}
                            
                            home_id = teams.get('home', {}).get('id')
                            for stat_group in f.get('statistics', []):
                                is_home = (stat_group.get('team', {}).get('id') == home_id)
                                for s in stat_group.get('statistics', []):
                                    tip, val = s.get('type', '').lower(), s.get('value', 0)
                                    val = int(str(val).replace('%', '')) if val and str(val).isdigit() or '%' in str(val) else 0
                                    
                                    if 'on target' in tip:
                                        if is_home: mac['shots_on_target_ev'] = val
                                        else: mac['shots_on_target_dep'] = val
                                    elif 'possession' in tip:
                                        if is_home: mac['possession_ev'] = val
                                        else: mac['possession_dep'] = val
                                    elif 'dangerous attacks' in tip:
                                        if is_home: mac['dangerous_attacks_ev'] = val
                                        else: mac['dangerous_attacks_dep'] = val
                                    elif 'corner' in tip:
                                        if is_home: mac['corner_ev'] = val
                                        else: mac['corner_dep'] = val
                            
                            son_gol = 0
                            for event in f.get('events', []):
                                if event.get('type') == 'Goal':
                                    gdk = int(event.get('time', {}).get('elapsed', 0) or 0)
                                    if gdk > son_gol: son_gol = gdk
                            mac['son_gol'] = son_gol
                            
                            maclar.append(mac)
    except Exception as e: logger.error(f"Mac Cekme Hatasi: {e}")
    return maclar

async def odds_cek(fixture_ids):
    if not fixture_ids: return {}
    odds_map = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/odds/live", headers=API_HEADERS, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get('response', []):
                        fid = str(item.get('fixture', {}).get('id', ''))
                        if fid in fixture_ids:
                            for bet in item.get('bets', []):
                                if 'asian handicap' in bet.get('name', '').lower():
                                    for v in bet.get('values', []):
                                        if 'home' in v.get('value', '').lower():
                                            for p in v.get('value', '').split():
                                                try:
                                                    odds_map[fid] = {'ah_deger': float(p)}
                                                    break
                                                except: pass
    except: pass
    return odds_map

def sonuc_kontrol(tahmin, bas_ev, bas_dep, fin_ev, fin_dep):
    yeni_ev = fin_ev - bas_ev
    yeni_dep = fin_dep - bas_dep
    if "GOL OLACAK" in tahmin: return "TUTTU" if (yeni_ev + yeni_dep) >= 1 else "KAYBETTI"
    elif "EV GOL" in tahmin: return "TUTTU" if yeni_ev >= 1 else "KAYBETTI"
    elif "DEP GOL" in tahmin: return "TUTTU" if yeni_dep >= 1 else "KAYBETTI"
    return "BELIRSIZ"

async def sonuc_bildir(bot, ev, dep, tahmin, sonuc, fin_ev, fin_dep):
    emoji = "✅✅ TAHMİN TUTTU!" if sonuc == "TUTTU" else "❌❌ TAHMİN KAYBETTİ!" if sonuc == "KAYBETTI" else "⚠️ BELİRSİZ"
    mesaj = f"📊 SONUÇ BİLDİRİMİ\n{ev} {fin_ev} - {fin_dep} {dep}\nTahmin: {tahmin}\n{emoji}"
    try: await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    except: pass

async def ana_dongu():
    threading.Thread(target=run_health_check, daemon=True).start()
    bot = Bot(token=TELEGRAM_TOKEN)
    
    simdi = datetime.now()
    gun_str = "Hafta Sonu" if simdi.weekday() >= 5 else "Hafta İçi"
    
    mesaj = (
        "🤖 KANTİTATİF ANALİZ BOTU V2.0\n\n"
        "✅ Rolling Window (Kayan Pencere İvmesi)\n"
        "✅ Exponential Decay (Üstel Soğuma Filtresi)\n"
        "✅ AH Death Zone (Skor Koruma Blokajı)\n"
        "✅ 4578X Premium Artefakt Sömürüsü\n"
        "✅ Yeni Altın Pencere (65-75')\n"
        "✅ Nesine Bülten Filtresi\n"
        "✅ Sonuç ve Kayıp Takibi\n\n"
        f"📅 Mod: {gun_str}\n"
        f"🎯 Min Puan Eşiği: {MIN_PUAN}\n\n"
        "HFT (Yüksek Frekanslı) Algoritma devrede. Av başlıyor 🚀"
    )
    await bot.send_message(chat_id=CHAT_ID, text=mesaj)
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(1800)
                continue

            maclar = await maclari_cek()
            aktif_idler = [m['id'] for m in maclar]

            for mac_id, bilgi in list(biten_maclar.items()):
                if mac_id not in aktif_idler:
                    sonuc = sonuc_kontrol(bilgi['tahmin'], bilgi['bas_ev'], bilgi['bas_dep'], bilgi['son_ev'], bilgi['son_dep'])
                    await sonuc_bildir(bot, bilgi['ev'], bilgi['dep'], bilgi['tahmin'], sonuc, bilgi['son_ev'], bilgi['son_dep'])
                    del biten_maclar[mac_id]

            adaylar = []
            for mac in maclar:
                mac_id = mac['id']
                
                if mac_id in bildirim_gonderilen:
                    biten_maclar[mac_id] = {
                        'ev': mac['ev'], 'dep': mac['dep'], 'tahmin': bildirim_gonderilen[mac_id]['tahmin'],
                        'bas_ev': bildirim_gonderilen[mac_id]['ev_gol'], 'bas_dep': bildirim_gonderilen[mac_id]['dep_gol'],
                        'son_ev': mac['ev_gol'], 'son_dep': mac['dep_gol'],
                    }

            # Adayları belirlemeden önce AH oranlarını çekelim ki Death Zone filtresi çalışsın
            tum_odds = await odds_cek(aktif_idler)
            for mac in maclar:
                if mac['id'] in tum_odds:
                    mac['ah_deger'] = tum_odds[mac['id']].get('ah_deger', 0.0)

                puan, detay, strateji, gecti = sinyal_hesapla(mac)
                
                if gecti and puan >= MIN_PUAN and puan > bildirim_gonderilen.get(mac['id'], {}).get('puan', 0):
                    adaylar.append((mac, puan, detay, strateji))

            if adaylar:
                for mac, puan, detay, strateji in adaylar:
                    tahmin, neden = tavsiye_uret(mac, strateji)
                    ai_yorum, ai_kasa = await gemini_analiz(mac, puan, strateji, tahmin, detay)
                    
                    await bildirim_gonder(bot, mac, puan, detay, strateji, tahmin, ai_yorum, ai_kasa)
                    bildirim_gonderilen[mac['id']] = {'puan': puan, 'tahmin': tahmin, 'ev_gol': mac['ev_gol'], 'dep_gol': mac['dep_gol']}

        except Exception as e: logger.error(f"Döngü Hatası: {e}")
        await asyncio.sleep(600) # 10 Dakika bekle (Rolling Window bu farkı ölçecek)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
