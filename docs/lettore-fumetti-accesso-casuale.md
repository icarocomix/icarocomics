# IcaroComics — lettore CBZ/CBR ad accesso casuale

## 1. Obiettivo

Applicazione desktop per Fedora/GNOME per leggere fumetti CBZ/CBR anche molto grandi (da centinaia di MB a oltre 1 GB) senza estrarre l'archivio su disco. Requisiti:

- all'apertura si legge solo l'indice dell'archivio (meno di 0,1 s);
- in RAM si estrae solo la pagina visibile, mentre le pagine vicine vengono precaricate in background;
- integrazione con Nautilus tramite file `.desktop` e associazioni MIME, così il doppio clic apre il fumetto;
- navigazione da tastiera, rotellina e mouse, zoom con adattamento a larghezza, altezza o pagina, modalità Manga (da destra a sinistra), auto-miglioramento dell'immagine in stile "I'm Feeling Lucky".

## 2. Decisioni

| Tema | Scelta | Alternative scartate e motivo |
|---|---|---|
| Toolkit | Python 3 + **PyQt6** | GTK4/PyGObject: integrazione con GNOME leggermente migliore (libadwaita), ma il disegno di immagini grandi con zoom e pan richiede GtkSnapshot/GdkTexture e la gestione dei thread tra GLib e Python è più scomoda. Qt offre `QImage` utilizzabile fuori dal thread GUI, `QThreadPool` con priorità e un disegno di porzioni di immagine molto efficiente. L'integrazione con GNOME (app_id Wayland, icona, MIME) si ottiene comunque con `setDesktopFileName`. |
| CBZ | `zipfile` della libreria standard | Nessuna alternativa necessaria: legge solo End Of Central Directory e Central Directory. |
| CBR | `unrar lb` per l'indice, `unrar p` per estrarre una singola voce su stdout | `rarfile` (non installato e comunque basato su unrar, con file temporanei per alcune operazioni); `7z` (il pacchetto di sistema non include il codec RAR: verificato, "Cannot open the file as archive"). |
| CB7 | `7z l -slt` / `7z e -so -spd` | Supporto aggiuntivo a basso costo; `-spd` disattiva i caratteri jolly nei nomi. |
| Formato | Riconoscimento dai magic bytes, non dall'estensione | Molti `.cbr` sono in realtà ZIP e viceversa. |
| Elaborazione immagini | Solo Pillow (LUT e filtri in C, rilasciano il GIL) | OpenCV (CLAHE): non installato, dipendenza pesante e con guadagno marginale per tavole a fumetti. numpy non serve a runtime. |
| Cache | Due LRU limitate in **byte**: byte compressi (192 MB) e immagini decodificate (640 MB), con le pagine della finestra di lettura protette dall'eliminazione | Una LRU limitata per numero di voci: le pagine variano da 1 a 40 MB decodificate. |
| Thread | 2 worker | Le risorse della macchina sono limitate e una decodifica dura poche decine di ms. |

## 3. Architettura

- **archive**: astrazione `ComicArchive` con backend ZIP, RAR e 7z. Fornisce l'indice ordinato in modo naturale (pagina 2 prima di pagina 10), filtra le estensioni immagine, scarta `__MACOSX` e i file nascosti, e legge una singola pagina come `bytes`. Per i RAR legge il flag "solido" dal main header (RAR4 e RAR5) per avvisare che, in quel caso, l'accesso alle pagine lontane costa di più.
- **loader**: `PageLoader` con `QThreadPool`. Ogni task legge i byte (dalla cache o dall'archivio), decodifica normalizzando in grigio (`L`) o `RGB`, applica l'eventuale auto-miglioramento e produce già la versione ridotta (Lanczos) per la vista corrente, calcolata con la stessa funzione di layout della GUI. Un contatore di generazione scarta i risultati arrivati dopo l'apertura di un altro archivio. A ogni cambio pagina i prefetch ancora in coda vengono annullati e ripianificati: prima la pagina corrente, poi 2 pagine avanti e 1 indietro nella direzione di lettura.
- **enhance**: pipeline di auto-miglioramento (vedi §3.1).
- **view**: widget dedicato senza scrollbar. Gestisce gli offset di scorrimento, centra le pagine più piccole della finestra, usa l'immagine pre-ridotta quando la pagina va rimpicciolita e disegna solo la porzione visibile quando la pagina va ingrandita (nessuna allocazione gigante al 400%). Supporta pan col trascinamento, rotellina e touchpad con cambio pagina al bordo e clic sui terzi laterali.
- **mainwindow**: azioni con scorciatoie (attive anche a schermo intero), toolbar con icone SVG proprie colorate dalla palette, slider invertito in modalità manga, impostazioni persistenti con `QSettings` (zoom, manga globale e per singolo file, ultima pagina letta per file, file recenti, geometria della finestra), drag & drop.
- **app**: entry point, gestore delle eccezioni non gestite (con PyQt6 farebbero terminare il processo) e self-test automatico `--selftest DIR`.

### 3.1 Pipeline di auto-miglioramento

1. Statistiche calcolate su una copia ridotta a circa 512 px.
2. Stretching dei livelli ai percentili 0,5% e 99,5%. I punti nero e bianco sono calcolati per canale ma pesati al 70% verso quelli di luminanza: così si corregge la dominante (carta ingiallita) senza alterare tavole dominate da un unico colore. L'intervallo minimo è di 64 livelli, quindi il guadagno resta al massimo 4×.
3. Gamma adattiva verso una luminanza media di 0,55, attenuata (esponente 0,35) e limitata tra 0,75 e 1,30. Stretching e gamma sono fusi in un'unica LUT per canale.
4. Le tavole quasi acromatiche (saturazione media < 12 e 95° percentile < 40) vengono convertite in grigio puro: la dominante della carta sparisce e la memoria occupata scende a un terzo. Le tavole a colori ricevono un aumento di saturazione di 1,15.
5. Contrasto locale con unsharp mask a raggio ampio (lato lungo / 200).

## 4. Scoperte durante sviluppo e test

- `unrar p` confronta il percorso completo in modo esatto: il solo nome del file non corrisponde. Non c'è quindi rischio di concatenare due voci con lo stesso nome in cartelle diverse, e le parentesi quadre nei nomi non vengono interpretate come caratteri jolly.
- Il `7z` di Fedora non apre i RAR. Per questo non viene usato come fallback dei CBR, ma solo per i CB7.
- Con l'unsharp mask completo, il contrasto locale costava circa 216 ms su una pagina da 10 Mpx. Siccome a raggio ampio la sfocatura è un segnale a bassa frequenza, ora viene calcolata su una copia ridotta 4× e riportata a piena risoluzione, poi ricomposta con un blend estrapolato. Lo scarto medio misurato è di 0,37 livelli (p99 = 1 livello), con circa il 40% di tempo in meno. Il toggle dell'auto-miglioramento su quella pagina è passato da 614 ms a circa 270–360 ms.
- Il doppio clic genera prima un clic singolo. Per questo il doppio clic attiva lo schermo intero solo nella zona centrale, mentre sui lati vale come un ulteriore cambio pagina.
- PyQt6 è installato nel site-packages dell'utente: il launcher non può usare `python3 -s`/`-I`.
- Il tema icone di Qt non è garantito sotto GNOME (in modalità offscreen risulta vuoto): le icone della toolbar sono SVG integrati.

### Misure (self-test end-to-end)

| Archivio | Dimensione | Pagine | Indice | Prima pagina | Sfoglio con prefetch | Salto all'ultima | Toggle auto-miglioramento |
|---|---|---|---|---|---|---|---|
| CBZ sintetico (JPEG 10 Mpx di rumore, caso peggiore) | 1,06 GB | 170 | 1,4 ms | 209 ms | 0,1 ms | 194 ms | 614 ms (prima dell'ottimizzazione) |
| Omnibus Thor, CBZ reale | 1,66 GB | 1353 | 15,5 ms | 54 ms | 0,1 ms | 40 ms | 112 ms |
| Saintia Sho, CBR reale (RAR4) | 228 MB | 202 | 7,2 ms | 104 ms | 0,1 ms | 120 ms | 273 ms |

La cache a regime occupa da 120 a 380 MB (12 pagine). Il processo avviato dal desktop sul file da 1,66 GB occupa circa 210 MB di RSS.

## 5. Riepilogo finale

**Consegnato:** applicazione completa, installata per l'utente e registrata come applicazione predefinita per `application/vnd.comicbook+zip`, `application/x-cbz`, `application/vnd.comicbook-rar`, `application/x-cbr` e `application/x-cb7`. L'avvio tramite `gio open` (lo stesso percorso del doppio clic in Nautilus) è stato verificato. Le associazioni precedenti (Evince) sono salvate e vengono ripristinate da `uninstall.sh`.

**Limiti noti:**

- Nei RAR **solidi** l'estrazione della pagina N richiede di decomprimere tutte le precedenti: è un limite intrinseco del formato, che la cache dei byte compressi mitiga solo in parte. L'app lo segnala nella barra di stato.
- Non sono gestiti archivi cifrati, né la visualizzazione a doppia pagina.
- Non è incluso un thumbnailer per Nautilus.
- Non è stato possibile creare CBR di test perché `rar` non è installato; sono stati usati CBR reali RAR4 e RAR5.

**File:**

- `icarocomics/__init__.py`
- `icarocomics/__main__.py`
- `icarocomics/app.py`
- `icarocomics/archive.py`
- `icarocomics/cache.py`
- `icarocomics/enhance.py`
- `icarocomics/icons.py`
- `icarocomics/layout.py`
- `icarocomics/loader.py`
- `icarocomics/mainwindow.py`
- `icarocomics/view.py`
- `icarocomics/data/icarocomics.svg`
- `install.sh`
- `uninstall.sh`
- `tests/make_samples.py`
- `README.md`
- `TODO.md`

**Installati:**

- `~/.local/share/icarocomics/`
- `~/.local/bin/icarocomics`
- `~/.local/share/applications/icarocomics.desktop`
- `~/.local/share/icons/hicolor/scalable/apps/icarocomics.svg`
- `~/.config/mimeapps.list` (associazioni)
