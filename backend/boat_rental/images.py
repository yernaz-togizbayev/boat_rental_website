"""Photographs for the harbour cards, the boat plates and the hero band.

Four sources, tried in order: a hand-picked Unsplash photo, a live Unsplash
search, the Wikipedia lead image, then a generic harbour from a small pool. The
hand-picked ones exist because a search for a small town tends to return a flag
or a map rather than a coastline.

Everything is hotlinked. IMAGE_FETCH=off skips the two live lookups, which
block the request; the hand-picked photos and the generic pool still render,
since only the visitor's browser ever fetches those.
"""

import json
import os
import random
import re
import urllib.parse
import urllib.request
import zlib

from flask import current_app

USER_AGENT = "IMSE-Group05-BoatRental/1.0 (university project; contact via repo)"

TIMEOUT_SECONDS = 3

UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def _unsplash(photo_id, width):
    """Hotlinked Unsplash CDN URL. width is the CSS slot at 2x, for retina."""
    return f"https://images.unsplash.com/photo-{photo_id}?w={width}&q=75&auto=format"


# Unsplash asks that a displayed photo credit its photographer with a link back
# to their profile, carrying these parameters so the referral is attributed.
# Their guidelines also say the download endpoint is only for a user actually
# taking a photo, such as setting a wallpaper or dropping it into a document.
# Hotlinking one for display does not count, so nothing here calls it.
UNSPLASH_UTM = "utm_source=imse_boat_rental&utm_medium=referral"
UNSPLASH_HOME = f"https://unsplash.com/?{UNSPLASH_UTM}"

_PHOTO_ID = re.compile(r"photo-([^?/]+)")

# Photographers, keyed by the id in the CDN URL above.
#
# Worth knowing if you ever add more: there is no API route from a CDN filename
# back to its photo. /photos/<id> wants Unsplash's short id and 404s on this
# one. Each of these was found instead by searching for the photo's subject and
# matching the filename against urls.raw in the results, which only works while
# the photo still ranks for some query. The ones no search turned up are left
# out rather than guessed at, and the credits page says so. Photos fetched live
# through the API register themselves below, so those are never uncredited.
PHOTO_CREDITS = {
    "1567899378494-47b22a2ae96a": ("Marcin Ciszewski", "collega"),
    "1581272281570-61907217b302": ("Miquel Gelabert", "miquelgd"),
    "1584212893031-410e387fbaf1": ("Rusty Watson", "rustyct1"),
    "1601581875309-fafbf2d3ed3a": ("Johnny Africa", "johnnyafrica"),
    "1529551739587-e242c564f727": ("Benjamín Gremler", "benjagremler"),
    "1580502304784-8985b7eb7260": ("James Ting", "jamesting"),
    "1414862625453-d87604a607e4": ("Ivan Ivankovic", "fjaka"),
    "1459679749680-18eb1eb37418": ("Javier M.", "jmelpri"),
    "1517670660212-61ed0b4fe8f9": ("Oscar Nord", "oscnord"),
    "1674333362725-84e9996aa6fb": ("Sofia Vila Flor", "sofiavilaflor"),
    "1758136692793-65ac5a91e06e": ("Zachary Moneypenny", "canon_guy84"),
    "1517696522815-46a004b80a2d": ("Val Vesa", "adspedia"),
    "1528580279421-f0b84f9d7640": ("Vidar Nordli-Mathisen", "vidarnm"),
}


def _profile(username):
    return f"https://unsplash.com/@{username}?{UNSPLASH_UTM}"


def credit_for(url):
    """{name, profile} for a photo whose photographer we know, else None."""
    match = _PHOTO_ID.search(url or "")
    who = PHOTO_CREDITS.get(match.group(1)) if match else None
    return {"name": who[0], "profile": _profile(who[1])} if who else None


def _remember_credit(photo):
    """Record the photographer of a photo the API just handed us."""
    user = photo.get("user") or {}
    match = _PHOTO_ID.search((photo.get("urls") or {}).get("raw", ""))
    if match and user.get("name") and user.get("username"):
        PHOTO_CREDITS[match.group(1)] = (user["name"], user["username"])


def photo_credits():
    """Every photographer we can name, once each, alphabetically."""
    unique = {name: username for name, username in PHOTO_CREDITS.values()}
    return [{"name": name, "profile": _profile(username)}
            for name, username in sorted(unique.items())]


BOAT_TYPE_IMAGES = {
    # Marcin Ciszewski, superyacht at anchor off a wooded shore
    "yacht": _unsplash("1567899378494-47b22a2ae96a", 780),
    # white cruising catamaran, both hulls broadside in turquoise water
    "catamaran": _unsplash("1581272281570-61907217b302", 780),
    # red sports cruiser under way, throwing spray
    "motorboat": _unsplash("1584212893031-410e387fbaf1", 780),
}

# Cards for the harbours we have a photo of. The card is small, 4:3 and darkened
# under a scrim, so these are wide coastal views rather than close-ups.
CITY_IMAGES = {
    "Mykonos": _unsplash("1601581875309-fafbf2d3ed3a", 600),    # Little Venice
    "Nice": _unsplash("1503696967350-ad1874122058", 600),       # Baie des Anges
    "Barcelona": _unsplash("1529551739587-e242c564f727", 600),  # Barceloneta
    "Santorini": _unsplash("1580502304784-8985b7eb7260", 600),  # Oia, blue hour
    "Dubrovnik": _unsplash("1414862625453-d87604a607e4", 600),  # old town harbour
    "Amsterdam": _unsplash("1459679749680-18eb1eb37418", 600),  # Damrak canal
    "Copenhagen": _unsplash("1550682837-891ae070f347", 600),    # Nyhavn
    "Hamburg": _unsplash("1565167808479-08dcdbb1d6d7", 600),    # container port
    "Stockholm": _unsplash("1667060306007-0e28bee8b8a9", 600),  # Riddarholmen
    "Mallorca": _unsplash("1517670660212-61ed0b4fe8f9", 600),   # north-coast cove
    "Madeira": _unsplash("1674333362725-84e9996aa6fb", 600),    # Funchal bay
    "San Francisco": _unsplash("1758136692793-65ac5a91e06e", 600),  # the bay
    "Tokyo": _unsplash("1628856860829-5daf4950cd63", 600),      # Rainbow Bridge
    "Sant Antoni": _unsplash("1547668932-54be495edf50", 600),   # Ibiza moorings
}

# Last resort, when both lookups come back empty. A pool rather than a single
# default, which made every unlisted harbour look like the same place. These
# four are deliberately unalike and show no recognisable landmark.
GENERIC_CITY_IMAGES = [
    _unsplash("1517696522815-46a004b80a2d", 600),  # aerial yacht basin
    _unsplash("1528580279421-f0b84f9d7640", 600),  # sailboats off a quay
    _unsplash("1703728843356-515de7a3b134", 600),  # masts at sunset
    _unsplash("1549045188-ea585b396e29", 600),     # pontoon rows
]


def _generic_city_image(city):
    """A stable pool pick for a city we have no photo of.

    crc32, not hash(): str hashing is salted per process, so the built-in would
    hand the same city a different harbour on every worker and every restart.
    """
    return GENERIC_CITY_IMAGES[zlib.crc32(city.encode()) % len(GENERIC_CITY_IMAGES)]


# A place article's lead image is very often a flag, a locator map or a coat of
# arms rather than a photograph. "Mallorca" resolves to Flag_of_Mallorca.svg.
# Matched with separators around them so a real photo whose filename happens to
# contain one of these words survives.
_NOT_A_PHOTO = re.compile(
    r"(^|[_\-.%28 ])("
    r"flag|flags|map|maps|locator|location|borders|outline|topographic|"
    r"coat[_\- ]of[_\- ]arms|blason|escudo|seal|logo|emblem|nomos|dimos|"
    r"banner|arms"
    r")([_\-.%29 ]|$)",
    re.IGNORECASE,
)

# Anything squarer than this crops to a sliver in the 4:3 card.
CARD_MIN_ASPECT = 1.3

# city -> url or None, for the life of this process. Both lookups are slow and
# a demo Unsplash key allows 50 requests an hour, so misses are cached as firmly
# as hits. Otherwise every page load asks again about the same few cities.
_RESOLVED = {}


def _looks_like_a_photo(url):
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    # A raster rendered from an SVG is a diagram by definition.
    if ".svg." in name.lower():
        return False
    return not _NOT_A_PHOTO.search(name)


def _search_unsplash(city, width):
    """Top landscape Unsplash hit for a city, or None. Needs UNSPLASH_ACCESS_KEY."""
    key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not key:
        return None

    query = urllib.parse.urlencode({
        "query": city,
        "orientation": "landscape",
        "content_filter": "high",
        "per_page": 1,
    })
    request = urllib.request.Request(
        f"{UNSPLASH_SEARCH}?{query}",
        headers={"User-Agent": USER_AGENT,
                 "Accept-Version": "v1",
                 "Authorization": f"Client-ID {key}"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        results = json.load(response).get("results") or []

    if not results:
        return None
    # Credit it before returning. This is the one path where we know the
    # photographer for certain, and that is lost once the response goes.
    _remember_credit(results[0])
    # urls.raw carries its own query string, so our sizing params get appended.
    return f"{results[0]['urls']['raw']}&w={width}&q=75&auto=format&fit=crop"


def _search_wikipedia(cities, width):
    """city -> lead-image URL, for the cities Wikipedia could resolve.

    One request for the whole batch. The API hands back the thumbnail's real
    dimensions, so dropping portrait images costs nothing.
    """
    query = urllib.parse.urlencode({
        "action": "query",
        "format": "json",
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": width,
        "pilicense": "free",
        "redirects": 1,
        "titles": "|".join(dict.fromkeys(cities)),
    })
    request = urllib.request.Request(
        f"{WIKIPEDIA_API}?{query}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.load(response)

    by_title = {}
    for page in payload.get("query", {}).get("pages", {}).values():
        thumb = page.get("thumbnail") or {}
        source, w, h = thumb.get("source"), thumb.get("width", 0), thumb.get("height", 1)
        if not source or not _looks_like_a_photo(source):
            continue
        if (w / h if h else 0) < CARD_MIN_ASPECT:
            continue
        by_title[page["title"]] = source

    # A title we asked for may have been redirected before it was answered.
    for redirect in payload.get("query", {}).get("redirects", []):
        if redirect["to"] in by_title:
            by_title[redirect["from"]] = by_title[redirect["to"]]

    return {c: by_title[c] for c in cities if c in by_title}


def _resolve_unknown_cities(cities, width):
    """city -> url for cities not in CITY_IMAGES. Never raises; may return {}.

    Unsplash first because its photographs are the better ones, Wikipedia second
    because it needs no key and has an article for cities Unsplash has never
    heard of. Whatever neither finds falls through to the generic pool.
    """
    found, unresolved = {}, []
    for city in cities:
        if city in _RESOLVED:
            if _RESOLVED[city]:
                found[city] = _RESOLVED[city]
            continue
        try:
            url = _search_unsplash(city, width)
        except Exception:
            current_app.logger.warning("Unsplash lookup failed for %s", city, exc_info=True)
            url = None
        if url:
            _RESOLVED[city] = url
            found[city] = url
        else:
            unresolved.append(city)

    if unresolved:
        try:
            by_city = _search_wikipedia(unresolved, width)
        except Exception:
            current_app.logger.warning("Wikipedia lookup failed; using generic harbours",
                                       exc_info=True)
            by_city = {}
        for city in unresolved:
            _RESOLVED[city] = by_city.get(city)
        found.update(by_city)

    return found

# The hero pool: cruising grounds rather than generic yacht stock. All landscape
# and all wide. The hero band is roughly 5:2, so anything squarer crops to its
# middle, and anything with a big empty sky reads as a grey smear behind the
# scrim.
HERO_IMAGES = [
    (_unsplash("1549893072-4bc678117f45", 1100), "Portofino"),
    (_unsplash("1583844056361-4418a8f2a985", 1100), "Positano"),
    (_unsplash("1700549586671-6d5868866337", 1100), "Bay of Kotor"),
    (_unsplash("1627669867775-fce0b561dad4", 1100), "Saint-Tropez"),
    (_unsplash("1616234643798-12dc87da67b4", 1100), "Cinque Terre"),
    (_unsplash("1605703296515-515167084932", 1100), "Hvar"),
    (_unsplash("1533656338503-b22f63e96cd8", 1100), "Amalfi Coast"),
    (_unsplash("1696468330266-60f1a84f8d17", 1100), "Capri"),
    (_unsplash("1726255988977-06023f676792", 1100), "Bodrum"),
    (_unsplash("1571510168951-bc6189f2dfad", 1100), "Valletta"),
    (_unsplash("1718255632182-f6e7bc73d08a", 1100), "Monaco"),
    (_unsplash("1677182302564-ffc6bb3252cd", 1100), "Cannes"),
    (_unsplash("1687469044048-d920c7c1bc04", 1100), "Bonifacio"),
    (_unsplash("1612279427382-f8349a383af8", 1100), "Zakynthos"),
    (_unsplash("1591899183619-d6944d90ef41", 1100), "Corfu"),
    (_unsplash("1624138784614-87fd1b6528f8", 1100), "Sydney Harbour"),
    (_unsplash("1643029891412-92f9a81a8c16", 1100), "Ha Long Bay"),
    (_unsplash("1601439678777-b2b3c56fa627", 1100), "Geirangerfjord"),
    (_unsplash("1582150050076-52baeeba4a74", 1100), "Lake Como"),
    (_unsplash("1767045561413-dee34ebe9ade", 1100), "Palma de Mallorca"),
    (_unsplash("1728363265942-92c2202b6596", 1100), "Perast"),
    (_unsplash("1575540291670-8d3b26f7d327", 1100), "Split"),
]

# How many of the pool a single visit sees. The template loads them one at a
# time to crossfade, so this bounds transfer, not first paint.
HERO_SLIDE_COUNT = 22


def _enabled():
    return os.environ.get("IMAGE_FETCH", "on").lower() not in ("off", "0", "false")


def hero_slides():
    """A fresh random handful of [(url, place)] for the rotating hero.

    Sampled per render rather than fixed, so the pool is worth its size: a
    returning visitor sees a different coastline instead of the same six.
    Not gated by IMAGE_FETCH: these are hotlinks the browser fetches, so they
    cost the server nothing.
    """
    return random.sample(HERO_IMAGES, min(HERO_SLIDE_COUNT, len(HERO_IMAGES)))


def city_and_boat_images(cities, width=600):
    """Images for the harbour cards and the boat-type plates.

    Returns (city_url_by_city, image_url_by_boat_type). Every city gets a URL:
    the hand-picked one if we have it, else a looked-up photo of that city, else
    a generic harbour. IMAGE_FETCH=off skips only the lookups, the one part that
    blocks the request; the hotlinks are the browser's business, not ours.
    """
    unknown = [c for c in cities if c not in CITY_IMAGES]
    looked_up = (_resolve_unknown_cities(unknown, width)
                 if unknown and _enabled() else {})

    city_urls = {
        c: CITY_IMAGES.get(c) or looked_up.get(c) or _generic_city_image(c)
        for c in cities
    }
    return city_urls, dict(BOAT_TYPE_IMAGES)
