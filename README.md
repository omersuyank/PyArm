Robot Kol - Python Seri Kontrol
================================

Kurulum
-------
1) Python 3.10+ kurulu olmalı.
2) Bağımlılıkları yükleyin:

```
pip install -r requirements.txt
```

Konsol Aracı (opsiyonel)
------------------------
```
python serial_control.py COM3 9600
```

GUI Uygulaması
--------------
```
python gui_app.py
```

Özellikler:
- Otomatik port algılama ve bağlanma (ilk bulunan porta bağlanır)
- Portları yenile & manuel bağlan
- 1–5 motor seçimi, d/a/w komutları
- Servo için [, ], c komutları
- Kayıt/oynatma (R/T/P/S/L/V)
- Seri log ekranı

Windows’ta Otomatik Çalıştırma (İsteğe bağlı)
--------------------------------------------
Görev Zamanlayıcı ile oturum açılışında `python gui_app.py` çalıştırabilirsiniz.


