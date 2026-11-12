# Závodní inženýr pro F1 25

Pit wall na iPadu pro hru **EA SPORTS F1 25**. Backend běží na herním PC (Windows), čte UDP telemetrii
ze hry a funguje jako závodní inženýr: sleduje závod, počítá strategii a **mluví na tebe rádiem**.
Frontend je webová aplikace – na iPadu ji otevřeš v Safari, nic se neinstaluje.

```
F1 25 ──UDP 20777──▶ backend (Python, PC) ──WebSocket──▶ iPad (Safari)
        60 Hz            parser → stav → inženýr          kokpit · rádio · hlas
```

## Co to umí

| Obrazovka | Obsah |
|---|---|
| **Závod** | hlavní obrazovka na jízdu: obří čísla – pozice, delta, odstupy s trendem, pneumatiky barvou, palivo, baterie, plán zastávky, vlajky přes celou šířku a poslední hláška rádia |
| **Kokpit** | pozice, čas kola a živá delta vůči nejlepšímu kolu, sektory, odstupy, rychlost / řazení / pedály, pneumatiky (jádro, povrch, tlak, brzdy, opotřebení), ERS, palivo |
| **Časomíra** | celé pole: ztráty, intervaly, poslední / nejlepší kolo, sektory (fialová / zelená), pneumatiky, zastávky, penalizace |
| **Mapa** | mapa trati kreslená z jízdy, pozice všech vozů, žluté vlajky, předpověď počasí |
| **Strategie** | plán zastávky, projekce opotřebení, okno zastávky, odhad pozice po výjezdu z boxů, sady pneumatik, palivo, srovnání tempa se soupeři |
| **Telemetrie** | mapa trati s kurzorem (klepneš do grafu → vidíš, kde to je), zatáčky Z1, Z2…, tabulka „kde ztrácíš a proč“, rychlost/plyn/brzda/řazení/volant – aktuální vs. poslední vs. nejlepší vs. nejlepší uložené kolo |
| **Setup** | doporučení přímo z telemetrie (prokluz kol → nedotáčivost/přetáčivost, blokování kol, teploty, opotřebení, speed trap), tvoje setupy na téhle trati seřazené podle nejlepšího kola, ruční výběr problému |
| **Vůz** | poškození karoserie, pneumatik a brzd, opotřebení pohonné jednotky |
| **Rádio** | inženýr hlásí kola a odstupy, DRS, pneumatiky, palivo, počasí, safety car, vlajky, penalizace, poškození, „box box“, výsledek. Ptát se můžeš tlačítky na iPadu nebo **tlačítkem na volantu** |

Nekritické hlášky si inženýr nechá na rovinku, kritické (vlajky, SC/VSC, červená, box, penalizace) řekne hned.
Mluví přirozeně a pokaždé trochu jinak; po kvalifikačním kole počká, až hra přepočítá pořadí, a v tréninku řekne,
ve které zatáčce ztrácíš nejvíc.

### Co si aplikace pamatuje

Po každém platném kole se pro danou trať uloží **nejrychlejší kolo** (jako referenční telemetrie), **mapa trati** a **setup**,
se kterým jsi kolo zajel. Příště tak máš mapu i srovnání hned od začátku. Když skončí session, na iPadu zůstanou poslední data
(s upozorněním nahoře), dokud nová session nepošle svoje.

Data jsou v `%LOCALAPPDATA%\RaceEngineer` (jiná složka přes `--data-dir`). Demo nic neukládá.

## Spuštění (Windows)

1. Nainstaluj [Python 3.10+](https://www.python.org/downloads/) (zaškrtni *Add python.exe to PATH*).
2. Stáhni projekt přes Git (ne jako zip – viz *Řešení problémů*):
   ```
   git clone -b claude/f1-25-race-engineer-app-q67ddf https://github.com/Fenixen/F1-Game-Telemetry.git
   cd F1-Game-Telemetry
   ```
3. Spusť **`start.bat`** – dvojklikem v Průzkumníku, nebo v terminálu:
   - **PowerShell** (výchozí terminál ve Windows 11): `.\start.bat`
   - **cmd**: `start.bat`

   Aplikace používá jen standardní knihovnu Pythonu – nic se nestahuje ani neinstaluje.
4. V okně se vypíše adresa pro iPad, např. `http://192.168.1.50:8080`. Otevři ji v Safari.
5. Windows se při prvním spuštění zeptá na firewall – povol přístup pro **soukromé sítě**.

Bez hry si aplikaci vyzkoušíš přes **`demo.bat`** (v PowerShellu `.\demo.bat`) – simulovaný závod.

### Nastavení ve hře

`Settings → Telemetry Settings`:

| Položka | Hodnota |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | `127.0.0.1` (hra i aplikace na stejném PC) |
| UDP Port | `20777` |
| UDP Send Rate | 60 Hz |
| UDP Format | **2025**, nebo **2026** se sezónním balíčkem 2026 |

Dotazy z volantu: v `Settings → Controls` přiřaď tlačítkům **UDP Action 1–8**
(1 odstupy, 2 pneumatiky, 3 palivo, 4 strategie, 5 počasí, 6 poškození, 7 tempo, 8 zopakuj).

### iPad

- **Sdílet → Přidat na plochu** = celá obrazovka bez lišty Safari.
- Český hlas: `Nastavení → Zpřístupnění → Předčítání obsahu → Hlasy → Čeština` (stáhni „vylepšenou“ variantu).
- Zhasínání displeje: `Nastavení → Displej a jas → Uzamčení → Nikdy`.

### Volby

```
.\start.bat --http-port 8080 --udp-port 20777      porty
.\start.bat --forward 127.0.0.1:20778              přeposílat telemetrii dál (SimHub apod.)
.\start.bat --demo --demo-speed 4 --demo-laps 8    simulátor
.\start.bat --demo --demo-format 2026              simulátor ve formátu 2026 (aktivní aero, overtake)
.\start.bat --record                               nahrát telemetrii ze hry do souboru (pro ladění)
.\start.bat --replay soubor.f1rec.gz                přehrát nahraný záznam bez hry
```

`build_exe.bat` sestaví samostatný `dist\RaceEngineer.exe` přes PyInstaller.

## Řešení problémů

| Příznak | Příčina a řešení |
|---|---|
| *„Inteligentní řízení aplikací zablokoval aplikaci s nebezpečnou příponou souboru“* | Smart App Control blokuje nepodepsané `.bat` soubory označené jako stažené z internetu (zip z GitHubu). Stáhni projekt přes `git clone`, nebo ve složce projektu v PowerShellu spusť `Get-ChildItem -Recurse \| Unblock-File`. Smart App Control nevypínej – podle verze Windows ho nemusí jít zapnout zpět. |
| *„The term 'start.bat' is not recognized…“* | PowerShell nespouští soubory z aktuální složky bez cesty. Použij `.\start.bat`. |
| Nechceš `.bat` vůbec | Totéž ručně: `py -3 -m race_engineer` (pro demo přidej `--demo`). |
| Port je obsazený | Neběží už aplikace v jiném okně? Jiné porty nastavíš přes `--http-port` a `--udp-port`. |
| iPad se nepřipojí | Povol aplikaci ve firewallu pro soukromé sítě a zkontroluj, že iPad i PC jsou ve stejné Wi-Fi. |
| Aplikace čeká na hru | Ve hře `UDP Format: 2025`, port `20777`, IP `127.0.0.1`; na konzoli ve stejné síti zadej IP počítače. |

## Jak to funguje pod kapotou

```
race_engineer/
  udp/packets.py      binární layouty paketů F1 25 (formát 2025) – dekódování i kódování
  udp/constants.py    číselníky (tratě, týmy, směsi, penalizace, bity tlačítek)
  state.py            model session složený z paketů; vrací doménové události (kolo, box…)
  strategy.py         projekce opotřebení, okno zastávky, pozice po výjezdu, výběr směsi
  engineer/engine.py  rádio: fronta, priority, cooldowny, čekání na rovinku
  engineer/rules.py   pravidla inženýra a odpovědi na dotazy
  engineer/setup_advisor.py  doporučení k nastavení
  snapshots.py        JSON pohledy pro frontend (20 Hz rychlý, 4 Hz pomalý)
  server.py           UDP listener, periodické odesílání, příkazy z iPadu
  webserver.py        vlastní HTTP + WebSocket server (RFC 6455) jen nad asyncio
  simulator.py        falešná hra – generuje skutečné binární pakety
frontend/             čistý HTML/CSS/JS bez build kroku a bez CDN (funguje offline v LAN)
tests/                pytest
```

- Layout každého paketu je deklarovaný jednou a při importu se ověřuje proti oficiálním velikostem
  datagramů (např. Car Telemetry = 1352 B). Stejné layouty používá simulátor, takže parser a testy
  nemůžou „odjet“ od sebe.
- UDP je vstup ze sítě: každý datagram se před dekódováním kontroluje na délku a formát,
  vadný se zahodí. Jména jezdců se ve frontendu vždy escapují.
- WebSocket odmítá spojení z cizích originů; server jinak nemá přihlášení – počítá se s domácí sítí.
- Žádné závislosti: HTTP i WebSocket server je napsaný nad `asyncio` ze standardní knihovny.
  Nic se nekompiluje, takže aplikaci nerozbije ani úplně nová verze Pythonu.
- Hlas běží v prohlížeči (Web Speech API), takže na PC není potřeba žádný TTS engine.

## Vývoj

```
pip install -r requirements-dev.txt
python -m pytest
python -m race_engineer --demo
```

## Omezení

- Formát **2025** je ověřený proti běžící hře. Formát **2026** (sezónní balíček) je napsaný podle komunitního popisu
  struktur (oficiální PDF od EA nebylo k dispozici); velikosti všech paketů sedí, ale proti hře zatím ověřený není.
  Když se něco chová divně, spusť aplikaci s `--record` a pošli záznam.
- Zatáčky Z1, Z2… se hledají z rychlosti na referenčním kole, takže číslování nemusí odpovídat oficiálním číslům zatáček
  a rychlé zatáčky bez zpomalení se nepočítají.
- Doporučení setupu z prokluzu pneumatik je heuristika: prahové hodnoty jsou odhad, ber je jako návrh.
- Hra nepřijímá nastavení vozu po síti – doporučené hodnoty je potřeba přepsat v garáži ručně.
  Rozsahy hodnot v poradci jsou orientační.
- Hlasový vstup (mluvení na inženýra) není: Safari povolí mikrofon jen přes HTTPS. Dotazy jdou tlačítky.
- V online lobby hra u soupeřů s vypnutou telemetrií posílá jen omezená data.
