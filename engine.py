import math
import requests

class EdgePicksEngine:

    @staticmethod
    def _safe_float(val, default=1.0):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    # 🌍 FETCH TODAY'S MATCHES FOR ANY SPORT (Football, NBA, NFL)
    @classmethod
    def get_todays_matches_by_sport(cls, api_key, sport_key="soccer_epl"):
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events"
        params = {"apiKey": api_key}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                events = response.json()
                today_list = []
                for event in events[:5]:
                    today_list.append({
                        "home": event['home_team'],
                        "away": event['away_team'],
                        "time": event['commence_time'][11:16]
                    })
                return today_list
            return []
        except Exception as e:
            print(f"API Fetch Error: {e}")
            return []

    # ⚽ FOOTBALL MODEL
    @classmethod
    def predict_football(cls, home_name, away_name):
        h_xg, a_xg = 1.6, 1.3
        h_xga, a_xga = 1.0, 1.1
        lambda_home = max(0.2, min(h_xg * a_xga * 1.15, 4.5))
        lambda_away = max(0.2, min(a_xg * h_xga, 4.5))

        def poisson(k, lamb):
            return (lamb ** k) * math.exp(-lamb) / math.factorial(k)

        p_home, p_draw, p_away = 0.0, 0.0, 0.0
        score_matrix = {}
        for h in range(7):
            for a in range(7):
                p = poisson(h, lambda_home) * poisson(a, lambda_away)
                score_matrix[(h, a)] = p
                if h > a: p_home += p
                elif h == a: p_draw += p
                else: p_away += p

        best_score = max(score_matrix, key=score_matrix.get)
        return {
            "success": True,
            "projected_score": f"{best_score[0]} - {best_score[1]}",
            "prob_home": round(p_home * 100, 1),
            "prob_draw": round(p_draw * 100, 1),
            "prob_away": round(p_away * 100, 1)
        }

    # 🏀 NBA MODEL
    @classmethod
    def predict_nba(cls, home_name, away_name):
        h_pts, a_pts = 114.5, 108.0
        return {
            "success": True,
            "projected_score": f"{round(h_pts)} - {round(a_pts)}",
            "total": round(h_pts + a_pts, 1),
            "spread": f"{home_name} (-6.5)"
        }

    # 🏈 NFL MODEL
    @classmethod
    def predict_nfl(cls, home_name, away_name):
        h_pts, a_pts = 24.5, 20.0
        return {
            "success": True,
            "projected_score": f"{round(h_pts)} - {round(a_pts)}",
            "total": round(h_pts + a_pts, 1),
            "spread": f"{home_name} (-4.5)"
      }
                      
