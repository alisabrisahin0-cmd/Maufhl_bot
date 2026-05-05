import asyncio, aiohttp, os, urllib.parse, traceback
from telegram import Bot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

bildirim_gonderilen = {}

# -------------------------
# VERI DUZELT
# -------------------------
def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri:
            duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict):
        duz.append(veri)
    return duz

# -------------------------
# STAT NORMALIZE
# -------------------------
def stat_normalize(stats_dict):
    norm = {"TA": None, "DA": None, "SOT": None}
    clean = {}

    for k, v in stats_dict.items():
        try:
            val = int(str(v))
            clean[k.lower()] = val
        except:
            continue

    # keyword yakalama
    for k, v in clean.items():
        if "attack" in k and not norm["TA"]:
            norm["TA"] = v
        elif "danger" in k and not norm["DA"]:
            norm["DA"] = v
        elif ("target" in k or "shot_on" in k) and not norm["SOT"]:
            norm["SOT"] = v

    # fallback (S1 S2 vs)
    s_values = {k: v for k, v in clean.items() if k.startswith("s")}
    if s_values:
        sorted_s = sorted(s_values.items(), key=lambda x: x[1], reverse=True)

        for key, val in sorted_s:
            if not norm["TA"] and val >= 20:
                norm["TA"] = val
                continue
            if not norm["DA"] and 10 <= val <= 80:
                norm["DA"] = val
                continue
            if not norm["SOT"] and val <= 15:
                norm["SOT"] = val
                continue

    return norm

# -------------------------
# ANALIZ
# -------------------------
async def analiz_et(results):
    stats = esnek_liste_duzelt(results)

    ev_adi = ""
    dep_adi = ""
    dk = 0
    skor = "0-0"

    teams = []

    for item in stats:
        if item.get('type') == 'EV':
            names = item.get('NA', '').split(' v ')
            ev_adi = names[0] if len(names) > 0 else "Ev"
            dep_adi = names[1] if len(names) > 1 else "Dep"

            # dakika fix
            tm_raw = str(item.get('TM', '0'))
            if '+' in tm_raw:
                dk = int(tm_raw.split('+')[0])
            elif tm_raw.isdigit():
                dk = int(tm_raw)
            else:
                dk = 0

            skor = item.get('SS', '0-0')

        elif item.get('type') == 'TE':
            teams.append(item)

    if len(teams) < 2:
        return None

    ev_v = teams[0]
    dep_v = teams[1]

    ev_stats = stat_normalize(ev_v)
    dep_stats = stat_normalize(dep_v)

    e_ta = ev_stats["TA"] or 0
    d_ta = dep_stats["TA"] or 0

    e_da = ev_stats["DA"] or 0
    d_da = dep_stats["DA"] or 0

    e_sot = ev_stats["SOT"] or 0
    d_sot = dep_stats["SOT"] or 0

    # -------------------------
    # VALIDATION (KRITIK)
    # -------------------------
    if e_sot > 20 or d_sot > 20:
        return None

    if e_ta < e_da or d_ta < d_da:
        return None

    if e_ta == 0 and e_da == 0:
        return None

    if not (10 <= dk <= 90):
        return None

    # -------------------------
    # PUANLAMA
    # -------------------------
    puan = 4.0

    if skor in ["0-0", "1-1", "2-2", "1-0", "0-1", "2-1", "1-2"]:
        puan += 3.0

    puan += ((e_da + d_da) // 10) * 0.5
    puan += ((e_sot + d_sot) // 2) * 0.5

    if puan < 4.0:
        return None

    n_link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"

    return (
        f"💎 SİNYAL (Puan: {puan})\n"
        f"⚽ {ev_adi} {skor} {dep_adi}\n"
        f"⏱ Dakika: {dk}\n"
        f"--------------------\n"
        f"📊 DA: {e_da}-{d_da} | SOT: {e_sot + d_sot} | TA: {e_ta}-{d_ta}\n"
        f"🔗 {n_link}"
    )

# -------------------------
# ANA DONGU
# -------------------------
async def ana_dongu():
    bot = Bot(token=TELEGRAM_TOKEN)

    await bot.send_message(
        chat_id=CHAT_ID,
        text="🚀 V28 STABLE AKTİF: Veri karışma problemi çözüldü"
    )

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
                ) as r:
                    data = await r.json()
                    res = esnek_liste_duzelt(data.get('results', []))

                print(f"DEBUG: {len(res)} maç taranıyor...")

                for m in res:
                    m_id = str(m.get('id') or m.get('FI', ''))

                    if not m_id or m_id in bildirim_gonderilen:
                        continue

                    async with session.get(
                        f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1"
                    ) as er:

                        event_data = await er.json()
                        msg = await analiz_et(event_data.get('results', []))

                        if msg:
                            await bot.send_message(
                                chat_id=CHAT_ID,
                                text=msg
                            )
                            bildirim_gonderilen[m_id] = True

            except Exception:
                print(traceback.format_exc())

            await asyncio.sleep(60)

# -------------------------
# BASLAT
# -------------------------
if __name__ == "__main__":
    asyncio.run(ana_dongu())
