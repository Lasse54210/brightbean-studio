# Blue Monkey Media fork

Dit is een fork van [brightbean-studio](https://github.com/brightbeanxyz/brightbean-studio)
met twee toevoegingen (integratietak `bmm/main`, deploy pint op de tags):

1. **Gestukte uploads**, zodat bestanden boven de 100 MB het wel halen.
2. **Instagram-plaatsingen**: per account kiezen tussen Reel, Story, carrousel en
   feedafbeelding, in plaats van alleen de automatische afleiding, met een
   waarschuwing als het bestand niet bij die plaatsing past.

Verder is het upstream.

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

Het script kijkt of upstream aan onze aanrakingsvlakken heeft gezeten. Zo niet,
dan is de rebase triviaal. Het waarschuwt ook als upstream aan de plekken zat
waar wij op leunen zonder ze te wijzigen (`storage.py`, `services.py`,
`models.py`, `apps/publisher/engine.py`, de frame-picker), want daar kan iets
stilzwijgend van betekenis veranderen zonder dat git een conflict meldt. De
plaatsingskeuze is daar het duidelijkste geval: herschrijft upstream
`_resolve_post_type`, dan staat ons paneel er nog steeds maar doet het niets.

En het kijkt of upstream zelf iets met multipart heeft gedaan. Als dat zo is,
gooi onze patch dan weg. Een patch die je niet meer draagt is altijd beter dan
een patch die netjes rebaset.

**Altijd rebasen, nooit mergen.** Dan blijft dit een kleine, leesbare set commits
bovenop upstream, en blijft de voetafdruk hierboven te controleren met
`git diff upstream/main --stat`.

**Een conflict is een stopteken.** Los het op of breek af (`git rebase --abort`);
forceer nooit. Een verkeerd opgeloste rebase in een uploadpad kost meer dan een
week wachten.

## Instagram-plaatsingen

De providers konden Reels, Stories en carrousels al publiceren, maar je kon de
keuze nergens maken. De plaatsing kwam uitsluitend uit
`platform_extra["post_type"]`, en de composer vulde `platform_extra` alleen voor
YouTube, Pinterest en TikTok. Dus gold altijd de automatische afleiding: één
video werd een Reel, meerdere bestanden een carrousel, een afbeelding een
feedpost. **Een Story was onbereikbaar** behalve door de kolom met de hand te
zetten.

De composer schrijft die hint nu wel, en daarmee werkt de rest vanzelf: de
publisher las hem altijd al. Daar hangen twee parameters aan die alleen op een
Reel mogen (`share_to_feed` en `thumb_offset`), en die worden per plaatsing
weggelaten in plaats van meegestuurd. Dat is geen netheid maar noodzaak:
Instagram negeert een parameter die niet bij het mediatype past niet, het keurt
de container af, en die fout komt minuten later terug als "container failed"
zonder verdere tekst.

De plaatsing wordt bij het opslaan gecontroleerd tegen de aangehangen media (een
Reel wil een video, een carrousel twee bestanden), met dezelfde regels en
dezelfde tekst aan beide kanten. Dezelfde reden: fout gekozen komt anders pas
bij het publiceren boven water.

**Automatisch** is een echte knop en niet alleen het ontbreken van een keuze. Hij
stuurt `auto`, en dat slaat de server op als "geen `post_type`", zodat de
publisher weer zelf afleidt. Een lege waarde kon dat niet zijn: leeg betekent al
"het paneel zei niets, laat staan wat er stond", en die betekenis is nodig om te
voorkomen dat een half gerenderd formulier een Story stilletjes terugzet.

Buiten een carrousel publiceert de provider alleen `media_urls[0]` en laat de
rest zonder bericht weg. Het paneel zegt dat dus zelf: "Only the first file is
published as a Reel. The other file is left out; pick Carousel to publish them
all."

Twee dingen die in de eerste review naar boven kwamen en waar je op moet letten
als je hier iets wijzigt:

- **De cover moet twee kanten op vertaald worden.** Het formulier stuurt
  `ig_cover_timestamp_ms`, de server bewaart `thumb_offset`. Een opgeslagen post
  komt dus terug met `thumb_offset` en zonder `video_cover_timestamp_ms`, en een
  paneel dat alleen het laatste leest toont geen cover en stuurt een leeg veld,
  waarmee de cover bij de volgende save verdwijnt. De `x-init` van het paneel
  is die terugvertaling.
- **Video-herkenning in de provider staat op het URL-pad, niet op de URL.**
  Upstream deed `url.endswith(".mp4")` op de hele string. Dat werkt alleen zolang
  media-URL's ongetekend zijn (onze stand: `S3_CUSTOM_DOMAIN` gezet). Een
  presigned URL eindigt op `?X-Amz-Signature=...` en dan leest elke video als
  afbeelding: een Story-video gaat als `image_url` de deur uit en de container
  faalt minuten later. `is_video_url` in `providers/instagram_placement.py`
  kijkt naar `urlsplit(url).path`, zodat beide URL-vormen hetzelfde doen. Dit is
  een upstream-fout die pas in ons pad kwam toen Story bereikbaar werd, en hoort
  bij de fixes die naar upstream kunnen.

### Formaat en duur

Bij de plaatsing hoort een bestand dat erbij past, en dat is precies wat
Instagram je niet op tijd vertelt. Een video van 16:9 als Reel wordt afgekapt
naar 9:16; een Story van 70 seconden wordt niet afgekapt maar geweigerd, en die
weigering komt asynchroon terug als "container failed", minuten nadat je hebt
ingepland.

Dus staan alle getallen op één plek (`apps/composer/instagram_specs.py`, gelezen
uit Meta's IG User Media-referentie op 2026-09-11) en komt er in het paneel te
staan wat er met dit bestand gaat gebeuren. Twee soorten melding, en het verschil
is het hele punt:

- Een **stop** maakt de plaatsing onkiesbaar en laat de server de opslag
  weigeren. Alleen voor wat Instagram echt weigert: de verplichte
  beeldverhouding (0,01:1 tot 10:1 voor een Reel, 0,1:1 tot 10:1 voor een Story)
  en de duur (3 seconden tot 15 minuten voor een Reel, 3 tot 60 seconden voor
  een Story).
- Een **waarschuwing** houdt niets tegen. "Deze video is 16:9. Een Reel is 9:16,
  dus Instagram snijdt het middelste stuk eruit." Bijsnijden is een keuze die je
  mag maken; een composer die weigert een 16:9-Reel in te plannen zou erger zijn
  dan het probleem.

De feedafbeelding is bewust de uitzondering: de referentie schrijft 4:5 tot
1,91:1 net zo hard voor als de rest, maar in de praktijk snijdt Instagram een
feedafbeelding bij in plaats van hem te weigeren. Dat is dus een waarschuwing.
Een onterechte stop kost een release, een onterechte waarschuwing een zin.

**De browser meet zelf.** Niet omdat dat mooier is, maar omdat
`MediaAsset.width/height/duration` door een achtergrondtaak (ffprobe) worden
gevuld en dus 0 zijn zolang die taak nog niet heeft gedraaid. Een `<video>` in de
lijst kent zijn eigen afmetingen meteen. De serverkant kijkt naar dezelfde
getallen uit de database, en een 0 betekent daar "nog niet gemeten" en nooit
"nul pixels breed" -- een blokkade op ontbrekende metadata zou elke verse upload
tegenhouden.

### Voetafdruk

| Bestand | Wat |
|---|---|
| `apps/composer/instagram_extras.py` | **nieuw**, de formulierkant en de validatieregels |
| `apps/composer/instagram_specs.py` | **nieuw**, alle getallen van Meta plus de controle |
| `templates/composer/partials/_instagram_settings.html` | **nieuw**, het paneel |
| `static/js/instagram-specs.js` | **nieuw**, dezelfde controle plus het meten in de browser |
| `providers/instagram_placement.py` | **nieuw**, de allowlist per plaatsing |
| `apps/composer/tests/test_instagram_extras.py` | **nieuw**, tests |
| `apps/composer/tests/test_instagram_specs.py` | **nieuw**, tests |
| `tests/providers/test_instagram_placement.py` | **nieuw**, tests |
| `apps/composer/views.py` | een import, een `elif` in `_sync_platform_posts`, een validatiepoort |
| `templates/composer/compose.html` | 1 include, 1 script-regel, plus `mediaKinds` in de Alpine-state |
| `providers/instagram.py` | 1 import, 2 aanroepen van `apply_placement`, 2 keer `is_video_url` in plaats van `endswith` |
| `providers/instagram_login.py` | 1 import, 2 aanroepen van `apply_placement`, 2 keer `is_video_url` in plaats van `endswith` |

Vier bestaande bestanden, rond de 40 regels. **Geen migratie**: `platform_extra`
bestaat al als kolom, en dat is precies waarom dit zo klein blijft.

Er is geen JavaScript-testopstelling in deze repo en die is er voor dit ene
bestand ook niet bij gekomen. De getallen staan vast in
`apps/composer/tests/test_instagram_specs.py`; dat `static/js/instagram-specs.js`
bij oplevering exact hetzelfde antwoord gaf is eenmalig gecontroleerd door beide
kanten over dezelfde 35 gevallen te draaien en de uitvoer te vergelijken. Wie een
getal wijzigt, wijzigt er dus twee. De spiegel is `check()`; `checkPost()` bestaat
alleen in de browser, want de server heeft genoeg aan de eerste blokkade terwijl
het paneel elke waarschuwing toont, per bestand genummerd.

Twee dingen die bewust hergebruikt zijn in plaats van nagebouwd:

- **De coverkiezer is die van TikTok**, inclusief de Alpine-sleutels
  (`video_cover_timestamp_ms`, `tiktokCoverPreview`). Een account is nooit
  tegelijk TikTok en Instagram, dus de sleutels botsen niet, en zo blijft de
  JavaScript van de frame-picker ongemoeid. Het formulierveld vertaalt het:
  `ig_cover_timestamp_ms` wordt `thumb_offset`.
- **`mediaKinds` hangt aan de bestaande `syncVideoAttached`**, de plek die al
  bijhield of er een video hangt. `mediaItems` kon dit niet beantwoorden: dat is
  alleen de eerste render en wordt nooit ververst.

### Wat er niet in zit

`user_tags`, `collaborators`, `location_id`, `audio_name` en `alt_text` staan wel
in de allowlist maar hebben geen UI. De waarschuwingen kijken naar beeldverhouding
en duur en niet naar bestandsgrootte, codec of framerate; die staan wel in de
referentie maar niet in `instagram_specs.py`. Feed-video als eigen plaatsing bestaat niet:
Instagram publiceert een losse video altijd als Reel. Herkadreren van video's
staat in het plan (`bmm/instagram-plaatsingen-plan.md`, increment 4) en is niet
gebouwd; eigen bestanden per account (increment 3) wel, zie hieronder.

## Registratie op uitnodiging

Upstream laat registratie open, en `apps/accounts/signals.py` geeft iedere
nieuwe gebruiker zonder uitnodiging een eigen "My Organization" met een eigen
workspace. Voor een gehost product is dat juist; voor een eigen instantie
betekent het dat iedereen die de loginpagina vindt een account met opslag op
onze bucket krijgt, en dat een collega die gewoon inlogt in zijn eigen lege
organisatie belandt en nooit onder Team members van het bureau verschijnt.

Sinds deze ronde is registratie **dicht** tenzij een van twee dingen geldt:

- `ACCOUNT_OPEN_SIGNUP=true` in de omgeving (het gedrag van upstream terug), of
- de bezoeker komt via een uitnodigingslink. `accept_invite` parkeert het token
  in de sessie voordat hij naar het aanmeldformulier stuurt, en het signaal na
  aanmelding accepteert het. Een geldig token is dus het bewijs dat iemand in
  een organisatie om dit account heeft gevraagd.

De regel staat op één plek, `apps/accounts/signup_policy.py`, en wordt gelezen
door de allauth-adapter voor e-mail (`AccountAdapter`), die voor Google
(`SocialAccountAdapter.is_open_for_signup`), de loginpagina (die "Sign up"
verbergt) en de pagina `account/signup_closed.html` die allauth toont als het
niet mag. Inloggen raakt het nooit: de poort beslist alleen of er een *nieuw*
account mag komen.

Wat er voor bestaande gebruikers verandert: niets. Wie al in zijn eigen
"My Organization" zit, komt in de goede organisatie via Team members > Invite op
exact het e-mailadres waarmee hij inlogt; `accept_invitation` eist die match.

## Wees-parts: afgebroken gestukte uploads opruimen

Een gestukte upload die gestart is en nooit voltooid of afgebroken, houdt al zijn
ontvangen parts vast. Die staan in geen enkele bucketlisting, zijn geen object,
geen `MediaAsset` wijst ernaar, en toch nemen ze ruimte in en gaan ze mee in
elke buckettar. Een browsertab die halverwege een video van 2 GB dichtgaat laat
2 GB achter, stil en voorgoed. Upstreams `sweep_pending_uploads` ruimt alleen de
rij en het (niet-bestaande) object op; de parts bleven.

`apps/media_library/multipart_sweep.py` is de ontbrekende helft: het somt de
open multipart-uploads in de bucket op en breekt af wat **ouder is dan 24 uur**
(`STALE_AFTER`) en **door geen levende `PendingUpload`-rij** wordt geclaimd. De
drempel is bewust langer dan de uploadsessie van 12 uur, zodat een lopende
overdracht nooit door een slecht getimede veegronde wordt afgekapt.

Twee ingangen op dezelfde code:

- de achtergrondtaak `run_stale_multipart_sweep`, dagelijks, geregistreerd naast
  de bestaande sweeps in `apps/media_library/apps.py` (registratie gebeurt bij
  `migrate`, dus de migrate-service van een deploy zet hem aan);
- `python manage.py abort_stale_multipart_uploads [--dry-run] [--older-than-hours N]`
  voor de eerste keer op een installatie met een achterstand. Draai hem eerst
  met `--dry-run`; hij noemt per upload wat hij zou doen en waarom.

Op een installatie zonder S3 doet allebei niets.

## Eigen media per account (increment 3)

`PlatformPost.platform_specific_media` bestond upstream al ("JSON list of media
asset IDs with platform-specific ordering/cropping"), werd door niemand
geschreven en alleen door de wezenopruiming gelezen. De composer schrijft hem
nu per Instagram-account, en `apps/publisher/media_selection.py` vertaalt hem
terug naar bijlagen voor de publisher.

Wat je ermee kunt: één post met een 9:16 voor de Story op het ene account en een
4:5 voor het feed op het andere, met bestanden die in de edit gemaakt zijn. In
het paneel staat onder de plaatsing het blok **Media for this account**; "Pick
own files" opent een kiezer over de mediabibliotheek (alle soorten, ook gedeelde
items), de volgorde van kiezen is de carrouselvolgorde en is met pijltjes te
wijzigen. Leeg betekent: publiceer de bijlagen van de post, precies zoals eerst.

De regels:

- De lijst wint, in lijstvolgorde. Een id dat niet meer bestaat wordt bij het
  publiceren overgeslagen; resolvet er niets, dan vallen we terug op de bijlagen
  van de post. Een bestaande post gedraagt zich dus exact als voorheen.
- Aan de poort is een verdwenen bestand juist wél een stop: anders publiceert de
  terugval stilletjes iets anders dan wat gekozen was.
- Alle plaatsingscontroles (soort, verhouding, duur) worden op de eigen bestanden
  gedaan zodra die er zijn, in het paneel en op de server. Een Reel die grijs was
  op de afbeelding van de post gaat aan zodra je voor het account een video kiest.
- Het formulierveld `ig_media_ids_<acc>` is net zo bewaakt als de plaatsing:
  afwezig is "houd wat er staat", aanwezig-en-leeg is "wis de lijst". Het
  paneel stuurt het veld altijd mee, dus leegmaken wist echt.
- Alt-tekst loopt mee als hetzelfde bestand ook aan de post hangt.

De kiezer stuurt per bestand de metingen van de server mee (breedte, hoogte,
duur; 0 is "nog niet gemeten" en blokkeert niets), want een bestand dat niet aan
de post hangt staat niet in het `#media-list` waar de browser de rest opmeet.

### Voetafdruk van deze ronde

| Bestand | Wat |
|---|---|
| `apps/accounts/signup_policy.py`, `context_processors.py`, `tests/test_signup_policy.py` | **nieuw** |
| `templates/account/signup_closed.html` | **nieuw** |
| `apps/media_library/multipart_sweep.py`, `management/commands/abort_stale_multipart_uploads.py`, `tests/test_multipart_sweep.py` | **nieuw** |
| `apps/publisher/media_selection.py`, `test_media_selection.py` | **nieuw** |
| `apps/composer/tests/test_instagram_own_media.py` | **nieuw** |
| `templates/composer/partials/_instagram_media_modal.html`, `account_media_picker.html` | **nieuw** |
| `apps/accounts/adapters.py` | een `AccountAdapter` en één methode op de bestaande social-adapter |
| `config/settings/base.py` | `ACCOUNT_OPEN_SIGNUP`, `ACCOUNT_ADAPTER`, één context processor |
| `templates/account/login.html` | `{% if signup_open %}` om de Sign-up-link |
| `apps/media_library/tasks.py` | één taak erbij, onderaan |
| `apps/media_library/apps.py` | één registratie erbij |
| `apps/publisher/engine.py` | één import, één regel: `resolve_attachments(platform_post)` |
| `apps/composer/views.py` | drie namen in de bestaande import, één regel in `_sync_platform_posts`, drie regels in de compose-context, één view `account_media_picker` |
| `apps/composer/urls.py` | één route |
| `apps/composer/instagram_extras.py` | eigen bestand van de fork, uitgebreid |
| `templates/composer/compose.html` | één include |
| `templates/composer/partials/_instagram_settings.html` | eigen bestand van de fork, uitgebreid |

Opnieuw **geen migratie**: `platform_specific_media` bestond al, en registratie
en opruiming schrijven geen kolom.

## Reacties per post

De inbox zette alle reacties op een hoop. Je zag wie iets schreef en wanneer,
maar niet waar het onder stond, en je kon de reacties onder een post ook niet
bij elkaar zetten.

Het opvallende is dat de koppeling er al was. `InboxMessage.related_post` staat
sinds de eerste migratie in het model en wordt door allebei de wegen gevuld:
het pollen zoekt hem per batch op in een query (`apps/inbox/tasks.py`,
`resolve_related_posts`) en de webhook doet dezelfde opzoeking per gebeurtenis
(`apps/inbox/webhooks.py`). Alleen las **geen enkele template hem ooit**. Dit is
dus geen nieuwe gegevensstroom, het is het zichtbaar maken van een die al liep.
Vandaar ook: geen migratie, geen env, geen achtergrondtaak.

Wat je nu ziet:

- **In de lijst** een chip onder de reactie met de eerste regel van de caption.
  Klik erop en de feed staat op die ene post.
- **In het detailpaneel** een kaart boven het bericht met de post, de
  publicatiedatum en drie uitgangen: bekijken op het platform, openen in de
  composer, en alle berichten onder dezelfde post.
- **Boven de filterbalk** een melding welke post actief is, met het totaal
  eronder en een knop terug naar alles.

Drie dingen die het ontwerp bepalen:

1. **Gefilterd wordt op het post-id van het platform, niet op de `PlatformPost`.**
   Daarmee vallen reacties op een post die niet uit deze Brightbean komt in
   dezelfde groep als de rest, en blijft een post die om wat voor reden dan ook
   niet gekoppeld raakte toch bij elkaar te zetten. Facebook bewaart twee
   spellingen van hetzelfde id (`post_id` met paginaprefix, `stored_post_id`
   zonder); het filter kijkt naar allebei, anders halveert het stilletjes een
   gesprek.
2. **Een ongekoppelde post heet geen "elders geplaatst".** YouTube noemt het
   `video_id` en LinkedIn `post_urn`, en `resolve_related_posts` kijkt naar geen
   van beide, dus die reacties zijn ongekoppeld terwijl de post wel degelijk uit
   Brightbean kan komen. De verwijzing toont dan het id en beweert verder niets.
   Die twee sleutels worden wel gelezen voor het groeperen, dus **filteren per
   post werkt daar al, koppelen nog niet.**
3. **De permalink wordt nooit verzonnen.** Facebook en Instagram geven
   `post_permalink_url` mee en die geven we door. Een provider die dat niet doet
   levert geen link op, in plaats van een gegokte URL die een 404 geeft.

De teller in de melding negeert bewust de andere filters. Die melding staat
buiten het stuk dat HTMX verwisselt, dus een teller die op status meerekent zou
blijven staan op de stand van het paginaladen en vanaf dat moment liegen.

### Voetafdruk

| Bestand | Wat |
|---|---|
| `apps/inbox/post_reference.py` | **nieuw**, de sleutel, het label en het filter |
| `apps/inbox/templatetags/bmm_inbox.py` | **nieuw**, de filter voor de templates |
| `templates/inbox/partials/_post_chip.html` | **nieuw**, de chip in de lijst |
| `templates/inbox/partials/_post_card.html` | **nieuw**, de kaart in het paneel |
| `templates/inbox/partials/_post_filter_notice.html` | **nieuw**, de actieve-postmelding |
| `apps/inbox/tests/test_post_reference.py` | **nieuw**, tests |
| `apps/inbox/views.py` | een import, `select_related` erbij, het filter, twee contextregels |
| `templates/inbox/feed.html` | 1 include |
| `templates/inbox/partials/_message_row.html` | 1 include |
| `templates/inbox/partials/_message_panel.html` | 1 include |
| `templates/inbox/partials/_empty_state.html` | 1 voorwaarde uitgebreid |
| `templates/inbox/partials/_filter_bar.html` | `[name='post']` in de vijf `hx-include`-lijsten |

Zes bestaande bestanden, rond de 25 regels. Die filterbalk is de enige plek die
aandacht vraagt bij een rebase: elk filter somt zelf op welke invoervelden het
meestuurt, dus een filter dat upstream erbij bouwt moet `[name='post']` ook in
zijn lijst krijgen, anders valt het postfilter weg zodra je dat nieuwe filter
gebruikt. Het verborgen invoerveld zelf staat in de melding, buiten het stuk dat
HTMX verwisselt.

### Wat er niet in zit

Koppelen van YouTube-, LinkedIn- en Mastodon-reacties aan hun `PlatformPost`
(daarvoor moeten die providers dezelfde sleutel schrijven, plus een backfill
voor wat al binnen is), en een ingang vanaf de post zelf: een "reacties (n)"-link
op een gepubliceerde post in de composer of de kalender. Bulkacties resetten de
lijst naar ongefilterd, ook voor dit filter; dat doet upstream voor elk filter
en is hier bewust niet rechtgetrokken.

### LinkedIn en YouTube koppelen ook

De eerste ronde liet twee providers half werken: LinkedIn-reacties
(`post_urn`) en YouTube-reacties (`video_id`) groepeerden wel op post, maar
kregen geen postkaart, want `_related_post_key` las alleen Facebooks twee
spellingen. Dat is nu dezelfde volgorde geworden: `_related_post_key` roept
`post_key` aan, zodat de ene tuple `POST_KEYS` zowel het groeperen als het
vullen van `related_post` stuurt. Een provider die erbij komt kan dus niet meer
in het ene wel en in het andere niet zitten.

Het werkt omdat de twee kanten dezelfde string bewaren. LinkedIn publiceert via
`POST /rest/posts` en legt `x-restli-id` vast als `platform_post_id`; de inbox
leest `GET /rest/posts` en neemt `id` van dezelfde post. YouTube doet hetzelfde
met het video-id. Er viel dus niets te vertalen, alleen te kijken.

Voor wat al binnen was is er `manage.py link_inbox_posts`, met `--dry-run` en
`--platform`. Hij koppelt alleen los: een bericht dat al ergens naar wijst
blijft staan, en de opzoeking is per account, zodat hetzelfde post-id bij twee
accounts niet overspringt.

**Wat hiermee niet opgelost is:** de inbox van LinkedIn haalt alleen de posts
van de ingelogde persoon op. `get_messages(access_token, since)` krijgt geen
account mee, dus de provider leidt de auteur af uit het profiel achter het
token (`urn:li:person:...`) terwijl publiceren wel naar een
`urn:li:organization:...` kan schrijven. Reacties op een bedrijfspagina komen
daardoor helemaal niet binnen, en dat is een ander gat dan dit.

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
- **De standaardtak van de fork is `bmm/main`**, niet `main`. Dat is
  belangrijker dan het lijkt: stond hij op `main`, dan levert een kale
  `git clone` van deze fork upstream-code **zonder** onze patches op, en dat is
  precies het soort verrassing dat je op een server ontdekt. Nu krijgt een clone
  meteen de goede tak. `bmm/main` is de integratietak met alles wat wij
  toevoegen; de tags staan erop. Tot 2026-09-11 heette die tak
  `bmm/gestukte-upload`, naar de eerste toevoeging, en die naam klopte niet meer
  toen er een tweede bij kwam. De featuretakken (`bmm/gestukte-upload`,
  `bmm/instagram-plaatsingen`) blijven staan als geschiedenis.

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
