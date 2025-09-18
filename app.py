from flask import Flask, render_template, request, jsonify, Response
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
import os, time, json

app = Flask(__name__)

# ---------------- MongoDB Setup ----------------

MONGODB_URI = os.environ.get("MONGODB_URI")
if not MONGODB_URI:
    raise Exception("Please set the MONGO_URI environment variable in Render")



# # --- CONFIG ---
# MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://ayush16r:Ayush16r@healxtrail.nlpleiz.mongodb.net/?retryWrites=true&w=majority&appName=HealXtrail")
# DB_NAME = os.getenv("DB_NAME", "mydb")   # your DB
# COLL = "bookings"                       # your collection

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
coll = db[COLL]

# --- DEPT SERVICE TIMES ---
DEPT_SERVICE_TIME = {
    "Emergency": 3,
    "Fever": 2,
    "Headache": 5,
    "General": 10,
    "General Medicine": 8,
    "Cardiology": 15
}

# --- SSE update counter ---
update_counter = {"val": 0}
def notify_update(): update_counter["val"] += 1

# --- HELPERS ---
def compute_stats():
    waiting = list(coll.find({"status": "waiting"}).sort("created_at", 1))
    in_progress = coll.find_one({"status": "in_progress"})
    completed_count = coll.count_documents({"status": "completed"})

    queue_len = len(waiting)
    total_min = 0

    if in_progress:
        dept = in_progress.get("department", "General")
        total_min += DEPT_SERVICE_TIME.get(dept, 10)

    for appt in waiting:
        dept = appt.get("department", "General")
        total_min += DEPT_SERVICE_TIME.get(dept, 10)

    waiting_list = [{
        "id": str(a["_id"]),
        "name": a.get("patient_name"),
        "department": a.get("department"),
        "booking_id": a.get("booking_id")
    } for a in waiting]

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
        "completed_today": completed_count,
        "waiting": waiting_list,
        "in_progress": in_prog,
        "service_time_map": DEPT_SERVICE_TIME,
        "update_counter": update_counter["val"]
    }

# --- ROUTES ---
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

    doc = coll.find_one({"booking_id": booking_id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    if "status" not in doc:
        coll.update_one({"_id": doc["_id"]}, {"$set": {"status": "waiting", "created_at": datetime.utcnow()}})

    if coll.count_documents({"status": "in_progress"}) == 0:
        oldest = coll.find_one({"status": "waiting"}, sort=[("created_at", 1)])
        if oldest:
            coll.update_one({"_id": oldest["_id"]}, {"$set": {"status": "in_progress", "started_at": datetime.utcnow()}})

    notify_update()
    return jsonify({"ok": True, "id": str(doc["_id"])})

@app.route("/api/complete/<id>", methods=["POST"])
def complete(id):
    try:
        _id = ObjectId(id)
    except:
        return jsonify({"error": "Invalid ID"}), 400

    now = datetime.utcnow()
    result = coll.update_one({"_id": _id, "status": "in_progress"},
                             {"$set": {"status": "completed", "completed_at": now}})

    if result.matched_count == 0:
        return jsonify({"error": "No in-progress appointment with that ID"}), 404

    next_wait = coll.find_one({"status": "waiting"}, sort=[("created_at", 1)])
    if next_wait:
        coll.update_one({"_id": next_wait["_id"]}, {"$set": {"status": "in_progress", "started_at": now}})

    notify_update()
    return jsonify({"ok": True, "stats": compute_stats()})

@app.route("/api/stats")
def stats():
    return jsonify(compute_stats())

@app.route("/stream")
def stream():
    def event_stream(last_val):
        current = last_val
        while True:
            if update_counter["val"] != current:
                current = update_counter["val"]
                data = compute_stats()
                yield f"id: {current}\nevent: update\ndata: {json.dumps(data)}\n\n"
            time.sleep(0.5)
    return Response(event_stream(update_counter["val"]), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
