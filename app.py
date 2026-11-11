import csv
import io
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request, redirect, session, url_for, make_response

BASE_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

try:
    import serial
except ImportError:
    serial = None

DB_PATH = BASE_DIR / "greenhouse.db"
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-development-secret")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# Demo login only. Do not use these credentials for a public production deployment.
DEMO_USERNAME = os.getenv("GREENHOUSE_USERNAME", "Macdonald")
DEMO_PASSWORD = os.getenv("GREENHOUSE_PASSWORD", "admin")
SERIAL_PORT = os.getenv("GREENHOUSE_SERIAL_PORT", "auto").strip()
SERIAL_BAUD = int(os.getenv("GREENHOUSE_SERIAL_BAUD", "9600"))
# Use an environment variable in real deployments. The supplied key is a convenience default for this prototype.
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_CITY = os.getenv("WEATHER_CITY", "Lilongwe")
state_lock = threading.Lock()
serial_lock = threading.Lock()
ser = None
latest = {
    "temperature": None, "humidity": None, "water_raw": None,
    "water_detected": None, "servo_angle": 0, "mode": "MANUAL",
    "fan_on": False, "led": False, "last_seen": None,
    "connected": False, "error": None,
}
weather_cache = {"data": None, "fetched_at": 0, "error": None}
last_history_write = 0


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            temperature REAL,
            humidity REAL,
            description TEXT,
            city TEXT,
            water_raw INTEGER,
            water_detected INTEGER,
            fan_on INTEGER,
            led_on INTEGER,
            mode TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            event TEXT NOT NULL,
            detail TEXT
        )""")


def log_event(event, detail=""):
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO events(recorded_at,event,detail) VALUES(?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"), event, detail))
    except sqlite3.Error:
        pass


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, message="Please log in first."), 401
            return redirect(url_for("index"))
        return fn(*args, **kwargs)
    return wrapper


def connect_serial():
    global ser
    if serial is None:
        latest["error"] = "pyserial is not installed. Install requirements.txt."
        return False
    try:
        if ser is None or not ser.is_open:
            port = SERIAL_PORT
            if not port or port.lower() == "auto":
                from serial.tools import list_ports
                ports = list(list_ports.comports())
                keywords = ("arduino", "usb", "ch340", "cp210", "ftdi", "ttyacm", "ttyusb", "usbmodem", "usbserial")
                likely = [p for p in ports if any(k in " ".join(str(v or "") for v in (p.device, p.description, p.manufacturer, p.product)).lower() for k in keywords)]
                if len(likely) == 1:
                    port = likely[0].device
                elif len(likely) > 1:
                    choices = ", ".join(p.device for p in likely)
                    raise RuntimeError(f"Multiple Arduino/USB serial devices found ({choices}). Set GREENHOUSE_SERIAL_PORT to the Arduino port.")
                else:
                    choices = ", ".join(p.device for p in ports) or "none"
                    raise RuntimeError(f"No Arduino/USB serial device found (available ports: {choices}). Connect the Arduino by USB or set GREENHOUSE_SERIAL_PORT.")
            ser = serial.Serial(port, SERIAL_BAUD, timeout=1, write_timeout=2)
            time.sleep(2)
            with state_lock:
                latest["connected"] = True
                latest["error"] = None
            return True
        return True
    except Exception as exc:
        with state_lock:
            latest["connected"] = False
            latest["error"] = str(exc) if isinstance(exc, RuntimeError) else f"Cannot open {port if 'port' in locals() else SERIAL_PORT}: {exc}"
        return False


def serial_reader():
    global ser
    while True:
        try:
            if ser is None or not ser.is_open:
                connect_serial()
                time.sleep(1)
                continue
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            data = json.loads(line)
            with state_lock:
                latest.update(data)
                latest["connected"] = True
                latest["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                latest["error"] = None
                # Compatibility with the FAN commands in the updated Arduino sketch.
                latest.setdefault("fan_on", False)
        except json.JSONDecodeError:
            continue
        except Exception as exc:
            with state_lock:
                latest["connected"] = False
                latest["error"] = str(exc)
            try:
                if ser:
                    ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(2)


def send_command(command):
    global ser
    try:
        with serial_lock:
            if ser is None or not ser.is_open:
                if not connect_serial():
                    return False, latest.get("error") or "Arduino is not connected."
            ser.write((command.strip() + "\n").encode("utf-8"))
        return True, "Command sent to Arduino."
    except Exception as exc:
        return False, str(exc)


def get_weather(force=False):
    now = time.time()
    if not force and weather_cache["data"] and now - weather_cache["fetched_at"] < 300:
        return weather_cache["data"], weather_cache["error"]
    if not OPENWEATHER_API_KEY:
        return weather_cache.get("data"), "Set OPENWEATHER_API_KEY to enable live weather."
    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": f"{WEATHER_CITY},MW", "appid": OPENWEATHER_API_KEY, "units": "metric"},
            timeout=8,
        )
        response.raise_for_status()
        raw = response.json()
        main = raw.get("main", {})
        weather_list = raw.get("weather", [{}])
        data = {
            "city": raw.get("name", WEATHER_CITY),
            "temperature": round(float(main["temp"]), 1),
            "humidity": int(main["humidity"]),
            "feels_like": round(float(main.get("feels_like", main["temp"])), 1),
            "pressure": main.get("pressure"),
            "wind_speed": raw.get("wind", {}).get("speed"),
            "description": weather_list[0].get("description", "Current conditions").title(),
            "icon": weather_list[0].get("icon", "01d"),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        weather_cache.update({"data": data, "fetched_at": now, "error": None})
        return data, None
    except Exception as exc:
        message = "Weather API unavailable. Check internet, API key, and OpenWeather account status."
        weather_cache["error"] = message
        return weather_cache.get("data"), message


def save_snapshot(weather):
    global last_history_write
    if not weather:
        return
    now = time.time()
    if now - last_history_write < 30:
        return
    with state_lock:
        snapshot = dict(latest)
    try:
        with db_connect() as conn:
            conn.execute("""INSERT INTO readings
                (recorded_at,temperature,humidity,description,city,water_raw,water_detected,fan_on,led_on,mode)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(timespec="seconds"), weather.get("temperature"),
                 weather.get("humidity"), weather.get("description"), weather.get("city"),
                 snapshot.get("water_raw"), None if snapshot.get("water_detected") is None else int(bool(snapshot.get("water_detected"))),
                 int(bool(snapshot.get("fan_on"))), int(bool(snapshot.get("led"))), snapshot.get("mode", "MANUAL")))
        last_history_write = now
    except sqlite3.Error:
        pass


@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == DEMO_USERNAME and password == DEMO_PASSWORD:
            session.clear()
            session["logged_in"] = True
            session["username"] = DEMO_USERNAME
            log_event("Login", f"{DEMO_USERNAME} signed in")
            return redirect(url_for("dashboard"))
        error = "Incorrect username or password. Try the demo credentials shown below."
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", error=error)


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html", username=session.get("username", DEMO_USERNAME), city=WEATHER_CITY)


@app.route("/logout", methods=["POST"])
def logout():
    log_event("Logout", session.get("username", "User") + " signed out")
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/status")
@login_required
def api_status():
    with state_lock:
        data = dict(latest)
    weather, weather_error = get_weather()
    data["weather"] = weather
    data["weather_error"] = weather_error
    data["city"] = WEATHER_CITY
    if weather:
        save_snapshot(weather)
    return jsonify(data)


@app.route("/api/weather/refresh", methods=["POST"])
@login_required
def refresh_weather():
    weather, error = get_weather(force=True)
    if weather:
        save_snapshot(weather)
    return jsonify(ok=bool(weather), weather=weather, message=error or "Weather updated.")


@app.route("/api/control", methods=["POST"])
@login_required
def api_control():
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    commands = {
        "fan_on": ("FAN:ON", {"fan_on": True, "mode": "MANUAL"}, "Fan turned on"),
        "fan_off": ("FAN:OFF", {"fan_on": False, "mode": "MANUAL"}, "Fan turned off"),
        "bulb_on": ("LED:ON", {"led": True}, "Bulb turned on"),
        "bulb_off": ("LED:OFF", {"led": False}, "Bulb turned off"),
        "auto": ("MODE:AUTO", {"mode": "AUTO"}, "Automatic mode selected"),
        "manual": ("MODE:MANUAL", {"mode": "MANUAL"}, "Manual mode selected"),
    }
    if action not in commands:
        return jsonify(ok=False, message="Unknown action."), 400
    command, updates, success_message = commands[action]
    ok, message = send_command(command)
    if ok:
        with state_lock:
            latest.update(updates)
        log_event("Control", success_message)
        return jsonify(ok=True, message=success_message)
    return jsonify(ok=False, message=message), 503


@app.route("/api/history")
@login_required
def api_history():
    try:
        hours = max(1, min(720, int(request.args.get("hours", "24"))))
    except ValueError:
        hours = 24
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM readings WHERE recorded_at >= ? ORDER BY recorded_at ASC", (cutoff,)).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/events")
@login_required
def api_events():
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/reports.csv")
@login_required
def report_csv():
    with db_connect() as conn:
        rows = conn.execute("SELECT recorded_at,city,temperature,humidity,description,water_raw,water_detected,fan_on,led_on,mode FROM readings ORDER BY id DESC LIMIT 5000").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Recorded at", "City", "Outdoor temperature (C)", "Outdoor humidity (%)", "Weather", "Water sensor raw", "Water detected", "Fan on", "Bulb on", "Mode"])
    for row in rows:
        writer.writerow(list(row))
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=greenhouse_report.csv"
    log_event("Report export", f"Exported {len(rows)} rows")
    return response


@app.route("/health")
def health():
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    thread = threading.Thread(target=serial_reader, daemon=True)
    thread.start()
    app.run(host="127.0.0.1", port=5000, debug=False)
