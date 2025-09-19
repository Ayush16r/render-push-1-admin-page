# app.py
from flask import Flask, render_template, request, jsonify, Response
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime, timezone
import os, time, json

app = Flask(__name__)

# ---------------- MongoDB Setup ----------------
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME", "mydb")       # default DB = mydb
COLL = os.environ.get("COLL", "bookings")         # default collection = bookings

if not MONGO_URI:
    raise Exception("Please set the MONGO_URI environment variable in Render")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
bookings_col = db[COLL]
updates_col = db.get_collection("updates")  # used to publish change events (DB-backed notifications)

# --- DEPT SERVICE TIMES ---
DEPT_SERVICE_TIME = {
    "Emergency": 3,
    "Fever": 2,
    "Headache": 5,
    "General": 10,
    "General Medicine": 8,
    "Cardiology": 15
}

# ---------------- Notification helpers (DB-backed) ----------------
def notify_update():
    """Insert a small update doc so all server workers can detect a change."""
    try:
        updates_col.insert_one({"ts": datetime.now(timezone.utc)})
    except Exception:
        pass

def get_latest_update_ts():
    """Return latest update timestamp (UTC) or epoch if none."""
    last = updates_col.find_one(sort=[("ts", -1)])
    return last["ts"] if last and "ts" in last else datetime(1970, 1, 1, tzinfo=timezone.utc)

# ---------------- Helpers ----------------
def _sort_key_by_created_or_oid(doc):
    """Return a datetime for sorting: prefer created_at, fallback to ObjectId generation time."""
    try:
        created = doc.get("created_at")
        if created:
            if created.tzinfo is None:
                return created.replace(tzinfo=timezone.utc)
            return created
        return doc["_id"].generation_time.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def ensure_defaults(doc_id, doc):
    """Make sure a booking has status + created_at."""
    update_fields = {}
    if "status" not in doc:
        update_fields["status"] = "waiting"
    if "created_at" not in doc:
        update_fields["created_at"] = datetime.now(timezone.utc)
    if update_fields:
        bookings_col.update_one({"_id": doc_id}, {"$set": update_fields})
        doc.update(update_fields)
    return doc

def compute_stats():
    # Fetch waiting docs and sort by created_at (or ObjectId time) FIFO
    waiting_docs = list(bookings_col.find({"status": "waiting"}))
    waiting_sorted = sorted(waiting_docs, key=_sort_key_by_created_or_oid)

    in_progress = bookings_col.find_one({"status": "in_progress"})
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    completed_today = bookings_col.count_documents({"status": "completed", "completed_at": {"$gte": today_start}})

    queue_len = len(waiting_sorted)
    total_min = 0

    if in_progress:
        dept = in_progress.get("department", "General")
        total_min += DEPT_SERVICE_TIME.get(dept, 10)

    for appt in waiting_sorted:
        dept = appt.get("department", "General")
        total_min += DEPT_SERVICE_TIME.get(dept, 10)

    waiting_list = [{
        "sno": idx + 1,
        "id": str(a["_id"]),
        "name": a.get("patient_name"),
        "department": a.get("department"),
        "booking_id": a.get("booking_id")
    } for idx, a in enumerate(waiting_sorted)]

    in_prog = None
    if in_progress:
        in_prog = {
            "id": str(in_progress["_id"]),
            "name": in_progress.get("patient_name"),
            "department": in_progress.get("department"),
            "booking_id": in_progress.get("booking_id")
        }

    return {
        "queue_length": queue_len,
        "estimated_wait_min": total_min,
        "completed_today": completed_today,
        "waiting": waiting_list,
        "in_progress": in_prog,
        "service_time_map": DEPT_SERVICE_TIME,
        "latest_update_ts": get_latest_update_ts().isoformat()
    }

# ---------------- Routes ----------------
@app.route("/")
def index():
    return render_template("index.html", departments=list(DEPT_SERVICE_TIME.keys()))

@app.route("/api/search", methods=["POST"])
def search():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON received"}), 400

    booking_id = data.get("booking_id", "").strip().upper()
    if not booking_id:
        return jsonify({"error": "Booking ID required"}), 400

    doc = bookings_col.find_one({"booking_id": booking_id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    doc = ensure_defaults(doc["_id"], doc)

    # If no one is currently in_progress, move the oldest waiting to in_progress (FIFO)
    if bookings_col.count_documents({"status": "in_progress"}) == 0:
        waiting_docs = list(bookings_col.find({"status": "waiting"}))
        if waiting_docs:
            oldest = sorted(waiting_docs, key=_sort_key_by_created_or_oid)[0]
            bookings_col.update_one(
                {"_id": oldest["_id"]},
                {"$set": {"status": "in_progress", "started_at": datetime.now(timezone.utc)}}
            )

    notify_update()
    return jsonify({"ok": True, "id": str(doc["_id"])})

@app.route("/api/complete/<id>", methods=["POST"])
def complete(id):
    try:
        _id = ObjectId(id)
    except Exception:
        return jsonify({"error": "Invalid ID"}), 400

    now = datetime.now(timezone.utc)
    result = bookings_col.update_one(
        {"_id": _id, "status": "in_progress"},
        {"$set": {"status": "completed", "completed_at": now}}
    )

    if result.matched_count == 0:
        return jsonify({"error": "No in-progress appointment with that ID"}), 404

    # Move next waiting → in-progress
    waiting_docs = list(bookings_col.find({"status": "waiting"}))
    if waiting_docs:
        next_wait = sorted(waiting_docs, key=_sort_key_by_created_or_oid)[0]
        bookings_col.update_one(
            {"_id": next_wait["_id"]},
            {"$set": {"status": "in_progress", "started_at": now}}
        )

    notify_update()
    return jsonify({"ok": True, "stats": compute_stats()})

@app.route("/api/stats")
def stats():
    stats_data = compute_stats()
    # Debugging: show all docs if needed
    stats_data["all_docs"] = [json.loads(json.dumps(doc, default=str)) for doc in bookings_col.find()]
    return jsonify(stats_data)

@app.route("/stream")
def stream():
    """Server-Sent Events stream. Sends initial state immediately, then sends updates when DB gets a new update doc."""
    def event_stream():
        last_ts = get_latest_update_ts()

        # Send initial payload right away
        data = compute_stats()
        yield f"event: update\ndata: {json.dumps(data)}\n\n"

        # Poll for changes (DB-backed signal) — safe to run with multiple workers
        while True:
            latest = get_latest_update_ts()
            if latest > last_ts:
                last_ts = latest
                data = compute_stats()
                yield f"event: update\ndata: {json.dumps(data)}\n\n"
            time.sleep(0.8)

    return Response(event_stream(), mimetype="text/event-stream")

# ---------------- Run (local) ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
