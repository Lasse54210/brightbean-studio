# Plan: Facebook-plaatsingen kiezen, net als bij Instagram

Stand 2026-10-05. Nog niets van gebouwd. Vastgelegd op verzoek van de
beheerder, na feedback van een klantteam: op Facebook wil je net als op
Instagram zelf kiezen in welk formaat een post verschijnt.

## Wat de beheerder wil

Per Facebook-account in de composer een rij met plaatsingen, zoals het
Instagram-paneel die heeft (`_instagram_settings.html`): **Automatisch**,
**Bericht**, **Reel**, **Story**. Je kiest het formaat, de composer zegt
meteen of de media erbij passen, en de server weigert wat Facebook toch niet
neemt.

## Wat er nu kan

- `providers/facebook.py` publiceert tekst/link, foto, meerdere foto's
  (`_publish_multi_photo`, via ongepubliceerde foto's plus `attached_media`),
  video (`/{page}/videos`) en Reel (`/{page}/video_reels`, start/upload/finish).
  `supported_post_types` is TEXT, IMAGE, VIDEO, REEL, LINK. **Story bestaat
  niet** in de provider.
- De keuze in de composer is alleen een `select` "Publish as" (Regular Page
  video of Facebook Reel) in `templates/composer/compose.html`, en die staat er
  alleen bij precies een video. Opslaan: de Facebook-tak in
  `_sync_platform_posts` (`apps/composer/views.py`) bewaart alleen
  `post_type = "reel"` als afwijking; anders leidt de engine het formaat af uit
  de media (`_resolve_post_type` in `apps/publisher/engine.py`).
- Sinds 2026-10-05 (zie "Eigen afbeelding als cover" in `BMM-FORK.md`) hangt
  in hetzelfde paneel een cover-afbeelding voor video en Reel.

## Wat Facebook toestaat (nog per endpoint na te lopen in de Graph-referentie)

| Formaat | Endpoint | Media | Opmerking |
|---|---|---|---|
| Bericht, tekst of link | `/{page}/feed` | geen | bestaat al |
| Bericht, foto('s) | `/{page}/photos`, meerdere via `attached_media` | max. `FACEBOOK_MAX_ATTACHED_MEDIA` | bestaat al |
| Bericht, video | `/{page}/videos` | 1 video | bestaat al, cover sinds 2026-10-05 |
| Reel | `/{page}/video_reels` | 1 video, 3-90 s, verticaal | bestaat al, cover sinds 2026-10-05 |
| Story, foto | foto ongepubliceerd naar `/{page}/photos`, dan `/{page}/photo_stories` | 1 afbeelding, tot 10 MB | **nieuw** |
| Story, video | `/{page}/video_stories`, start/upload/finish | 1 mp4, 9:16, min. 540x960, 24-60 fps, 3-60 of 3-90 s (de Meta-pagina spreekt zichzelf tegen, nalopen) | **nieuw** |

Rechten voor Stories: `pages_manage_posts`, `pages_read_engagement`,
`pages_show_list`; de Page-login vraagt die al (controleren in
`required_scopes`). Een Story heeft geen tekst onder de post, dus caption,
eerste reactie en cover vallen daar weg.

## Increments

**1. Plaatsingsrij in de composer.** Vervang de `select` door een rij zoals bij
Instagram, eigen partial (`_facebook_settings.html`) en eigen module
(`apps/composer/facebook_extras.py`) naar het model van
`instagram_extras.py`: `build_facebook_extra`, `placement_media_error`
gespiegeld in JS en Python met dezelfde teksten, en "Automatisch" als echte
keuze die geen `post_type` opslaat. Een submit zonder het paneel mag niets
wissen (zelfde regel als Instagram en TikTok). Bestaande posts met
`post_type = "reel"` moeten als Reel terugkomen; zonder hint is het Automatisch.
Bericht dwingt geen `post_type` af dat niet bij de media past: Bericht met een
video wordt VIDEO, met foto's IMAGE, zonder media TEXT/LINK.

**2. Story in de provider.** `PostType.STORY` in `supported_post_types`,
`_publish_photo_story` en `_publish_video_story` in een fork-bestand
(`providers/facebook_stories.py`), met een regel in `publish_post`. Zelfde
foutregels als `_publish_reel`: een 2xx die niet gelukt is, faalt met naam van
de stap; alles na de geslaagde publicatie is best-effort en mag nooit raisen
(een retry is een dubbele post). Geen caption meesturen.

**3. Formaat en duur.** Waarschuwen en blokkeren zoals
`apps/composer/instagram_specs.py` en `static/js/instagram-specs.js` dat voor
Instagram doen: Reel en Story-video 3-90 (of 60) s en verticaal, Story-foto tot
10 MB. Stop = Facebook weigert het; waarschuwing = het gaat, maar bijgesneden.

**4. Eventueel: eigen media per Facebook-account,** zoals increment 3 bij
Instagram (`apps/publisher/media_selection.py`), zodat de Story een 9:16-versie
krijgt en het bericht de 4:5. Pas doen als het team erom vraagt.

## Randvoorwaarden

- Fork-regel: eigen code in nieuwe bestanden, in upstream-bestanden alleen een
  import en een regel. Voetafdruk per increment in `BMM-FORK.md`.
- Geen migratie: alles in `platform_extra`.
- **Niets publiceren op een klantpagina om te testen.** Lokaal testen met
  nepaccounts (Playwright, zie de testopstelling in de geheugen-/werknotities
  van de beheerder); echt publiceren alleen op een testpagina van het bureau, en
  dan pas na akkoord.
- Deze repo is publiek: geen klant- of persoonsnamen in code, commits of dit
  plan.

## Eerst beslissen

1. Komt **Story** in increment 1 al in de rij (en uitgegrijsd tot increment 2),
   of pas als de provider hem kan?
2. Moet "Bericht" met meerdere foto's ook een video kunnen bevatten? Facebook
   neemt dat niet in een `attached_media`-post; voorstel: weigeren met uitleg.
3. Is er een testpagina op Facebook waarop het team Story en Reel echt mag
   proberen?
