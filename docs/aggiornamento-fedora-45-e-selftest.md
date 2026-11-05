# Dopo l'aggiornamento a Fedora 45: dipendenza PyQt6 e isolamento del self-test

## 1. Obiettivo

Far ripartire IcaroComics dopo l'aggiornamento di sistema da Fedora 44 a Fedora 45 e rendere il self-test (`--selftest`) indipendente dalle preferenze salvate dall'utente.

## 2. Diagnosi del mancato avvio

- Con l'aggiornamento di sistema del 4 novembre 2026, Python è passato dalla versione 3.14 alla 3.15.
- PyQt6 non arrivava da un pacchetto RPM. Era installato con `pip --user` in `~/.local/lib/python3.14/site-packages`, una cartella che il nuovo interprete non legge più. Il launcher si fermava quindi con `ModuleNotFoundError: No module named 'PyQt6'`.
- Le altre dipendenze (Pillow, numpy, unrar, 7zip) sono pacchetti di sistema e sono state aggiornate insieme alla distribuzione.
- Il codice dell'applicazione e la sua installazione non avevano difetti: launcher, file `.desktop` e associazioni MIME erano integri.

**Decisione:** installare il pacchetto di sistema `python3-pyqt6`. È stata scartata la reinstallazione con `pip --user`, che si romperebbe di nuovo al prossimo cambio di versione minore di Python. Non serve rieseguire `install.sh`, perché il launcher usa già `/usr/bin/python3`.

## 3. Problema emerso nel self-test

Con le dipendenze ripristinate, il self-test scadeva comunque in timeout. Le cause erano due:

- Con `enhance=true` già presente nelle preferenze, il passo che attiva l'auto-miglioramento non provocava alcun cambio di stato. Il segnale `page_shown` atteso non veniva emesso e il test restava in attesa fino alla scadenza.
- Più in generale, il test ereditava le preferenze dell'utente (zoom, manga globale e per singolo file) e ci scriveva sopra: `recent`, `last_dir`, avanzamento di lettura del file di prova e, alla chiusura della finestra, la geometria 1000×1100 usata dal test. La vecchia pulizia finale ripristinava solo tre impostazioni, e per giunta su valori fissi invece che su quelli originali.

## 4. Soluzione adottata

- **Stato iniziale neutro:** prima di aprire il file, il test spegne auto-miglioramento e manga (sia globale sia per il file di prova) e imposta "adatta pagina". I passi successivi producono così sempre un cambio di stato reale.
- **Ripristino integrale delle preferenze:** all'avvio il test salva una copia esatta del file INI di QSettings. Al termine, sia in caso di successo sia di fallimento e dopo la chiusura della finestra (che salva geometria e avanzamento), lo riscrive identico. Se il file all'avvio non esisteva, lo cancella.
- **Alternativa scartata:** copiare le preferenze chiave per chiave tramite QSettings. Nel formato INI i tipi non si conservano (ad esempio una lista con un solo elemento viene riletta come stringa), quindi riscrivere i valori potrebbe alterare preferenze come l'elenco dei file recenti.

## 5. Verifiche

Le prove sono state eseguite in modalità offscreen con una cartella di configurazione isolata.

- Con una copia delle preferenze reali (`enhance=true`): esito `ok: true`, toggle dell'auto-miglioramento in circa 136 ms, file delle preferenze identico byte per byte (stesso SHA-1) prima e dopo il test.
- Senza un file di preferenze iniziale: esito `ok: true`, e al termine non rimane alcun file.
- Con un esito negativo provocato apposta (archivio di 3 pagine, mentre il test ne sfoglia 5, quindi timeout): preferenze ripristinate identiche.

## 6. Limiti noti

- Il test richiede archivi con almeno 7 pagine (prima pagina più 5 sfogli più margine). Con archivi più corti va in timeout invece di adattare il numero di sfogli.
- La copia installata in `~/.local/share/icarocomics/app` si aggiorna solo rieseguendo `install.sh`.

## 7. File toccati

- `icarocomics/app.py`
- `docs/aggiornamento-fedora-45-e-selftest.md`
- `docs/lettore-fumetti-accesso-casuale.md`
- `TODO.md`
