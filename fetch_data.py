"""
fetch_data.py
=============
Fetches current-day PM2.5 from:
  - Air Quality Ontario (AQO) — 43 stations, hourly scrape
  - BC Ministry of Environment — single CSV, 60+ stations, hourly
  - Alberta Environment OData API — 56 stations, hourly

Updates index.html with DATA, DATA_AB, DATA_BC objects and meta fields.

Run manually:  python fetch_data.py
Run by GitHub Actions: automatically on schedule
"""

import re, sys, json, time, random, io
import requests
from datetime import datetime, date, timezone, timedelta

# ── Configuration ──────────────────────────────────────────────────────────────

BASELINE  = 6.0
MIN_HOURS = 6
MAX_PM25  = 999
RETRIES   = 4
TIMEOUT   = 30
DELAY_MIN = 1.5
DELAY_MAX = 3.5
RETRY_WAIT = 90

ON_STATIONS = [
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

BC_EXCLUDE = ['Mine','Smelter','Mill','Pulp','Kitimat','Alcan','Teck','Cominco']

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
]

# ── Ontario ─────────────────────────────────────────────────────────────────────

def fetch_on_station(sid, day, month, year):
    url = (f'https://www.airqualityontario.com/aqhi/chart.php'
           f'?stationid={sid}&pol_code=124'
           f'&start_day={day}&start_month={month}&start_year={year}&showType=table')
    ds = f"{year}-{month:02d}-{day:02d}"
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={
                'User-Agent': random.choice(USER_AGENTS),
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-CA,en;q=0.9',
                'Referer': 'https://www.airqualityontario.com/',
            })
            tables = re.findall(r'<table[^>]*>(.*?)</table>', r.text, re.DOTALL)
            if not tables:
                time.sleep(random.uniform(1,2)); continue
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[0], re.DOTALL)
            for row in rows[1:]:
                cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
                cleaned = [re.sub(r'<[^>]+>',' ',c).strip() for c in cells]
                if cleaned and ds in cleaned[0]:
                    nums = [float(v) for v in cleaned[1:]
                            if v.replace('.','').isdigit() and 0<=float(v)<=MAX_PM25]
                    if len(nums) >= MIN_HOURS:
                        return {'mean':round(sum(nums)/len(nums),1),
                                'max':round(max(nums),1),'hrs':len(nums)}
        except:
            if attempt < RETRIES-1: time.sleep(2**attempt)
    return None


def fetch_ontario():
    edt = timezone(timedelta(hours=-4))
    now = datetime.now(edt)
    day, month, year = now.day, now.month, now.year
    date_str = now.strftime('%Y-%m-%d')
    ampm = 'a.m.' if now.hour < 12 else 'p.m.'
    time_str = now.strftime('%-I:%M') + ' ' + ampm + ' EDT'

    print(f"\nOntario: fetching {len(ON_STATIONS)} stations...")
    results, failed = {}, []

    for sid, name in ON_STATIONS:
        r = fetch_on_station(sid, day, month, year)
        if r:
            results[sid] = {'name':name,'sid':sid,**r}
            print(f"  ✓ {name}: {r['mean']} µg/m³")
        else:
            failed.append((sid,name))
            print(f"  – {name}: no data")
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    if failed:
        print(f"\n  Retrying {len(failed)} stations after {RETRY_WAIT}s...")
        time.sleep(RETRY_WAIT)
        for sid, name in failed:
            r = fetch_on_station(sid, day, month, year)
            if r:
                results[sid] = {'name':name,'sid':sid,**r}
                print(f"  ✓ {name}: {r['mean']} µg/m³ [retry]")
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    stations = sorted(results.values(), key=lambda x: x['name'])
    print(f"  Ontario: {len(stations)}/43 stations")
    return {'date':date_str,'time':time_str,
            'n_stations':len(stations),'stations':stations}


# ── BC ───────────────────────────────────────────────────────────────────────────

def fetch_bc():
    pdt = timezone(timedelta(hours=-7))
    now = datetime.now(pdt)
    date_str = now.strftime('%Y-%m-%d')
    ampm = 'a.m.' if now.hour < 12 else 'p.m.'
    time_str = now.strftime('%-I:%M') + ' ' + ampm + ' PDT'
    today = date.fromisoformat(date_str)

    print(f"\nBC: fetching CSV from BC ENV...")
    url = ('https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/'
           'Hourly_Raw_Air_Data/Air_Quality/PM25.csv')
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f"  BC fetch failed: {e}")
        return None

    import csv
    from collections import defaultdict
    date_str_today = today.strftime('%Y-%m-%d')
    reader = csv.DictReader(io.StringIO(r.text))
    station_vals  = defaultdict(list)
    station_maxes = defaultdict(float)
    station_emsid = {}

    for row in reader:
        dt_raw = row.get('DATE_PST','')
        if not dt_raw.startswith(date_str_today):
            continue
        name = row.get('STATION_NAME','').strip()
        ems  = row.get('EMS_ID','').strip()
        # Exclude industrial
        if any(ex.lower() in name.lower() for ex in BC_EXCLUDE):
            continue
        try:
            v = float(row.get('RAW_VALUE',''))
            if not (0 <= v <= MAX_PM25):
                continue
        except (ValueError, TypeError):
            continue
        station_vals[name].append(v)
        if v > station_maxes[name]:
            station_maxes[name] = v
        station_emsid[name] = ems

    # If fewer than 10 stations have 3+ hours, fall back to yesterday
    BC_MIN = 3
    qualifying = {k:v for k,v in station_vals.items() if len(v) >= BC_MIN}
    if len(qualifying) < 10:
        print(f"  BC: only {len(qualifying)} stations today — using yesterday")
        from datetime import timedelta as td
        yesterday = (today - td(days=1)).strftime('%Y-%m-%d')
        r3 = requests.get(url, timeout=60)
        reader3 = csv.DictReader(io.StringIO(r3.text))
        station_vals2  = defaultdict(list)
        station_maxes2 = defaultdict(float)
        station_emsid2 = {}
        for row in reader3:
            if not row.get('DATE_PST','').startswith(yesterday): continue
            name = row.get('STATION_NAME','').strip()
            ems  = row.get('EMS_ID','').strip()
            if any(ex.lower() in name.lower() for ex in BC_EXCLUDE): continue
            try:
                v = float(row.get('RAW_VALUE',''))
                if not (0 <= v <= MAX_PM25): continue
            except: continue
            station_vals2[name].append(v)
            if v > station_maxes2[name]: station_maxes2[name] = v
            station_emsid2[name] = ems
        station_vals  = station_vals2
        station_maxes = station_maxes2
        station_emsid = station_emsid2
        BC_MIN = MIN_HOURS
        date_str = yesterday
        time_str = 'prior day data'

    stations = []
    for name, vals in station_vals.items():
        if len(vals) >= BC_MIN:
            stations.append({'name': name,
                             'sid':  station_emsid.get(name,''),
                             'mean': round(sum(vals)/len(vals),1),
                             'max':  round(station_maxes[name],1),
                             'hrs':  len(vals)})

    print(f"  BC: {len(stations)} stations reporting")
    return {'date':date_str,'time':time_str,
            'n_stations':len(stations),'stations':stations}


# ── Alberta ──────────────────────────────────────────────────────────────────────

def fetch_alberta():
    mdt = timezone(timedelta(hours=-6))
    now = datetime.now(mdt)
    datekey = now.strftime('%Y%m%d')
    ampm = 'a.m.' if now.hour < 12 else 'p.m.'
    time_str = now.strftime('%-I:%M') + ' ' + ampm + ' MDT'
    date_str = now.strftime('%Y-%m-%d')

    print(f"\nAlberta: fetching OData API...")
    base = 'https://data.environment.alberta.ca/EDWServices/aqhi/odata/'
    url  = (f"{base}StationMeasurements?"
            f"$filter=ParameterKey eq 62 and DateKey eq {datekey}"
            f"&$top=2000")

    all_rows = []
    skip = 0
    while True:
        try:
            r = requests.get(url + f'&$skip={skip}', timeout=30)
            data = r.json().get('value', [])
            if not data: break
            all_rows.extend(data)
            if len(data) < 200: break
            skip += 200
        except:
            break

    if not all_rows:
        print("  Alberta: no data")
        return None

    # Aggregate by station
    from collections import defaultdict
    station_data = defaultdict(list)
    station_names = {}
    for row in all_rows:
        v = row.get('Value')
        if v is not None and 0 <= float(v) <= MAX_PM25:
            key = str(row.get('StationKey',''))
            station_data[key].append(float(v))
            station_names[key] = str(row.get('StationName',''))

    stations = []
    for key, vals in station_data.items():
        if len(vals) >= MIN_HOURS:
            stations.append({'name': station_names[key],
                             'sid':  key,
                             'mean': round(sum(vals)/len(vals),1),
                             'max':  round(max(vals),1),
                             'hrs':  len(vals)})
    stations.sort(key=lambda x: -x['mean'])
    print(f"  Alberta: {len(stations)} stations reporting")
    return {'date':date_str,'time':time_str,
            'n_stations':len(stations),'stations':stations}


# ── Update index.html ────────────────────────────────────────────────────────────

def update_html(on_data, ab_data, bc_data):
    try:
        with open('index.html','r',encoding='utf-8') as f:
            html = f.read()
    except FileNotFoundError:
        print("ERROR: index.html not found"); sys.exit(1)

    # Update DATA (Ontario)
    dj = json.dumps(on_data)
    html = re.sub(r'const DATA\s*=\s*\{[^;]+\};',
                  lambda m,_d=dj: f'const DATA = {_d};',
                  html, flags=re.DOTALL)

    # Update DATA_AB
    if ab_data:
        dj_ab = json.dumps(ab_data)
        html = re.sub(r'const DATA_AB\s*=\s*\{[^;]+\};',
                      lambda m,_d=dj_ab: f'const DATA_AB = {_d};',
                      html, flags=re.DOTALL)

    # Update DATA_BC
    if bc_data:
        dj_bc = json.dumps(bc_data)
        if 'const DATA_BC' in html:
            html = re.sub(r'const DATA_BC\s*=\s*\{[^;]+\};',
                          lambda m,_d=dj_bc: f'const DATA_BC = {_d};',
                          html, flags=re.DOTALL)
        else:
            # Insert after DATA_AB
            html = html.replace(
                'const SCALARS=',
                f'const DATA_BC = {dj_bc};\n\nconst SCALARS='
            )

    # Update meta
    d_fmt = datetime.strptime(on_data['date'],'%Y-%m-%d').strftime('%B %-d, %Y')
    html = re.sub(r'<span id="meta-date">[^<]+</span>',
                  f'<span id="meta-date">{d_fmt}</span>', html)
    html = re.sub(r'<span id="meta-updated">[^<]+</span>',
                  lambda m,_t=on_data['time']:
                  f'<span id="meta-updated">Updated {_t}</span>', html)
    html = re.sub(r'<span id="meta-stations">[^<]+</span>',
                  f'<span id="meta-stations">'
                  f'{on_data["n_stations"]} of 43 stations reporting</span>', html)

    # Add BC to PROV config if not there
    if "'BC'" not in html and '"BC"' not in html:
        html = html.replace(
            "AB:{pop_M:4.9, label:'Alberta', tz:'MDT',n_total:56}",
            "AB:{pop_M:4.9, label:'Alberta', tz:'MDT',n_total:56},\n  BC:{pop_M:5.6, label:'British Columbia', tz:'PDT',n_total:62}"
        )

    with open('index.html','w',encoding='utf-8') as f:
        f.write(html)
    print(f"\nindex.html updated — ON:{on_data['n_stations']} AB:{ab_data['n_stations'] if ab_data else 0} BC:{bc_data['n_stations'] if bc_data else 0}")


# ── Main ─────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    on_data = fetch_ontario()
    ab_data = fetch_alberta()
    bc_data = fetch_bc()

    if not on_data or not on_data['stations']:
        print("No Ontario data — aborting"); sys.exit(1)

    update_html(on_data, ab_data, bc_data)
    print("Done.")
