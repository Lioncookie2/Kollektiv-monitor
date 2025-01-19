from flask import Flask, jsonify, render_template
import requests
import sqlite3
import threading
from time import sleep
import xml.etree.ElementTree as ET
import logging
import datetime
import os


# ----------------------------------------------------------------------
# Konfigurasjon
# ----------------------------------------------------------------------
API_URL = "https://api.entur.io/realtime/v1/rest/vm"
API_HEADERS = {"ET-Client-Name": "kollektiv-forsinkelser", "Accept": "application/xml"}

DATABASE = "train_delays2.db"
FETCH_INTERVAL = 60  # sekunder mellom hver API-henting

app = Flask(__name__)

current_day = datetime.date.today()

logging.basicConfig(level=logging.INFO, filename="fetch_log.log", filemode="a",
                    format="%(asctime)s - %(levelname)s - %(message)s")


# ----------------------------------------------------------------------
# Opprett (eller oppdater) database
# ----------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Opprett delays-tabell med UNIQUE constraint
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS delays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line TEXT,
            station TEXT,
            transport TEXT,
            delay_minutes REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(line, station, transport)
        )
    """)

    # Opprett daily_history for historikk
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_history (
            date TEXT,
            transport TEXT,
            total_delays INTEGER,
            average_delay REAL,
            max_delay REAL,
            PRIMARY KEY(date, transport)
        )
    """)

    conn.commit()
    conn.close()

# ----------------------------------------------------------------------
# Parse API-respons (Entur) - delt opp på buss, tog, trikk
# ----------------------------------------------------------------------
def parse_entur_response(xml_response, do_print=False):
    ns = {'siri': 'http://www.siri.org.uk/siri'}
    root = ET.fromstring(xml_response)

    # Hent ut alle <VehicleActivity>...
    activities = root.findall('.//siri:VehicleActivity', ns)

    if do_print:
        print("\n== Starter parsing av aktiviteter (BUSS, TOG, TRIKK) ==\n")

    delays = []

    for i, activity in enumerate(activities):
        journey = activity.find('.//siri:MonitoredVehicleJourney', ns)
        if journey is None:
            continue

        # Sjekk transporttype
        vehicle_mode_elem = journey.find('siri:VehicleMode', ns)
        transport_type = vehicle_mode_elem.text.lower() if vehicle_mode_elem is not None else "ukjent"

        # Bare fortsett hvis det er buss, tog eller trikk
        if transport_type not in ["bus", "rail", "tram"]:
            continue

        # Forsinkelse
        delay_elem = journey.find('siri:Delay', ns)
        total_delay_minutes = 0.0
        if delay_elem is not None and delay_elem.text and delay_elem.text.startswith('PT'):
            total_delay_minutes = parse_delay_to_minutes(delay_elem.text)

        # Hopper over hvis forsinkelsen < 2 min
        if total_delay_minutes < 2.0:
            continue

        # Linjenavn
        line_elem = journey.find('siri:PublishedLineName', ns)
        line_ref = journey.find('siri:LineRef', ns)
        line_name = (line_elem.text if line_elem is not None
                     else line_ref.text if line_ref is not None
                     else "Ukjent")

        # Hvis linjenavn fortsatt er "Ukjent," prøv å hente det fra <PublishedLineName>
        if line_name == "Ukjent":
            published_line_name = journey.find('siri:PublishedLineName', ns)
            if published_line_name is not None and published_line_name.text:
                line_name = published_line_name.text
                if do_print:
                    print(f"Linjenavn oppdatert til: {line_name}")

        # Felles for alle: <MonitoredCall> -> <StopPointName> = "Nåværende posisjon"
        monitored_call = journey.find('siri:MonitoredCall', ns)
        station_name = "Ukjent"
        if monitored_call is not None:
            station_elem = monitored_call.find('siri:StopPointName', ns)
            if station_elem is not None:
                station_name = station_elem.text

        # For enkel oversikt vil vi vite endestasjonen. For buss/trikk = DestinationDisplay
        destination_name = "Ukjent"
        if monitored_call is not None:
            dest_display_elem = monitored_call.find('siri:DestinationDisplay', ns)
            if dest_display_elem is not None:
                destination_name = dest_display_elem.text

        journey_dest_name = journey.find('siri:DestinationName', ns)
        if journey_dest_name is not None and journey_dest_name.text:
            if destination_name == "Ukjent":
                destination_name = journey_dest_name.text

        # Logg resultatet
        if do_print:
            print(f"[{transport_type.upper()}] Linje: {line_name} | Stasjon: {station_name} | Endestasjon: {destination_name} | Forsinkelse: {total_delay_minutes:.1f} min")

        # Legg til i listen som skal returneres
        delays.append({
            "line": line_name,
            "station": station_name,
            "transport": transport_type,
            "delay_minutes": round(total_delay_minutes, 2),
        })

    print(f"Fetched {len(delays)} delays")
    return delays


# ----------------------------------------------------------------------
# Arkivring av gårsdagens data
# ----------------------------------------------------------------------
def archive_previous_day_data(day: datetime.date):
    day_str = day.isoformat()
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Gruppér på transport
    cursor.execute("""
        SELECT transport, COUNT(*), AVG(delay_minutes), MAX(delay_minutes)
        FROM delays
        WHERE date(timestamp) = ?
        GROUP BY transport
    """, (day_str,))
    rows = cursor.fetchall()

    # For hver transport-linje, lagre i daily_history
    for (transport, count_delays, avg_delay, max_delay) in rows:
        if not avg_delay:
            avg_delay = 0
        if not max_delay:
            max_delay = 0

        cursor.execute("""
            INSERT OR REPLACE INTO daily_history (date, transport, total_delays, average_delay, max_delay)
            VALUES (?, ?, ?, ?, ?)
        """, (
            day_str,
            transport,
            count_delays,
            avg_delay,
            max_delay
        ))

    # Nå sletter vi forrige dags rader fra 'delays', som før
    cursor.execute("""
        DELETE FROM delays
        WHERE date(timestamp) = ?
    """, (day_str,))

    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Hjelpefunksjon: parse "PT1M30S" -> float (minutter)
# ----------------------------------------------------------------------
def parse_delay_to_minutes(pt_string):
    text = pt_string.replace("PT", "")
    if text.startswith("-"):
        # Ignorerer negative forsinkelser (tidlige avganger)
        return 0

    minutes = 0
    seconds = 0

    if "M" in text:
        parts = text.split("M")
        try:
            minutes = int(parts[0])
        except:
            minutes = 0
        if len(parts) > 1 and "S" in parts[1]:
            try:
                seconds = int(parts[1].replace("S", ""))
            except:
                seconds = 0
    elif "S" in text:
        try:
            seconds = int(text.replace("S", ""))
        except:
            seconds = 0

    return minutes + seconds / 60.0


# ----------------------------------------------------------------------
# Hent data fra API (kalles av bakgrunnstråd)
# ----------------------------------------------------------------------
def fetch_delays():
    try:
        response = requests.get(API_URL, headers=API_HEADERS, timeout=10)
        response.raise_for_status()
        print("Data hentet fra API")
        return parse_entur_response(response.text, do_print=True)
    except requests.exceptions.RequestException as e:
        logging.error(f"API-henting feilet: {e}")
        print(f"Feil ved API-henting: {e}")
        return []


# ----------------------------------------------------------------------
# Lagre data i databasen
# ----------------------------------------------------------------------
def save_delays(delays):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Valgfritt: Hvis du fremdeles vil slette data eldre enn 1 time, kan du la dette stå.
    # Men husk at du da kaster ut alt mulig. Hvis du vil holde dagens data hele dagen,
    # kan du kommentere ut denne linjen:
    #
    # cursor.execute("DELETE FROM delays WHERE timestamp < datetime('now', '-1 hour')")

    for delay in delays:
        cursor.execute("""
            INSERT INTO delays (line, station, transport, delay_minutes, timestamp)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(line, station, transport) DO UPDATE
            SET
                delay_minutes = excluded.delay_minutes,
                timestamp = excluded.timestamp
        """, (
            delay["line"],
            delay["station"],
            delay["transport"],
            delay["delay_minutes"]
        ))
    
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Bakgrunnstråd: henter og lagrer data hver FETCH_INTERVAL
# ----------------------------------------------------------------------
def start_background_job():
    global current_day
    print("Starter background thread...")
    while True:
        try:
            # 1) Hent og lagre dagens forsinkelser
            delays = fetch_delays()      # henter fra Entur
            save_delays(delays)         # lagrer inn i db (delays-tabellen)

            # 2) Sjekk om vi har ny dag
            today = datetime.date.today()
            if today != current_day:
                # Arkiver forrige dag 
                archive_previous_day_data(current_day)
                current_day = today

            # 3) Vent litt
            sleep(FETCH_INTERVAL)

        except Exception as e:
            logging.error(f"Bakgrunnsjobb feilet: {e}")
            sleep(10)  # Vent før nytt forsøk


# ----------------------------------------------------------------------
# Dailystats
# ----------------------------------------------------------------------
@app.route("/daily_stats")
def daily_stats():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Eks: hent siste 7 dager
    cursor.execute("""
        SELECT date, total_delays, average_delay, max_delay
        FROM daily_history
        ORDER BY date DESC
        LIMIT 7
    """)
    rows = cursor.fetchall()
    print(cursor.fetchall())
    print(rows)
    conn.close()

    # Snu rekkefølgen om du vil ha eldste først
    rows.reverse()

    result = []
    for row in rows:
        result.append({
            "date": row[0],
            "total_delays": row[1],
            "average_delay": row[2],
            "max_delay": row[3]
        })
    return jsonify(result)

# ----------------------------------------------------------------------
# API-endepunkt: Hent forsinkelsesdata (siste time)
# ----------------------------------------------------------------------
@app.route("/delays")
def get_delays():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    # Filtrer på dagens dato (lokaltid). 
    # Bruker "date('now','localtime')" for å matche dagens YYYY-MM-DD i lokaltid.
    cursor.execute("""
        SELECT line, station, transport, delay_minutes, timestamp
        FROM delays
        WHERE date(timestamp) = date('now','localtime')
        ORDER BY timestamp DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        line = r[0]
        # Rensk litt i linje-feltet om det har :Line:
        if ':Line:' in line:
            line = line.split(':Line:')[1]
            if '_' in line:
                line = line.split('_')[0]

        result.append({
            "line": line,
            "station": r[1],
            "transport": r[2],
            "delay_minutes": r[3],
            "timestamp": r[4]
        })

    print(f"Sending {len(result)} delays from *today* to frontend.")
    return jsonify(result)



# ----------------------------------------------------------------------
# API-endepunkt: enkel statistikk (siste 24 timer) -- (Valgfritt)
# ----------------------------------------------------------------------
@app.route("/stats")
def stats():
    print("Henter statistikk...")
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*), AVG(delay_minutes)
        FROM delays
        WHERE timestamp >= datetime('now', '-1 day')
    """)
    total, avg_delay = cursor.fetchone()
    if avg_delay is None:
        avg_delay = 0
    print(f"Totalt antall forsinkelser siste døgn: {total}, Gjennomsnittlig forsinkelse: {avg_delay:.2f}")
    conn.close()
    return jsonify({
        "total_delays_24h": total,
        "average_delay_24h": round(avg_delay, 1)
    })

# ----------------------------------------------------------------------
# Historisk statistikk
# ----------------------------------------------------------------------
@app.route("/total_2025/<transport>")
def total_2025(transport):
    print("=== /total_2025 route triggered with transport =", transport)
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()

        if transport == "all":
            cursor.execute("""
                SELECT SUM(total_delays)
                FROM daily_history
                WHERE date >= '2025-01-01' AND date <= '2025-12-31'
            """)
        else:
            cursor.execute("""
                SELECT SUM(total_delays)
                FROM daily_history
                WHERE date >= '2025-01-01' AND date <= '2025-12-31'
                  AND transport = ?
            """, (transport,))

        row = cursor.fetchone()
        total_count = row[0] if row and row[0] else 0

        conn.close()
        return jsonify({"transport": transport, "total_2025": total_count})

    except Exception as e:
        print(f"Feil i total_2025({transport}):", e)
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------------------------
# Hovedside
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ----------------------------------------------------------------------
# Oppstart
# ----------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    # Start bakgrunnstråd
    threading.Thread(target=start_background_job, daemon=True).start()

    # HENT PORT-FRA MILJØVARIABEL => Standard 5000 hvis den ikke finnes
    port = int(os.environ.get("PORT", 5000))

    # Kjør på "0.0.0.0" slik at Render kan exponse den
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
