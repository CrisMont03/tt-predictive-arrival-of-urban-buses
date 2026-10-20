"""Conservative reader of the official CAMe press archive; never executes JS."""
from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

CAME_URL = "https://www.gob.mx/comisionambiental/archivo/prensa?idiom=es-MX"
LOCAL_ZONE = ZoneInfo("America/Mexico_City")


def normalize(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.lower())
                            if not unicodedata.combining(c)).split())


class PressArchive(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.articles = []
        self.current = None
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            self.current = {"title": "", "published_at": None, "url": None}
        if self.current is None:
            return
        if tag == "h2":
            self.in_heading = True
        elif tag == "time":
            self.current["published_at"] = attrs.get("datetime")
        elif tag == "a" and attrs.get("href", "").startswith("/comisionambiental/prensa/"):
            self.current["url"] = urljoin(CAME_URL, attrs["href"])

    def handle_data(self, data):
        if self.current is not None and self.in_heading:
            self.current["title"] += data

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_heading = False
        elif tag == "article" and self.current is not None:
            self.articles.append(self.current)
            self.current = None
            self.in_heading = False


def parse_archive(payload):
    # The official endpoint may return HTML or jQuery append statements.
    # Decode string literals only. Never eval or execute remote JavaScript.
    parser = PressArchive()
    if payload.lstrip().startswith("<"):
        parser.feed(payload)
    else:
        fragments = re.findall(r'\$\("#prensa"\)\.append\(("(?:\\.|[^"\\])*")\);', payload)
        if not fragments:
            raise ValueError("unrecognized_archive")
        for fragment in fragments:
            parser.feed(json.loads(fragment))
    if not parser.articles:
        raise ValueError("empty_archive")
    return parser.articles


def environmental_state(payload, now, old_notice_hours=24):
    """Return (state or None, provenance). Scope is the whole ZMVM.

    Carry the last explicit declaration, even if old, as requested by the project.
    Provenance distinguishes the last published state from a live confirmation.
    """
    relevant = []
    for article in parse_archive(payload):
        title = normalize(article["title"])
        if not re.search(r"\bcontingencias? ambiental(?:es)?\b", title):
            continue
        if not article["published_at"] or not article["url"]:
            return None, {"status": "unavailable", "reason": "undated_notice"}
        try:
            published = datetime.fromisoformat(article["published_at"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=LOCAL_ZONE)
        except ValueError:
            return None, {"status": "unavailable", "reason": "invalid_notice_date"}
        relevant.append((published, title, article))
    if not relevant:
        return None, {"status": "unavailable", "reason": "no_relevant_notice"}
    latest_date = max(item[0] for item in relevant)
    latest = [item for item in relevant if item[0] == latest_date]
    if len({item[1] for item in latest}) != 1:
        return None, {"status": "unavailable", "reason": "conflicting_notices"}
    published, title, article = latest[0]
    evidence = {"source_url": article["url"], "published_at": published.isoformat(),
                "title": " ".join(article["title"].split()), "scope": "zmvm",
                "state_basis": "latest_recognized_official_notice"}
    if published > now:
        return None, {**evidence, "status": "unavailable", "reason": "future_notice"}
    evidence.update({"notice_age_hours": round((now - published).total_seconds() / 3600, 2),
                     "notice_is_old": now - published > timedelta(hours=old_notice_hours)})
    if "regional" in title or "zona metropolitana del valle de mexico" not in title:
        return None, {**evidence, "status": "unavailable", "reason": "uncertain_geographic_scope"}
    if "parcial" in title or ("suspende" in title and "mantiene" in title):
        return None, {**evidence, "status": "unavailable", "reason": "ambiguous_notice"}
    if title.startswith(("se suspende la contingencia ambiental", "se suspenden las contingencias ambientales")):
        state = 0
    elif re.match(r"^(?:se activa|se mantiene|continua|se continua) (?:la )?"
                  r"(?:fase (?:i|ii|1|2) de )?(?:la )?contingencia ambiental\b", title):
        state = 1
    else:
        return None, {**evidence, "status": "unavailable", "reason": "unrecognized_notice"}
    if state == 0:
        def pollutants(text):
            return {name for name, pattern in (
                ("ozone", r"\bozono\b"), ("pm10", r"\bpm\s*10\b"),
                ("pm25", r"\bpm\s*2[.,]5\b"),
            ) if re.search(pattern, text)}
        current_pollutants = pollutants(title)
        if any(now - date <= timedelta(hours=24) and
               pollutants(other_title) != current_pollutants
               for date, other_title, _ in relevant):
            return None, {**evidence, "status": "unavailable", "reason": "multiple_pollutant_episodes"}
    # Cache freshness is based on retrieval time in ContextService, not on the
    # bulletin's publication date. Its age is informational, not an invalidation.
    return state, {**evidence, "status": "available", "reason": None,
                   "warning": "old_notice_carried_forward" if evidence["notice_is_old"] else None}
