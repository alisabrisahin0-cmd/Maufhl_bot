import asyncio
import os
from telegram import Bot

async def main():
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    
    print("TEST 1: Bot çalışmaya başladı.")
    print(f"TEST 2: Token okundu mu? -> {'Evet' if token else 'HAYIR'}")
    
    if token and chat_id:
        bot = Bot(token=token)
        try:
            await bot.send_message(chat_id=chat_id, text="TEST BAŞARILI: Bot yaşıyor!")
            print("TEST 3: Telegram mesajı başarıyla gönderildi.")
        except Exception as e:
            print(f"TEST 3 HATASI: Telegram'a bağlanamadı -> {e}")
    else:
        print("KRİTİK HATA: Railway Variables kısmında TELEGRAM_TOKEN veya CHAT_ID eksik!")

if __name__ == "__main__":
    asyncio.run(main())
