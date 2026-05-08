import time

bot_calisiyor = True
islem_sayaci = 0

while bot_calisiyor:
    print("Bot işlem yapıyor...")
    islem_sayaci += 1
    
    # Burada botun ana görevleri yer alır (API isteği, veri filtreleme vb.)
    time.sleep(1) 
    
    # Belirli bir koşul gerçekleştiğinde botu durdur
    if islem_sayaci >= 5:
        print("Belirlenen işlem sınırına ulaşıldı. Bot durduruluyor...")
        bot_calisiyor = False

print("Bot başarıyla kapatıldı.")
