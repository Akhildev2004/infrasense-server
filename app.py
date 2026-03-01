from flask import Flask, jsonify, request
from flask_cors import CORS
import datetime

app = Flask(__name__)
CORS(app)

DEVICE_ID = "INFRA-001"
ZONE = "Column_A1"

HISTORY = []
LATEST_DATA = None
LAST_UPDATE_TIME = None
OFFLINE_THRESHOLD = 15  # seconds

# 🔹 Alert Structures
ACTIVE_ALERTS = {}
ALERT_HISTORY = []


def get_utc_now():
    """Return current UTC time with timezone info"""
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)


def evaluate_overall(data):
    status = {}

    if data["strain"] < 250:
        status["strain"] = "SAFE"
    elif data["strain"] < 350:
        status["strain"] = "WARNING"
    else:
        status["strain"] = "CRITICAL"

    if data["vibration"] < 0.05:
        status["vibration"] = "SAFE"
    elif data["vibration"] < 0.08:
        status["vibration"] = "WARNING"
    else:
        status["vibration"] = "CRITICAL"

    if data["temperature"] < 35:
        status["temperature"] = "SAFE"
    elif data["temperature"] < 45:
        status["temperature"] = "WARNING"
    else:
        status["temperature"] = "CRITICAL"

    if data["humidity"] < 70:
        status["humidity"] = "SAFE"
    elif data["humidity"] < 85:
        status["humidity"] = "WARNING"
    else:
        status["humidity"] = "CRITICAL"

    if data["crack"] < 0.3:
        status["crack"] = "SAFE"
    elif data["crack"] < 0.5:
        status["crack"] = "WARNING"
    else:
        status["crack"] = "CRITICAL"

    overall = "SAFE"
    if "CRITICAL" in status.values():
        overall = "CRITICAL"
    elif "WARNING" in status.values():
        overall = "WARNING"

    return overall, status


@app.route("/api/device", methods=["GET", "POST"])
def receive_device_data():
    global LATEST_DATA, LAST_UPDATE_TIME

    try:
        strain = float(request.args.get("value1"))
        temperature = float(request.args.get("value2"))
        crack = float(request.args.get("value3"))
        vibration = float(request.args.get("value4"))
        humidity = float(request.args.get("value5"))

        now = get_utc_now()
        now_iso = now.isoformat()

        LATEST_DATA = {
            "device_id": DEVICE_ID,
            "zone": ZONE,
            "timestamp": now_iso,
            "strain": strain,
            "vibration": vibration,
            "temperature": temperature,
            "humidity": humidity,
            "crack": crack
        }

        LAST_UPDATE_TIME = now

        # 🔹 Save History (last 100 readings)
        HISTORY.append({
            "time": now_iso,
            "strain": strain,
            "vibration": vibration,
            "temperature": temperature,
            "humidity": humidity,
            "crack": crack
        })

        if len(HISTORY) > 100:
            HISTORY.pop(0)

        # 🔹 Event-Based Alert Logic
        overall, param_status = evaluate_overall(LATEST_DATA)

        for param, stat in param_status.items():

            # Create alert if abnormal and not already active
            if stat != "SAFE":
                if param not in ACTIVE_ALERTS:
                    ACTIVE_ALERTS[param] = {
                        "parameter": param,
                        "severity": stat,
                        "start_time": now_iso,
                        "zone": ZONE
                    }

            # Resolve alert if returned to SAFE
            else:
                if param in ACTIVE_ALERTS:
                    resolved_alert = ACTIVE_ALERTS.pop(param)
                    resolved_alert["end_time"] = now_iso
                    ALERT_HISTORY.append(resolved_alert)

    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing sensor values"}), 400

    return jsonify({"status": "data received"})


@app.route("/api/live", methods=["GET"])
def live_data():
    global LATEST_DATA, LAST_UPDATE_TIME

    if LATEST_DATA is None:
        return jsonify({
            "device_status": "WAITING",
            "error": "No device data received yet"
        })

    device_status = "ONLINE"

    if LAST_UPDATE_TIME:
        diff = (get_utc_now() - LAST_UPDATE_TIME).total_seconds()
        if diff > OFFLINE_THRESHOLD:
            device_status = "OFFLINE"

    overall, param_status = evaluate_overall(LATEST_DATA)

    return jsonify({
        "device_status": device_status,
        "data": LATEST_DATA,
        "status": {
            "overall": overall,
            "parameters": param_status
        }
    })


# 🔹 Active Alerts Endpoint
@app.route("/api/alerts", methods=["GET"])
def get_active_alerts():
    return jsonify(list(ACTIVE_ALERTS.values()))


# 🔹 Resolved Alerts Endpoint
@app.route("/api/alert-history", methods=["GET"])
def get_alert_history():
    return jsonify(ALERT_HISTORY)


@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(HISTORY)


@app.route("/api/report", methods=["GET"])
def get_report():
    if not HISTORY:
        return jsonify({
            "status": "NO DATA",
            "summary": "No monitoring data available.",
            "advice": "Start device to generate report."
        })

    warnings = 0
    critical = 0

    for d in HISTORY:
        if d["crack"] > 0.5:
            critical += 1
        if d["strain"] > 300 or d["humidity"] > 80:
            warnings += 1

    if critical > 5:
        status = "CRITICAL"
        summary = "Severe structural distress detected."
        advice = "Immediate inspection recommended."
    elif warnings > 5:
        status = "WARNING"
        summary = "Early structural degradation signs observed."
        advice = "Schedule preventive maintenance."
    else:
        status = "SAFE"
        summary = "Structure performing within acceptable limits."
        advice = "Continue routine monitoring."

    return jsonify({
        "status": status,
        "summary": summary,
        "advice": advice
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
