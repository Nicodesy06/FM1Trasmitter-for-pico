# FM-1 Transporter multiboard: rapporto di porting (DollaTek RP2040)

Stato al 2026-10-11. Ogni affermazione è marcata con il livello di prova che ha realmente:

- **HARDWARE VERIFIED**: osservato su una scheda fisica;
- **BUILD VERIFIED**: compilato, immagine controllata (formato, marcatore scheda, hash
  riproducibili), non eseguito su hardware;
- **SIMULATED**: eseguito contro il simulatore o il codice firmware compilato per PC con un
  mock della ROM JieLi;
- **NON VERIFICATO**: ipotesi o dato del venditore.

In questo lavoro **nessuna scheda fisica è stata collegata**: niente di quanto segue è
HARDWARE VERIFIED, tranne ciò che lo era già a monte (la XIAO con il firmware upstream
`a632d92`, che ha recuperato l'unità dell'autore di MvaveFM1Unbricker il 2026-10-09).

## 1. Sorgenti di partenza

| Componente | Commit usato | Licenza | Uso |
|---|---|---|---|
| kurogedelic/FM-1-transporter | `a632d923203170e3a08565f4056dbb3fd3d65ca5` | MIT | importato intatto (commit `6724113`), poi modificato |
| Quixotic7/MvaveFM1Unbricker | `b8c8e8afbcba3e45b262b3b624ab9d0244b92628` | MIT | importato intatto in `recovery/` (commit `897b189`), poi modificato |
| sekigon-gonnoc/Pico-PIO-USB | `fe9133fc513b82cc3dc62c67cb51f2339cf29ef7` (0.6.1) | MIT | sottomodulo, come a monte, + la stessa patch |
| raspberrypi/pico-sdk | 2.2.0 `a1438dff1d38bd9c65dbd693f0e5db4b9ae91779` | BSD-3-Clause | build |
| hathach/tinyusb (nel SDK) | `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` | MIT | build |
| kagaimiq/jl-uboot-tool | `adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a` | MIT (strumento) | solo `wl82loader.bin` (sha256 `d41da612…`), **non ridistribuito** |
| Arm GNU Toolchain | 14.2.Rel1 (sha256 dell'archivio verificato con quello pubblicato da Arm) | GPL/varie | build di `dist/` |

I dettagli e gli avvisi di licenza sono in [UPSTREAM.md](../UPSTREAM.md).

## 2. Analisi del firmware originale

| Area | Dove | Dipende dalla scheda? |
|---|---|---|
| CMake | `CMakeLists.txt` | `PICO_BOARD` non impostato nel file: va passato a mano (`-DPICO_BOARD=seeed_xiao_rp2040`). Senza, il SDK sceglie `pico` in silenzio e il firmware pilota comunque i LED della XIAO (GP16/GP17 come uscite). La patch di Pico-PIO-USB richiede il comando POSIX `patch`. |
| Pin D+/D- | `src/recovery.h` `PIN_DP 0`, `PIN_DM 1`; `src/pio_host.c` `cfg.pin_dp = PIN_DP` | GPIO, non pin fisici: uguali su XIAO, Pico, DollaTek |
| LED | `src/recovery.c` `LED_R 17`, `LED_G 16`, `LED_B 25`, attivi bassi | **sì**: LED RGB della XIAO. Su Pico/DollaTek GP16/GP17 sono pin del connettore |
| USB_KEY | `src/recovery.c`, `pio/usb_key.pio` (PIO0, 2 state machine, clock PIO 1 MHz = clk_sys/120) | no |
| Host PIO USB | `src/pio_host.c` (PIO1 intero, 1 canale DMA, TinyUSB rhport 1) | no |
| Clock | `main.c` `set_sys_clock_khz(120000, true)` | serve un quarzo da 12 MHz (lo richiede anche la ROM USB dell'RP2040) |
| Dual core | core 0: USB device, log, protocollo; core 1: recovery → host, UBOOT | no |
| CDC | `src/usb_device.c`: 2 CDC + interfaccia reset, `2E8A:000A` | no |
| Protocollo seriale | `src/transporter_proto.c` (righe, `read` binario + CRC32, `wsec` 4 KiB) | no |
| Loader WL82 | `src/jieli_uboot.c`, blob incluso a build time | no |
| Dump / identificazione | `jl_info`, `jl_flash_read` | no |
| Errori | log su console, `ERR …` sul canale dati, retry ×3 sulle letture, riavvio automatico (max 3) se D+ è alto senza enumerazione | no |

### 2.1 Risposte alle domande della specifica

1. **Pin hardcoded**: `PIN_DP`/`PIN_DM` (recovery.h), `LED_R/G/B` (recovery.c), `pio0` per la
   recovery e `pio_tx_num = 1` per l'host. Il canale DMA è scelto a runtime.
2. **Dipendenze XIAO**: solo i LED (RGB attivo basso), la scelta `PICO_BOARD` (boot2 W25Q080,
   flash 2 MB, avvio quarzo ×64), i testi di console e la documentazione. Il WS2812 della XIAO
   (GP12/GP11) non è usato.
3. **GP0/GP1 sulla DollaTek**: sono i pin 1 e 2 del lato sinistro (foto), consecutivi come
   richiede `PIO_USB_PINOUT_DPDM` (D- = D+ + 1) e il gruppo `set pins` della USB_KEY.
   *NON VERIFICATO su hardware* (la piastra potrebbe avere serigrafie diverse dalla foto:
   `piotest` e la fase B lo confermeranno).
4. **Risorse PIO**: PIO0 (2 SM, 16 istruzioni) per la recovery, PIO1 intero per
   Pico-PIO-USB: identiche su ogni RP2040. *BUILD VERIFIED*.
5. **Clock**: portabile su ogni scheda RP2040 con quarzo 12 MHz. Lasciato a 120 MHz (la
   frequenza con cui USB_KEY e PIO USB sono stati collaudati); il build rifiuta altri valori.
   Per un quarzo sconosciuto il profilo DollaTek allunga il tempo di avvio (×64, come XIAO).
6. **Flash di capacità diversa**: non influisce sul funzionamento (il firmware non usa la flash
   per dati); influisce su boot2 e velocità XIP. Il profilo DollaTek usa boot2 `generic_03h`,
   immagine linkata per 2 MB e `copy_to_ram`: indipendente da modello e capacità della flash.
7. **Conflitti LED**: sì, il firmware upstream su una Pico/DollaTek pilotava GP16/GP17 (pin
   del connettore) e trattava GP25 come attivo basso. Ora i LED vengono dal profilo; i GPIO
   riservati (GP23/GP24 sulla DollaTek, 23/24/29 sulla Pico) sono vietati ai LED e a D+/D-.
   La **Pico W** non è supportata: il suo GP25 è il chip-select del modulo radio.
8. **CDC indipendente dalla scheda**: sì (USB nativa dell'RP2040, descrittori fissi).
9. **Preservato senza modifiche** (verificato da `tests/test_xiao_regression.py` contro
   l'import intatto): `jieli_uboot.c/.h`, `usb_key.pio`, la patch Pico-PIO-USB,
   `tusb_config.h`, `log.c/.h`, `usb_device.c`, `docs/PROTOCOL.md`; tutte le costanti di
   temporizzazione e i corpi delle funzioni di `recovery.c` e della logica host in
   `pio_host.c` (salvo le righe che registrano la fase per `diag`).

## 3. Che cosa è stato fatto

### 3.1 Profili scheda

`boards/<id>.json` è la sola fonte: la legge CMake (`cmake/fm1t_board.cmake`, genera
`fm1t_board_config.h`) e la leggono gli strumenti host. Validazioni in entrambi: SoC RP2040,
D- = D+ + 1, pinout DPDM, PIO recovery ≠ PIO host, 120 MHz, quarzo 12 MHz, LED non su D+/D-
né su pin riservati, LED distinti, flash del build ≤ dichiarata, profili `UNVALIDATED`
disabilitati.

| Profilo | Ruolo | Abilitato | PICO_BOARD | boot2 / esecuzione | LED | Livello |
|---|---|---|---|---|---|---|
| `xiao_rp2040` | riferimento | sì | `seeed_xiao_rp2040` (SDK) | W25Q080 / XIP (come upstream) | RGB 17/16/25 attivi bassi | firmware upstream: HARDWARE VERIFIED; build multiboard: BUILD VERIFIED |
| `dollatek_rp2040_typec` | principale | sì | `fm1t_dollatek_rp2040_typec` (custom) | generic_03h / copy_to_ram, 2 MB | GP25 (non verificato) | BUILD VERIFIED |
| `pico_rp2040` | secondario | sì | `pico` (SDK) | W25Q080 / XIP | GP25 attivo alto | BUILD VERIFIED |
| `generic_rp2040` | sperimentale | **no** | `fm1t_generic_rp2040` | generic_03h / copy_to_ram | nessuno | non validato |

Il target Pico SDK standard **non basta** per la DollaTek: `pico` assume una W25Q16 (boot2
W25Q080 a clkdiv 2, 2 MB) e definisce GP23/24/29 come SMPS/VBUS/VSYS. Il board header custom
`boards/include/boards/fm1t_dollatek_rp2040_typec.h` evita ogni assunzione sulla flash e non
definisce una UART di default (GP0/GP1 sono le linee USB dell'FM-1).

### 3.2 Firmware (modifiche minime)

- `src/board.c/.h`: LED dal profilo (nessuno, singolo, RGB; polarità), asserzioni statiche
  sui pin, `#error` se lo stdio UART venisse abilitato.
- `src/recovery.c`, `src/pio_host.c`, `src/main.c`: PIO e pin dal profilo, registrazione della
  fase corrente (una scrittura di variabile), banner con scheda/versione/build/flash.
- `src/selftest.c/.h`: comandi `ident`, `hwinfo`, `diag`, `piotest`, `ledtest`, `reboot`,
  `bootsel` sulla porta dati, risposti dal **core 0** (come `ping`), quindi disponibili mentre
  il core 1 invia la USB_KEY senza FM-1. Non toccano il protocollo JieLi.
- `src/flash_info.c`, `src/flash_sfdp.c`: JEDEC ID e capacità SFDP della flash **della
  scheda**, letti una volta all'avvio prima di USB e core 1.
- Build id = ultimo commit che ha toccato gli input del firmware; data di binary info = data
  di quel commit (a monte era `__DATE__`): **build riproducibili** (stesso commit ⇒ stessi byte).
- `cmake/fm1t_patch.cmake`: la patch Pico-PIO-USB si applica con `patch` o `git apply`, con
  normalizzazione CRLF (checkout Windows).

Occupazione (Arm GCC 14.2, con loader): DollaTek copy_to_ram 160 192 B di 256 KiB di SRAM
principale; XIAO 67 816 B di SRAM e 109 292 B di flash. *BUILD VERIFIED*.

### 3.3 Strumenti host

- `fm1-board-tool` (`host/fm1_board_tool`, Python + pyserial): `boards`, `list`, `probe`,
  `build`, `make-firmware`, `flash`, `selftest`, `transporter-info`, `console`, `fm1-check`,
  `hwtest`. Porte riconosciute per VID/PID, numero di interfaccia CDC (0 console, 2 dati) e
  handshake `ping`/`ident`; la console non riceve mai scritture; porte di altri dispositivi
  mai aperte. Disco `RPI-RP2` riconosciuto dal contenuto (e su Windows da etichetta + unità
  rimovibile), mai dalla sola lettera; nessuna formattazione.
- `scripts/build.ps1`: build Windows con i controlli 1-11 della specifica.
- `scripts/hardware-test.ps1`, `scripts/fm1-board-tool.ps1`, `fm1-board-tool.cmd`.
- `recovery/fm1_transporter_recover.py`: `--board` obbligatorio per `setup`, `flash-board`,
  `wizard` (il wizard lo chiede); `flash-xiao` = `flash-board --board xiao_rp2040`; la XIAO
  continua a ricevere il firmware upstream collaudato; le altre schede il firmware multiboard
  da `dist/`; verifica `ident` prima di qualsiasi richiesta all'FM-1; `restore` richiede il
  record della doppia lettura; funzioni read-only (identificazione chip/flash, backup, doppia
  lettura, SHA-256, confronto, dry run, verifica per settore) invariate.

## 4. Risultati delle prove eseguite

| Prova | Esito | Livello |
|---|---|---|
| Build upstream `a632d92` per XIAO (toolchain 14.2) vs UF2 placeholder dell'Unbricker | stessa dimensione; 334 byte diversi: stringa `__DATE__` e disposizione di newlib (toolchain macOS vs Linux) | BUILD VERIFIED |
| Build XIAO, DollaTek, Pico (e generic con permesso esplicito), 0 warning | OK | BUILD VERIFIED |
| Riproducibilità: due build pulite indipendenti | byte identici | BUILD VERIFIED |
| Inserimento del loader nell'immagine placeholder = build con loader | identico, per tutte le schede (`spliced_uf2_sha256` nei manifest) | BUILD VERIFIED |
| `boot2` DollaTek | `generic_03h` (nessuna programmazione dei registri di stato); XIAO: W25Q080 | BUILD VERIFIED |
| `copy_to_ram` DollaTek | `.text` a `0x200000C0`, `memmap_copy_to_ram.ld` | BUILD VERIFIED |
| Protocollo dati + JieLi su mock UBOOT (27 test): framing, CRC, limiti `read`, rifiuto `wsec` fuori da `[0x4000,0x93000)`, CRC errato, round trip cifratura RAM, blocco RAM-run col loader attivo, sessione di primo contatto senza erase/write | OK | SIMULATED |
| Decodifica chiave chip: mock con cifratura reimplementata in modo indipendente → firmware legge `980F` | OK | SIMULATED |
| Self-test, flash via BOOTSEL, watchdog/riconnessione, disconnessione durante lettura, stallo, timeout, più schede, due dischi RPI-RP2, firmware legacy, FM-1 che non risponde, chip sbagliato, letture instabili | OK (25 + 6 test) | SIMULATED |
| `scripts/build.ps1` (PowerShell 7.4.6 su Linux): build, manifest, cronologia, SHA-256, rifiuto loader sconosciuto, SDK mancante, scheda disabilitata | OK | BUILD VERIFIED |
| Suite dell'Unbricker modificata (con loader reale disponibile) | 74 passati, 8 saltati (servono il pacchetto V15 proprietario o dump privati) | SIMULATED |
| Suite del progetto | 191 test passati (`python -m pytest tests`) | SIMULATED / BUILD VERIFIED |

## 5. Problemi riscontrati

1. **Upstream senza `PICO_BOARD` sceglie `pico` in silenzio** e pilota GP16/GP17 come uscite
   (LED della XIAO). Risolto: profilo obbligatorio o dedotto; `PICO_BOARD` sconosciuto rifiutato.
2. **`patch` non esiste su Windows** e un checkout CRLF rompe gli hunk. Risolto con
   `cmake/fm1t_patch.cmake` (provato con `patch`, `git apply` e sorgenti CRLF).
3. **pico-sdk 2.x ha bisogno di picotool per generare l'UF2**; senza picotool installato lo
   scarica e compila (servono rete e un compilatore C++ per il PC). L'Unbricker dice "picotool
   is not needed": vero solo in quel senso. `build.ps1` usa il picotool dell'estensione VS Code
   se presente.
4. **`__DATE__` nella binary info** rende la stessa sorgente diversa da un giorno all'altro.
   Risolto (data del commit).
5. **Linker e marcatore**: una costante `used` veniva comunque eliminata da `--gc-sections`;
   ora il marcatore `FM1T-BOARD:<id>;` è usato da `ident`.
6. **Interferenza con firmware legacy**: inviare `ident` a un Transporter upstream accoda la
   riga per il core 1 e sposta di una posizione la risposta al primo `status` del flusso
   dell'Unbricker. Risolto: lo strumento di recupero non invia mai `ident` al firmware legacy;
   `fm1-board-tool` segnala il firmware legacy e chiede di ricollegare la scheda.
7. **Unbricker**: il "tocco a 1200 baud" poteva finire sulla porta dati (il firmware ascolta
   solo la console, interfaccia 0) e la ricerca della porta scriveva `ping` anche sulla console;
   su Windows `RPI-RP2` era cercato sulle lettere D:-Z: con un controllo del contenuto più
   debole. Corretti tutti e tre.
8. **Richieste FM-1 durante la USB_KEY**: `status`/`info` inviati mentre il core 1 è in keying
   restano in coda e bloccano le successive. Il self-test non ne invia mai; `fm1-check` attende
   la fase `uboot` interrogando `diag` (risposto dal core 0).
9. **PowerShell**: le costanti esadecimali oltre `0x7FFFFFFF` sono Int32 negativi; il confronto
   della famiglia UF2 falliva. Corretto (trovato eseguendo lo script).
10. **Documentazione vs sorgenti** (a monte): `docs/PROTOCOL.md` dice che la ROM "did not release
    D+ in 6 s", ma `recovery.c` documenta il rilascio dopo 4 s del 2026-10-01 e lo gestisce;
    `pio_host.h` dice "No memory or flash commands" ma `wsec` esiste; le velocità di dump
    citate variano (~1,5 s via Mac nativo, ~3 s nel README, "about a minute" nel wizard per due
    letture). Nessuna di queste incongruenze cambia il codice.
11. **Build id legato a HEAD**: un commit di sola documentazione cambiava i byte del firmware.
    Risolto (ultimo commit degli input firmware).

## 6. Rischi residui e decisioni

- `copy_to_ram` cambia l'esecuzione rispetto alla XIAO (da SRAM invece che da flash XIP):
  le temporizzazioni USB_KEY sono generate dal PIO e basate su timer, le funzioni critiche di
  Pico-PIO-USB erano già in RAM; l'effetto atteso è meno jitter, ma **va verificato in fase B**.
- Il watchdog hardware **non** è abilitato durante il funzionamento: un reset a metà di una
  scrittura di settore lascerebbe l'FM-1 in uno stato peggiore. È usato solo per i riavvii
  richiesti (`reboot`, `rekey`) e per il riavvio automatico già presente a monte.
- Il simulatore modella ciò che gli strumenti osservano, non l'elettronica: nessun risultato
  SIMULATED sostituisce la fase B.

## 7. Verifiche ancora necessarie su hardware fisico

In ordine; ognuna è coperta da `scripts\hardware-test.ps1` (fase A o B) o indicata.

| # | Verifica | Come | Esito atteso |
|---|---|---|---|
| 1 | La DollaTek entra in BOOTSEL e appare `RPI-RP2` su Windows 11 | fase A | PASS |
| 2 | L'UF2 DollaTek si carica e il firmware parte da RAM | fase A | porte COM "FM-1 Transporter" |
| 3 | Due porte COM, console e dati distinte (driver `usbser` di Windows) | fase A | PASS |
| 4 | `ident`: scheda/versione/build | fase A | `board=dollatek_rp2040_typec` |
| 5 | Flash reale: JEDEC e capacità SFDP vs 16 MB dichiarati | fase A (`hwinfo`) | annotare il valore |
| 6 | LED su GP25: presenza e polarità; natura del LED centrale | fase A (test LED) + ispezione | annotare |
| 7 | `piotest`: fronti su GP0 e GP1 durante il keying | fase A | > 0 su entrambi |
| 8 | Console: righe `KEY:` ogni 2 s | fase A | PASS |
| 9 | Riavvio watchdog e ricomparsa della porta (numero COM) | fase A | `reset=reboot-cmd` |
| 10 | `flash` da firmware in esecuzione (`bootsel` e tocco 1200 baud) | `fm1-board-tool flash` | PASS |
| 11 | Forma d'onda USB_KEY (0x16EF, ~19 µs per bit, pacchetto 340 µs) e impulsi 4 µs/1 ms | analizzatore logico su GP0/GP1 | conforme |
| 12 | Continuità e assenza di corti, VBUS isolato | multimetro (checklist fase B) | PASS |
| 13 | ACK della ROM JieLi alla USB_KEY | fase B | fase `pulses` |
| 14 | Enumerazione PIO USB `4C4A:8057` e INQUIRY `WL82/UBOOT1.00` | fase B | fase `uboot` |
| 15 | `info`: `key=980F type=3 id=856014` | fase B | PASS |
| 16 | Due letture complete identiche (e ripetibili in una seconda sessione) | fase B | stesso SHA-256 |
| 17 | Solo dopo 1-16: dry run del ripristino con lo strumento di recupero | `restore` senza `--write` | elenco settori |

Solo quando 1-16 sono PASS il profilo DollaTek può passare da BUILD VERIFIED a HARDWARE
VERIFIED (aggiornare `boards/dollatek_rp2040_typec.json`: `validation.level`, `verified`,
`leds.verified`, `flash.verified`, e se la flash è confermata valutare boot2 più veloce).
