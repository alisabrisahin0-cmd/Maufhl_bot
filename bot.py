import asyncio, aiohttp, os, urllib.parse, traceback
from telegram import Bot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

# Sinyal hafızası (Aynı maçı tekrar atmamak için)
bildirim_gonderilen = {}

def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri: duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict): duz.append(veri)
    return duz

async def akilli_denetleme(ev_v, dep_v, dk):
    """Veriyi sadece rakam olarak değil, mantık olarak ayrıştırır."""
    try:
        def ayikla(v):
            r = {k: int(val) for k, val in v.items() if k.startswith('S') and str(val).isdigit()}
            return sorted(r.items(), key=lambda x: x[1], reverse=True)

        e = ayikla(ev_v)
        d = ayikla(dep_v)

        if len(e) < 3 or len(d) < 3: return None

        # 1. POSSESSION (100 KURALI) DENETİMİ
        ta_e, ta_d = e[0][1], d[0][1]
        if (ta_e + ta_d) == 100:
            ta_e, da_e, sot_e = e[1][1], e[2][1], e[3][1]
            ta_d, da_d, sot_d = d[1][1], d[2][1], d[3][1]
        else:
            ta_e, da_e, sot_e = e[0][1], e[1][1], e[2][1]
            ta_d, da_d, sot_d = d[0][1], d[1][1], d[2][1]

        # 2. FİZİKSEL LİMİT (13 DK'DA 17 ŞUT ENGELLİ)
        toplam_sot = sot_e + sot_d
        if toplam_sot > (dk * 0.7): # Dakika başı 0.7 şuttan fazlası şüphelidir
            sot_e = 0; sot_d = 0

        return {"TA": ta_e + ta_d, "DA": da_e + da_d, "SOT": sot_e + sot_d}
    except: return None

async def analiz_ve_gonder(bot, results):
    try:
        stats = esnek_liste_duzelt(results)
        ev_adi = ""; dep_adi = ""; dk = 0; skor = "0-0"; ev_v = {}; dep_v = {}

        for item in stats:
            if item.get('type') == 'EV':
                names = item.get('NA', '').split(' v ')
                ev_adi = names[0] if len(names) > 0 else "Ev"
                dep_adi = names[1] if len(names) > 1 else "Dep"
                dk = int(str(item.get('TM', 0)) or 0)
                skor = item.get('SS', '0-0')
            elif item.get('type') == 'TE':
                if str(item.get('ID')) == '1': ev_v = item
                else: dep_v = item

        res = await akilli_denetleme(ev_v, dep_v, dk)
        if not res or not (15 <= dk <= 85): return # Dakika filtresi

        # PUANLAMA MOTORU (GERÇEKÇİ)
        puan = 4.0
        if skor in ["0-0", "1-1", "1-0", "0-1"]: puan += 3.0
        puan += min((res["DA"] // 10) * 0.5, 3.0)
        puan += min((res["SOT"] // 2) * 0.5, 2.0)

        # Erken dakika koruması
        if dk < 20: puan = min(puan, 8.5)

        if puan >= 7.0:
            link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"
            msg = (f"💎 **SİNYAL (Puan: {round(puan,1)})**\n⚽ {ev_adi} {skor} {dep_adi}\n⏱ Dakika: {dk}\n"
                   f"--------------------\n📊 DA: {res['DA']} | SOT: {res['SOT']} | TA: {res['TA']}\n🔗 [Nesine]({link})")
            return msg
    except: return None

async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="🚀 **V33.0 AKTİF:** Denetleme mekanizması ve çökme koruması ile sistem ayakta.")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1") as r:
                    res = (await r.json()).get('results', [])
                
                for m in esnek_liste_duzelt(res):
                    m_id = str(m.get('id') or m.get('FI', ''))
                    if not m_id or m_id in bildirim_gonderilen: continue
                    
                    async with session.get(f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1") as er:
                        msg = await analiz_ve_gonder(bot, (await er.json()).get('results', []))
                        if msg:
                            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                            bildirim_gonderilen[m_id] = True
            except Exception as e:
                print(f"Döngü Hatası: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(ana_dongu())
