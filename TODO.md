# IcaroComics — avanzamento lavori

- [x] Verifica ambiente (PyQt6 6.11, Pillow 12.3, numpy, unrar 7.23, 7z 26.04, GNOME/Wayland)
- [x] Backend archivi (`archive.py`): ZIP (central directory), RAR (unrar p), 7z, rilevamento magic
- [x] Pipeline auto-miglioramento (`enhance.py`)
- [x] Cache LRU a budget di byte + loader asincrono con prefetch (`cache.py`, `loader.py`)
- [x] Vista pagina con zoom/fit/manga (`view.py`)
- [x] Finestra principale, toolbar, scorciatoie, impostazioni (`mainwindow.py`, `app.py`)
- [x] Icona SVG, file .desktop, script `install.sh` / `uninstall.sh`
- [x] Generazione archivi di test + self-test offscreen (tempi indice, rendering)
- [x] Installazione sul sistema + registrazione MIME + verifica `xdg-mime`
- [x] Avvio reale tramite associazione MIME
- [x] Documentazione `docs/` e README
