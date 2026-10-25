import csv
import difflib
import io
import math
import re
import statistics
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

ODDS_BASE = "https://api.the-odds-api.com/v4/sports"
STATS_BASE = "https://www.football-data.co.uk/mmz4281"

# key -> (Odds API sport key, football-data.co.uk code, display name)
LEAGUES = {
    "epl": ("soccer_epl", "E0", "Premier League"),
    "laliga": ("soccer_spain_la_liga", "SP1", "La Liga"),
    "bundesliga": ("soccer_germany_bundesliga", "D1", "Bundesliga"),
    "seriea": ("soccer_italy_serie_a", "I1", "Serie A"),
    "ligue1": ("soccer_france_ligue_one", "F1", "Ligue 1"),
}

# Words removed from team names before matching (Odds API vs football-data names)
_DROP = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "rc", "rcd", "ud",
    "cd", "ca", "sv", "vfb", "vfl", "fsv", "tsg", "fk", "osc", "ogc", "bc",
    "sco", "1", "real", "olympique", "stade", "borussia",
}

# Odds API name (normalised) -> football-data name (normalised)
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

SHRINK_K = 5.0  # pulls small samples toward the league average


def _norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return " ".join(t for t in s.split() if t not in _DROP)


def _season_codes(now):
    y = now.year % 100
    start = y if now.month >= 7 else y - 1
    cur = f"{start:02d}{start + 1:02d}"
    prev = f"{start - 1:02d}{start:02d}"
    return cur, prev


def _pmf(mu, kmax):
    p = math.exp(-mu)
    out = [p]
    for k in range(1, kmax + 1):
        p *= mu / k
        out.append(p)
    return out


def _lean_total(mu):
    """Nearest half-line to the expected total and the more likely side."""
    line = math.floor(mu) + 0.5
    cdf = sum(_pmf(mu, int(line)))
    p_over = 1.0 - cdf
    if p_over >= 0.5:
        return {"mu": mu, "line": line, "side": "Over", "prob": p_over}
    return {"mu": mu, "line": line, "side": "Under", "prob": 1.0 - p_over}


class EdgePicksEngine:
    _stats_cache = {}
    _lines_cache = {}

    # ------------------------------------------------------------ fixtures
    @classmethod
    def get_events(cls, api_key, sport_key, hours=24):
        """Upcoming events in the next `hours` hours. Raises RuntimeError on API errors."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=hours)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        params = {
            "apiKey": api_key,
            "dateFormat": "iso",
            "commenceTimeFrom": now.strftime(fmt),
            "commenceTimeTo": end.strftime(fmt),
        }
        r = requests.get(f"{ODDS_BASE}/{sport_key}/events", params=params, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"Odds API error {r.status_code}: {r.text[:120]}")
        out = []
        for ev in r.json():
            try:
                t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if not (now <= t <= end):
                continue
            out.append({
                "home": ev["home_team"],
                "away": ev["away_team"],
                "when": t,
                "time": t.strftime("%a %H:%M UTC"),
            })
        out.sort(key=lambda e: e["when"])
        return out

    # ------------------------------------------------------- football stats
    @classmethod
    def _download(cls, season, div, weight):
        url = f"{STATS_BASE}/{season}/{div}.csv"
        try:
            r = requests.get(url, timeout=25)
            if r.status_code != 200:
                print(f"Stats download {url} -> {r.status_code}")
                return []
            text = r.content.decode("utf-8-sig", errors="replace")
        except Exception as e:
            print(f"Stats download error {url}: {e}")
            return []
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                rows.append((
                    weight,
                    row["HomeTeam"].strip(), row["AwayTeam"].strip(),
                    (float(row["FTHG"]), float(row["HC"]), float(row["HY"]) + float(row["HR"])),
                    (float(row["FTAG"]), float(row["AC"]), float(row["AY"]) + float(row["AR"])),
                ))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        return rows

    @staticmethod
    def _aggregate(rows):
        teams = {}
        lg = {"home": [0.0, 0.0, 0.0], "away": [0.0, 0.0, 0.0]}
        lg_n = 0.0
        for w, h, a, hv, av in rows:
            for team, venue, mine, theirs in ((h, "home", hv, av), (a, "away", av, hv)):
                rec = teams.setdefault(team, {
                    "home": {"n": 0.0, "f": [0.0] * 3, "a": [0.0] * 3},
                    "away": {"n": 0.0, "f": [0.0] * 3, "a": [0.0] * 3},
                })[venue]
                rec["n"] += w
                for i in range(3):
                    rec["f"][i] += w * mine[i]
                    rec["a"][i] += w * theirs[i]
            lg_n += w
            for i in range(3):
                lg["home"][i] += w * hv[i]
                lg["away"][i] += w * av[i]
        return {
            "teams": teams,
            "lg_home": [max(x / lg_n, 1e-6) for x in lg["home"]],
            "lg_away": [max(x / lg_n, 1e-6) for x in lg["away"]],
            "norm_map": {_norm(t): t for t in teams},
        }

    @classmethod
    def _load_league(cls, league_key):
        cached = cls._stats_cache.get(league_key)
        if cached and time.time() - cached[0] < 6 * 3600:
            return cached[1]
        div = LEAGUES[league_key][1]
        cur, prev = _season_codes(datetime.now(timezone.utc))
        rows = cls._download(cur, div, 1.0) + cls._download(prev, div, 0.5)
        if not rows:
            return cached[1] if cached else None
        stats = cls._aggregate(rows)
        cls._stats_cache[league_key] = (time.time(), stats)
        return stats

    @staticmethod
    def _resolve(stats, name):
        norm = _norm(name)
        names = stats["norm_map"]
        if norm in names:
            return names[norm]
        alias = ALIASES.get(norm)
        if alias in names:
            return names[alias]
        tokens = set(norm.split())
        cands = [orig for n, orig in names.items()
                 if n.split() and (set(n.split()) <= tokens or tokens <= set(n.split()))]
        if len(cands) == 1:
            return cands[0]
        close = difflib.get_close_matches(norm, list(names), n=1, cutoff=0.72)
        if close:
            return names[close[0]]
        print(f"[team match] no match for '{name}'")
        return None

    @staticmethod
    def _expect(stats, home, away, i):
        lg_h, lg_a = stats["lg_home"][i], stats["lg_away"][i]
        H = stats["teams"].get(home)
        A = stats["teams"].get(away)

        def shrunk(rec, key, prior):
            if not rec or rec["n"] <= 0:
                return prior, 0.0
            return (rec[key][i] + prior * SHRINK_K) / (rec["n"] + SHRINK_K), rec["n"]

        hf, n1 = shrunk(H["home"] if H else None, "f", lg_h)
        aa, n2 = shrunk(A["away"] if A else None, "a", lg_h)
        af, n3 = shrunk(A["away"] if A else None, "f", lg_a)
        ha, n4 = shrunk(H["home"] if H else None, "a", lg_a)
        return hf * aa / lg_h, af * ha / lg_a, min(n1, n2, n3, n4)

    @classmethod
    def predict_football(cls, league_key, home_name, away_name):
        stats = cls._load_league(league_key)
        if not stats:
            return {"success": False, "error": "Team stats unavailable right now (data source down)."}
        home = cls._resolve(stats, home_name)
        away = cls._resolve(stats, away_name)

        mu_h, mu_a, n = cls._expect(stats, home, away, 0)
        c_h, c_a, _ = cls._expect(stats, home, away, 1)
        k_h, k_a, _ = cls._expect(stats, home, away, 2)

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

        if home is None or away is None:
            conf = "Low (team not found, league averages used)"
        elif n >= 10:
            conf = "High"
        elif n >= 5:
            conf = "Medium"
        else:
            conf = "Low (small sample)"

        return {
            "success": True,
            "xg": (mu_h, mu_a),
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
            "top_scores": scores[:3],
            "over": over,
            "btts": btts,
            "corners": _lean_total(c_h + c_a),
            "cards": _lean_total(k_h + k_a),
            "confidence": conf,
        }

    # --------------------------------------------- NBA / NFL (market based)
    @classmethod
    def get_market_lines(cls, api_key, sport_key, hours=36):
        """Bookmaker consensus spread/total -> implied score. Costs 2 API credits, cached 15 min."""
        cached = cls._lines_cache.get(sport_key)
        if cached and time.time() - cached[0] < 900:
            return cached[1]
        now = datetime.now(timezone.utc)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        params = {
            "apiKey": api_key, "regions": "us", "markets": "spreads,totals",
            "oddsFormat": "decimal", "dateFormat": "iso",
            "commenceTimeFrom": now.strftime(fmt),
            "commenceTimeTo": (now + timedelta(hours=hours)).strftime(fmt),
        }
        r = requests.get(f"{ODDS_BASE}/{sport_key}/odds", params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Odds API error {r.status_code}: {r.text[:120]}")
        out = []
        for ev in r.json():
            spreads, totals = [], []
            for bk in ev.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    for o in mk.get("outcomes", []):
                        if "point" not in o:
                            continue
                        if mk["key"] == "spreads" and o["name"] == ev["home_team"]:
                            spreads.append(o["point"])
                        elif mk["key"] == "totals" and o["name"] == "Over":
                            totals.append(o["point"])
            if not spreads or not totals:
                continue
            sp, tot = statistics.median(spreads), statistics.median(totals)
            t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            out.append({
                "home": ev["home_team"], "away": ev["away_team"],
                "time": t.strftime("%a %H:%M UTC"), "when": t,
                "spread": sp, "total": tot,
                "home_pts": (tot - sp) / 2, "away_pts": (tot + sp) / 2,
            })
        out.sort(key=lambda e: e["when"])
        cls._lines_cache[sport_key] = (time.time(), out)
        return out
