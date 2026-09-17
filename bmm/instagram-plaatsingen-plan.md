# Plan: Instagram-plaatsingen en videoformaten

Stand 2026-09-11. Opgesteld na een code-inspectie van de fork op tak
`bmm/gestukte-upload`.

**Voortgang.** Increment 0, 1 en 2 zijn gebouwd op tak
`bmm/instagram-plaatsingen` (2026-09-11). Wat daar per increment van afweek staat
onderaan dat increment onder "Zo is het gebouwd". Increment 3 en 4 staan nog
open, en de drie beslissingen onderaan dit stuk staan nog open behalve de cover:
die is als tijdstempel gebouwd, zoals voorgesteld. Nog te doen door de beheerder: het IG
**Business**-testaccount uit increment 0.

## Wat er nu niet kan

De providers kunnen Reels, Stories en carrousels wel publiceren
(`providers/instagram.py:313-330`, `providers/instagram_login.py:312-322`),
maar je kunt de keuze nergens maken:

- De plaatsing komt uitsluitend uit `platform_extra["post_type"]`
  (`apps/publisher/engine.py:586-592`), en de composer vult `platform_extra`
  alleen voor YouTube, Pinterest en TikTok (`apps/composer/views.py:162-222`).
  Voor Instagram staat er niets, en `templates/composer/compose.html` heeft geen
  enkel Instagram-veld. De API kent het ook niet: `PlatformOverride`
  (`apps/api/schemas.py:129`) doet alleen titel, caption en eerste reactie.
- Dus geldt altijd de automatische afleiding: 1 video wordt een Reel, meerdere
  bestanden worden een carrousel, een afbeelding wordt een feedpost. Story is
  onbereikbaar behalve door `post_type` met de hand in de database te zetten.
- Niet in de payload: `share_to_feed`, `cover_url`, `thumb_offset`,
  `audio_name`, `collaborators`, `user_tags`, `location_id`, `alt_text`.
- Beeldverhoudingen: `width`, `height` en `aspect_ratio` worden wel gemeten
  (`apps/media_library/models.py:199`) maar nergens gebruikt. Geen validatie,
  geen waarschuwing. `processed_variants` is een dode kolom: hij staat in het
  model en in de eerste migratie en verder nergens. Videobewerking is alleen
  trimmen (`trim_video`, ffmpeg met `-c copy`); croppen zit alleen op
  afbeeldingen via Cropper.js.
- `PlatformPost.platform_specific_media` bestaat als kolom maar wordt door niets
  gevuld, en de engine leest hem niet: attachments komen altijd van
  `platform_post.post.media_attachments` (`apps/publisher/engine.py:430`).

## Randvoorwaarden uit de fork

Deze staan in `BMM-FORK.md` en zijn bepalend voor het ontwerp:

1. **Alles wat wij toevoegen gaat in nieuwe bestanden.** Aanrakingen in
   upstream-code blijven geteld en gedocumenteerd.
2. **Geen migraties.** Een eigen migratienummer botst vroeg of laat met upstream.
   Dat kan hier ook: `platform_extra`, `platform_specific_media` en
   `processed_variants` bestaan alle drie al als kolom.
3. **Rebasen op upstream, nooit mergen.** Nieuwe aanrakingsvlakken moeten in
   `bmm/update-upstream.sh` bij de waarschuwingslijst.
4. Na acceptatie taggen als `bmm-JJJJ.MM.DD`, want upstream tagt niet.

## Geverifieerde API-parameters

Nagekeken op 2026-09-11 tegen de Graph API-referentie van `POST /{ig-user-id}/media`:

| Parameter | Geldt voor |
|---|---|
| `media_type` | `CAROUSEL`, `REELS`, `STORIES` (leeg = feedafbeelding) |
| `share_to_feed` | alleen Reels |
| `cover_url` | alleen Reels |
| `thumb_offset` | video's en Reels, in milliseconden |
| `audio_name` | alleen Reels |
| `collaborators` | feedafbeeldingen, Reels, carrousels; max 3; niet bij Stories |
| `user_tags` | afbeeldingen, video's, Stories |
| `location_id` | afbeeldingen en video's; niet in carrousels |
| `alt_text` | alleen afbeeldingen, max 1000 tekens |
| `children` | alleen carrousel, max 10 |

Beeldverhouding Reels: toegestaan 0,01:1 tot 10:1, aanbevolen 9:16. De rest is
bij increment 2 nagekeken en klopt; ze staan nu in `apps/composer/instagram_specs.py`:

| Wat | Beeldverhouding | Duur |
|---|---|---|
| Reel | verplicht 0,01:1 tot 10:1, aanbevolen 9:16 | 3 s tot 15 min |
| Story-video | verplicht 0,1:1 tot 10:1, aanbevolen 9:16 | 3 s tot 60 s |
| Story-afbeelding | alleen aanbevolen 9:16 | n.v.t. |
| Feedafbeelding | 4:5 tot 1,91:1 | n.v.t. |
| Carrousel | per item, als feedafbeelding; maximaal 10 items | n.v.t. |

Twee dingen om te weten bij die tabel. De Reel en de Story hebben echt
verschillende ondergrenzen (0,01 tegen 0,1); dat is geen typefout van ons. En de
15 minuten voor een Reel is wat de referentie zegt, terwijl derden melden dat de
API in de praktijk op 90 seconden blijft steken. Wij houden de referentie aan;
gaat een Reel van vier minuten alsnog stuk, dan is dat de eerste regel om te
verlagen.

Voor een feedvideo en voor carrouselitems geeft de referentie geen eigen tabel.
Er staat alleen dat je carrouselitems als gewone afbeeldings- of videocontainers
aanmaakt, en dat is dus ook hoe ze beoordeeld worden.

---

## Increment 0: voorbereiding

- Tak `bmm/instagram-plaatsingen` aftakken van `bmm/gestukte-upload` (de
  standaardtak van de fork), zodat de uploadpatch eronder blijft liggen.
- `bmm/update-upstream.sh` uitbreiden met de nieuwe aanrakingsvlakken:
  `apps/composer/views.py`, `templates/composer/compose.html`,
  `apps/publisher/engine.py`, `apps/media_library/services.py`.
- Een Instagram-testaccount klaarzetten dat een **Business**-account is: Stories
  publiceren via de API werkt niet op een Creator-account. Dit is de enige
  externe voorwaarde en moet vroeg getest, niet aan het eind.

Klaar als: de tak staat, het script waarschuwt op de nieuwe bestanden, en er is
een IG-account waarop een teststory geplaatst kan worden.

**Zo is het gebouwd (2026-09-11).** Tak staat. In `update-upstream.sh` zijn de
vier composer- en providerbestanden bij de aanrakingsvlakken gezet; twee
bestanden staan bewust bij "waar we op leunen" in plaats daarvan:
`apps/publisher/engine.py` (wij wijzigen het niet, maar de hele keuze hangt aan
`_resolve_post_type`) en `choose_from_video_card.html` (de coverkiezer die we
hergebruiken). Die twee geven geen conflict als upstream ze herschrijft, en
precies daarom moeten ze gelezen worden.
**Open voor de beheerder: het IG Business-testaccount.** Zonder dat kan increment 1 wel
gebouwd maar niet afgetekend worden.

---

## Increment 1: plaatsingskeuze in de composer

Het hart van de klus. Per Instagram-account kiezen: Reel, Story, Carrousel of
Feedafbeelding, plus `share_to_feed` en een cover.

**Nieuwe bestanden**

- `apps/composer/instagram_extras.py`: de toegestane plaatsingen en
  `build_instagram_extra(request, acc_id, existing)` die er een `platform_extra`
  van maakt. Regels: onbekende waarde laat de bestaande staan (zelfde keuze als
  bij de TikTok-privacy, zodat een half submit niets wist), `share_to_feed`
  alleen bij Reel, cover alleen bij Reel of video.
- `templates/composer/partials/_instagram_settings.html`: het paneel, gemodelleerd
  op het TikTok-blok in `compose.html:791-1090`. De coverkiezer hergebruikt
  `templates/composer/partials/choose_from_video_card.html` met `mode="timestamp"`,
  precies zoals TikTok, en levert `thumb_offset` in milliseconden.
- `providers/instagram_placement.py`: `apply_placement(payload, content)` die de
  geverifieerde allowlist hierboven uit `content.extra` in de containerpayload
  zet, en per plaatsing weglaat wat daar niet mag. Een bestand, twee providers.
- Tests: `apps/composer/tests/test_instagram_extras.py` en
  `tests/providers/test_instagram_placement.py`.

**Aanrakingen in upstream-code**

- `apps/composer/views.py`: een `elif` voor `instagram` en `instagram_login` in
  `_sync_platform_posts` die `build_instagram_extra` aanroept. Ongeveer 5 regels.
- `templates/composer/compose.html`: een `{% include %}`-regel.
- `providers/instagram.py` en `providers/instagram_login.py`: een aanroep van
  `apply_placement` in `_publish_single`. Een regel per bestand.

**De engine blijft ongemoeid.** `_resolve_post_type` leest de hint uit
`platform_extra` al, dus zodra de composer hem schrijft werkt Story gewoon.

**Validatie.** De gekozen plaatsing moet bij de aangehangen media passen: Reel en
Story willen video, carrousel wil er twee of meer. Serverzijde in het bestaande
patroon van `_validate_pinterest_board_selection`, plus dezelfde melding
client-side zodat je het ziet voordat je inplant.

Klaar als: een Reel, een Story, een carrousel en een feedafbeelding elk op het
testaccount geplaatst zijn, `share_to_feed` uit aantoonbaar een Reel buiten het
feedraster houdt, en een gekozen cover terugkomt in de Reels-tab.

**Zo is het gebouwd (2026-09-11).** Code af, 142 composer- en provider-tests
groen, ruff/mypy/`manage.py check` schoon. Het aftekenen hierboven kan pas als
het testaccount er is.

Vier dingen liepen anders dan gepland:

- **Twee aanroepen per provider in plaats van een.** Ook de carrouselcontainer
  krijgt `apply_placement`, anders staat `collaborators` in de allowlist op
  `CAROUSEL` terwijl niets het daar ooit zet.
- **`mediaKinds` erbij in de Alpine-state van `compose.html`.** De
  client-side validatie moet weten hoeveel bestanden er hangen en of het eerste
  een video is. `mediaItems` kon dat niet beantwoorden: dat is de eerste render
  en wordt nooit ververst. `hasVideoAttached` wordt wel bijgehouden, en
  `mediaKinds` hangt nu aan diezelfde `syncVideoAttached`.
- **De cover is Reel-only, niet Reel-of-Story.** `thumb_offset` geldt voor
  video's en Reels, en Instagram publiceert een losse video als Reel. Een Story
  staat in geen raster en heeft niets om de miniatuur van te zijn.
- **`post.pk` is geen "bestaat al"-test.** `Post` heeft een UUID-primary key met
  een default, dus een ongesavede post heeft er al een. De validatie gebruikt
  `post._state.adding` om te bepalen of ze naar `PostMedia` of naar de
  sessiemedia moet kijken. Met `post.pk` zou een gloednieuwe post op een lege
  medialijst beoordeeld zijn, en dat kwam er alleen uit doordat er een test voor
  was.

En een correctie op een aanname die uit de TikTok-code kwam: `int()` weigert
niet-ASCII cijfers **niet** (`int("\u0661\u0662")` is 12). De opmerking daarover
in `views.py` van upstream klopt niet. Voor een millisecondewaarde maakt het
niets uit, maar de tests pinnen nu wat er echt gebeurt.

---

## Increment 2: waarschuwen op formaat en duur

Signaleren, niet omzetten. Dit is de kleine increment die het meeste dagelijkse
gedoe wegneemt.

**Nieuwe bestanden**

- `apps/composer/instagram_specs.py`: per plaatsing de toegestane en de
  aanbevolen beeldverhouding, de maximale duur en de codec-eisen als leestekst,
  plus een pure functie `check_media(width, height, duration, placement)` die een
  lijst waarschuwingen teruggeeft. Een plek voor alle getallen, dus een plek om
  bij te werken als Meta ze verandert.
- Tests: `apps/composer/tests/test_instagram_specs.py`, puur op die functie.

**Aanrakingen in upstream-code**

- `apps/composer/views.py`: `media_items` (regel 552-562) krijgt `width`,
  `height`, `duration` en `asset_id` mee. Vier regels. De mediabibliotheek heeft
  die waarden al, ze werden alleen niet doorgegeven.

De waarschuwing komt in het paneel uit increment 1 en is niet blokkerend:
"Deze video is 16:9. Als Reel wordt hij naar 9:16 afgekapt." Alleen wat de API
echt weigert (buiten 0,01:1 tot 10:1, of een Reel zonder video) blokkeert.

Klaar als: een 16:9-video als Reel een waarschuwing geeft en een 9:16-video niet,
en de blokkerende gevallen niet in de wachtrij komen.

**Zo is het gebouwd (2026-09-11).** Code af, 61 tests op de twee
Instagram-testbestanden, 1525 van de 1526 in de hele suite groen (de ene die
faalt is `test_facebook.py`, die tegen een hardgecodeerde datum van 2026-08-07
vergelijkt en verlopen is; raakt geen bestand uit onze diff). Ruff, mypy en
`manage.py check` schoon.

Vijf dingen liepen anders dan gepland:

- **`views.py` is niet aangeraakt.** Het plan wilde `width`, `height` en
  `duration` meegeven aan `media_items`, maar dat is precies dezelfde val als
  `mediaItems` in increment 1: het is de eerste render en wordt nooit ververst,
  dus een video die je na het laden aanhangt zou geen waarschuwing krijgen. De
  browser meet nu zelf uit de DOM (`video.videoWidth`, `img.naturalWidth`), wat
  altijd klopt en bovendien al werkt voordat de upload klaar is. Netto: nul
  nieuwe regels in `views.py`, een script-regel in `compose.html`, en een
  waarschuwing die niet achterloopt.
- **De servergetallen lopen juist wel achter, en dat is de reden dat een 0 niets
  mag doen.** `MediaAsset.width/height/duration` worden gevuld door een
  `@background`-taak met ffprobe. Tussen de upload en die taak in staat alles op
  0. Blokkeren op "korter dan 3 seconden" zou dan elke verse upload tegenhouden.
  Nul, None en negatief betekenen daarom overal "niet gemeten".
- **De feedafbeelding waarschuwt en blokkeert niet**, hoewel de referentie 4:5
  tot 1,91:1 net zo hard opschrijft als de Reel-grenzen. Instagram snijdt een
  feedafbeelding bij in plaats van hem te weigeren, en een onterechte stop kost
  meer dan een onterechte waarschuwing. Als een feedafbeelding ooit wél als
  "container failed" terugkomt, is dat de eerste regel om te herzien.
- **De carrousel is er als maximum bij gekomen**, want de referentie noemt 10
  items en een elfde wordt geweigerd. Dat hoort bij dezelfde controle als "een
  carrousel wil er minstens twee" en kostte één regel aan beide kanten.
- **De per-item-lus voor een carrousel kan vandaag niet afgaan.** Een
  carrouselitem wordt als feedafbeelding beoordeeld en daar blokkeert niets, dus
  die lus levert altijd `None`. Hij staat er toch, omdat hij wél nodig is zodra
  iemand een van die regels hard maakt; er staat een test die faalt op het moment
  dat dat gebeurt. Het "bestand N:"-voorvoegsel dat ik er eerst bij had is er
  weer uit: onbereikbare tekst is geen vooruitziendheid maar dode code.

En een aanname die sneuvelde: de spiegel Python/JavaScript is niet op zicht te
vertrouwen. Beide kanten zijn eenmalig over dezelfde 35 gevallen gedraaid en de
uitvoer is vergeleken; dat is geen vaste test geworden, want er is geen
JS-testopstelling in deze repo en er een binnenhalen is duurder dan dit
increment.

---

## Increment 3: eigen media per account

**Gebouwd (2026-09-14).** Afwijkingen van het plan hieronder: de kiezer is een
eigen modal (`_instagram_media_modal.html`) met een eigen view
(`account_media_picker`) in plaats van de bestaande media-modal, want die hangt
bestanden server-side aan de post en dat is precies wat hier niet mag; een
verdwenen bestand is aan de poort een stop en bij het publiceren een
overslaan; alt-tekst loopt mee als het bestand ook aan de post hangt; en alle
plaatsingscontroles kijken naar de eigen bestanden zodra die er zijn. Zie
`BMM-FORK.md`, "Eigen media per account".

Zodat een post een 9:16-versie voor de Story en een 4:5-versie voor het feed kan
hebben, met bestanden die wij zelf in de edit maken.

**Nieuwe bestanden**

- `apps/publisher/media_selection.py`: `resolve_attachments(platform_post)` die
  `platform_specific_media` gebruikt als daar een lijst asset-ID's staat en
  anders terugvalt op de post-attachments. De volgorde van de lijst is de
  volgorde van de carrousel.
- Tests: `apps/publisher/tests/test_media_selection.py`, inclusief een geval waar
  een ID niet meer bestaat (dan terugvallen, niet crashen).

**Aanrakingen in upstream-code**

- `apps/publisher/engine.py`: regel 430 vervangen door een aanroep van
  `resolve_attachments`. Een regel.
- `apps/composer/views.py`: `ig_media_ids_<accId>` opslaan in
  `platform_specific_media`. Ongeveer 3 regels, in hetzelfde blok als
  increment 1.
- Het paneel krijgt een mediakiezer die de bestaande modal hergebruikt.

**Wat al goed staat:** de opruiming van wezen leest `platform_specific_media` al
(`apps/media_library/services.py:752` en `:787`), dus een bestand dat alleen aan
een account hangt wordt niet weggegooid. Dupliceren kopieert het veld ook al
(`apps/composer/services.py:321`).

Klaar als: een post met twee accounts twee verschillende video's publiceert, en
een verwijderd bestand de publicatie laat terugvallen in plaats van laat falen.

---

## Increment 4: automatisch herkadreren

De grote, en de enige die ik zou uitstellen tot 1 tot 3 in gebruik zijn.

**Nieuwe bestanden**

- `apps/media_library/reframe.py`: ffmpeg naar 9:16, 4:5 en 1:1. Dit is
  re-encoden (H.264, AAC), geen `-c copy` zoals bij trimmen, dus het duurt en het
  kost kwaliteit. Twee strategieen: gecentreerd croppen, of passen binnen het
  kader met een onscherpe achtergrond. Beide zijn een gok over waar het beeld
  zit.
- `apps/media_library/reframe_tasks.py`: een `@background`-taak in het bestaande
  `background_task`-patroon van `apps/media_library/tasks.py`.
- Tests: `apps/media_library/tests/test_reframe.py`, met een kort testbestand.

**Opslagmodel.** Een variant is een nieuw `MediaAsset` in dezelfde map, en het
origineel houdt de verwijzing in `processed_variants`:
`{"9:16": {"asset_id": "...", "width": 1080, "height": 1920}}`. Dat is de dode
kolom die er al staat, dus geen migratie, en de variant loopt daarmee automatisch
mee in de picker, de quota en de presigned URL's. `MediaAssetVersion` is hier
bewust niet het model: dat is versiegeschiedenis die het origineel vervangt,
terwijl een variant ernaast moet bestaan.

**Twee dingen die hierbij mee moeten**

- De opruiming van wezen kent `processed_variants` niet. Een variant die nog aan
  geen post hangt en ouder is dan 14 dagen wordt dus opgeruimd. Het ID moet mee
  in `_json_referenced_asset_ids` (`apps/media_library/services.py`). Twee
  regels, maar zonder dit verdwijnen bestanden.
- Drie varianten per video betekent ruwweg vier keer de opslag per video.
  Herkadreren dus alleen op verzoek, nooit automatisch bij uploaden.

Klaar als: een 16:9-video op verzoek een 9:16-variant oplevert die als
account-media gekozen kan worden, de variant de opruiming overleeft, en de
kwaliteit door de eigenaren is bekeken voordat het aangaat.

---

## Voetafdruk in upstream-code

Het cijfer dat telt bij het bijhouden van de fork. Alles bij elkaar:

| Bestand | Wat | Increment |
|---|---|---|
| `apps/composer/views.py` | ongeveer 12 regels: extras-blok, validatie, later `platform_specific_media` | 1, 3 |
| `templates/composer/compose.html` | 1 include-regel, `mediaKinds`, 1 script-regel | 1, 2 |
| `providers/instagram.py` | 1 regel | 1 |
| `providers/instagram_login.py` | 1 regel | 1 |
| `apps/publisher/engine.py` | 1 regel | 3 |
| `apps/media_library/services.py` | 2 regels | 4 |

Zes bestaande bestanden, rond de 18 regels, geen migratie. Dat is dezelfde orde
als de uploadpatch en dus te dragen.

Increment 2 kwam goedkoper uit dan hier begroot: `views.py` is er niet voor
aangeraakt, omdat de browser de afmetingen zelf meet in plaats van ze uit
`media_items` te lezen. Dat scheelt vier regels in `views.py` en kost er één in
`compose.html`.

## Wat we niet doen

- `user_tags`, `collaborators`, `location_id`, `audio_name` en `alt_text`. Ze
  staan in `providers/instagram_placement.py` als allowlist, maar er komt geen UI
  voor. Dat is een aparte keuze als iemand het nodig heeft.
- Feed-video als eigen plaatsing. Instagram publiceert een losse video altijd als
  Reel; de code doet dat al bewust (`providers/instagram.py:313-319`).
- Herkadreren van afbeeldingen. Cropper.js doet dat al.

## Eerst beslissen

1. **Cover via `thumb_offset` of `cover_url`.** Het tijdstempel is het
   eenvoudigst en hergebruikt de TikTok-kiezer die er al ligt. Een eigen
   coverbeeld is mooier maar vraagt een publieke URL en dus de
   `cover_image_asset_id`-route die Pinterest gebruikt. Mijn voorstel:
   tijdstempel in increment 1, coverbeeld pas als jullie het missen.
2. **Waar het herkadreren gebeurt.** In de tool met ffmpeg, of in de edit en
   daarna als eigen bestand naar binnen. Increment 3 maakt het tweede pad
   volwaardig bruikbaar, en dat is voor een videobedrijf misschien het eerlijke
   antwoord.
3. **Upstreamen of niet.** Increment 1 en 2 zijn geen BMM-eigenaardigheid: dit
   mist voor iedere Brightbean-gebruiker. Een pull request naar
   `brightbeanxyz/brightbean-studio` haalt het rebasewerk op termijn weg. Dat
   maakt de bouw wel iets duurder, want dan moet het in het Engels en zonder
   onze eigen aannames.
