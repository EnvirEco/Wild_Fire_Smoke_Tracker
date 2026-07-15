"""
fetch_data.py
=============
Fetches current-day PM2.5 data from Air Quality Ontario for all 43 stations,
calculates health impacts using CCI AQBAT v3.0 scalars, and updates
index.html with fresh data.

Run manually:  python fetch_data.py
Run by GitHub Actions: automatically on schedule

Outputs: updates DATA, meta date/time/station count in index.html
"""

import re
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── Configuration ──────────────────────────────────────────────────────────────

BASELINE_UGM3 = 6.0    # clean air baseline µg/m³
ON_POP_M      = 15.1   # Ontario population millions
MIN_HOURS     = 6      # minimum hours for a valid daily mean
MAX_PM25      = 999    # sanity cap

STATIONS = [
    ('47045','Barrie'),         ('54012','Belleville'),
    ('46090','Brampton'),       ('21005','Brantford'),
    ('44008','Burlington'),     ('13001','Chatham'),
    ('56051','Cornwall'),       ('49010','Dorset'),
    ('15020','Grand Bend'),     ('28028','Guelph'),
    ('29000','Hamilton Downtown'),('29214','Hamilton Mountain'),
    ('29118','Hamilton West'),  ('52023','Kingston'),
    ('26060','Kitchener'),      ('15026','London'),
    ('13021','Merlin'),         ('44029','Milton'),
    ('46108','Mississauga'),    ('56010','Morrisburg'),
    ('48006','Newmarket'),      ('75010','North Bay'),
    ('44017','Oakville'),       ('45027','Oshawa'),
    ('51002','Ottawa Central'), ('51001','Ottawa Downtown'),
    ('49005','Parry Sound'),    ('51010','Petawawa'),
    ('59006','Peterborough'),   ('16015','Port Stanley'),
    ('14111','Sarnia'),         ('71078','Sault Ste. Marie'),
    ('27067','St. Catharines'), ('48002','Stouffville'),
    ('77233','Sudbury'),        ('63200','Thunder Bay'),
    ('18007','Tiverton'),       ('31129','Toronto Downtown'),
    ('33003','Toronto East'),   ('34021','Toronto North'),
    ('35125','Toronto West'),   ('12008','Windsor Downtown'),
    ('12016','Windsor West'),
]

# ── Fetch ───────────────────────────────────────────────────────────────────────

def fetch_station(sid, day, month, year, retries=3):
    url = (f'https://www.airqualityontario.com/aqhi/chart.php'
           f'?stationid={sid}&pol_code=124'
           f'&start_day={day}&start_month={month}&start_year={year}'
           f'&showType=table')
    ds = f"{year}-{month:02d}-{day:02d}"

    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=20,
                             headers={'User-Agent': 'CCI-smoke-tracker/1.0'})
            tables = re.findall(r'<table[^>]*>(.*?)</table>', r.text, re.DOTALL)
            if not tables:
                time.sleep(1 + attempt)
                continue
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[0], re.DOTALL)
            for row in rows[1:]:
                cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                cleaned = [re.sub(r'<[^>]+>', ' ', c).strip() for c in cells]
                if cleaned and ds in cleaned[0]:
                    nums = []
                    for v in cleaned[1:]:
                        try:
                            f = float(v)
                            if 0 <= f <= MAX_PM25:
                                nums.append(f)
                        except ValueError:
                            pass
                    if len(nums) >= MIN_HOURS:
                        return {
                            'mean': round(sum(nums) / len(nums), 1),
                            'max':  round(max(nums), 1),
                            'hrs':  len(nums),
                        }
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def fetch_all():
    # Use Eastern time for the date
    eastern = timezone(timedelta(hours=-4))  # EDT
    now_et  = datetime.now(eastern)
    day, month, year = now_et.day, now_et.month, now_et.year
    date_str = now_et.strftime('%Y-%m-%d')
    ampm = 'a.m.' if now_et.hour < 12 else 'p.m.'
    time_str = now_et.strftime('%-I:%M') + ' ' + ampm + ' EDT'

    print(f"Fetching {date_str} ({time_str})...")

    results = []
    no_data = []
    for sid, name in STATIONS:
        r = fetch_station(sid, day, month, year)
        if r:
            results.append({'name': name, 'sid': sid, **r})
            print(f"  ✓ {name}: {r['mean']} µg/m³ ({r['hrs']}h)")
        else:
            no_data.append(name)
            print(f"  – {name}: no data")

    print(f"\n{len(results)}/43 stations reporting")
    if no_data:
        print(f"No data: {', '.join(no_data)}")

    return {
        'date':      date_str,
        'time':      time_str,
        'n_stations': len(results),
        'stations':  results,
    }


# ── Update index.html ───────────────────────────────────────────────────────────

def update_html(data):
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            html = f.read()
    except FileNotFoundError:
        print("ERROR: index.html not found — run from repo root")
        sys.exit(1)

    n     = data['n_stations']
    d     = data['date']
    t     = data['time']
    # Format date for display e.g. "July 15, 2026"
    dt    = datetime.strptime(d, '%Y-%m-%d')
    d_fmt = dt.strftime('%B %-d, %Y')

    # Replace DATA object
    data_json = json.dumps(data)
    html = re.sub(
        r'const DATA\s*=\s*\{[^;]+\};',
        lambda m, _d=data_json: f'const DATA = {_d};',
        html,
        flags=re.DOTALL
    )

    # Update meta date
    html = re.sub(
        r'<span>[A-Z][a-z]+ \d+, \d{4}</span>',
        f'<span>{d_fmt}</span>',
        html
    )

    # Update meta time
    t_escaped = t
    html = re.sub(
        r'<span>Updated [^<]+</span>',
        lambda m, _t=t_escaped: f'<span>Updated {_t}</span>',
        html
    )

    # Update station count text
    html = re.sub(
        r'<span>\d+ of 43 stations reporting</span>',
        f'<span>{n} of 43 stations reporting</span>',
        html
    )

    # Update hours of observations
    if data['stations']:
        max_hrs = max(s['hrs'] for s in data['stations'])
        html = re.sub(
            r'<span>\d+ hours? of observations</span>',
            f'<span>{max_hrs} hours of observations</span>',
            html
        )

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"index.html updated: {n}/43 stations, {d_fmt}, {t}")


# ── Main ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    data = fetch_all()
    if not data['stations']:
        print("No station data retrieved — index.html not updated")
        sys.exit(1)
    update_html(data)
    print("Done.")
