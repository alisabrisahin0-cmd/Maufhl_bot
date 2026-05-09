import time

def bot_ana_fonksiyon():
    # Botunun asıl yapacağı işler burada yer alır
    print("Bot çalışıyor...")
    # Örnek: bir hata oluştuğunu varsayalım
    # raise Exception("Beklenmedik bir hata!") 

while True:
    try:
        bot_ana_fonksiyon()
    except Exception as e:
        print(f"Hata oluştu: {e}. 5 saniye içinde yeniden başlatılıyor...")
        time.sleep(5) # Döngünün çok hızlı dönüp sistemi yormaması için
