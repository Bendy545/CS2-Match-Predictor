import os
import re
import pickle
import statistics
import requests
import time
import pandas as pd
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

load_dotenv()

app = Flask(__name__)

API_KEY = os.environ.get("FACEIT_API_KEY", "")
BASE_URL = "https://open.faceit.com/data/v4"

try:
    with open(os.path.join(MODELS_DIR, "gb_classifier-5.pkl"), "rb") as f:
        gb_classifier = pickle.load(f)
    print("Gradient Boosting classifier loaded")
except Exception as e:
    gb_classifier = None
    print(f"Warning: Could not load GB Classifier: {e}")

try:
    with open(os.path.join(MODELS_DIR, "rf_regressor-4.pkl"), "rb") as f:
        rf_regressor = pickle.load(f)
    print("Random Forest regressor loaded")
except Exception as e:
    rf_regressor = None
    print(f"Warning: Could not load Random Forest regressor: {e}")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})

def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(3):
        time.sleep(0.3)
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
        except requests.RequestException:
            time.sleep(1)
    return None


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_match_id(room_input):
    room_input = room_input.strip()
    match = re.search(r'/room/([\w-]+)', room_input)
    if match:
        return match.group(1)
    return room_input

def search_player(nickname):
    data = api_get("/search/players", {"nickname": nickname, "game": "cs2", "limit": 1})
    if data and data.get("items"):
        return data["items"][0]["player_id"]
    return None

def get_player_info(player_id):
    profile = api_get(f"/players/{player_id}")
    stats = api_get(f"/players/{player_id}/stats/cs2")

    elo, level = 0, 0
    nickname = "Unknown"
    if profile:
        cs2 = profile.get("games", {}).get("cs2", {})
        elo = to_float(cs2.get("faceit_elo", 0))
        level = to_float(cs2.get("skill_level", 0))
        nickname = profile.get("nickname", "Unknown")

    kd, hs, wr, matches = 1.0, 45.0, 50.0, 0
    if stats and stats.get("lifetime"):
        lt = stats["lifetime"]
        kd = to_float(lt.get("Average K/D Ratio", 1.0))
        hs = to_float(lt.get("Average Headshots %", 45.0))
        wr = to_float(lt.get("Win Rate %", 50.0))
        matches = to_float(lt.get("Matches", 0))

    return {
        "nickname": nickname,
        "elo": elo,
        "level": level,
        "kd": kd,
        "hs_pct": hs,
        "win_rate": wr,
        "matches": matches,
    }


def get_match_info(match_id):
    """Fetch match details including both team rosters and map."""
    data = api_get(f"/matches/{match_id}")
    if not data:
        return None

    teams = data.get("teams", {})
    roster1 = teams.get("faction1", {}).get("roster", [])
    roster2 = teams.get("faction2", {}).get("roster", [])
    team1_name = teams.get("faction1", {}).get("name", "Team 1")
    team2_name = teams.get("faction2", {}).get("name", "Team 2")

    if len(roster1) != 5 or len(roster2) != 5:
        return None

    map_pick = data.get("voting", {}).get("map", {}).get("pick", ["de_dust2"])
    map_name = map_pick[0] if isinstance(map_pick, list) else map_pick

    t1_nicknames = [p.get("nickname", "") for p in roster1]
    t2_nicknames = [p.get("nickname", "") for p in roster2]
    t1_ids = [p.get("player_id", "") for p in roster1]
    t2_ids = [p.get("player_id", "") for p in roster2]

    return {
        "team1_name": team1_name,
        "team2_name": team2_name,
        "team1_nicknames": t1_nicknames,
        "team2_nicknames": t2_nicknames,
        "team1_ids": t1_ids,
        "team2_ids": t2_ids,
        "map": map_name,
        "status": data.get("status", ""),
    }

def team_averages(players_data):
    elos = [p["elo"] for p in players_data]
    return {
        "avg_elo": statistics.mean(elos),
        "elo_std": statistics.stdev(elos) if len(elos) > 1 else 0,
        "min_elo": min(elos),
        "max_elo": max(elos),
        "avg_kd": statistics.mean([p["kd"] for p in players_data]),
        "avg_hs_pct": statistics.mean([p["hs_pct"] for p in players_data]),
        "avg_win_rate": statistics.mean([p["win_rate"] for p in players_data]),
        "avg_matches": statistics.mean([p["matches"] for p in players_data]),
        "avg_level": statistics.mean([p["level"] for p in players_data]),
    }

def build_features(t1, t2, map_name):
    exp_ratio = t1["avg_matches"] / max(t2["avg_matches"], 1)
    exp_ratio = min(exp_ratio, 5)

    row = {
        "elo_diff": t1["avg_elo"] - t2["avg_elo"],
        "kd_diff": t1["avg_kd"] - t2["avg_kd"],
        "wr_diff": t1["avg_win_rate"] - t2["avg_win_rate"],
        "hs_diff": t1["avg_hs_pct"] - t2["avg_hs_pct"],
        "level_diff": t1["avg_level"] - t2["avg_level"],
        "elo_total": t1["avg_elo"] + t2["avg_elo"],
        "experience_ratio": exp_ratio,
        "map": map_name,
    }
    return pd.DataFrame([row])


def run_prediction(t1_players, t2_players, map_name):
    t1 = team_averages(t1_players)
    t2 = team_averages(t2_players)
    features = build_features(t1, t2, map_name)

    win_prob = 0.5
    if gb_classifier:
        win_prob = float(gb_classifier.predict_proba(features)[0][1])

    score_diff = 0.0
    if rf_regressor:
        score_diff = float(rf_regressor.predict(features)[0])

    winner = "Team 1" if win_prob >= 0.5 else "Team 2"
    confidence = win_prob if win_prob >= 0.5 else 1 - win_prob

    base_winner = 13
    base_loser = max(0, round(13 - abs(score_diff)))
    if winner == "Team 1":
        est_t1 = base_winner
        est_t2 = base_loser
    else:
        est_t1 = base_loser
        est_t2 = base_winner

    return {
        "winner": winner,
        "win_probability": round(win_prob * 100, 1),
        "confidence": round(confidence * 100, 1),
        "score_diff": round(score_diff, 1),
        "estimated_score": f"{est_t1}-{est_t2}",
        "team1": {
            "players": t1_players,
            "avg_elo": round(t1["avg_elo"]),
            "avg_kd": round(t1["avg_kd"], 2),
            "avg_wr": round(t1["avg_win_rate"], 1),
        },
        "team2": {
            "players": t2_players,
            "avg_elo": round(t2["avg_elo"]),
            "avg_kd": round(t2["avg_kd"], 2),
            "avg_wr": round(t2["avg_win_rate"], 1),
        },
        "map": map_name,
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/room/<path:room_input>")
def get_room(room_input):
    match_id = extract_match_id(room_input)
    match_info = get_match_info(match_id)
    if not match_info:
        return jsonify({"error": "Match not found or invalid room ID"}), 404
    return jsonify(match_info)

@app.route("/api/predict", methods=["POST"])
def predict():
    data = request.json

    team1_names = data.get("team1", [])
    team2_names = data.get("team2", [])
    map_name = data.get("map", "de_dust2")

    if len(team1_names) != 5 or len(team2_names) != 5:
        return jsonify({"error": "Each team must have exactly 5 players"}), 400

    t1_players = []
    t2_players = []

    for nick in team1_names:
        pid = search_player(nick.strip())
        if not pid:
            return jsonify({"error": f"Player not found: {nick}"}), 404
        info = get_player_info(pid)
        t1_players.append(info)

    for nick in team2_names:
        pid = search_player(nick.strip())
        if not pid:
            return jsonify({"error": f"Player not found: {nick}"}), 404
        info = get_player_info(pid)
        t2_players.append(info)

    result = run_prediction(t1_players, t2_players, map_name)
    return jsonify(result)

@app.route("/api/predict-room", methods=["POST"])
def predict_room():
    data = request.json
    room_input = data.get("room", "")

    if not room_input:
        return jsonify({"error": "Please enter a room URL or match ID"}), 400

    match_id = extract_match_id(room_input)
    match_info = get_match_info(match_id)

    if not match_info:
        return jsonify({"error": "Match not found or invalid room ID"}), 404

    t1_players = []
    t2_players = []

    for pid in match_info["team1_ids"]:
        info = get_player_info(pid)
        t1_players.append(info)

    for pid in match_info["team2_ids"]:
        info = get_player_info(pid)
        t2_players.append(info)

    map_name = match_info["map"]
    result = run_prediction(t1_players, t2_players, map_name)
    result["team1_name"] = match_info["team1_name"]
    result["team2_name"] = match_info["team2_name"]

    return jsonify(result)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
