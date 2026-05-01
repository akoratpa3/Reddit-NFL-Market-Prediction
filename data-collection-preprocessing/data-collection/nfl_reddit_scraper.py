import pandas as pd
import requests
import time
import sys
import os
import re
from datetime import date, timedelta, time as dtime, datetime, timezone
from math import floor

import pytz
from openpyxl import load_workbook

XLSX_PATH = "2025-2026 NFL Games.xlsx"
OUTPUT_DIR = "reddit_data"
DELAY_SECONDS = 10 
PRE_GAME_MINS =30     
MAX_GAME_MINS = 240    
BATCH_SIZE =50  
#first game
WEEK1_THURSDAY = date(2025, 9, 4)
DAY_OFFSET     = {"Thu": 0, "Fri": 1, "Sat": 2, "Sun": 3, "Mon": 4}
ET             = pytz.timezone("America/New_York")

HEADERS = {"User-Agent": "NFL Reddit Scraper"}
def get_url(cell) -> list:
    urls = []
    if cell.hyperlink and cell.hyperlink.target:
        urls.append(cell.hyperlink.target)
    val = cell.value
    if val and isinstance(val, str):
        for token in val.split():
            if token.startswith("http") and token not in urls:
                urls.append(token)
    return urls

def parse_formula_value(raw) -> str:
    if not raw:
        return ""
    s = str(raw)
    match = re.search(r'"([^"]+)"\s*\)\s*\)', s)
    return match.group(1) if match else s

def kickoff_utc(week: int, day: str, start_time: dtime) -> float:
    game_date = WEEK1_THURSDAY + timedelta(weeks=week - 1) + timedelta(days=DAY_OFFSET.get(day, 3))
    return ET.localize(datetime.combine(game_date, start_time)).timestamp()

def load_games(xlsx_path: str) -> list:
    wb = load_workbook(xlsx_path)
    ws = wb["Regular Season "]
    games = []
    for row in ws.iter_rows(min_row=2, values_only=False):
        week      = row[0].value
        start_raw = row[2].value
        day       = row[4].value
        if not isinstance(week, (int, float)) or not (1 <= int(week) <= 18):
            continue
        urls = get_url(row[6])
        if not urls:
            continue

        ko_utc = None
        if isinstance(start_raw, dtime) and day in DAY_OFFSET:
            ko_utc = kickoff_utc(int(week), day, start_raw)

        games.append({
            "week":       int(week),
            "game":       row[1].value or "",
            "day":        day,
            "score":      parse_formula_value(row[9].value),
            "winner":     parse_formula_value(row[10].value),
            "thread_urls": urls,
            "ko_utc":     ko_utc,   
        })

    return games

def extract_thread_id(url: str) -> str:
    match = re.search(r"/comments/([a-z0-9]+)/", url)
    return match.group(1) if match else None

def parse_comments_recursive(children: list, results: list):

    for child in children:
        if child.get("kind") != "t1":
            continue
        d = child["data"]
        results.append({
            "created_utc": d.get("created_utc", 0),
            "body":        d.get("body", ""),
            "score":       d.get("score", 0),
            "author":      str(d.get("author", "")),
        })
        replies = d.get("replies")
        if isinstance(replies, dict):
            parse_comments_recursive(replies["data"]["children"], results)

MAX_CONSECUTIVE_429S = 3 

def fetch_more_comments(thread_id: str, more_ids: list, results: list):

    consecutive_429s = 0
    for i in range(0, len(more_ids), 100):
        batch = more_ids[i:i + 100]
        url = "https://www.reddit.com/api/morechildren.json"
        params = {
            "link_id":  f"t3_{thread_id}",
            "children": ",".join(batch),
            "api_type": "json",
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                consecutive_429s = 0
                things = resp.json().get("json", {}).get("data", {}).get("things", [])
                parse_comments_recursive(things, results)
            else:
                print(f"    [!] morechildren returned {resp.status_code}")
                if resp.status_code == 429:
                    consecutive_429s += 1
                    if consecutive_429s >= MAX_CONSECUTIVE_429S:
                        print(f"    [!] {MAX_CONSECUTIVE_429S} consecutive 429s — skipping remaining morechildren.")
                        break
        except Exception as e:
            print(f"    [!] morechildren error: {e}")
        time.sleep(2)

def scrape_thread(urls: list) -> list:

    all_results = []
    for url in urls:
        clean_url = url.rstrip("/") + ".json?limit=500&sort=new"
        thread_id = extract_thread_id(url)

        try:
            resp = requests.get(clean_url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"  [!] {resp.status_code} for {url}")
                continue

            data = resp.json()
            children = data[1]["data"]["children"]

            results = []
            more_ids = []

            for child in children:
                if child["kind"] == "t1":
                    parse_comments_recursive([child], results)
                elif child["kind"] == "more":
                    more_ids.extend(child["data"].get("children", []))

            if more_ids and thread_id:
                print(f"  Expanding {len(more_ids)} 'load more' comment IDs...")
                fetch_more_comments(thread_id, more_ids, results)

            print(f"  [{url.split('/comments/')[1].split('/')[0]}] {len(results):,} comments")
            all_results.extend(results)

        except Exception as e:
            print(f"  [!] Error for {url}: {e}")

    return all_results


def bucket_into_30s(comments: list, ko_utc: float) -> pd.DataFrame:
    if not comments:
        return pd.DataFrame()

    t0 = ko_utc - (PRE_GAME_MINS * 60)   
    t_end = ko_utc + (MAX_GAME_MINS * 60)

    df = pd.DataFrame(comments)
    df = df[df["created_utc"] <= t_end]

    df["timestamp"] = pd.to_datetime(df["created_utc"], unit="s", utc=True)
    df = df.set_index("timestamp").sort_index()

    bucketed = df.resample("30s").agg(
        comment_count=("score", "count"),
        avg_score=("score", "mean"),
        all_text=("body", lambda x: " | ".join(x)),).round(2)

    t0_ts = pd.Timestamp(t0, unit="s", tz="UTC")
    bucketed["offset_from_kickoff_min"] = (
        (bucketed.index - t0_ts).total_seconds() / 60).round(2)

    bucketed["timestamp_et"] = bucketed.index.tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M:%S")

    bucketed = bucketed[bucketed["comment_count"] > 0]
    return bucketed


def safe_filename(s: str) -> str:
    return re.sub(r"[^\w\-_]", "_", s).strip("_")

def already_done(fname: str) -> bool:
    return os.path.exists(f"{OUTPUT_DIR}/buckets/{fname}_buckets.csv")

def main():
    resume     = "--resume" in sys.argv
    batch_size = BATCH_SIZE

    os.makedirs(f"{OUTPUT_DIR}/per_game", exist_ok=True)
    os.makedirs(f"{OUTPUT_DIR}/buckets",  exist_ok=True)

    games = load_games(XLSX_PATH)

    if resume:
        pending = [g for g in games if not already_done(f"week{g['week']:02d}_{safe_filename(g['game'])}")]
        print(f"{len(games) - len(pending)} already scraped, {len(pending)} remaining.")
    else:
        pending = games

    batch = pending[:batch_size] if batch_size else pending
    print(f"Running batch of {len(batch)} games.")

    all_buckets  = []
    all_comments = []

    for i, game in enumerate(batch):
        week  = game["week"]
        name  = game["game"]
        urls  = game["thread_urls"]
        ko    = game["ko_utc"]
        fname = f"week{week:02d}_{safe_filename(name)}"

        print(f"Week {week}: {name}")

        comments = scrape_thread(urls)
        print(f"  Fetched {len(comments):,} comments total")

        if not comments:
            time.sleep(DELAY_SECONDS)
            continue

        raw_df = pd.DataFrame(comments)
        raw_df["week"]        = week
        raw_df["game"]        = name
        raw_df["final_score"] = game["score"]
        raw_df["winner"]      = game["winner"]
        raw_df["thread_url"]  = " | ".join(urls)
        raw_df.to_csv(f"{OUTPUT_DIR}/per_game/{fname}.csv", index=False)
        all_comments.append(raw_df)

        if ko is None:
            earliest = min(c["created_utc"] for c in comments if c["created_utc"])
            ko = earliest

        bucketed = bucket_into_30s(comments, ko)
        if not bucketed.empty:
            bucketed["week"]        = week
            bucketed["game"]        = name
            bucketed["final_score"] = game["score"]
            bucketed["winner"]      = game["winner"]
            bucketed["thread_url"]  = " | ".join(urls)
            bucketed.to_csv(f"{OUTPUT_DIR}/buckets/{fname}_buckets.csv")
            all_buckets.append(bucketed)
            print(f"{len(bucketed)}")

        time.sleep(DELAY_SECONDS)

    master_comments_path = f"{OUTPUT_DIR}/master_comments.csv"
    master_buckets_path  = f"{OUTPUT_DIR}/master_buckets.csv"

    if all_comments:
        new_comments = pd.concat(all_comments, ignore_index=True)
        if resume and os.path.exists(master_comments_path):
            pd.concat([pd.read_csv(master_comments_path), new_comments], ignore_index=True).to_csv(master_comments_path, index=False)
        else:
            new_comments.to_csv(master_comments_path, index=False)

    if all_buckets:
        new_buckets = pd.concat(all_buckets)
        if resume and os.path.exists(master_buckets_path):
            pd.concat([pd.read_csv(master_buckets_path), new_buckets]).to_csv(master_buckets_path)
        else:
            new_buckets.to_csv(master_buckets_path)
if __name__ == "__main__":
    main()
