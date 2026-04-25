import os
import re
import time
import base64
import requests
import pandas as pd
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

# creds setup here. note that you need a Kalshi account, which does require a SSID.
API_KEY_ID = "[]]"

PRIVATE_KEY_PEM = """
-----BEGIN RSA PRIVATE KEY-----
[]
-----END RSA PRIVATE KEY-----
"""

BASE_URL   = "https://api.elections.kalshi.com/trade-api/v2"
OUTPUT_DIR = "kalshi_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# authentication, a sample of this is provided in the Kalshi API docs.
def get_auth_headers():
    private_key = serialization.load_pem_private_key(
        PRIVATE_KEY_PEM.strip().encode("utf-8"),
        password=None,
        backend=default_backend()
    )
    timestamp = str(int(time.time() * 1000))
    msg       = timestamp + API_KEY_ID
    signature = private_key.sign(
        msg.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "Content-Type":            "application/json",
        "KALSHI-ACCESS-KEY":       API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
    }


def kalshi_get(endpoint, params=None):
    url = BASE_URL + endpoint
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=get_auth_headers(),
                                params=params, timeout=15)
            if resp.status_code == 429:
                print("Curerntly being rate limited, 5 second delay")
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Something failed, check your request headers. The exception was: {e}")
            time.sleep(2)
    return None

# mapping from the name on reddit to the kalshi city keywords they use
TEAM_CITY = {
    "49ers":      ["san francisco"],
    "Bears":      ["chicago"],
    "Bengals":    ["cincinnati"],
    "Bills":      ["buffalo"],
    "Broncos":    ["denver"],
    "Browns":     ["cleveland"],
    "Buccaneers": ["tampa bay"],
    "Cardinals":  ["arizona"],
    "Chargers":   ["los angeles c"],
    "Chiefs":     ["kansas city"],
    "Colts":      ["indianapolis"],
    "Cowboys":    ["dallas"],
    "Dolphins":   ["miami"],
    "Eagles":     ["philadelphia"],
    "Falcons":    ["atlanta"],
    "Giants":     ["new york g"],
    "Jaguars":    ["jacksonville"],
    "Jets":       ["new york j"],
    "Lions":      ["detroit"],
    "Packers":    ["green bay"],
    "Panthers":   ["carolina"],
    "Patriots":   ["new england"],
    "Raiders":    ["las vegas"],
    "Rams":       ["los angeles r"],
    "Ravens":     ["baltimore"],
    "Saints":     ["new orleans"],
    "Seahawks":   ["seattle"],
    "Steelers":   ["pittsburgh"],
    "Texans":     ["houston"],
    "Titans":     ["tennessee"],
    "Vikings":    ["minnesota"],
    "Commanders": ["washington"],
}

# map from the NFL week (the week the game occurred) to the time interval (a weekend) they actually occurred over
WEEK_DATE_RANGE = {
    1:  ("25SEP04", "25SEP08"),
    2:  ("25SEP11", "25SEP15"),
    3:  ("25SEP18", "25SEP22"),
    4:  ("25SEP25", "25SEP29"),
    5:  ("25OCT02", "25OCT06"),
    6:  ("25OCT09", "25OCT13"),
    7:  ("25OCT16", "25OCT20"),
    8:  ("25OCT23", "25OCT27"),
    9:  ("25OCT30", "25NOV03"),
    10: ("25NOV06", "25NOV10"),
    11: ("25NOV13", "25NOV17"),
    12: ("25NOV20", "25NOV27"),
    13: ("25NOV28", "25DEC02"),
    14: ("25DEC04", "25DEC09"),
    15: ("25DEC11", "25DEC15"),
    16: ("25DEC18", "25DEC22"),
    17: ("25DEC25", "25DEC30"),
    18: ("26JAN01", "26JAN05"),
}

# using the names of the game from reddit, so just extracting all the relevant parts (home team, away team, etc)
def parse_reddit_game(game_str):
    game_str = re.sub(r'\s*\([A-Za-z][a-z ]+\)\s*$', '', game_str).strip()
    m = re.match(
        r'^(.+?)\s*\(([A-Z]+)\)\s*(?:at|vs)\s*(.+?)\s*\(([A-Z]+)\)\s*$',
        game_str
    )
    if not m:
        return None
    away_name, away_abbr, home_name, home_abbr = m.groups()
    return away_name.strip(), away_abbr, home_name.strip(), home_abbr

# many, many data cleaning methods. needed to add these as we go, since reddit + kalshi formats are completely different.
def team_in_title(team_name, title_lower):
    keywords = TEAM_CITY.get(team_name, [team_name.lower()])
    return any(kw in title_lower for kw in keywords)

# converts a date string into a raw number (unix timing)
def date_str_to_int(date_str):
    dm = re.match(r'(\d{2})([A-Z]{3})(\d{2})', date_str)
    if not dm:
        return 0
    yy, mon, dd = dm.groups()
    month = MONTH_MAP.get(mon, 0)
    return (2000 + int(yy)) * 10000 + month * 100 + int(dd)

# make sure this ticker from Kalshi is actually the right game in the right week
def ticker_matches_week(ticker, week):
    m = re.search(r'-(\d{2}[A-Z]{3}\d{2})', ticker)
    if not m:
        return True
    date_int = date_str_to_int(m.group(1))
    low, high = WEEK_DATE_RANGE.get(week, ("", ""))
    if not low:
        return True
    return date_str_to_int(low) <= date_int <= date_str_to_int(high)

# big method used to map the name of the game from reddit to the actual kalshi ticker
def match_reddit_to_kalshi(reddit_game_str, kalshi_markets, week=None):
    parsed = parse_reddit_game(reddit_game_str)
    if not parsed:
        return None

    away_name, away_abbr, home_name, home_abbr = parsed

    def find_matches(suffix):
        results = []
        for market in kalshi_markets:
            title  = market.get("title", "").lower()
            ticker = market.get("ticker", "")
            if not (team_in_title(away_name, title) and team_in_title(home_name, title)):
                continue
            if week and not ticker_matches_week(ticker, week):
                continue
            if ticker.endswith(f"-{suffix}"):
                results.append(market)
        return results

    matches = find_matches(away_abbr)
    if not matches:
        matches = find_matches(home_abbr)
    if not matches:
        for market in kalshi_markets:
            title  = market.get("title", "").lower()
            ticker = market.get("ticker", "")
            if team_in_title(away_name, title) and team_in_title(home_name, title):
                if week and not ticker_matches_week(ticker, week):
                    continue
                matches.append(market)
                break

    return matches[0] if matches else None

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

def get_game_window(ticker):
    m = re.search(r'-(\d{2})([A-Z]{3})(\d{2})', ticker)
    if not m:
        return None, None
    yy, mon, dd = m.groups()
    year  = 2000 + int(yy)
    month = MONTH_MAP.get(mon)
    day   = int(dd)
    if not month:
        return None, None
    game_date = pd.Timestamp(year=year, month=month, day=day, tz="UTC")
    start     = game_date + pd.Timedelta(hours=12)
    end       = game_date + pd.Timedelta(hours=28)
    return int(start.timestamp()), int(end.timestamp())

# note that these are 1min intervals
def fetch_candlesticks(ticker):
    start_ts, end_ts = get_game_window(ticker)
    if start_ts is None:
        print(f"Something went wrong with this: {ticker}")
        return pd.DataFrame()

    data = kalshi_get(
        f"/historical/markets/{ticker}/candlesticks",
        params={
            "start_ts":        start_ts,
            "end_ts":          end_ts,
            "period_interval": 1,
        }
    )

    if not data or not data.get("candlesticks"):
        return pd.DataFrame()

    records = []
    for candle in data["candlesticks"]:
        price = candle.get("price", {})
        records.append({"timestamp":   pd.Timestamp(candle["end_period_ts"], unit="s", tz="UTC"), "price_close": float(price.get("close", 0) or 0),
            "price_open":  float(price.get("open",  0) or 0), "price_mean":  float(price.get("mean",  0) or 0), "price_high":  float(price.get("high",  0) or 0),
            "price_low":   float(price.get("low",   0) or 0),
            "volume":      float(candle.get("volume", 0) or 0),
        })

    return pd.DataFrame(records)

def fetch_all_nfl_markets():
    all_markets = []
    cursor      = None
    page        = 0

    print("Fetching all KXNFLGAME markets, note that these are classified as HISTORICAL")

    while True:
        page  += 1
        params = {"series_ticker": "KXNFLGAME", "limit": 200}
        if cursor:
            params["cursor"] = cursor

        data = kalshi_get("/historical/markets", params=params)
        if not data or not data.get("markets"):
            break

        markets = data["markets"]
        all_markets.extend(markets)
        cursor  = data.get("cursor")

        oldest = markets[-1].get("ticker", "")
        print(f"Page {page}: {len(markets)} markets, oldest: {oldest}")

        if not cursor:
            print("No more pages.")
            break

        time.sleep(0.15)

    # note we want regular season games to ensure things match with the Reddit dataset
    regular_season = [
        m for m in all_markets
        if not any(pre in m.get("ticker", "")
                   for pre in ["25AUG", "25JUL", "25JUN"])
    ]

    print(f"Total markets fetched: {len(all_markets)}")
    print(f"Regular season markets: {len(regular_season)}")
    return regular_season

# map from the week of the game to the actual title of the game (title comes from game title in Reddit megathread)
REDDIT_GAMES = [
    (1,  "49ers (SF) at Seahawks (SEA)"),
    (1,  "Bengals (CIN) at Browns (CLE)"),
    (1,  "Buccaneers (TB) at Falcons (ATL)"),
    (1,  "Cardinals (ARI) at Saints (NO)"),
    (1,  "Cowboys (DAL) at Eagles (PHI)"),
    (1,  "Dolphins (MIA) at Colts (IND)"),
    (1,  "Giants (NYG) at Commanders (WAS)"),
    (1,  "Lions (DET) at Packers (GB)"),
    (1,  "Panthers (CAR) at Jaguars (JAX)"),
    (1,  "Raiders (LV) at Patriots (NE)"),
    (1,  "Ravens (BAL) at Bills (BUF)"),
    (1,  "Steelers (PIT) at Jets (NYJ)"),
    (1,  "Texans (HOU) at Rams (LAR)"),
    (1,  "Titans (TEN) at Broncos (DEN)"),
    (2,  "49ers (SF) at Saints (NO)"),
    (2,  "Bears (CHI) at Lions (DET)"),
    (2,  "Bills (BUF) at Jets (NYJ)"),
    (2,  "Broncos (DEN) at Colts (IND)"),
    (2,  "Eagles (PHI) at Chiefs (KC)"),
    (2,  "Falcons (ATL) at Vikings (MIN)"),
    (2,  "Giants (NYG) at Cowboys (DAL)"),
    (2,  "Jaguars (JAX) at Bengals (CIN)"),
    (2,  "Panthers (CAR) at Cardinals (ARI)"),
    (2,  "Patriots (NE) at Dolphins (MIA)"),
    (2,  "Rams (LAR) at Titans (TEN)"),
    (2,  "Seahawks (SEA) at Steelers (PIT)"),
    (3,  "Bengals (CIN) at Vikings (MIN)"),
    (3,  "Broncos (DEN) at Chargers (LAC)"),
    (3,  "Cardinals (ARI) at 49ers (SF)"),
    (3,  "Chiefs (KC) at Giants (NYG)"),
    (3,  "Colts (IND) at Titans (TEN)"),
    (3,  "Cowboys (DAL) at Bears (CHI)"),
    (3,  "Falcons (ATL) at Panthers (CAR)"),
    (3,  "Jets (NYJ) at Buccaneers (TB)"),
    (3,  "Packers (GB) at Browns (CLE)"),
    (3,  "Raiders (LV) at Commanders (WAS)"),
    (3,  "Rams (LAR) at Eagles (PHI)"),
    (3,  "Saints (NO) at Seahawks (SEA)"),
    (3,  "Steelers (PIT) at Patriots (NE)"),
    (3,  "Texans (HOU) at Jaguars (JAX)"),
    (4,  "Bears (CHI) at Raiders (LV)"),
    (4,  "Browns (CLE) at Lions (DET)"),
    (4,  "Chargers (LAC) at Giants (NYG)"),
    (4,  "Colts (IND) at Rams (LAR)"),
    (4,  "Commanders (WAS) at Falcons (ATL)"),
    (4,  "Eagles (PHI) at Buccaneers (TB)"),
    (4,  "Jaguars (JAX) at 49ers (SF)"),
    (4,  "Packers (GB) at Cowboys (DAL)"),
    (4,  "Panthers (CAR) at Patriots (NE)"),
    (4,  "Ravens (BAL) at Chiefs (KC)"),
    (4,  "Saints (NO) at Bills (BUF)"),
    (4,  "Titans (TEN) at Texans (HOU)"),
    (4,  "Vikings (MIN) vs Steelers (PIT) (Dublin)"),
    (5,  "Broncos (DEN) at Eagles (PHI)"),
    (5,  "Buccaneers (TB) at Seahawks (SEA)"),
    (5,  "Commanders (WAS) at Chargers (LAC)"),
    (5,  "Cowboys (DAL) at Jets (NYJ)"),
    (5,  "Dolphins (MIA) at Panthers (CAR)"),
    (5,  "Giants (NYG) at Saints (NO)"),
    (5,  "Lions (DET) at Bengals (CIN)"),
    (5,  "Patriots (NE) at Bills (BUF)"),
    (5,  "Raiders (LV) at Colts (IND)"),
    (5,  "Titans (TEN) at Cardinals (ARI)"),
    (5,  "Vikings (MIN) vs Browns (CLE) (Tottenham)"),
    (6,  "49ers (SF) at Buccaneers (TB)"),
    (6,  "Bengals (CIN) at Packers (GB)"),
    (6,  "Broncos (DEN) vs Jets (NYJ) (Tottenham)"),
    (6,  "Browns (CLE) at Steelers (PIT)"),
    (6,  "Cardinals (ARI) at Colts (IND)"),
    (6,  "Chargers (LAC) at Dolphins (MIA)"),
    (6,  "Cowboys (DAL) at Panthers (CAR)"),
    (6,  "Lions (DET) at Chiefs (KC)"),
    (6,  "Patriots (NE) at Saints (NO)"),
    (6,  "Rams (LAR) at Ravens (BAL)"),
    (6,  "Seahawks (SEA) at Jaguars (JAX)"),
    (6,  "Titans (TEN) at Raiders (LV)"),
    (7,  "Colts (IND) at Chargers (LAC)"),
    (7,  "Commanders (WAS) at Cowboys (DAL)"),
    (7,  "Falcons (ATL) at 49ers (SF)"),
    (7,  "Giants (NYG) at Broncos (DEN)"),
    (7,  "Packers (GB) at Cardinals (ARI)"),
    (7,  "Panthers (CAR) at Jets (NYJ)"),
    (7,  "Patriots (NE) at Titans (TEN)"),
    (7,  "Rams (LAR) vs Jaguars (JAX) (Wembley)"),
    (8,  "49ers (SF) at Texans (HOU)"),
    (8,  "Bears (CHI) at Ravens (BAL)"),
    (8,  "Bills (BUF) at Panthers (CAR)"),
    (8,  "Browns (CLE) at Patriots (NE)"),
    (8,  "Buccaneers (TB) at Saints (NO)"),
    (8,  "Cowboys (DAL) at Broncos (DEN)"),
    (8,  "Dolphins (MIA) at Falcons (ATL)"),
    (8,  "Giants (NYG) at Eagles (PHI)"),
    (8,  "Jets (NYJ) at Bengals (CIN)"),
    (8,  "Packers (GB) at Steelers (PIT)"),
    (8,  "Titans (TEN) at Colts (IND)"),
    (9,  "49ers (SF) at Giants (NYG)"),
    (9,  "Bears (CHI) at Bengals (CIN)"),
    (9,  "Broncos (DEN) at Texans (HOU)"),
    (9,  "Chargers (LAC) at Titans (TEN)"),
    (9,  "Chiefs (KC) at Bills (BUF)"),
    (9,  "Colts (IND) at Steelers (PIT)"),
    (9,  "Falcons (ATL) at Patriots (NE)"),
    (9,  "Jaguars (JAX) at Raiders (LV)"),
    (9,  "Panthers (CAR) at Packers (GB)"),
    (9,  "Saints (NO) at Rams (LAR)"),
    (9,  "Seahawks (SEA) at Commanders (WAS)"),
    (9,  "Vikings (MIN) at Lions (DET)"),
    (10, "Bills (BUF) at Dolphins (MIA)"),
    (10, "Browns (CLE) at Jets (NYJ)"),
    (10, "Cardinals (ARI) at Seahawks (SEA)"),
    (10, "Falcons (ATL) vs Colts (IND) (Berlin)"),
    (10, "Giants (NYG) at Bears (CHI)"),
    (10, "Jaguars (JAX) at Texans (HOU)"),
    (10, "Lions (DET) at Commanders (WAS)"),
    (10, "Patriots (NE) at Buccaneers (TB)"),
    (10, "Rams (LAR) at 49ers (SF)"),
    (10, "Ravens (BAL) at Vikings (MIN)"),
    (10, "Saints (NO) at Panthers (CAR)"),
    (10, "Steelers (PIT) at Chargers (LAC)"),
    (11, "49ers (SF) at Cardinals (ARI)"),
    (11, "Bears (CHI) at Vikings (MIN)"),
    (11, "Bengals (CIN) at Steelers (PIT)"),
    (11, "Buccaneers (TB) at Bills (BUF)"),
    (11, "Chargers (LAC) at Jaguars (JAX)"),
    (11, "Chiefs (KC) at Broncos (DEN)"),
    (11, "Commanders (WAS) vs Dolphins (MIA) (Madrid)"),
    (11, "Lions (DET) at Eagles (PHI)"),
    (11, "Packers (GB) at Giants (NYG)"),
    (11, "Panthers (CAR) at Falcons (ATL)"),
    (11, "Ravens (BAL) at Browns (CLE)"),
    (11, "Seahawks (SEA) at Rams (LAR)"),
    (11, "Texans (HOU) at Titans (TEN)"),
    (12, "Browns (CLE) at Raiders (LV)"),
    (12, "Buccaneers (TB) at Rams (LAR)"),
    (12, "Colts (IND) at Chiefs (KC)"),
    (12, "Eagles (PHI) at Cowboys (DAL)"),
    (12, "Falcons (ATL) at Saints (NO)"),
    (12, "Giants (NYG) at Lions (DET)"),
    (12, "Jaguars (JAX) at Cardinals (ARI)"),
    (12, "Jets (NYJ) at Ravens (BAL)"),
    (12, "Patriots (NE) at Bengals (CIN)"),
    (12, "Seahawks (SEA) at Titans (TEN)"),
    (12, "Steelers (PIT) at Bears (CHI)"),
    (12, "Vikings (MIN) at Packers (GB)"),
    (13, "49ers (SF) at Browns (CLE)"),
    (13, "Bills (BUF) at Steelers (PIT)"),
    (13, "Broncos (DEN) at Commanders (WAS)"),
    (13, "Cardinals (ARI) at Buccaneers (TB)"),
    (13, "Falcons (ATL) at Jets (NYJ)"),
    (13, "Jaguars (JAX) at Titans (TEN)"),
    (13, "Raiders (LV) at Chargers (LAC)"),
    (13, "Rams (LAR) at Panthers (CAR)"),
    (13, "Saints (NO) at Dolphins (MIA)"),
    (13, "Texans (HOU) at Colts (IND)"),
    (13, "Vikings (MIN) at Seahawks (SEA)"),
    (14, "Bears (CHI) at Packers (GB)"),
    (14, "Bengals (CIN) at Bills (BUF)"),
    (14, "Broncos (DEN) at Raiders (LV)"),
    (14, "Colts (IND) at Jaguars (JAX)"),
    (14, "Commanders (WAS) at Vikings (MIN)"),
    (14, "Dolphins (MIA) at Jets (NYJ)"),
    (14, "Rams (LAR) at Cardinals (ARI)"),
    (14, "Saints (NO) at Buccaneers (TB)"),
    (14, "Seahawks (SEA) at Falcons (ATL)"),
    (14, "Texans (HOU) at Chiefs (KC)"),
    (14, "Titans (TEN) at Browns (CLE)"),
    (15, "Bills (BUF) at Patriots (NE)"),
    (15, "Browns (CLE) at Bears (CHI)"),
    (15, "Cardinals (ARI) at Texans (HOU)"),
    (15, "Chargers (LAC) at Chiefs (KC)"),
    (15, "Colts (IND) at Seahawks (SEA)"),
    (15, "Jets (NYJ) at Jaguars (JAX)"),
    (15, "Lions (DET) at Rams (LAR)"),
    (15, "Packers (GB) at Broncos (DEN)"),
    (15, "Panthers (CAR) at Saints (NO)"),
    (15, "Raiders (LV) at Eagles (PHI)"),
    (15, "Ravens (BAL) at Bengals (CIN)"),
    (15, "Titans (TEN) at 49ers (SF)"),
    (15, "Vikings (MIN) at Cowboys (DAL)"),
    (16, "Bengals (CIN) at Dolphins (MIA)"),
    (16, "Bills (BUF) at Browns (CLE)"),
    (16, "Buccaneers (TB) at Panthers (CAR)"),
    (16, "Chargers (LAC) at Cowboys (DAL)"),
    (16, "Chiefs (KC) at Titans (TEN)"),
    (16, "Falcons (ATL) at Cardinals (ARI)"),
    (16, "Jaguars (JAX) at Broncos (DEN)"),
    (16, "Jets (NYJ) at Saints (NO)"),
    (16, "Patriots (NE) at Ravens (BAL)"),
    (16, "Raiders (LV) at Texans (HOU)"),
    (16, "Steelers (PIT) at Lions (DET)"),
    (16, "Vikings (MIN) at Giants (NYG)"),
    (17, "Bears (CHI) at 49ers (SF)"),
    (17, "Buccaneers (TB) at Dolphins (MIA)"),
    (17, "Cardinals (ARI) at Bengals (CIN)"),
    (17, "Eagles (PHI) at Bills (BUF)"),
    (17, "Giants (NYG) at Raiders (LV)"),
    (17, "Jaguars (JAX) at Colts (IND)"),
    (17, "Patriots (NE) at Jets (NYJ)"),
    (17, "Saints (NO) at Titans (TEN)"),
    (17, "Seahawks (SEA) at Panthers (CAR)"),
    (17, "Steelers (PIT) at Browns (CLE)"),
    (18, "Browns (CLE) at Bengals (CIN)"),
    (18, "Cardinals (ARI) at Rams (LAR)"),
    (18, "Chargers (LAC) at Broncos (DEN)"),
    (18, "Chiefs (KC) at Raiders (LV)"),
    (18, "Colts (IND) at Texans (HOU)"),
    (18, "Commanders (WAS) at Eagles (PHI)"),
    (18, "Cowboys (DAL) at Giants (NYG)"),
    (18, "Dolphins (MIA) at Patriots (NE)"),
    (18, "Jets (NYJ) at Bills (BUF)"),
    (18, "Lions (DET) at Bears (CHI)"),
    (18, "Packers (GB) at Vikings (MIN)"),
    (18, "Ravens (BAL) at Steelers (PIT)"),
    (18, "Saints (NO) at Falcons (ATL)"),
    (18, "Titans (TEN) at Jaguars (JAX)"),
]

def main():
    print("STARTING DATA COLLECTION")

    # start by getting all of the markets
    print("\nStep 1: Fetching all KXNFLGAME HISTORICAL markets")
    kalshi_markets = fetch_all_nfl_markets()
    if not kalshi_markets:
        print("Something wrong with creds, double check!!!!")
        return
    print(f"# of markets available: {len(kalshi_markets)}")

    # match from reddit thread --> kalshi market
    matched   = []
    unmatched = []
    seen      = set()

    for week, game_str in REDDIT_GAMES:
        market = match_reddit_to_kalshi(game_str, kalshi_markets, week=week)

        if market is None:
            print(f"  NO MATCH:  Week {week:2d} — {game_str}")
            unmatched.append((week, game_str))
            continue

        ticker = market.get("ticker", "")
        if ticker in seen:
            print(f"  DUPLICATE: Week {week:2d} — {game_str} → {ticker}")
            unmatched.append((week, game_str))
            continue

        seen.add(ticker)
        matched.append({
            "week":          week,
            "reddit_game":   game_str,
            "kalshi_ticker": ticker,
            "kalshi_title":  market.get("title", ""),
        })

    if unmatched:
        print(f"Unmatched ({len(unmatched)}):")
        for week, game_str in unmatched:
            print(f"  Week {week:2d}: {game_str}")

    # get the candlesticks
    all_dfs = []

    for i, match in enumerate(matched):
        ticker    = match["kalshi_ticker"]
        safe_name = ticker.replace("/", "_").replace(":", "_")
        csv_path  = os.path.join(OUTPUT_DIR, f"{safe_name}.csv")

        # don't do duplicate work
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            all_dfs.append(df)
            continue

        df = fetch_candlesticks(ticker)

        if df.empty:
            print("no data.")
            continue

        df["kalshi_ticker"] = ticker
        df["kalshi_title"]  = match["kalshi_title"]
        df["reddit_game"]   = match["reddit_game"]
        df["week"]          = match["week"]

        df.to_csv(csv_path, index=False) # converting to CSV for visual inspection
        print(f"{len(df)} rows → {csv_path}")

        all_dfs.append(df)
        time.sleep(0.3)

    # combine everything into parquet (compacted data representation)
    if all_dfs:
        master      = pd.concat(all_dfs, ignore_index=True)
        master_path = os.path.join(OUTPUT_DIR, "kalshi_all_games.parquet")
        master.to_parquet(master_path, index=False)
    else:
        print("\nNo data fetched.")

    print("\nDone.")


if __name__ == "__main__":
    main()