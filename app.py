from flask import Flask, jsonify, Response, request
from flask_cors import CORS
import datetime
import csv
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# -----------------------------
# APP SETUP
# -----------------------------
app = Flask(__name__)
CORS(app)

# -----------------------------
# GLOBAL VARIABLES
# -----------------------------
DEVICE_ID = "INFRA-001"
ZONE = "Column_A1"

ALERTS = []
HISTORY = []
LATEST_DATA = None

# -----------------------------
# STATUS EVALUATION
# -----------------------------
def evaluate_overall(data):
    status = {}

    # Strain (µε)
    if data["strain"] < 250:
        status["strain"] = "SAFE"
    elif data["strain"] < 350:
        status["strain"] = "WARNING"
    else:
        status["strain"] = "CRITICAL"

    # Vibration (g)
    if data["vibration"] < 0.05:
        status["vibration"] = "SAFE"
    elif data["vibration"] < 0.08:
        status["vibration"] = "WARNING"
    else:
        status["vibration"] = "CRITICAL"

    # Temperature (°C)
    if data["temperature"] < 35:
        status["temperature"] = "SAFE"
    elif data["temperature"] < 45:
        status["temperature"] = "WARNING"
    else:
        status["temperature"] = "CRITICAL"

    # Humidity (%)
    if data["humidity"] < 70:
        status["humidity"] = "SAFE"
    elif data["humidity"] < 85:
        status["humidity"] = "WARNING"
    else:
        status["humidity"] = "CRITICAL"

    # Crack width (mm)
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

# -----------------------------
# DEVICE DATA INGEST (ESP)
# -----------------------------
@app.route("/api/device", methods=["GET", "POST"])
def receive_device_data():
    global LATEST_DATA

    try:
        strain = float(request.args.get("value1"))
        temperature = float(request.args.get("value2"))
        crack = float(request.args.get("value3"))
        vibration = float(request.args.get("value4"))
        humidity = float(request.args.get("value5"))

        LATEST_DATA = {
            "device_id": DEVICE_ID,
            "zone": ZONE,
            "timestamp": datetime.datetime.now().isoformat(),
            "strain": strain,
            "vibration": vibration,
            "temperature": temperature,
            "humidity": humidity,
            "crack": crack
        }

    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing sensor values"}), 400

    return jsonify({"status": "data received"})

# -----------------------------
# LIVE DATA API
# -----------------------------
@app.route("/api/live", methods=["GET"])
def live_data():
    global LATEST_DATA

    if LATEST_DATA is None:
        return jsonify({
            "error": "No device data received yet",
            "device_status": "WAITING"
        }), 200

    data = LATEST_DATA
    overall, param_status = evaluate_overall(data)

    ALERTS.clear()
    for param, stat in param_status.items():
        if stat != "SAFE":
            ALERTS.append({
                "zone": data["zone"],
                "parameter": param,
                "severity": stat,
                "value": data[param],
                "time": data["timestamp"]
            })

    HISTORY.append({
        "time": data["timestamp"],
        "strain": data["strain"],
        "vibration": data["vibration"],
        "temperature": data["temperature"],
        "humidity": data["humidity"],
        "crack": data["crack"]
    })

    if len(HISTORY) > 100:
        HISTORY.pop(0)

    return jsonify({
        "data": data,
        "status": {
            "overall": overall,
            "parameters": param_status
        }
    })

# -----------------------------
# ALERTS API
# -----------------------------
@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    return jsonify(ALERTS)

# -----------------------------
# HISTORY API
# -----------------------------
@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(HISTORY)

# -----------------------------
# SIMPLE AI REPORT
# -----------------------------
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

# -----------------------------
# RUN SERVER
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)