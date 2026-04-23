# MAÇ ANALİZ TELEGRAM BOTU — KURULUM REHBERİ

## ADIM 1 — Telegram Bot Token Al (2 dakika)

1. Telegram'da @BotFather'ı aç
2. /newbot yaz
3. Bot ismi gir (örn: MacAnalizim)
4. Kullanıcı adı gir (örn: MacAnalizimBot)
5. Sana bir TOKEN verecek → bot.py'de TELEGRAM_TOKEN'a yaz

## ADIM 2 — Chat ID Al (1 dakika)

1. Telegram'da @userinfobot'u aç
2. /start yaz
3. Sana ID numaranı verecek → bot.py'de CHAT_ID'ye yaz

## ADIM 3 — Railway.app Ücretsiz Sunucu (5 dakika)

1. https://railway.app adresine git
2. GitHub ile giriş yap (GitHub hesabı yoksa ücretsiz aç)
3. "New Project" → "Deploy from GitHub repo"
4. Bu 3 dosyayı GitHub'a yükle:
   - bot.py
   - requirements.txt
   - Procfile (içeriği: worker: python bot.py)
5. Railway otomatik çalıştırır!

## ADIM 4 — Environment Variables

Railway'de Variables sekmesine git:
- TELEGRAM_TOKEN = (BotFather'dan aldığın token)
- CHAT_ID = (userinfobot'tan aldığın ID)

## ADIM 5 — Procfile Oluştur

Procfile adında bir dosya oluştur (uzantısız), içine şunu yaz:
worker: python bot.py

## BOT NASIL ÇALIŞIR?

- Her 2 dakikada SofaScore'dan canlı maçları çeker
- Sinyal puanı 6+ olan maçlar için Telegram bildirimi gönderir
- Şöyle mesaj gelir:

✅ Barcelona 2–0 Celta Vigo
⏱ 23. Dakika
📊 Sinyal Puanı: 7/12
✅ KG yok ama gol farkı 2
✅ Gol Hızı 0.087/dk
🎯 GOL OLACAK (S)

## AYARLAR (bot.py'de değiştir)

MIN_PUAN = 6      → Kaç puandan bildirim gelsin (7 daha az bildirim, daha güvenli)
KONTROL_SURESI = 120  → Kaç saniyede kontrol (120 = 2 dakika)

## SORUN ÇIKARSA

Telegram: @MacAnalizDestek grubuna yaz
