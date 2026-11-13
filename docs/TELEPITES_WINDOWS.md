# AI Privacy Gateway A-0.3.0 — telepítés Windowsra

Ez az útmutató a Windows 10/11 x64 rendszeren végzett bírálói kipróbáláshoz készült. A telepítő ZIP forráskódot tartalmaz; a Python-környezetet az első indításkor a csomag mappájában készíti el. Rendszergazdai jogosultság általában nem szükséges. A program csak a saját gépen, a `http://127.0.0.1:8765/` címen szolgál ki oldalt.

**Első használat röviden:** a helyi bemutatóhoz nem kell API-kulcs. A valódi modellválaszhoz a külön OpenAI-indítót kell használni, majd az alkalmazás Üzemeltető felületén saját modellkapcsolatot, API-kulcsot és két hívási profilt kell beállítani. A magyar névfelismerő külön, jóváhagyható letöltés; kihagyható, mert a kézi kijelölés működik. A kiadási csomag **nem tartalmaz API-kulcsot vagy korábbi felhasználói adatot**.

## 1. Mire lesz szükség?

1. Windows 10 vagy 11, 64 bites számítógép, működő böngésző és internetkapcsolat az első telepítéshez. A külső modell és a névfelismerő használatához később is kell internet.
2. **Python 3.14, 64 bites változat.** A Python hivatalos [Windows telepítési oldaláról](https://www.python.org/downloads/windows/) telepítsd a *Python Install Manager* programot. A telepítő után nyiss új **Parancssor** ablakot, és írd be: `py install 3.14`. A Python [hivatalos Windows útmutatója](https://docs.python.org/3.14/using/windows.html) írja le ezt a telepítési módot.
3. Ellenőrzés ugyanott: `py -3.14 --version`. A válasz `Python 3.14...` kezdetű legyen. Ha a `py` parancs nem található, zárd be és nyisd meg újra a Parancssort; ha továbbra sem működik, kövesd a Python telepítőjének PATH-hibaelhárítását.
4. A telepítéshez lehetőleg legyen néhány GB szabad hely. A külön magyar névfelismerő letöltése önmagában körülbelül 127 MB, a függőségei és a helyi környezet további helyet igényelnek.

## 2. A csomag letöltése és kibontása

1. Nyisd meg a [GitHub A-0.3.0 kiadási oldalát](https://github.com/szawinka/AI-Privacy-Gateway/releases/tag/A-0.3.0). A **Windows ZIP letöltése** hivatkozás a kiadás ismertetőjében szerepel; a fájl a repó `dist/A-0.3.0` mappájában van. Közvetlen link: [AI-Privacy-Gateway-A-0.3.0-Windows.zip](https://raw.githubusercontent.com/szawinka/AI-Privacy-Gateway/A-0.3.0/dist/A-0.3.0/AI-Privacy-Gateway-A-0.3.0-Windows.zip).
2. Ugyanonnan töltsd le a [SHA256SUMS.txt](https://raw.githubusercontent.com/szawinka/AI-Privacy-Gateway/A-0.3.0/dist/A-0.3.0/SHA256SUMS.txt) fájlt is, ha ellenőrzőösszeget szeretnél összevetni. A GitHub automatikusan készített „Source code” ZIP-je is forráskód, de ez az útmutató a külön ellenőrzött Windows ZIP-hez tartozik.
3. A letöltött ZIP-en válaszd a Windows **Összes kibontása** műveletét. Például a Dokumentumok mappába bontsd ki. **Ne a ZIP-en belül indítsd a programot.** A kicsomagolt mappában a `Start-Windows.cmd`, a `Start-OpenAI-Windows.cmd`, a `README.md` és a `gateway` mappa együtt legyen.
4. Ha ellenőrizni szeretnéd a letöltést, a kiadáshoz mellékelt `SHA256SUMS.txt` fájlban szerepel a ZIP ellenőrzőösszege. PowerShellben a `Get-FileHash .\AI-Privacy-Gateway-A-0.3.0-Windows.zip -Algorithm SHA256` parancs eredményének egyeznie kell vele. Az ellenőrzéshez a ZIP-et tartalmazó mappában nyiss PowerShellt.

## 3. Első indítás: kulcs nélküli helyi bemutató

1. A kicsomagolt mappában kattints kétszer a **`Start-Windows.cmd`** fájlra. Első futáskor a program a saját `.venv` mappájába telepíti a rögzített Python-függőségeket; ez internetet igényel és néhány percig tarthat. A megnyíló fekete ablakot hagyd nyitva.
2. Ha a Windows rákérdez, hogy a letöltött parancsfájlt futtatod-e, előbb ellenőrizd, hogy a fenti GitHub-kiadásból származik. A fájl egyszerű szövegként a Jegyzettömbben is megnyitható és átolvasható.
3. Amikor az ablakban megjelenik a helyi cím, nyisd meg böngészőben: **[http://127.0.0.1:8765/](http://127.0.0.1:8765/)**. A kezdőlapon három szerepcsempe látszik.
4. Kattints a **Kérő** csempére. A demó felhasználóneve és jelszava a külön belépőoldalon előre ki van töltve; nyomd meg a **Belépés** gombot. A két rövid szintetikus példa külső modell és API-kulcs nélkül próbálható ki.
5. A leállításhoz térj vissza a fekete ablakhoz, nyomd meg a **Ctrl+C** billentyűket, majd zárd be az ablakot. Újraindításhoz ugyanazt a `Start-Windows.cmd` fájlt használd. A helyi ügyek a kicsomagolt mappában maradnak.

## 4. Valódi OpenAI-modell használata

Ezt csak akkor végezd el, ha **saját OpenAI API-kulcsod** és a választott modellhez hozzáférésed van. A modellhívás a szolgáltatói fiókodban költséget okozhat. A program nem tartalmaz kulcsot, és nem ad át senki másnak kulcsot. Az OpenAI [API-indító útmutatója](https://developers.openai.com/api/docs) írja le a kulcs létrehozását.

1. Állítsd le a kulcs nélküli demót a **Ctrl+C** billentyűkkel. Egyszerre egy példány használja a 8765-ös címet.
2. A kicsomagolt mappában kattints kétszer a **`Start-OpenAI-Windows.cmd`** fájlra. Ez ugyanazt az alkalmazást indítja, és kizárólag az OpenAI Responses API `https://api.openai.com/v1` alapcímét engedélyezi a külső küldéshez. A kapcsolat a [Responses API](https://developers.openai.com/api/reference/responses/overview) `POST /v1/responses` végpontját használja. A program ettől még nem indít hívást.
3. Nyisd meg a helyi kezdőlapot, válaszd az **Üzemeltető** csempét, majd lépj be az előre kitöltött demóadatokkal.
4. A **Modellkapcsolatok** oldalon az **Új modellkapcsolat** részt töltsd ki:

   | Mező | Mit adj meg? |
   |---|---|
   | Név | Például `Saját OpenAI` |
   | API-protokoll | `openai-responses` |
   | HTTPS alapcím | `https://api.openai.com/v1` |
   | Modellazonosító | A saját fiókodban elérhető szöveges modell pontos azonosítója, például a [modelljegyzékben](https://developers.openai.com/api/docs/models) szereplő azonosító. A modell hozzáférhetősége fiókonként eltérhet. |
   | Projekt- vagy telepítésazonosító | Hagyd üresen, ha nem használod. |
   | Hitelesítés | `API-kulcs` |
   | Új API-kulcs | **Csak itt** írd be a saját kulcsodat. Ne küldd el e-mailben, és ne másold dokumentumba. |
   | Engedélyezett konfiguráció | Jelöld be. |

5. Nyomd meg a **Mentés** gombot. A felület csak azt jelzi, hogy van-e mentett kulcs; a kulcs értékét nem írja vissza. A kulcs a saját géped kicsomagolt mappájának `.local` helyi adattárába kerül, amely nincs benne a ZIP-ben és a GitHub-repozitóriumban.
6. Válaszd a **Hívási profilok** menüt, majd az **Új hívási profil** részen hozz létre **két profilt** ugyanahhoz a kapcsolatodhoz:

   | Profil | Feladat | Javasolt kimeneti tokenkorlát | Javasolt időkorlát |
   |---|---|---:|---:|
   | `Összefoglalás` | Dokumentum-összefoglalás | 600 | 60 másodperc |
   | `Levéltervezet` | Levéltervezet | 800 | 60 másodperc |

   A `Temperature` és `Top-p` mezőt első próbán hagyd üresen; a kimeneti forma **Szöveg** maradjon. Mindkét profilt külön **Mentés** gombbal rögzítsd.
7. A **Kérő** szerepben egy fejlesztési mintából vagy saját szövegből készíts ügyet, válaszd ki a megfelelő profilt, ellenőrizd a jelöléseket és a kezelt kérést. Ha az ügy ellenőri döntést kér, az **Ellenőr** munkakosarában külön hagyd jóvá. **A külső hívást csak a Kérő „Küldés a szolgáltatóhoz” gombja indítja el** a jóváhagyás után.

Az alkalmazás szolgáltatófüggetlen magja más támogatott protokollal is használható, de ahhoz az indításkor a megfelelő `--allow-https-target PROTOKOLL HTTPS_ALAPCÍM` párt kell megadni. Az OpenAI-indító kényelmi beállítás, nem a programba épített kizárólagos szolgáltató.

## 5. Opcionális magyar névfelismerő

A **Kérő** a saját szövegben a modell nélkül is kézzel kijelölhet bármilyen érzékeny szakaszt. A névfelismerő csak javaslatokat készít személynevekre; nem helyettesíti a kézi ellenőrzést.

Telepítéshez az **Üzemeltető** menüben nyisd meg a **Magyar névfelismerő** oldalt. Jelöld be a letöltés jóváhagyását, majd nyomd meg a **Telepítés indítása** gombot. A letöltés és a külön Python-környezet összeállítása hosszabb ideig tarthat. A telepítés a csomag `.local/nlp` mappájába kerül, és a felület később verziót és állapotot mutat. Ha most kihagyod, később ugyanitt telepítheted. A letöltéshez a PyPI és a modell Hugging Face oldala elérhető kell legyen.

## 6. Gyakori elakadások

| Jelenség | Teendő |
|---|---|
| `py` nem található vagy nem 3.14-et indít | Telepítsd a Python Install Managert, majd új Parancssorban futtasd: `py install 3.14` és `py -3.14 --version`. |
| A fekete ablak rögtön bezárul | Nyisd meg a kicsomagolt mappában a Parancssort, írd be: `Start-Windows.cmd`, és olvasd el a hibaüzenetet. A fájl hibánál vár egy billentyűre. |
| A csomagok letöltése elakad | Ellenőrizd az internetet, a vállalati proxy- vagy tűzfalszabályokat, és próbáld meg később ugyanazt az indítót. A telepítő nem tartalmaz offline függőségkészletet. |
| A böngészőben nem nyílik meg a helyi cím | Nézd meg, hogy a fekete ablak még fut-e. Ha a 8765-ös port foglalt, zárd le az előző példányt a Ctrl+C billentyűkkel. |
| Nincs választható külső modellprofil | A `Start-OpenAI-Windows.cmd` indítót használd, majd az Üzemeltetőnél ments engedélyezett modellkapcsolatot API-kulccsal és a feladathoz tartozó hívási profilt. |
| A modellhívás hibát jelez | Ellenőrizd a saját kulcs érvényességét, a modellazonosítót, a szolgáltatói fiók hozzáférését és az internetet. A jóváhagyott, kezelt előnézetet is nézd át. |
| A névfelismerő nincs telepítve | A kézi kijelölés használható. Az Üzemeltető később külön jóváhagyással telepítheti. |

## 7. Adatok, frissítés és eltávolítás

A `.venv` a futási csomagokat, a `.local` a helyi ügyeket, modellbeállításokat, API-kulcsot és az opcionális névfelismerőt tartalmazhatja. **Ezeket ne töltsd fel GitHubra és ne küldd el hibajegy mellékleteként.** Az alkalmazás ügytára jelenleg nem alkalmazásszinten titkosított; saját, valódi érzékeny adatok helyett szintetikus mintákkal próbálj. A programot a fekete ablakban **Ctrl+C**-vel állítsd le.

Frissítéskor az új kiadást **külön mappába** bontsd ki. Ne másold át automatikusan a korábbi `.local` és `.venv` mappát: ez személyes adatot és kulcsot vinne tovább. Ha már nincs szükséged a próbára, a leállított alkalmazás teljes kicsomagolt mappájának törlésével a hozzá tartozó helyi környezet és adatok is eltávolíthatók. Törlés előtt mentsd el, amit meg szeretnél őrizni.
