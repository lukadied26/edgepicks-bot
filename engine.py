import csv
import difflib
import io
import math
import re
import statistics
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

ODDS_BASE = "https://api.the-odds-api.com/v4"
STATS_BASE = "https://www.football-data.co.uk/mmz4281"
APIF_BASE = "https://v3.football.api-sports.io"

TOP5 = ["epl", "laliga", "bundesliga", "seriea", "ligue1"]
CUPS = ["ucl", "uel", "uecl", "carabao", "facup", "dfbpokal", "copadelrey", "coppaitalia", "coupe"]

# "fd"   = football-data.co.uk file code (full stats model: goals, corners, cards)
# "apif" = API-Football league id (team news + referee)
COMPETITIONS = {
    "epl": {"name": "Premier League", "keys": ["soccer_epl"], "fd": "E0", "apif": 39},
    "laliga": {"name": "La Liga", "keys": ["soccer_spain_la_liga"], "fd": "SP1", "apif": 140},
    "bundesliga": {"name": "Bundesliga", "keys": ["soccer_germany_bundesliga"], "fd": "D1", "apif": 78},
    "seriea": {"name": "Serie A", "keys": ["soccer_italy_serie_a"], "fd": "I1", "apif": 135},
    "ligue1": {"name": "Ligue 1", "keys": ["soccer_france_ligue_one"], "fd": "F1", "apif": 61},
    "ucl": {"name": "Champions League", "keys": ["soccer_uefa_champs_league"], "apif": 2},
    "uel": {"name": "Europa League", "keys": ["soccer_uefa_europa_league"], "apif": 3},
    "uecl": {
        "name": "Conference League", "tokens": ("conference",),
        "fallback": ["soccer_uefa_europa_conference_league"], "apif": 848,
    },
    "mls": {"name": "MLS", "keys": ["soccer_usa_mls"], "apif": 253, "calendar_season": True},
    "saudi": {
        "name": "Saudi Pro League", "tokens": ("saudi",),
        "fallback": ["soccer_saudi_arabia_pro_league"], "apif": 307,
    },
    "carabao": {"name": "Carabao Cup", "keys": ["soccer_england_efl_cup"]},
    "facup": {"name": "FA Cup", "keys": ["soccer_fa_cup"]},
    "dfbpokal": {"name": "DFB-Pokal", "keys": ["soccer_germany_dfb_pokal"]},
    "copadelrey": {"name": "Copa del Rey", "keys": ["soccer_spain_copa_del_rey"]},
    "coppaitalia": {"name": "Coppa Italia", "keys": ["soccer_italy_coppa_italia"]},
    "coupe": {"name": "Coupe de France", "keys": ["soccer_france_coupe_de_france"]},
    "internationals": {
        "name": "International matches",
        "tokens": ("world_cup", "nations_league", "euro", "copa", "afcon", "africa", "friendl",
                   "international", "qualif", "gold_cup", "asian"),
        "exclude": ("europa", "champs", "club", "women", "u21", "u19", "conference",
                    "libertadores", "sudamericana", "champions_cup", "del_rey", "winner"),
        "fallback": [],
    },
}

# Words removed from team names before matching
_DROP = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "rc", "rcd", "ud",
    "cd", "ca", "sv", "vfb", "vfl", "fsv", "tsg", "fk", "osc", "ogc", "bc",
    "sco", "1", "real", "olympique", "stade", "borussia",
}

# normalised name from the odds feed -> normalised name in the stats files
ALIASES = {
    "manchester united": "man united",
    "manchester city": "man city",
    "nottingham forest": "nottm forest",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "brighton and hove albion": "brighton",
    "atletico madrid": "ath madrid",
    "athletic bilbao": "ath bilbao",
    "athletic club": "ath bilbao",
    "celta vigo": "celta",
    "rayo vallecano": "vallecano",
    "deportivo alaves": "alaves",
    "monchengladbach": "mgladbach",
    "eintracht frankfurt": "ein frankfurt",
    "bayer leverkusen": "leverkusen",
    "hamburger sv": "hamburg",
    "inter milan": "inter",
    "internazionale": "inter",
    "paris saint germain": "paris sg",
    "lyonnais": "lyon",
    "rennais": "rennes",
    "brestois": "brest",
    "saint etienne": "st etienne",
}

SHRINK_K = 5.0        # pulls small samples toward the league average
REF_K = 6.0           # same idea for referees
APIF_DAILY_CAP = 90   # stay under the free 100 requests a day
CREDIT_FLOOR = 40     # stop spending Odds API credits below this many left


# ------------------------------------------------------------------ helpers
_CACHE = {}
_LOCKS = {}
_G = threading.Lock()

def _cached(key, ttl, fn):
    """Return a saved answer if fresh, otherwise rebuild it once (others wait)."""
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    with _G:
        lock = _LOCKS.setdefault(key, threading.Lock())
    with lock:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            val = fn()
        except Exception:
            if hit:
                return hit[1]  # serve the last good copy if the source is down
            raise
        _CACHE[key] = (time.time(), val)
        return val


def _norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return " ".join(t for t in s.split() if t not in _DROP)


def _match_name(name, candidates):
    norm_map = {_norm(c): c for c in candidates}
    n = _norm(name)
    if n in norm_map:
        return norm_map[n]
    alias = ALIASES.get(n)
    if alias in norm_map:
        return norm_map[alias]
    toks = set(n.split())
    cands = [orig for k, orig in norm_map.items()
             if k.split() and (set(k.split()) <= toks or toks <= set(k.split()))]
    if len(cands) == 1:
        return cands[0]
    close = difflib.get_close_matches(n, list(norm_map), n=1, cutoff=0.72)
    return norm_map[close[0]] if close else None


def _ref_key(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s.split(",")[0])
    parts = s.split()
    return f"{parts[0][0]} {parts[-1]}" if parts else ""


def _parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _season_codes(now):
    y = now.year % 100
    start = y if now.month >= 7 else y - 1
    return f"{start:02d}{start + 1:02d}", f"{start - 1:02d}{start:02d}"


def _pmf(mu, kmax):
    p = math.exp(-mu)
    out = [p]
    for k in range(1, kmax + 1):
        p *= mu / k
        out.append(p)
    return out


def _lean_total(mu):
    """Nearest half-line to the expected total, and the more likely side."""
    line = math.floor(mu) + 0.5
    p_over = 1.0 - sum(_pmf(mu, int(line)))
    if p_over >= 0.5:
        return {"mu": mu, "line": line, "side": "Over", "prob": p_over}
    return {"mu": mu, "line": line, "side": "Under", "prob": 1.0 - p_over}


def _p1x2(mu_h, mu_a):
    ph, pa = _pmf(mu_h, 10), _pmf(mu_a, 10)
    mass = sum(ph) * sum(pa)
    h = d = a = 0.0
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x * y / mass
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    return h, d, a


def _summarize(mu_h, mu_a):
    ph, pa = _pmf(mu_h, 10), _pmf(mu_a, 10)
    mass = sum(ph) * sum(pa)
    p_home = p_draw = p_away = btts = 0.0
    over = {1.5: 0.0, 2.5: 0.0, 3.5: 0.0}
    scores = []
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x * y / mass
            scores.append((f"{i}-{j}", p))
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i > 0 and j > 0:
                btts += p
            for line in over:
                if i + j > line:
                    over[line] += p
    scores.sort(key=lambda s: -s[1])
    return {
        "xg": (mu_h, mu_a),
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "top_scores": scores[:3], "over": over, "btts": btts,
    }

# --------------------------------------------------------------- Odds API
def _odds_get(path, odds_key, **params):
    r = requests.get(f"{ODDS_BASE}{path}", params={"apiKey": odds_key, **params}, timeout=20)
    left = r.headers.get("x-requests-remaining") if getattr(r, "headers", None) else None
    if left is not None:
        try:
            EdgePicksEngine.credits_left = float(left)
        except ValueError:
            pass
    if r.status_code != 200:
        raise RuntimeError(f"Odds API error {r.status_code}: {r.text[:120]}")
    return r.json()


def _discover(odds_key):
    """Active soccer sport keys from the free sports list."""
    return _cached("sports", 6 * 3600, lambda: [
        s["key"] for s in _odds_get("/sports", odds_key) if s.get("key", "").startswith("soccer_")])


def _keys_for(comp_id, odds_key):
    comp = COMPETITIONS[comp_id]
    if comp.get("keys"):
        return comp["keys"]
    try:
        found = _discover(odds_key)
    except Exception as e:
        print(f"[discover] {e}")
        found = []
    keys = [k for k in found if any(t in k for t in comp["tokens"])
            and not any(x in k for x in comp.get("exclude", ()))]
    return keys or list(comp.get("fallback", []))


def _events_for_key(odds_key, sport_key):
    def fetch():
        now = datetime.now(timezone.utc)
        data = _odds_get(f"/sports/{sport_key}/events", odds_key, dateFormat="iso",
                         commenceTimeFrom=_iso(now), commenceTimeTo=_iso(now + timedelta(hours=120)))
        out = []
        for ev in data:
            try:
                out.append({"id": ev["id"], "sport_key": sport_key, "home": ev["home_team"],
                            "away": ev["away_team"], "when": _parse_time(ev["commence_time"])})
            except (KeyError, ValueError):
                continue
        return out
    return _cached(f"ev:{sport_key}", 3600, fetch)


def _consensus(ev):
    """Average the bookmakers' prices (margin removed) into probabilities."""
    home, away = ev["home_team"], ev["away_team"]
    h2h, tot = [], {}
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk["key"] == "h2h":
                pr = {o["name"]: o["price"] for o in mk["outcomes"] if o.get("price")}
                if home in pr and away in pr and "Draw" in pr:
                    inv = {k: 1.0 / pr[k] for k in (home, "Draw", away)}
                    s = sum(inv.values())
                    h2h.append((inv[home] / s, inv["Draw"] / s, inv[away] / s))
            elif mk["key"] == "totals":
                by_pt = {}
                for o in mk["outcomes"]:
                    if "point" in o and o.get("price"):
                        by_pt.setdefault(o["point"], {})[o["name"]] = 1.0 / o["price"]
                for pt, d in by_pt.items():
                    if "Over" in d and "Under" in d:
                        tot.setdefault(pt, []).append(d["Over"] / (d["Over"] + d["Under"]))
    if not h2h:
        return None
    ph = statistics.mean(x[0] for x in h2h)
    pd = statistics.mean(x[1] for x in h2h)
    pa = statistics.mean(x[2] for x in h2h)
    s = ph + pd + pa
    line = p_over = None
    if tot:
        line = max(tot, key=lambda p: (len(tot[p]), -abs(p - 2.5)))
        p_over = statistics.mean(tot[line])
    return {"ph": ph / s, "pd": pd / s, "pa": pa / s, "line": line, "p_over": p_over}


def _fit_lambdas(c):
    """Find goal rates whose Poisson result matches the bookmakers' prices."""
    mu = 2.6
    if c["line"] is not None:
        lo, hi = 0.3, 7.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if 1.0 - sum(_pmf(mid, int(math.floor(c["line"])))) < c["p_over"]:
                lo = mid
            else:
                hi = mid
        mu = (lo + hi) / 2
    best, best_err = (mu / 2, mu / 2), 9.0
    for i in range(5, 96):
        f = i / 100.0
        h, d, a = _p1x2(mu * f, mu * (1 - f))
        err = (h - c["ph"]) ** 2 + (d - c["pd"]) ** 2 + (a - c["pa"]) ** 2
        if err < best_err:
            best, best_err = (mu * f, mu * (1 - f)), err
    return best


# ------------------------------------------------------------- API-Football
_BUD = {"day": None, "used": 0, "disabled_until": 0.0}
_BUD_LOCK = threading.Lock()


def _apif_get(path, apif_key, **params):
    """One API-Football request with a daily cap. Returns None if skipped or failed."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _BUD_LOCK:
        if _BUD["day"] != today:
            _BUD.update(day=today, used=0)
        if _BUD["used"] >= APIF_DAILY_CAP or time.time() < _BUD["disabled_until"]:
            return None
        _BUD["used"] += 1
    try:
        r = requests.get(f"{APIF_BASE}{path}", headers={"x-apisports-key": apif_key},
                         params=params, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"[API-Football] {path} failed: {e}")
        return None
    if r.status_code != 200 or data.get("errors"):
        print(f"[API-Football] {path} {params} -> {r.status_code} {data.get('errors')}")
        _BUD["disabled_until"] = time.time() + 3600  # don't waste requests on a plan/season error
        return None
    return data.get("response", [])


def _apif_season(comp, when):
    if comp.get("calendar_season"):
        return when.year
    return when.year if when.month >= 7 else when.year - 1
