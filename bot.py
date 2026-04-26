async def gemini_analiz(mac, puan, strateji, tahmin, neden):
    if not GEMINI_KEY:
        return "AI Devre Disi", 1.5

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"

    prompt = f"Analist olarak şu maçı 2 cümlede yorumla: {mac['ev']} {mac['ev_gol']}-{mac['dep_gol']} {mac['dep']} ({mac['dakika']}.dk). Strateji: {strateji}. Tahmin: {tahmin}. JSON: {{\"yorum\": \"...\", \"kasa\": 1.5}}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload
            ) as response:

                if response.status != 200:
                    return "AI hatasi", 1.0

                data = await response.json()

                text = data["candidates"][0]["content"]["parts"][0]["text"]

                try:
                    parsed = json.loads(text)
                    return parsed.get("yorum", "Yorum yok"), parsed.get("kasa", 1.5)
                except:
                    return text, 1.5

    except Exception as e:
        logger.error(f"Gemini hata: {e}")
        return "AI hata verdi", 1.0
