import asyncio, aiohttp, os, urllib.parse, traceback
from telegram import Bot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
BETSAPI_TOKEN = os.getenv("BETSAPI_TOKEN", "")

bildirim_gonderilen = {}

# -----------------------------
# SAFE JSON FETCH
# -----------------------------
async def safe_get_json(session, url):
    try:
        async with session.get(url, timeout=10) as r:
            return await r.json()
    except:
        return {}

# -----------------------------
# LIST FLATTEN
# -----------------------------
def esnek_liste_duzelt(veri):
    duz = []
    if isinstance(veri, list):
        for e in veri:
            duz.extend(esnek_liste_duzelt(e))
    elif isinstance(veri, dict):
        duz.append(veri)
    return duz

# -----------------------------
# POSSESSION DETECTION
# -----------------------------
def detect_possession_pair(values):
    if len(values) < 2:
        return False

    vals = list(values)
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            a, b = vals[i], vals[j]
            if 0 <= a <= 100 and 0 <= b <= 100:
                if 95 <= (a + b) <= 105:
                    return True
    return False

# -----------------------------
# STAT NORMALIZER (FINAL)
# -----------------------------
def stat_normalize(stats_dict):
    norm = {"TA": None, "DA": None, "SOT": None}
    clean = {}

    for k, v in stats_dict.items():
        try:
            val = int(str(v))
            clean[k.lower()] = val
        except:
            continue

    values = list(clean.values())

    # 🚫 possession data -> reject
    if detect_possession_pair(values):
        return norm

    # keyword match
    for k, v in clean.items():
        if "attack" in k and norm["TA"] is None:
            norm["TA"] = v
        elif "danger" in k and norm["DA"] is None:
            norm["DA"] = v
        elif ("target" in k or "shot" in k) and norm["SOT"] is None:
            norm["SOT"] = v

    # fallback S1 S2 S3
    s_values = {k: v for k, v in clean.items() if k.startswith("s")}
    sorted_s = sorted(s_values.items(), key=lambda x: x[1], reverse=True)

    for _, val in sorted_s:
        if norm["TA"] is None and val >= 20:
            norm["TA"] = val
        elif norm["DA"] is None and 10 <= val <= 80:
            norm["DA"] = val
        elif norm["SOT"] is None and val <= 15:
            norm["SOT"] = val

    return norm

# -----------------------------
# ANALYSIS ENGINE
# -----------------------------
async def analiz_et(results):
    stats = esnek_liste_duzelt(results)

    ev_adi = ""
    dep_adi = ""
    dk = 0
    skor = "0-0"

    teams = []

    for item in stats:
        if item.get("type") == "EV":
            names = item.get("NA", "").split(" v ")
            ev_adi = names[0] if len(names) > 0 else "Ev"
            dep_adi = names[1] if len(names) > 1 else "Dep"

            tm_raw = str(item.get("TM", "0"))
            if "+" in tm_raw:
                dk = int(tm_raw.split("+")[0])
            elif tm_raw.isdigit():
                dk = int(tm_raw)
            else:
                dk = 0

            skor = item.get("SS", "0-0")

        elif item.get("type") == "TE":
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

    # -----------------------------
    # HARD FILTERS
    # -----------------------------

    if e_sot > 15 or d_sot > 15:
        return None

    if (e_sot + d_sot) < 3:
        return None

    if (e_da + d_da) < 40:
        return None

    if e_ta < e_da or d_ta < d_da:
        return None

    if e_ta == 0 and d_ta == 0:
        return None

    if not (10 <= dk <= 90):
        return None

    # SCORE FILTER
    try:
        e_score, d_score = map(int, skor.split("-"))
        if abs(e_score - d_score) >= 2 and dk > 30:
            return None
    except:
        pass

    # -----------------------------
    # SCORING
    # -----------------------------
    puan = 4.0

    if skor in ["0-0", "1-1", "2-2", "1-0", "0-1", "2-1", "1-2"]:
        puan += 3.0

    puan += ((e_da + d_da) // 10) * 0.5
    puan += ((e_sot + d_sot) // 2) * 0.5

    if puan < 4.0:
        return None

    link = f"https://www.nesine.com/iddaa/arama?text={urllib.parse.quote(ev_adi)}"

    return (
        f"💎 SİNYAL (Puan: {puan})\n"
        f"⚽ {ev_adi} {skor} {dep_adi}\n"
        f"⏱ Dakika: {dk}\n"
        f"--------------------\n"
        f"📊 DA: {e_da}-{d_da} | SOT: {e_sot + d_sot} | TA: {e_ta}-{d_ta}\n"
        f"🔗 {link}"
    )

# -----------------------------
# MAIN LOOP
# -----------------------------
async def main():
    bot = Bot(token=TELEGRAM_TOKEN)

    await bot.send_message(chat_id=CHAT_ID, text="🚀 V29 STABLE AKTİF")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                data = await safe_get_json(
                    session,
                    f"https://api.betsapi.com/v3/bet365/inplay_filter?token={BETSAPI_TOKEN}&sport_id=1"
                )

                res = esnek_liste_duzelt(data.get("results", []))

                print(f"DEBUG: {len(res)} maç")

                for m in res:
                    m_id = str(m.get("id") or m.get("FI", ""))

                    if not m_id or m_id in bildirim_gonderilen:
                        continue

                    event_data = await safe_get_json(
                        session,
                        f"https://api.betsapi.com/v3/bet365/event?token={BETSAPI_TOKEN}&FI={m_id}&stats=1"
                    )

                    msg = await analiz_et(event_data.get("results", []))

                    if msg:
                        await bot.send_message(chat_id=CHAT_ID, text=msg)
                        bildirim_gonderilen[m_id] = True

                # memory cleanup
                if len(bildirim_gonderilen) > 500:
                    bildirim_gonderilen.clear()

            except Exception:
                print(traceback.format_exc())

            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
