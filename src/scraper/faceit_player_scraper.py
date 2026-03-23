import requests
import time
import csv
import os
import json
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

API_KEY = os.environ.get("FACEIT_API_KEY", "")
BASE_URL = "https://open.faceit.com/data/v4"
INPUT_CSV = "data/faceit_matches.csv"
OUTPUT_CSV = "data/player_match_stats.csv"
PROGRESS_FILE = "data/player_progress.json"

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})


def api_get(endpoint, params=None):
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


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def get_player_lifetime(player_id):
    profile = api_get(f"/players/{player_id}")
    stats = api_get(f"/players/{player_id}/stats/cs2")

    elo, level = 0, 0
    if profile:
        cs2 = profile.get("games", {}).get("cs2", {})
        elo = to_float(cs2.get("faceit_elo", 0))
        level = to_float(cs2.get("skill_level", 0))

    kd, hs, wr, matches = 1.0, 45.0, 50.0, 0
    if stats and stats.get("lifetime"):
        lt = stats["lifetime"]
        kd = to_float(lt.get("Average K/D Ratio", 1.0))
        hs = to_float(lt.get("Average Headshots %", 45.0))
        wr = to_float(lt.get("Win Rate %", 50.0))
        matches = to_float(lt.get("Matches", 0))

    return {
        "elo": elo, "level": level,
        "kd": kd, "hs_pct": hs,
        "win_rate": wr, "matches": matches,
    }


def get_match_stats(match_id):
    data = api_get(f"/matches/{match_id}/stats")
    if not data or not data.get("rounds"):
        return None
    return data["rounds"][0] if data["rounds"] else None


def get_match_info(match_id):
    data = api_get(f"/matches/{match_id}")
    if not data or data.get("status") != "FINISHED":
        return None
    return data


def process_match(match_id, player_cache):
    match_info = get_match_info(match_id)
    if not match_info:
        return []

    match_stats = get_match_stats(match_id)
    if not match_stats:
        return []

    teams_info = match_info.get("teams", {})
    roster1 = teams_info.get("faction1", {}).get("roster", [])
    roster2 = teams_info.get("faction2", {}).get("roster", [])

    if len(roster1) != 5 or len(roster2) != 5:
        return []

    map_pick = match_info.get("voting", {}).get("map", {}).get("pick", ["unknown"])
    map_name = map_pick[0] if isinstance(map_pick, list) else map_pick

    winner = match_info.get("results", {}).get("winner", "")

    perf_lookup = {}
    stats_teams = match_stats.get("teams", [])
    for team_data in stats_teams:
        for player in team_data.get("players", []):
            pid = player.get("player_id")
            ps = player.get("player_stats", {})
            perf_lookup[pid] = {
                "kills": to_float(ps.get("Kills", 0)),
                "deaths": to_float(ps.get("Deaths", 0)),
                "assists": to_float(ps.get("Assists", 0)),
                "headshots": to_float(ps.get("Headshots", 0)),
                "mvps": to_float(ps.get("MVPs", 0)),
                "adr": to_float(ps.get("ADR", 0)),
            }

    t1_ids = [p.get("player_id") for p in roster1]
    t2_ids = [p.get("player_id") for p in roster2]

    for pid in t1_ids + t2_ids:
        if pid and pid not in player_cache:
            player_cache[pid] = get_player_lifetime(pid)

    t1_stats = [player_cache.get(pid, {}) for pid in t1_ids if pid]
    t2_stats = [player_cache.get(pid, {}) for pid in t2_ids if pid]

    if len(t1_stats) != 5 or len(t2_stats) != 5:
        return []

    def team_avg(stats_list, key):
        vals = [s.get(key, 0) for s in stats_list]
        return sum(vals) / len(vals) if vals else 0

    t1_avg_elo = team_avg(t1_stats, "elo")
    t2_avg_elo = team_avg(t2_stats, "elo")
    t1_avg_kd = team_avg(t1_stats, "kd")
    t2_avg_kd = team_avg(t2_stats, "kd")
    t1_avg_wr = team_avg(t1_stats, "win_rate")
    t2_avg_wr = team_avg(t2_stats, "win_rate")

    rows = []

    for pid in t1_ids:
        if not pid or pid not in player_cache or pid not in perf_lookup:
            continue
        plife = player_cache[pid]
        pperf = perf_lookup[pid]

        rows.append({
            "match_id": match_id,
            "map": map_name,
            "player_elo": plife["elo"],
            "player_kd": plife["kd"],
            "player_hs_pct": plife["hs_pct"],
            "player_wr": plife["win_rate"],
            "player_matches": plife["matches"],
            "player_level": plife["level"],
            "team_avg_elo": round(t1_avg_elo, 2),
            "team_avg_kd": round(t1_avg_kd, 3),
            "opp_avg_elo": round(t2_avg_elo, 2),
            "opp_avg_kd": round(t2_avg_kd, 3),
            "opp_avg_wr": round(t2_avg_wr, 2),
            "elo_gap": round(plife["elo"] - t2_avg_elo, 2),
            "kills": pperf["kills"],
            "deaths": pperf["deaths"],
            "assists": pperf["assists"],
        })

    for pid in t2_ids:
        if not pid or pid not in player_cache or pid not in perf_lookup:
            continue

        plife = player_cache[pid]
        pperf = perf_lookup[pid]

        rows.append({
            "match_id": match_id,
            "map": map_name,
            "player_elo": plife["elo"],
            "player_kd": plife["kd"],
            "player_hs_pct": plife["hs_pct"],
            "player_wr": plife["win_rate"],
            "player_matches": plife["matches"],
            "player_level": plife["level"],
            "team_avg_elo": round(t2_avg_elo, 2),
            "team_avg_kd": round(t2_avg_kd, 3),
            "opp_avg_elo": round(t1_avg_elo, 2),
            "opp_avg_kd": round(t1_avg_kd, 3),
            "opp_avg_wr": round(t1_avg_wr, 2),
            "elo_gap": round(plife["elo"] - t1_avg_elo, 2),
            "kills": pperf["kills"],
            "deaths": pperf["deaths"],
            "assists": pperf["assists"],
        })

    return rows

def save_rows(records):
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
            return set(json.load(f).get("done", []))
    return set()


def save_progress(done):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"done": list(done)}, f)


def main():
    if not API_KEY:
        print("Set FACEIT_API_KEY in .env")
        return

    df = pd.read_csv(INPUT_CSV)
    match_ids = df["match_id"].tolist()

    done = load_progress()
    cache = {}
    total = len(match_ids)

    print(f"Total matches: {total} | Already done: {len(done)}")

    batch = []
    for i, mid in enumerate(match_ids):
        if mid in done:
            continue

        rows = process_match(mid, cache)
        if rows:
            batch.extend(rows)
            done.add(mid)

        print(f"  [{len(done)}/{total}] {mid[:12]}... -> {len(rows)} player rows")

        if len(batch) >= 100:
            save_rows(batch)
            save_progress(done)
            batch = []
            print(f"--- Saved batch | Done: {len(done)}/{total} ---")

    if batch:
        save_rows(batch)
        save_progress(done)

    print(f"\nDone! Processed {len(done)} matches -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()