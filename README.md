# AI Privacy Gateway — A-0.3.0

Károly Tamás szakdolgozati prototípusa (2026). A Windowsra előkészített forráscsomag helyi, három szerepkörös alkalmazás. Szintetikus példával vagy saját szöveggel próbálható. A külső modellhez a felhasználó külön konfigurálja saját szolgáltatói kapcsolatát és API-kulcsát. A kiadás **nem tartalmaz API-kulcsot, helyi ügyadatot vagy futási adatbázist**.

## Windows — innen indulj

1. Kövesd a [részletes Windows telepítési útmutatót](docs/TELEPITES_WINDOWS.md). Python 3.14 és első indításkor internet kell.
2. A kulcs nélküli helyi próba indítója: **`Start-Windows.cmd`**.
3. Saját OpenAI-kulcs használatához: **`Start-OpenAI-Windows.cmd`**, majd a helyi **Üzemeltető** felületen add meg a kulcsot és a hívási profilokat.
4. A három szerep munkafolyamata a [felhasználói útmutatóban](docs/FELHASZNALOI_UTMUTATO.md) szerepel.

A böngészős cím: **http://127.0.0.1:8765/**. A szolgáltatás a parancsablakban **Ctrl+C**-vel állítható le. A `.venv` és `.local` mappák az első használatkor helyben jönnek létre; ne töltsd fel őket.

## Forrásellenőrzés

Windows Parancssorban a kicsomagolt mappából a `Start-Windows.cmd --check` telepíti a rögzített függőségeket, ellenőrzi őket és futtatja az automatizált teszteket. A GitHub Windows Actions munkafolyamata ugyanezt tiszta Windows-környezetben is ellenőrzi. Ez nem helyettesíti az összes bírálói gépen végzett próbát.

Más rendszeren a forrás CPython 3.14 virtuális környezetből indítható. A Windows-útmutató és a kiadási ZIP Windowsra készült.

## Adatvédelmi határok

Az ügyek és az érzékeny értékek helyben, a `.local` alatt tárolódnak. A helyi ügytár nem alkalmazásszinten titkosított. Külső szolgáltató csak a külön jóváhagyott, kezelt kérés külön küldési lépésében kap tartalmat. Bírálói próbához használj szintetikus adatokat. A modellválasz helyességét a felhasználó ellenőrzi.

Szerzőségi információ: [NOTICE.md](NOTICE.md). A repó jelenleg nem ad külön nyílt forrású felhasználási licencet.
