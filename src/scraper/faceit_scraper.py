import requests
import time
import json
import csv
import os
import statistics
from datetime import datetime, timezone
from collections import deque
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("FACEIT_API_KEY", "")
BASE_URL = "https://open.faceit.com/data/v4"
TARGET = 1500
POLL_INTERVAL = 10
OUTPUT_CSV = "faceit_matches.csv"
PROGRESS_FILE = "progress.json"

SEED_PLAYERS = [
    "s1mple", "NiKo", "ZywOo", "donk", "m0NESY",
    "ropz", "broky", "frozen", "rain", "device",
    "Twistzz", "EliGE", "NAF", "blameF", "Magisk",
]

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})

def api_get(endpoint, params=None):
    """Send GET request to FACEIT API with rate limiting and retries."""
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(3):
        time.sleep(0.4)
        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(60)
            if resp.status_code == 404:
                return None
        except requests.RequestException:
            time.sleep(2)
    return None


def search_player(nickname):
    """Find a player by nickname, return their ID."""
    data = api_get("/search/players", {"nickname": nickname, "game": "cs2", "limit": 1})
    if data and data.get("items"):
        return data["items"][0]["player_id"]
    return None


def get_player_history(player_id):
    now = int(datetime.now(timezone.utc).timestamp())
    params = {"game": "cs2", "limit": 5, "from": now - 3600}
    data = api_get(f"/players/{player_id}/history", params)
    return data.get("items", []) if data else []


def get_player_info(player_id):
    profile = api_get(f"/players/{player_id}")
    stats = api_get(f"/players/{player_id}/stats/cs2")

    elo, level = 0, 0
    if profile:
        cs2 = profile.get("games", {}).get("cs2", {})
        elo = to_float(cs2.get("faceit_elo", 0))
        level = to_float(cs2.get("skill_level", 0))

    kd, hs, wr, matches, recent = 1.0, 45.0, 50.0, 0, []
    if stats and stats.get("lifetime"):
        lt = stats["lifetime"]
        kd = to_float(lt.get("Average K/D Ratio", 1.0))
        hs = to_float(lt.get("Average Headshots %", 45.0))
        wr = to_float(lt.get("Win Rate %", 50.0))
        matches = to_float(lt.get("Matches", 0))
        recent = lt.get("Recent Results", [])

    return {
        "elo": elo, "level": level,
        "kd": kd, "hs_pct": hs, "win_rate": wr,
        "matches": matches, "recent": recent,
    }

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def recent_form(results):
    if not results:
        return 0.5
    wins = sum(1 for r in results[:5] if str(r) == "1")
    return wins / min(len(results), 5)


def team_averages(players_data):
    if len(players_data) < 5:
        return None
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
        "avg_form": statistics.mean([recent_form(p["recent"]) for p in players_data]),
    }

def process_match(match_id, player_cache):
    match = api_get(f"/matches/{match_id}")
    if not match or match.get("status") != "FINISHED":
        return None, []

    teams = match.get("teams", {})
    roster1 = teams.get("faction1", {}).get("roster", [])
    roster2 = teams.get("faction2", {}).get("roster", [])
    if len(roster1) != 5 or len(roster2) != 5:
        return None, []

    winner = match.get("results", {}).get("winner")
    score = match.get("results", {}).get("score", {})
    if not winner:
        return None, []

    map_pick = match.get("voting", {}).get("map", {}).get("pick", ["unknown"])
    map_name = map_pick[0] if isinstance(map_pick, list) else map_pick

    discovered = []
    t1_data, t2_data = [], []

    for player in roster1:
        pid = player.get("player_id")
        if pid:
            if pid not in player_cache:
                player_cache[pid] = get_player_info(pid)
            t1_data.append(player_cache[pid])
            discovered.append(pid)

    for player in roster2:
        pid = player.get("player_id")
        if pid:
            if pid not in player_cache:
                player_cache[pid] = get_player_info(pid)
            t2_data.append(player_cache[pid])
            discovered.append(pid)

    t1 = team_averages(t1_data)
    t2 = team_averages(t2_data)
    if not t1 or not t2:
        return None, []

    row = {
        "match_id": match_id,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "map": map_name,
        "t1_avg_elo": round(t1["avg_elo"], 2),
        "t1_elo_std": round(t1["elo_std"], 2),
        "t1_min_elo": round(t1["min_elo"], 2),
        "t1_max_elo": round(t1["max_elo"], 2),
        "t1_avg_kd": round(t1["avg_kd"], 3),
        "t1_avg_hs_pct": round(t1["avg_hs_pct"], 2),
        "t1_avg_win_rate": round(t1["avg_win_rate"], 2),
        "t1_avg_matches": round(t1["avg_matches"], 1),
        "t1_avg_level": round(t1["avg_level"], 2),
        "t1_avg_form": round(t1["avg_form"], 3),
        "t2_avg_elo": round(t2["avg_elo"], 2),
        "t2_elo_std": round(t2["elo_std"], 2),
        "t2_min_elo": round(t2["min_elo"], 2),
        "t2_max_elo": round(t2["max_elo"], 2),
        "t2_avg_kd": round(t2["avg_kd"], 3),
        "t2_avg_hs_pct": round(t2["avg_hs_pct"], 2),
        "t2_avg_win_rate": round(t2["avg_win_rate"], 2),
        "t2_avg_matches": round(t2["avg_matches"], 1),
        "t2_avg_level": round(t2["avg_level"], 2),
        "t2_avg_form": round(t2["avg_form"], 3),
        "elo_diff": round(t1["avg_elo"] - t2["avg_elo"], 2),
        "kd_diff": round(t1["avg_kd"] - t2["avg_kd"], 3),
        "wr_diff": round(t1["avg_win_rate"] - t2["avg_win_rate"], 2),
        "score_t1": to_float(score.get("faction1", 0)),
        "score_t2": to_float(score.get("faction2", 0)),
        "team1_win": 1 if winner == "faction1" else 0,
    }

    return row, discovered

def save_to_csv(records):
    if not records:
        return
    file_exists = os.path.exists(OUTPUT_CSV) and os.path.getsize(OUTPUT_CSV) > 0
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"match_ids": [], "players": []}


def save_progress(match_ids, players):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"match_ids": list(match_ids), "players": list(players)[-2000:]}, f)

def main():
    if not API_KEY:
        print("Missing API key! Set FACEIT_API_KEY in your .env file.")
        return

    progress = load_progress()
    collected = set(progress["match_ids"])
    pool = deque(progress["players"], maxlen=2000)
    cache = {}

    print(f"Start | Already collected: {len(collected)} | Target: {TARGET}")

    if not pool:
        print("Finding seed players...")
        for nick in SEED_PLAYERS:
            pid = search_player(nick)
            if pid:
                pool.append(pid)
                print(f"  + {nick}")

    while len(collected) < TARGET:
        new_records = []

        for _ in range(min(30, len(pool))):
            pid = pool[0]
            pool.rotate(-1)

            for match in get_player_history(pid):
                mid = match.get("match_id")
                if not mid or mid in collected:
                    continue

                row, new_players = process_match(mid, cache)
                if row is None:
                    continue

                new_records.append(row)
                collected.add(mid)

                for p in new_players:
                    if p not in pool:
                        pool.append(p)

                print(f"  [{len(collected)}/{TARGET}] {mid[:10]} | {row['map']} | "
                      f"ELO {row['t1_avg_elo']:.0f} vs {row['t2_avg_elo']:.0f}")

                if len(collected) >= TARGET:
                    break
            if len(collected) >= TARGET:
                break

        save_to_csv(new_records)
        save_progress(collected, pool)
        print(f"--- Saved {len(new_records)} new | Total: {len(collected)}/{TARGET} ---")

        if len(collected) < TARGET:
            time.sleep(POLL_INTERVAL)

    print(f"\nDone! Collected {len(collected)} matches -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()