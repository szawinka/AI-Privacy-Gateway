# Opcionális magyar névfelismerő

A kicsomagolt vagy klónozott projekt gyökérmappájából futtasd az alábbi parancsot CPython 3.14 használatával.

- macOS/Linux: `python3.14 -B -m gateway.install_nlp`
- Windows (PowerShell): `py -3.14 -B -m gateway.install_nlp`

A telepítő jóváhagyás után letölti a spaCy 3.8.18-at, a HuSpaCy `hu_core_news_md` 3.8.1 modellt (127 MB) és a függőségeket. A modellfájlt rögzített forrásrevízióból tölti le és SHA-256-lenyomattal ellenőrzi. A környezet és a betöltés ellenőrzését jelző állapot a projekt `.local/nlp` könyvtárába kerül.

A kihagyáshoz válaszolj nemmel vagy add meg a `--skip` kapcsolót. Előre jóváhagyott automatizált telepítéshez a `--yes` kapcsoló használható. A webes felületen az Üzemeltető a **Névfelismerő telepítése** menüpontban, kifejezett jóváhagyással indíthatja a letöltést. Ugyanitt látható a verzió és az aktivitás, illetve elérhető a kikapcsolás, az aktiválás, a rögzített verzió újratelepítése és az eltávolítás. A Kérő a **Névfelismerő állapota** menüpontban ellenőrizheti az elérhetőséget, és szükség esetén az Üzemeltetőhöz fordulhat.

A telepítés macOS ARM64-en ellenőrzött. Aktív telepítés esetén a saját szöveges felület automatikus névjavaslatokat ad. A javaslatok megtekinthetők, törölhetők és kézi kijelöléssel pótolhatók.
