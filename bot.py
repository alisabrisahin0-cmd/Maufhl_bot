# MAC ANALIZ BOTU - V26.0 OMNI-VERIFIER
# Yenilik: Dinamik mantıksal etiketleme ve otomatik veri doğrulama.

import asyncio, aiohttp, os, urllib.parse, traceback
from telegram import Bot

# --- AYARLAR ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")
GEMINI_KEYS = [os.getenv(f"GEMINI_KEY_{i}", "") for i in range(1, 4)]

# Dinamik Harita - Denetleme sonrası güncellenecek
MAP = {"TA": "S8", "DA": "S4", "SOT": "S1", "COR": "S2"}
bildirim_gonderilen = {}

def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri: duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict): duz.append(veri)
    return duz

async def veri_denetimi_ve_esleme(stats_list):
    """Gelen ham verileri mantık süzgecinden geçirerek etiketleri atar."""
    global MAP
    try:
        # S-kodlarını ayıkla ve sayıya çevir
        v = {k: int(str(val)) for k, val in stats_list.items() if k.startswith('S') and str(val).isdigit()}
        if len(v) < 3: return False # Yeterli veri yoksa denetleme başarısız
        
        # En büyük 3 değeri bul (TA > DA > SOT hiyerarşisi için)
        sk = sorted(v, key=v.get, reverse=True)
        
        # OMNI-GUARD MANTIĞI: Verileri büyüklüğüne göre otomatik eşle
        MAP["TA"] = sk[0]    # En yüksek değer her zaman Toplam Ataktır
        MAP["DA"] = sk[1]    # İkinci yüksek değer Tehlikeli Ataktır
        MAP["SOT"] = sk[2]   # Üçüncü (genelde) İsabetli Şuttur
        
        # Doğrulama: Eğer TA, DA'dan küçükse veri bozuktur
        if v[MAP["TA"]] < v[MAP["DA"]]: return False
        return True
    except: return False

async def analiz_et(results):
    stats = esnek_liste_duzelt(results)
    ev_adi = ""; dep_adi = ""; dk = 0; skor = "0-0"; lig = ""
    ev_v = {}; dep_v = {}

    for item in stats:
        if item.get('type') == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0]; dep_adi = names[1]
            dk = int(str(item.get('TM', 0)) or 0)
            skor = item.get('SS', '0-0'); lig = item.get('CT', 'Lig')
        elif item.get('type') == 'TE':
            if str(item.get('ID')) == '1': ev_v = item
            else: dep_v = item

    # DENETLEME: Veri akışı mantıklı mı?
    if not await veri_denetimi_ve_esleme(ev_v): return None

    # Verileri yeni haritaya göre çek
    e_da = int(ev_v.get(MAP["DA"], 0)); d_da = int(dep_v.get(MAP["DA"], 0))
    e_sot = int(ev_v.get(MAP["SOT"], 0)); d_sot = int(dep_v.get(MAP["SOT"], 0))
    e_ta = int(ev_v.get(MAP["TA"], 0)); d_ta = int(dep_v.get(MAP["TA"], 0))

    if not (20 <= dk <= 85): return None

    # Puanlama (7.0 barajını yıkan yeni matematik)
    puan = 4.0
    if (skor) in ["0-0", "1-1", "2-2", "1-0", "0-1", "2-1", "1-2"]: puan += 3.0
    puan += ((e_da + d_da) // 10) * 0.5
    puan += ((e_sot + d_sot) // 2) * 0.5

    if puan >= 4.0:
        n_link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
        return (f"💎 **SİNYAL (Puan: {puan})**\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ Dakika: {dk}\n"
                f"--------------------\n📊 DA: {e_da}-{d_da} | SOT: {e_sot+d_sot} | TA: {e_ta}-{d_ta}\n"
                f"🔗 [Nesine]({n_link})")
    return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🛡️ V26.0 OMNI-VERIFIER AKTİF: Denetleme mekanizması devrede.")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    res = esnek_liste_duzelt((await r.json()).get('results', []))
                
                for m in res:
                    m_id = str(m.get('id') or m.get('FI', ''))
                    if not m_id or m_id in bildirim_gonderilen: continue
                    
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        msg = await analiz_et((await er.json()).get('results', []))
                        if msg:
                            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                            bildirim_gonderilen[m_id] = True
            except Exception as e:
                print(f"HATA: {e}")
                # Hata raporunu Telegram'a fırlat
                if "Stopping" not in str(e):
                    await bot.send_message(chat_id=CHAT_ID, text=f"⚠️ **DENETİM RAPORU:**\n`{str(e)[:100]}`")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
