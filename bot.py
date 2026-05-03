# ================================================
# GEMİNİ AI (V8.3 THE ORACLE - YÖN GÖSTERİCİ MOTOR)
# ================================================
async def get_next_gemini_key():
    global gemini_key_index
    if not GEMINI_KEYS: return None
    key = GEMINI_KEYS[gemini_key_index]
    gemini_key_index = (gemini_key_index + 1) % len(GEMINI_KEYS)
    return key

async def gemini_analiz(mac, tahmin, neden):
    if not GEMINI_KEYS: 
        return "AI API anahtarı tanımlanmadı.", True
    
    prompt = f"""Sen yıllarını canlı bahise vermiş, sahadaki taktiksel savaşı ve maçın kaderini okuyan efsanevi bir bahis üstadısın. 
KURAL 1: Bana klişe ve ezbere laflar etme. Rakamları (skor, korner) ASLA TEKRAR ETME.
KURAL 2: Bizi YÖNLENDİR! Maçın gizli hikayesini anlat ve bahsin kaderini belirle.

GİZLİ MAÇ VERİSİ (Sadece senin analiz yapman için):
Ev: {mac['ev']} | Dep: {mac['dep']} | Skor: {mac['ev_gol']}-{mac['dep_gol']} | Dakika: {mac['dakika']}
Korner: Ev {mac['ev_korner']} - Dep {mac['dep_korner']}
Kırmızı Kart: Ev {mac['ev_kirmizi']} - Dep {mac['dep_kirmizi']}
Botun Sistemsel Tahmini: {tahmin}

GÖREVİN:
1. Bu verilere bakarak sahada kimin kimi dövdüğünü, kimin kontratak aradığını, maçın nereye kırılacağını SEZGİLERİNLE yaz.
2. Bize net bir yön ver! Örneğin: "Ev sahibi saldırıyor gibi görünse de şu an şuursuzca bir baskı var, bu maç 0-0'a kilitlenmeye çok müsait, uzak durun!" ya da "Korner ablukası deplasmanın gardını tamamen düşürmüş, gol adeta geliyorum diyor, botun tahmini kusursuz, tereddütsüz girilir."
3. 2 veya 3 cümlelik, ezber bozan, sokak jargonuyla değil ama 'uzman' diliyle net bir analiz yaz. Riskliyse 'gir' değerini false yap.

SADECE ŞU JSON FORMATINDA YANIT VER:
{{"yorum": "Senin efsanevi, yol gösterici ve taktiksel analizin", "gir": true}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}], 
        "generationConfig": {
            "temperature": 0.95, # Maksimum yaratıcılık ve analiz gücü
            "responseMimeType": "application/json"
        }
    }

    for deneme in range(3):
        secilen_key = await get_next_gemini_key()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={secilen_key}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        res = json.loads(text)
                        
                        await asyncio.sleep(2) 
                        return res.get('yorum', 'Saha içi baskı artmış durumda.'), res.get('gir', True)
                        
                    elif resp.status == 429:
                        logger.warning(f"⚠️ Gemini 429 Hatası. 5sn bekleniyor...")
                        await asyncio.sleep(5)
                        continue
                    else:
                        hata_detay = await resp.text()
                        logger.error(f"Gemini Hata Kodu: {resp.status} - {hata_detay}")
                        break
        except Exception as e: 
            logger.error(f"Gemini Bağlantı Hatası: {e}")
            await asyncio.sleep(2)
    
    return "Maçın taktiksel savaşı kızışmış durumda, botun verdiği sinyal istatistiksel olarak destekleniyor.", True
