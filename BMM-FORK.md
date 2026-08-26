# Blue Monkey Media fork

Dit is een fork van [brightbean-studio](https://github.com/brightbeanxyz/brightbean-studio)
met één toevoeging: **gestukte uploads**, zodat bestanden boven de 100 MB het wel
halen. Verder is het upstream.

Alles wat wij toevoegen staat in nieuwe bestanden. Dat is een bewuste keuze en
geen toeval: het bepaalt wat het kost om deze fork bij te houden.

## Wat het probleem was

Cloudflare weigert één request met een body boven 100 MB (Free en Pro) met een
413 die de applicatie nooit ziet. De mediabibliotheek post elk bestand als één
request, dus elke upload boven die grens mislukte, terwijl
`MEDIA_LIBRARY_MAX_VIDEO_SIZE` 1 GB toestaat.

Gemeten tegen onze eigen opstelling op 2026-08-25:

| Wat | Resultaat |
|---|---|
| 95 MiB in één request | 200 |
| 120 MiB in één request | **413**, afgegeven door Cloudflare |
| 120 MiB als S3-multipart | 200, 9 s |

De grens geldt dus **per request, niet per bestand**. Een multipart-upload knipt
het bestand in parts die elk hun eigen request zijn, en dan bijt de limiet niet
meer. Wat er moest veranderen is *waar* het knippen gebeurt: in de browser, niet
op de server.

## Wat de patch doet

De browser knipt het bestand in parts van 16 MiB en PUT elk part rechtstreeks
naar de objectopslag met een presigned URL. De server geeft die URL's uit,
verzamelt de parts en valideert daarna de **opgeslagen** bytes.

Dat laatste is niet nieuw: `inspect_uploaded_object` deed dat al voor de
presigned-uploadroute van de MCP-tools, en wij hangen er alleen aan. De grootte
komt uit een HEAD, het bestandstype uit de eerste bytes. Wat de client beweert
wordt nergens vertrouwd.

Twee dingen zijn strenger dan bij de bestaande presigned route:

- **De grootte moet exact kloppen** met wat bij het openen is opgegeven. Bij een
  directe upload kun je een individueel part niet begrenzen, en dit dicht dat gat
  achteraf volledig.
- **Een afgekeurd object wordt weggegooid.** Faalt de validatie na het
  samenvoegen, dan gaat het object weg in plaats van in de bucket te blijven
  liggen.

Hervatten zit erin: de client vraagt op welke parts al binnen zijn en stuurt
alleen de rest. Bij een half gigabyte over een matige verbinding is opnieuw
beginnen geen optie.

## Voetafdruk in upstream-code

Dit is het cijfer dat telt bij het bijhouden van een fork:

| Bestand | Wat |
|---|---|
| `apps/media_library/multipart.py` | **nieuw**, de opslagkant |
| `apps/media_library/multipart_views.py` | **nieuw**, vier endpoints |
| `apps/media_library/tests/test_multipart_upload.py` | **nieuw**, tests |
| `static/js/chunked-upload.js` | **nieuw**, de browserkant |
| `bmm/` | **nieuw**, dit onderhoudsgereedschap |
| `apps/media_library/urls.py` | 4 routes plus een import |
| `templates/media_library/library_index.html` | 1 script-regel |

Twee bestaande bestanden, ongeveer 25 regels. De browserkant raakt het
Alpine-component **niet** aan: het staticbestand omhult de globale
`mediaLibrary()` en vervangt alleen `startUpload`. Bestanden onder de drempel
blijven het originele pad nemen.

**Er is geen migratie.** Een gestukte upload hergebruikt de bestaande
`PendingUpload`-rij, en de S3-`UploadId` wordt met `list_multipart_uploads`
teruggevonden op de unieke `storage_key` in plaats van opgeslagen. Dat scheelt
niet alleen een tabel: een eigen migratienummer botst vroeg of laat met een
migratie van upstream, en dat is normaal wat een gedragen Django-patch duur maakt.

## Bijwerken

```bash
bmm/update-upstream.sh            # kijken, niets wijzigen
bmm/update-upstream.sh --rebase   # echt rebasen
```

Het script kijkt of upstream aan onze twee aanrakingsvlakken heeft gezeten. Zo
niet, dan is de rebase triviaal. Het waarschuwt ook als upstream aan de helpers
zat waar wij op leunen (`storage.py`, `services.py`, `models.py`), want daar kan
iets stilzwijgend van betekenis veranderen zonder dat git een conflict meldt.

En het kijkt of upstream zelf iets met multipart heeft gedaan. Als dat zo is,
gooi onze patch dan weg. Een patch die je niet meer draagt is altijd beter dan
een patch die netjes rebaset.

**Altijd rebasen, nooit mergen.** Dan blijft dit een kleine, leesbare set commits
bovenop upstream, en blijft de voetafdruk hierboven te controleren met
`git diff upstream/main --stat`.

**Een conflict is een stopteken.** Los het op of breek af (`git rebase --abort`);
forceer nooit. Een verkeerd opgeloste rebase in een uploadpad kost meer dan een
week wachten.

## Verplicht: `S3_PUBLIC_ENDPOINT_URL`

De browser PUT de parts **rechtstreeks** naar de opslag met een presigned URL. Die
URL moet dus ondertekend zijn voor een host die de browser kan bereiken.

In onze opstelling staat `S3_ENDPOINT_URL` bewust op een intern adres
(`http://bmm-minio:9000`), zodat serververkeer de tunnel niet op hoeft. Een URL die
daarvoor is ondertekend kan een browser niet resolven, en dan **hangt de upload**
tot hij afloopt in plaats van dat hij netjes faalt. Dat is precies wat er misging
bij de eerste uitrol.

Zet daarom in de `.env` van de stack:

```
S3_PUBLIC_ENDPOINT_URL=https://s3.bluemonkeymedia.nl
```

Dat moet **exact** de host zijn die de browser aanroept, inclusief scheme: een
SigV4-handtekening dekt de host. Alleen de part-bytes gaan hierlangs; aanmaken,
opsommen, afronden en afbreken blijven op de interne endpoint.

Staat de variabele niet, dan tekent de gewone opslagclient en werkt alles zoals
voorheen. Voor een opstelling waar de app en de browser dezelfde endpoint zien is
er dus niets te doen.

## De "Compare & pull request"-banner op GitHub

Die staat er, en die kun je niet weghalen: GitHub zet hem op elke fork waarvan een
tak voorloopt op het origineel. Twee dingen om te weten:

- **Die knop maakt een pull request naar upstream**, niet naar onze eigen fork.
  Er staat dus niets klaar en er is niets misgegaan; GitHub biedt alleen aan onze
  patch naar brightbeanxyz te sturen. Doe dat bewust of niet, maar niet per
  ongeluk.
- **De standaardtak van de fork is `bmm/gestukte-upload`**, niet `main`. Dat is
  belangrijker dan het lijkt: stond hij op `main`, dan levert een kale
  `git clone` van deze fork upstream-code **zonder** onze patch op, en dat is
  precies het soort verrassing dat je op een server ontdekt. Nu krijgt een clone
  meteen de goede tak.

`main` blijft bewust een schone spiegel van upstream. Het bijwerkscript rebaset op
`upstream/main` en heeft die spiegel niet nodig, maar het is handig om te kunnen
vergelijken.

## Wij taggen, want upstream doet dat niet

Upstream heeft **nul tags** (nagekeken 2026-08-25, 529 commits sinds maart). Er is
dus geen release om op te pinnen, en een deployinstructie die `git checkout
<release-tag>` zegt kan niet uitgevoerd worden.

Daarom taggen we zelf, na elke geslaagde rebase:

```bash
git tag -a bmm-$(date +%Y.%m.%d) -m "upstream <sha>"
git push origin --tags
```

De deploy pint op die tag; het tagbericht zegt op welke upstream-commit hij
staat. Dat is de enige plek waar de vraag "welke upstream draaien we eigenlijk"
te beantwoorden is.

## Licentie

Upstream is AGPL-3.0 en deze fork dus ook. Artikel 13 verplicht je om gebruikers
die de gewijzigde versie over het netwerk gebruiken de broncode aan te bieden. Die
fork is publiek, en daarmee is dat afgedekt. **Houd hem publiek**; een privékopie
van een gewijzigde AGPL-applicatie waar klanten in werken zou die verplichting
open laten staan.

Beter nog is upstreamen. Er staat al een `test_presigned_upload.py` en een
`CONTRIBUTING.md`, dus de maintainers hebben dit pad zelf gebouwd en zijn er
vermoedelijk ontvankelijk voor. Neemt upstream het over, dan verdwijnen zowel het
rebasewerk als deze licentie-overweging.
