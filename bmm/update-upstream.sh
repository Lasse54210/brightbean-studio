#!/usr/bin/env bash
# Werk de fork bij naar de nieuwste upstream, met een waarschuwing vooraf.
#
# Gebruik:
#   bmm/update-upstream.sh            kijken wat er zou gebeuren, niets wijzigen
#   bmm/update-upstream.sh --rebase   echt rebasen
#
# Waarom een script en geen losse commando's: het enige dat je vóór een rebase
# wilt weten is of upstream aan ONZE twee aanrakingsvlakken heeft gezeten. Zo
# niet, dan is de rebase per definitie triviaal en kun je hem blind doen. Zo wel,
# dan wil je die diff eerst lezen. Dat onderscheid is precies wat je vergeet als
# je het met de hand doet.
set -euo pipefail

# De enige bestaande upstream-bestanden die onze patch aanraakt. Alles wat we
# toevoegen staat in nieuwe bestanden, en die kunnen niet conflicteren. Verandert
# deze lijst, werk hem hier bij: dit script is dan de enige plek die het weet.
RAAKVLAKKEN=(
  # gestukte upload
  "apps/media_library/urls.py"
  "templates/media_library/library_index.html"
  # instagram-plaatsingen
  "apps/composer/views.py"
  "templates/composer/compose.html"
  "providers/instagram.py"
  "providers/instagram_login.py"
  # reacties per post
  "apps/inbox/views.py"
  "templates/inbox/feed.html"
  "templates/inbox/partials/_filter_bar.html"
  "templates/inbox/partials/_message_row.html"
  "templates/inbox/partials/_message_panel.html"
  "templates/inbox/partials/_empty_state.html"
)

# Bestanden waar onze code in leeft. Niet conflictgevoelig, maar wel de plekken
# waar upstream ons kan inhalen (bijvoorbeeld door zelf multipart te bouwen, of
# door de storage-helpers te herschrijven die wij importeren).
LEUNT_OP=(
  "apps/media_library/storage.py"
  "apps/media_library/services.py"
  "apps/media_library/models.py"
  # De plaatsingskeuze werkt alleen doordat _resolve_post_type de hint uit
  # platform_extra leest en doordat de engine platform_extra doorgeeft als
  # content.extra. Herschrijft upstream dat, dan staat onze keuze er nog wel
  # maar doet hij niets, en dat merk je niet aan een conflict.
  "apps/publisher/engine.py"
  # De coverkiezer hergebruikt de frame-picker van TikTok, inclusief de
  # Alpine-sleutel video_cover_timestamp_ms.
  "templates/composer/partials/choose_from_video_card.html"
  # De waarschuwingen meten de aangehangen media uit de DOM: elke thumbnail is
  # een .media-thumb in #media-list met daarin een <video> of een <img>.
  # Verandert die opbouw, dan meet static/js/instagram-specs.js stilletjes niets
  # meer en verdwijnen de waarschuwingen zonder foutmelding.
  "templates/composer/partials/media_list.html"
  "templates/composer/partials/media_list_pending.html"
  # Datzelfde script registreert een Alpine-store en moet dus vóór Alpine
  # draaien. Dat klopt alleen zolang base.html Alpine met `defer` laadt.
  "templates/base.html"
  # De postverwijzing leeft van twee dingen die upstream vult: de sleutels in
  # InboxMessage.extra (stored_post_id, post_id, post_permalink_url) en het
  # opzoeken van related_post. Verdwijnt of hernoemt een van die sleutels, dan
  # blijft de chip staan maar wijst hij nergens meer heen, zonder foutmelding.
  "apps/inbox/tasks.py"
  "apps/inbox/webhooks.py"
  "providers/facebook.py"
  "providers/meta_comments.py"
)

REBASE=0
[ "${1:-}" = "--rebase" ] && REBASE=1

if [ -n "$(git status --porcelain)" ]; then
  echo "FOUT: werkboom niet schoon. Commit of stash eerst." >&2
  exit 1
fi

# Een rebase schrijft commits, dus zonder ingestelde identiteit valt hij halverwege
# om. Hier vooraf controleren, want anders lijkt dat op een conflict en dat is een
# heel ander probleem. Deze fork heeft geen identiteit uit de repo geërfd.
if ! git config user.email >/dev/null 2>&1; then
  echo "FOUT: geen git-identiteit in deze checkout. Zet hem eenmalig:" >&2
  echo '  git config user.name "Blue Monkey Media"' >&2
  echo '  git config user.email "dev@bluemonkeymedia.nl"' >&2
  exit 1
fi

TAK="$(git branch --show-current)"
echo "Tak: ${TAK}"

git fetch --quiet upstream
HUIDIG="$(git merge-base HEAD upstream/main)"
NIEUW="$(git rev-parse upstream/main)"

if [ "${HUIDIG}" = "${NIEUW}" ]; then
  echo "Al bij: upstream/main is ${NIEUW:0:9} en daar zitten we op."
  exit 0
fi

AANTAL="$(git rev-list --count "${HUIDIG}..${NIEUW}")"
echo "Upstream loopt ${AANTAL} commits voor (${HUIDIG:0:9} -> ${NIEUW:0:9})."
echo

spannend=0
echo "== Onze aanrakingsvlakken =="
for f in "${RAAKVLAKKEN[@]}"; do
  n="$(git rev-list --count "${HUIDIG}..${NIEUW}" -- "${f}")"
  if [ "${n}" -gt 0 ]; then
    echo "  GEWIJZIGD (${n}x)  ${f}"
    spannend=1
  else
    echo "  ongemoeid         ${f}"
  fi
done

echo
echo "== Waar we op leunen =="
for f in "${LEUNT_OP[@]}"; do
  n="$(git rev-list --count "${HUIDIG}..${NIEUW}" -- "${f}")"
  if [ "${n}" -gt 0 ]; then
    echo "  GEWIJZIGD (${n}x)  ${f}   <- lees deze diff, ook zonder conflict"
  else
    echo "  ongemoeid         ${f}"
  fi
done

echo
if [ "${spannend}" -eq 0 ]; then
  echo "Geen van onze aanrakingsvlakken is aangeraakt: de rebase is triviaal."
else
  echo "Let op: upstream zat aan een bestand dat wij ook wijzigen."
  echo "Bekijk eerst:  git diff ${HUIDIG}..${NIEUW} -- ${RAAKVLAKKEN[*]}"
fi

# Heeft upstream zelf multipart gebouwd? Dan kan onze patch eruit en dat is beter
# nieuws dan een geslaagde rebase.
if git log "${HUIDIG}..${NIEUW}" --oneline | grep -iE "multipart|chunked|resumable" ; then
  echo
  echo "^ Upstream noemt multipart of chunked in een commit. Kijk of onze patch"
  echo "  overbodig is geworden; hem weggooien is altijd beter dan hem dragen."
fi

if [ "${REBASE}" -eq 0 ]; then
  echo
  echo "Niets gewijzigd. Voer uit met --rebase om echt te rebasen."
  exit 0
fi

echo
echo "Rebasen op upstream/main..."
if ! git rebase "${NIEUW}"; then
  echo >&2
  # Onderscheid maken tussen een conflict en iets anders. Een mislukte rebase
  # kan ook een ontbrekende hook, een volle schijf of een verkeerde ref zijn, en
  # dat alles "CONFLICT" noemen stuurt je de verkeerde kant op.
  if [ -d "$(git rev-parse --git-path rebase-merge)" ] ||
     [ -d "$(git rev-parse --git-path rebase-apply)" ]; then
    echo "CONFLICT. Niet forceren en niet doorduwen: los het op of breek af met" >&2
    echo "  git rebase --abort" >&2
    echo "Bij twijfel afbreken; een verkeerd opgeloste rebase in een uploadpad" >&2
    echo "kost meer dan een week wachten." >&2
  else
    echo "De rebase is niet eens begonnen; dit is geen conflict." >&2
    echo "Lees de fout hierboven; de werkboom staat nog zoals hij stond." >&2
  fi
  exit 1
fi

echo
echo "Rebase gelukt. Nu nog, en dit is geen formaliteit:"
echo "  1. tests: docker compose run --rm app pytest apps/media_library \\"
echo "              apps/composer/tests/test_instagram_extras.py \\"
echo "              apps/composer/tests/test_instagram_specs.py \\"
echo "              tests/providers/test_instagram_placement.py"
echo "  2. tag:   git tag -a bmm-\$(date +%Y.%m.%d) -m \"upstream ${NIEUW}\""
echo "  3. push:  git push --force-with-lease origin ${TAK} && git push origin --tags"
echo
echo "Die tag is niet optioneel: upstream geeft zelf geen releases uit, dus onze"
echo "tag is het enige waar de deploy op kan pinnen."
