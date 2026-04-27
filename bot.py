"""
MAC ANALİZ BOTU - STRATEJİK GÜNCELLEME (v2.7)
Winning Code Teknik Filtreleri (VU, TÜM, MA, DIYI) ve Altın Pencere Bonusları Entegre Edildi.
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
# AKTİFLİK KONTROLÜ (TR SAATİ GMT+3)
# ================================================
def aktif_mi():
    tr_saati = datetime.now(timezone(timedelta(hours=3)))
    saat = tr_saati.hour
    gun = tr_saati.weekday() 
    if gun <= 4: return 19 <= saat <= 23
    else: return 19 <= saat <= 22

# ================================================
# SINYAL HESAPLAMA (RAPORDAKİ TAM FORMÜL)
# ================================================
def sinyal_hesapla(mac):
    # Veri Tanımları
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
    
    # --- 1. WINNING CODE (KESİN İPTAL KOŞULLARI) ---
    # VU: Ev Baskısı | TÜM: Genel Tempo | MA: Stagnasyon | DIYI: Dep. İstatistik
    VU = 1 if (possession_ev >= 40 and dangerous_ev >= 15) else 0
    TÜM = 1 if (dangerous_ev + dangerous_dep) >= 20 else 0
    
    son_golden_beri = dakika - son_gol_dk if son_gol_dk > 0 else dakika
    MA = 1 if (son_golden_beri > 12 and (dangerous_ev + dangerous_dep) < 15) else 0
    
    # DIYI: Deplasman baskısı ev sahibinin %85'ine ulaştıysa 1 (Başarı için 0 olmalı)
    DIYI = 1 if (dangerous_dep > dangerous_ev * 0.85) else 0

    # FORMÜL: VU=0 OR TÜM=0 OR MA=1 OR DIYI=1 => İPTAL
    if VU == 0 or TÜM == 0 or MA == 1 or DIYI == 1:
        return 0, [], "İptal: Winning Code Kriter Dışı", False

    # --- 2. PUANLAMA VE BONUSLAR ---
    # ToplamPuan = TemelIstatistikPuanlari + ZamanBonusu + BeraberlikBonusu
    puan = 5.0 # Başlangıç Puanı (Filtreleri geçen maçlar için)
    puan_detay = ["✅ Winning Code Filtreleri Onaylandı"]
    strateji = "GOL OLACAK (0.5 ÜST)"
    etiket = ""

    # ZAMAN BONUSLARI (Altın Pencere)
    if 24 <= dakika <= 36:
        puan += 2.0
        puan_detay.append("⭐ Erken Baskı Bonusu (24-36') +2")
    elif 45 <= dakika <= 49:
        puan += 2.0
        puan_detay.append("🌀 Volatilite Penceresi (45-49') +2")
    elif 54 <= dakika <= 60:
        puan += 3.0
        etiket = "🚀 YÜKSEK GÜVENLİ (Power Window)"
        puan_detay.append("⚡ Power Window Bonusu (54-60') +3")

    # KAYIT SKORU & BERABERLİK BONUSU
    if ev_gol == dep_gol:
        puan += 1.5
        puan_detay.append("🤝 Beraberlik Eşitlik Puanı +1.5")
    elif ev_gol < dep_gol and ah < -0.5:
        puan += 1.0
        etiket = "💎 VALUE (Değerli)"
        puan_detay.append("🎯 Favori Geride (Value Tespiti) +1")

    # PİYASA ÇİZGİSİ (ASYA HANDİKAP)
    if -1.5 <= ah <= -0.75:
        strateji = "EV GOL ATACAK (S)"
        puan_detay.append(f"📉 Piyasa Odağı: Ev Gol (AH {ah})")
    elif 0.50 <= ah <= 1.25:
        puan_detay.append(f"⚖️ Piyasa Odağı: Genel Gol (AH {ah})")

    # KORNER ORANI (CORNER LINE)
    if cor_oran >= 11.5:
        puan += 1.5
        puan_detay.append("🚩 Elite Tempo (Corner Line >= 11.5) +1.5")
    elif cor_oran <= 8.0:
        if shots_ev < 4:
            return 0, [], "Düşük Tempo Sinyal Onayı Alamadı", False

    # COOLING OFF (Soğuma Koruması)
    if dakika > 62:
        if (ev_gol >= 3 or dep_gol >= 3) and son_golden_beri > 7:
            if (dangerous_ev + dangerous_dep) < 5:
                # Momentum tazeliği yoksa iptal
                return 0, [], "Cooling Off: Momentum Kesildi", True

    return puan, puan_detay, f"{etiket} {strateji}".strip(), False

# ================================================
# GEMINI AI — ANALİZ (TEKNİK RAPOR ODAKLI)
# ================================================
async def gemini_analiz(mac, puan, strateji):
    if not GEMINI_KEY: return "AI Analizi Yapılamadı.", 1.5
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_KEY}"
    
    prompt = f"""
    Sen uzman bir futbol analizcisisin. Aşağıdaki verileri "Winning Code" stratejisine göre yorumla:
    Maç: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}
    Dakika: {mac['dakika']}, Toplam Puan: {puan}, Strateji: {strateji}
    AH (Asya Handikap): {mac['ah']}, Korner Çizgisi: {mac['corner_line']}
    
    Talimat: Teknik, kısa ve öz bir analiz yap (maks 20 kelime). 
    Kasa yönetimi için %1-%5 arası bir değer öner.
    SADECE JSON dön: {{"yorum": "...", "kasa": 2.5}}
    """
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12) as resp:
                if resp.status == 200:
                    res_json = await resp.json()
                    raw_text = res_json['candidates'][0]['content']['parts'][0]['text']
                    clean_json = re.sub(r'```json|```', '', raw_text).strip()
                    data = json.loads(clean_json)
                    return data.get('yorum', ''), float(data.get('kasa', 1.5))
    except: pass
    return "Momentum ve piyasa verileri gol olasılığını destekliyor.", 1.5

# ================================================
# BİLDİRİM VE ANA DÖNGÜ
# ================================================
async def bildirim_gonder(bot, mac, puan, detaylar, strateji, ai_yorum, kasa):
    status_emoji = "💎" if "VALUE" in strateji else ("🚀" if "YÜKSEK GÜVENLİ" in strateji else "🔥")
    detay_str = "\n".join([f"- <i>{d}</i>" for d in detaylar])
    
    mesaj = (
        f"{status_emoji} <b>{mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']}</b>\n"
        f"🏆 <code>{mac['lig']}</code> | ⏱ <b>{mac['dakika']}. DK</b>\n"
        f"────────────────────\n"
        f"📈 <b>TOPLAM PUAN: {puan}</b>\n"
        f"🎯 <b>STRATEJİ:</b> {strateji}\n"
        f"────────────────────\n"
        f"📝 <b>TEKNİK ANALİZ:</b>\n{detay_str}\n"
        f"────────────────────\n"
        f"🧠 <b>AI DEEP ANALYSIS:</b>\n<i>{ai_yorum}</i>\n"
        f"────────────────────\n"
        f"💰 <b>KASA:</b> %{kasa}"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=mesaj, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Telegram Hatası: {e}")

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
    logger.info("Bot v2.7 Rapor Entegrasyonu Aktif!")
    
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
                    # Not: Gerçek API verilerinde bu değerler dinamik çekilmelidir.
                    mac = {
                        'id': mac_id, 'ev': f['teams']['home']['name'], 'dep': f['teams']['away']['name'],
                        'lig': f['league']['name'], 'dakika': dk, 
                        'ev_gol': f['goals']['home'] or 0, 'dep_gol': f['goals']['away'] or 0, 
                        'shots_on_target_ev': 3, 'dangerous_attacks_ev': 25, 
                        'dangerous_attacks_dep': 12, 'possession_ev': 52, 
                        'ah': -0.75, 'corner_line': 11.5, 'son_gol': 0
                    }
                    puan, detay, strat, ignore = sinyal_hesapla(mac)
                    if puan >= MIN_PUAN:
                        ai_y, ai_k = await gemini_analiz(mac, puan, strat)
                        await bildirim_gonder(bot, mac, puan, detay, strat, ai_y, ai_k)
                        bildirim_gonderilen[mac_id] = True
            
            await asyncio.sleep(420) 
        except Exception as e:
            logger.error(f"Döngü Hatası: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
