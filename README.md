# AI Privacy Gateway bírálói bemutató

Verzió A-0.2.0-rc1. A helyi szakdolgozati prototípus szintetikus példával és saját szöveggel próbálható ki. A csomagban forráskód, tesztek és telepítési útmutató található. A helyi adatbázisok az első futtatáskor jönnek létre.

## Telepítés

CPython 3.14, pip és böngésző szükséges. A függőségek első telepítéséhez internetkapcsolat kell. Csomagold ki a forrást, majd lépj a README-t tartalmazó könyvtárba.

### macOS és Linux

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -B -m gateway.web_demo --guided-demo
```

### Windows indítás

Csomagold ki a ZIP-et, majd futtasd a `Start-Windows.cmd` fájlt. Az első futtatás létrehozza a projekt virtuális környezetét és telepíti a rögzített függőségeket. A futtatáshoz CPython 3.14 és a `py` parancs szükséges. A terminálban megjelenő helyi címet nyisd meg a böngészőben.

### Windows PowerShell kézi indítás

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m gateway.web_demo --guided-demo
```

A csempés kezdőlap a [helyi címen](http://127.0.0.1:8765/) érhető el. A kiválasztott szerepkör belépőoldalán a bemutató neve és kódja előre kitöltött. Az elkülönített demó futási adatai a `.local/futtatas/A/guided-demo` könyvtárba kerülnek. Leállítás a terminálban a **Ctrl+C** billentyűkkel.

## Helyi próba

1. Lépj be **Kérőként**. Indítsd el a dokumentumdemót, ellenőrizd a kezelt előnézetet, majd válaszd a **Jóváhagyás** és a **Helyi futtatás** gombot.
2. A levélhez vagy a saját szöveg ellenőri próbájához lépj be **Ellenőrként** egy másik böngészőprofilban vagy privát ablakban. A két munkamenet maradjon nyitva.
3. A kérői ablakban indíts levéldemót, vagy a **Saját szöveg** oldalon adj meg forrást és kérést. Jelölj meg védendő értéket, majd kérj maszkolt vagy tokenizált előnézetet ellenőrzésre.
4. Az ellenőr a munkakosárból nyithatja meg a hozzá rendelt ügyet. Jóváhagyás után a kérő ugyanott nyithatja meg a futtatást. A jóváhagyás legfeljebb öt percig használható. Az ügylista az aktív munkamenethez kötődik.
5. A kérő a **Mentett érzékeny elemek** alatt kereshet és kezelheti a korábban elmentett értékeket. A **Névfelismerő állapota** jelzi az opcionális modell elérhetőségét. A kézi kijelölés önmagában is használható.

Az **Üzemeltető** kezeli a modellkapcsolatokat és hívási profilokat. Üres telepítésnél előbb kapcsolatot kell létrehozni, hogy profil készülhessen. A **Névfelismerő telepítése** menüpontban a letöltés jóváhagyással indítható. A modell és függőségei a `.local/nlp` alá kerülnek, külön környezetbe. A mentett modell ki- és bekapcsolható, illetve jóváhagyással újratelepíthető vagy eltávolítható.

## Külső szolgáltatói próba

A helyi demó rögzített válaszokkal működik és tiltja az API-kulcs mentését. Külső próba esetén külön, kézi belépésű példányt kell indítani a kiválasztott protokoll és pontos HTTPS-alapcím engedélyezésével. Az indítási paraméter alakja `--allow-https-target PROTOKOLL HTTPS_ALAPCÍM`. A terminál a három szerepkör kódját kiírja. A külön helyi tár a `--runtime-dir`, a helyi port a `--port` paraméterrel választható meg.

Az üzemeltető a kapcsolatnál adja meg a szolgáltatói címet, modellazonosítót és saját API-kulcsát, majd létrehozza a feladathoz tartozó hívási profilt. Az API-kulcs az adott gép helyi, elkülönített SQLite-adatbázisába kerül. A szolgáltató a jóváhagyott kezelt kéréshez tartozó hitelesítési fejlécben kapja meg. Külső próbához használj szintetikus szöveget és ellenőrizd a szolgáltatói fiók feltételeit.

## Ellenőrzés

A forrás tesztjei a telepített virtuális környezet Python-parancsával futtathatók: `-B -m unittest discover -s gateway/tests -q`. A kiadásjelölt tiszta telepítési próbája és a Windows/Linux futtatás ellenőrzése külön lépés.
