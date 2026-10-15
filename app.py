import json
import logging
import math
import os
import sqlite3
from datetime import datetime, timedelta

import requests
from flask import Flask, g, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="/static")
app.logger.setLevel(logging.INFO)

# Vercel's writable filesystem is ephemeral. Use /tmp there; use the project folder locally.
IS_VERCEL = bool(os.environ.get("VERCEL"))
DB = os.environ.get("ECOTRACK_DB_PATH", "/tmp/ecotrack.db" if IS_VERCEL else os.path.join(BASE_DIR, "ecotrack.db"))

# Approximate emission factors (kg CO2e); see README for limitations.
F = {
    "car_km": 0.17, "bus_km": 0.08, "bike_km": 0.07, "kwh": 0.82,
    "lpg_kg": 3.0, "meat_meal": 2.5, "veg_meal": 0.9, "waste_kg": 0.45,
}

CHALLENGES = [
    {"id": 1, "title": "Car-free week", "desc": "Swap 20 km of driving for bus, bike or walking.", "kg": 3.4},
    {"id": 2, "title": "Meatless Monday", "desc": "Eat vegetarian for a full day.", "kg": 4.0},
    {"id": 3, "title": "Lights out hour", "desc": "Switch off unused lights and fans for a day.", "kg": 1.2},
    {"id": 4, "title": "Zero-waste lunch", "desc": "Carry a reusable bottle and box for a week.", "kg": 1.5},
    {"id": 5, "title": "Cold wash", "desc": "Wash clothes in cold water for a week.", "kg": 2.0},
]


def db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
        conn = sqlite3.connect(DB, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            college TEXT NOT NULL,
            UNIQUE(name, college)
        );
        CREATE TABLE IF NOT EXISTS footprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            total REAL NOT NULL,
            breakdown TEXT NOT NULL,
            created TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            kg_saved REAL NOT NULL,
            challenge_id INTEGER,
            created TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            UNIQUE(user_id, challenge_id)
        );
    """)
    # Add challenge_id to databases created by older versions.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(actions)").fetchall()}
    if "challenge_id" not in columns:
        conn.execute("ALTER TABLE actions ADD COLUMN challenge_id INTEGER")
    conn.commit()
    conn.close()


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def positive_number(data, key):
    value = data.get(key, 0)
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number.")
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number.")
    if number < 0:
        raise ValueError(f"{key} cannot be negative.")
    return number


def calculate(data):
    n = lambda key: positive_number(data, key)
    parts = {
        "transport": n("car_km") * F["car_km"] + n("bus_km") * F["bus_km"] + n("bike_km") * F["bike_km"],
        # Electricity and LPG are monthly inputs, converted to approximate weekly values.
        "energy": n("kwh") * F["kwh"] / 4.3 + n("lpg_kg") * F["lpg_kg"] / 4.3,
        "food": n("meat_meals") * F["meat_meal"] + n("veg_meals") * F["veg_meal"],
        "waste": n("waste_kg") * F["waste_kg"],
    }
    parts = {key: round(value, 1) for key, value in parts.items()}
    return round(sum(parts.values()), 1), parts


TIPS = {
    "transport": [("Take the bus or share a ride twice a week", 3.0), ("Walk or cycle trips under 3 km", 2.0)],
    "energy": [("Switch to LED bulbs and efficient appliances", 2.5), ("Unplug chargers and devices when idle", 1.0)],
    "food": [("Replace three meat meals with vegetarian ones", 4.5), ("Buy local, seasonal produce", 1.0)],
    "waste": [("Compost kitchen scraps", 1.2), ("Use a reusable bottle and bag", 0.8)],
}


def rule_recs(parts):
    recs = []
    for category in sorted(parts, key=parts.get, reverse=True)[:2]:
        for tip, kg in TIPS[category]:
            recs.append({"category": category, "tip": tip, "kg_saved_week": kg})
    return recs


def ai_recs(total, parts, college):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    prompt = (
        f"A student at {college or 'a college'} in India has an estimated footprint of {total} kg CO2e/week. "
        f"Breakdown: {json.dumps(parts)}. Give four specific, realistic eco tips. Reply only with a JSON array "
        'of objects with keys category (transport, energy, food, or waste), tip, and kg_saved_week (number).'
    )
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
                "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        response.raise_for_status()
        content = response.json().get("content", [])
        text = next((item.get("text", "") for item in content if item.get("type") == "text"), "")
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)
        if not isinstance(result, list):
            return None
        clean = []
        for item in result[:4]:
            if not isinstance(item, dict):
                continue
            category, tip = item.get("category"), item.get("tip")
            try:
                saved = float(item.get("kg_saved_week", 0))
            except (TypeError, ValueError):
                continue
            if category in TIPS and isinstance(tip, str) and tip.strip() and math.isfinite(saved) and saved >= 0:
                clean.append({"category": category, "tip": tip.strip()[:300], "kg_saved_week": round(saved, 1)})
        return clean or None
    except (requests.RequestException, ValueError, KeyError, TypeError, StopIteration):
        app.logger.exception("AI recommendations failed; using built-in recommendations")
        return None


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", app="EcoTrack AI 2")


@app.post("/api/login")
def login():
    data = json_body()
    name = str(data.get("name") or "").strip()[:80]
    college = str(data.get("college") or "").strip()[:120]
    if not name or not college:
        return jsonify(error="Enter your name and college."), 400
    conn = db()
    conn.execute("INSERT OR IGNORE INTO users(name, college) VALUES(?, ?)", (name, college))
    conn.commit()
    user = conn.execute("SELECT id, name, college FROM users WHERE name=? AND college=?", (name, college)).fetchone()
    return jsonify(dict(user))


@app.post("/api/footprint")
def footprint():
    data = json_body()
    try:
        total, parts = calculate(data)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    recommendations = ai_recs(total, parts, data.get("college")) or rule_recs(parts)
    user_id = data.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return jsonify(error="Invalid user_id."), 400
        conn = db()
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            return jsonify(error="User not found. Please sign in again."), 404
        conn.execute("INSERT INTO footprints(user_id,total,breakdown,created) VALUES(?,?,?,?)",
                     (user_id, total, json.dumps(parts), now()))
        conn.commit()
    return jsonify(total=total, breakdown=parts, recommendations=recommendations)


@app.get("/api/challenges")
def challenges():
    return jsonify(CHALLENGES)


@app.post("/api/actions")
def log_action():
    data = json_body()
    try:
        user_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify(error="Please sign in before recording an action."), 400
    conn = db()
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        return jsonify(error="User not found. Please sign in again."), 404

    challenge_id = data.get("challenge_id")
    if challenge_id is not None:
        try:
            challenge_id = int(challenge_id)
        except (TypeError, ValueError):
            return jsonify(error="Invalid challenge."), 400
        challenge = next((item for item in CHALLENGES if item["id"] == challenge_id), None)
        if challenge is None:
            return jsonify(error="Unknown challenge."), 404
        label, kg = challenge["title"], challenge["kg"]
    else:
        label = str(data.get("label") or "").strip()[:120]
        try:
            kg = float(data.get("kg_saved"))
        except (TypeError, ValueError):
            return jsonify(error="kg_saved must be a number."), 400
        if not label or not math.isfinite(kg) or kg < 0:
            return jsonify(error="Enter a label and a non-negative finite kg_saved value."), 400
        challenge_id = None

    try:
        conn.execute(
            "INSERT INTO actions(user_id,label,kg_saved,challenge_id,created) VALUES(?,?,?,?,?)",
            (user_id, label, kg, challenge_id, now()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="This challenge has already been recorded for this user."), 409
    return jsonify(ok=True)


@app.get("/api/dashboard/<int:uid>")
def dashboard(uid):
    conn = db()
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        return jsonify(error="User not found. Please sign in again."), 404
    footprints = conn.execute(
        "SELECT total, breakdown, created FROM footprints WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,)
    ).fetchall()
    actions = conn.execute(
        "SELECT label, kg_saved, created FROM actions WHERE user_id=? ORDER BY id DESC", (uid,)
    ).fetchall()
    since = (datetime.utcnow() - timedelta(days=7)).isoformat(timespec="seconds")
    return jsonify(
        latest={"total": footprints[0]["total"], "breakdown": json.loads(footprints[0]["breakdown"])} if footprints else None,
        history=[row["total"] for row in reversed(footprints)],
        total_saved=round(sum(row["kg_saved"] for row in actions), 1),
        week_saved=round(sum(row["kg_saved"] for row in actions if row["created"] >= since), 1),
        action_count=len(actions),
        recent=[dict(row) for row in actions[:6]],
    )


@app.get("/api/leaderboard")
def leaderboard():
    conn = db()
    people = conn.execute("""SELECT u.name, u.college, ROUND(COALESCE(SUM(a.kg_saved),0),1) saved
        FROM users u LEFT JOIN actions a ON a.user_id=u.id GROUP BY u.id ORDER BY saved DESC, u.name ASC LIMIT 10""").fetchall()
    colleges = conn.execute("""SELECT u.college, COUNT(DISTINCT u.id) members, ROUND(COALESCE(SUM(a.kg_saved),0),1) saved
        FROM users u LEFT JOIN actions a ON a.user_id=u.id GROUP BY u.college ORDER BY saved DESC, u.college ASC LIMIT 10""").fetchall()
    return jsonify(people=[dict(row) for row in people], colleges=[dict(row) for row in colleges])


# Create tables at import/startup. Vercel's /tmp database is ephemeral; see README.
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
