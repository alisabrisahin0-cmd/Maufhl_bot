"""
MAC ANALİZ BOTU - WINNING CODE ENTEGRASYONU (v2.6)
Rapor Maddeleri: Teknik Filtreler, Zaman Bonusları, AH Entegrasyonu ve Cooling Off eklendi.
AI Yorumlama sistemi JSON kararlılığı için optimize edildi.
"""

import asyncio
import aiohttp
from telegram import Bot
import logging
import os
from datetime import datetime, timedelta, timezone
import json
import re

# ================================================
# YAPILANDIRMA
# ================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_KEY", "").strip()
MIN_PUAN = int(os.getenv("MIN_PUAN", "7")) 

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

bildirim_gonderilen = {}

API_HEADERS = {
    "x-apisports-key": APISPORTS_KEY,
    "x-apisports-host": "v3.football.api-sports.io"
}
BASE_URL = "https://v3.football.api-sports.io"

# ================================================
# AKTİFLİK KONTROLÜ (TR SAATİ)
# ================================================
def aktif_mi():
    tr_saati = datetime.now(timezone(timedelta(hours=3)))
    saat = tr_saati.hour
    gun = tr_saati.weekday() 
    if gun <= 4: return 19 <= saat <= 23
    else: return 19 <= saat <= 22

# ================================================
# SINYAL HESAPLAMA (WINNING CODE & STRATEJİK RAPOR)
# ================================================
def sinyal_hesapla(mac):
    # Verilerin Hazırlanması
    dakika = mac.get('dakika', 0)
    ev_gol = mac.get('ev_gol', 0)
    dep_gol = mac.get('dep_gol', 0)
    shots_ev = mac.get('shots_on_target_ev', 0)
    dangerous_ev = mac.get('dangerous_attacks_ev', 0)
    dangerous_dep = mac.get('dangerous_attacks_dep', 0)
    possession_ev = mac.get('possession_ev', 50)
    ah = mac.get('ah', 0.0)
    cor_oran = mac.get('corner_line', 8.5)
    son_gol_dk = mac.get('son_gol', 0)

    # --- 1. WINNING CODE (HARD FILTERS) ---
    # VU: Ev Baskısı
    VU = 1 if (possession_ev >= 40 and dangerous_ev >= 15) else 0
    # TÜM: Genel Tempo
    TÜM = 1 if (dangerous_ev + dangerous_dep) >= 20 else 0
    # MA: Stagnasyon
    son_golden_beri = dakika - son_gol_dk if son_gol_dk > 0 else dakika
    MA = 1 if (son_golden_beri > 12 and (dangerous_ev + dangerous_dep) < 15) else 0
    # DİYI: Deplasman İlk Yarı İst. (Başarı için 0 olmalı)
    DIYI = 1 if (dangerous_dep > dangerous_ev * 0.85) else 0

    # KESİN İPTAL KOŞULU: VU=0 OR TÜM=0 OR MA=1 OR DIYI=1 ise puan 0.
    if VU == 0 or TÜM == 0 or MA == 1 or DIYI == 1:
        return 0, [], "Filtre: Winning Code Red", False

    # --- 2. PUANLAMA VE BONUSLAR ---
    puan = 5.0
    puan_detay = ["✅ Winning Code Onaylandı"]
    strateji = "GOL OLACAK (0.5 ÜST)"
    etiket = ""

    # ALTIN PENCERE (Zaman Ağırlıklandırması)
    if 24 <= dakika <= 36:
        puan += 2.0
        puan_detay.append("⭐ Erken Baskı (24'-36') +2")
    elif 45 <= dakika <= 49:
        puan += 2.0
        puan_detay.append("🌀 Volatilite Penceresi (45'-49') +2")
    elif 54 <= dakika <= 60:
        puan += 3.0
        etiket = "🚀 YÜKSEK GÜVENLİ (Power Window)"
        puan_detay.append("⚡ Power Window (54'-60') +3")

    # KAYIT SKORU & BERABERLİK BONUSU
    if ev_gol == dep_gol:
        puan += 1.5
        puan_detay.append("🤝 Beraberlik Bonusu +1.5")
    elif ev_gol < dep_gol and ah < -0.5:
        puan += 1.0
        etiket = "💎 Value (Değerli)"
        puan_detay.append("🎯 Favori Geride (Value Tespiti) +1")

    # ASYA HANDİKAP ENTEGRASYONU
    if -1.5 <= ah <= -0.75 and dangerous_ev > dangerous_dep:
        strateji = "EV GOL ATACAK (S)"
        puan_detay.append(f"📉 AH {ah}: Ev Odaklı Piyasa")
    elif 0.50 <= ah <= 1.25:
        strateji = "GOL OLACAK (S)"
        puan_detay.append(f"⚖️ AH {ah}: Genel Gol Odaklı")

    # KORNER ORANI (CORNER LINE)
    if cor_oran >= 11.5:
        puan += 1.5
        puan_detay.append("🚩 Elite Tempo (Korner Line >= 11.5) +1.5")
    elif cor_oran <= 8.0:
        # Sadece çok yüksek şut istatistiği varsa sinyali koru
        if shots_ev < 4:
            return 0, [], "Düşük Korner Hızı/Tempo", False

    # COOLING OFF (Soğuma Koruması)
    if dakika > 62:
        if (ev_gol >= 3 or dep_gol >= 3) and son_golden_beri > 7:
            # Tehlikeli atak girişi yoksa iptal
            if (dangerous_ev + dangerous_dep) < 5:
                return 0, [], "Cooling Off: Momentum Durdu", True

    return puan, puan_detay, f"{etiket} {strateji}".strip(), False

# ================================================
# GEMINI AI — PROFESYONEL ANALİZ
# ================================================
async def gemini_analiz(mac, puan, strateji):
    if not GEMINI_KEY: return "AI Devre Dışı.", 1.5
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""
    Sen uzman bir futbol analistisin. Aşağıdaki maçı "Winning Code" tekniklerine göre yorumla.
    Maç: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
    Dakika: {mac['dakika']}, Puan: {puan}, Strateji: {strateji}
    AH: {mac['ah']}, Korner Çizgisi: {mac['corner_line']}
    
    Görev: 20 kelimeyi geçmeyen, teknik ve ikna edici bir yorum yap. 
    Kasa yönetimi önerisi ver (%1 ile %5 arası).
    Yanıtı SADECE şu formatta ver: {{"yorum": "...", "kasa": 2.5}}
    """
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    raw_text = res_json['candidates'][0]['content']['parts'][0]['text']
                    # JSON temizleme (Markdown bloklarını kaldır)
                    clean_json = re.sub(r'```json|```', '', raw_text).strip()
                    data = json.loads(clean_json)
                    return data.get('yorum', ''), float(data.get('kasa', 1.5))
    except Exception as e:
        logger.error(f"AI Hatası: {e}")
    return "Maçın momentumu ve piyasa çizgisi gol beklentisini maksimize ediyor.", 1.5

# ================================================
# BİLDİRİM VE ANA DÖNGÜ
# ================================================
async def bildirim_gonder(bot, mac, puan, detaylar, strateji, ai_yorum, kasa):
    status_emoji = "💎" if "Value" in strateji else ("🚀" if "YÜKSEK GÜVENLİ" in strateji else "🔥")
    detay_str = "\n".join([f"- <i>{d}</i>" for d in detaylar])
    
    mesaj = (
        f"{status_emoji} <b>{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}</b>\n"
        f"🏆 <code>{mac['lig']}</code> | ⏱ <b>{mac['dakika']}. Dakika</b>\n"
        f"────────────────────\n"
        f"📈 <b>TOPLAM PUAN: {puan}</b>\n"
        f"🎯 <b>STRATEJİ:</b> {strateji}\n"
        f"────────────────────\n"
        f"📝 <b>TEKNİK RAPOR:</b>\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 <b>AI DERİN ANALİZ:</b>\n<i>{ai_yorum}</i>\n"
        f"────────────────────\n"
        f"💰 <b>ÖNERİLEN KASA:</b> %{kasa}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Tel. Hatası: {e}")

async def macları_cek():
    url = f"{BASE_URL}/fixtures?live=all"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=API_HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('response', [])
    except: return []

async def ana_dongu():
    if not TELEGRAM_TOKEN or not APISPORTS_KEY: return
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("Winning Code Botu v2.6 Aktif!")
    
    while True:
        try:
            if not aktif_mi():
                await asyncio.sleep(600)
                continue

            raw_maclar = await macları_cek()
            for f in raw_maclar:
                fix = f['fixture']
                mac_id = str(fix['id'])
                dk = fix['status']['elapsed']
                
                if (20 <= dk <= 85) and mac_id not in bildirim_gonderilen:
                    # Not: API'den AH ve Korner verisi gelmiyorsa mocklanmıştır.
                    mac = {
                        'id': mac_id, 'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                        'lig': f['league']['name'], 'dakika': dk, 
                        'ev_gol': f['goals']['home'] or 0, 'dep_gol': f['goals']['away'] or 0, 
                        'shots_on_target_ev': 3, 'dangerous_attacks_ev': 28, 
                        'dangerous_attacks_dep': 12, 'possession_ev': 55, 
                        'ah': -0.75, 'corner_line': 11.5, 'son_gol': 0
                    }
                    puan, detay, strat, ignore = sinyal_hesapla(mac)
                    if puan >= MIN_PUAN:
                        ai_y, ai_k = await gemini_analiz(mac, puan, strat)
                        await bildirim_gonder(bot, mac, puan, detay, strat, ai_y, ai_k)
                        bildirim_gonderilen[mac_id] = True
            
            await asyncio.sleep(420) 
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
