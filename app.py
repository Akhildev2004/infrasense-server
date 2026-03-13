from flask import Flask, jsonify, request, Response
from flask_cors import CORS
import datetime
import csv
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

app = Flask(__name__)
CORS(app, origins=["http://localhost:3000", "http://localhost:8000", "http://localhost:5500", "http://localhost:5501", "http://localhost:5502", "http://127.0.0.1:3000", "http://127.0.0.1:8000", "http://127.0.0.1:5500", "http://127.0.0.1:5501", "http://127.0.0.1:5502", "file://"])

DEVICE_ID = "INFRA-001"

# ==============================
# SESSION STORAGE
# ==============================

SESSION = {
    "id": None,
    "building": None,
    "zone": None,
    "start_time": None,
    "end_time": None,
    "active": False
}

SESSION_COUNTER = 0
SESSION_ARCHIVE = []

# ==============================
# MONITORING DATA
# ==============================

HISTORY = []
LATEST_DATA = None
LAST_UPDATE_TIME = None
OFFLINE_THRESHOLD = 15

ACTIVE_ALERTS = {}
ALERT_HISTORY = []

# ==============================

def get_utc_now():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

# ==============================

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

# ==============================
# DEVICE DATA RECEIVE
# ==============================

@app.route("/api/device", methods=["GET", "POST"])
def receive_device_data():

    global LATEST_DATA, LAST_UPDATE_TIME

    if not SESSION["active"]:
        return jsonify({"error": "No active session"}), 400

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
            "zone": SESSION["zone"],
            "timestamp": now_iso,
            "strain": strain,
            "vibration": vibration,
            "temperature": temperature,
            "humidity": humidity,
            "crack": crack
        }

        LAST_UPDATE_TIME = now

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

        overall, param_status = evaluate_overall(LATEST_DATA)

        for param, stat in param_status.items():

            if stat != "SAFE":

                if param not in ACTIVE_ALERTS:
                    ACTIVE_ALERTS[param] = {
                        "parameter": param,
                        "severity": stat,
                        "start_time": now_iso,
                        "zone": SESSION["zone"]
                    }

            else:

                if param in ACTIVE_ALERTS:
                    resolved = ACTIVE_ALERTS.pop(param)
                    resolved["end_time"] = now_iso
                    ALERT_HISTORY.append(resolved)

    except (TypeError, ValueError):

        return jsonify({
            "error": "Invalid or missing sensor values"
        }), 400

    return jsonify({"status": "data received"})

# ==============================
# LIVE DATA
# ==============================

@app.route("/api/live", methods=["GET"])
def live_data():

    if not SESSION["active"]:
        return jsonify({
            "device_status": "SESSION_STOPPED"
        })

    if LATEST_DATA is None:

        return jsonify({
            "device_status": "WAITING"
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

# ==============================
# ALERT APIs
# ==============================

@app.route("/api/alerts", methods=["GET"])
def get_active_alerts():
    return jsonify(list(ACTIVE_ALERTS.values()))

@app.route("/api/alert-history", methods=["GET"])
def get_alert_history():
    return jsonify(ALERT_HISTORY)

# ==============================
# HISTORY
# ==============================

@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(HISTORY)

# ==============================
# SESSION START
# ==============================

@app.route("/api/session/start", methods=["POST"])
def start_session():

    global SESSION_COUNTER
    global HISTORY, ACTIVE_ALERTS, ALERT_HISTORY, LATEST_DATA

    if SESSION["active"]:
        return jsonify({"error": "Session already active"}), 400

    data = request.json

    SESSION_COUNTER += 1

    SESSION["id"] = SESSION_COUNTER
    SESSION["building"] = data.get("building")
    SESSION["zone"] = data.get("zone")
    SESSION["start_time"] = get_utc_now().isoformat()
    SESSION["end_time"] = None
    SESSION["active"] = True

    HISTORY.clear()
    ACTIVE_ALERTS.clear()
    ALERT_HISTORY.clear()
    LATEST_DATA = None

    return jsonify({
        "message": "Session started",
        "session": SESSION
    })

# ==============================
# SESSION END
# ==============================

@app.route("/api/session/end", methods=["POST"])
def end_session():

    global HISTORY, ACTIVE_ALERTS, ALERT_HISTORY, LATEST_DATA, LAST_UPDATE_TIME

    if not SESSION["active"]:
        return jsonify({"error": "No active session"}), 400

    SESSION["end_time"] = get_utc_now().isoformat()
    SESSION["active"] = False

    SESSION_ARCHIVE.append({
        "building": SESSION["building"],
        "zone": SESSION["zone"],
        "start_time": SESSION["start_time"],
        "end_time": SESSION["end_time"],
        "history": HISTORY.copy(),
        "alerts": ALERT_HISTORY.copy()
    })

    HISTORY.clear()
    ACTIVE_ALERTS.clear()
    ALERT_HISTORY.clear()
    LATEST_DATA = None
    LAST_UPDATE_TIME = None

    return jsonify({
        "message": "Session ended",
        "session": SESSION
    })

# ==============================
# REPORT
# ==============================

@app.route("/api/report", methods=["GET"])
def get_report():

    if not HISTORY:
        return jsonify({
            "status": "NO DATA",
            "summary": "No monitoring data available.",
            "advice": "Start a monitoring session and collect sensor data to generate analysis."
        })

    # Analyze sensor readings for comprehensive assessment
    strain_values = [d["strain"] for d in HISTORY if d["strain"] is not None]
    vibration_values = [d["vibration"] for d in HISTORY if d["vibration"] is not None]
    temperature_values = [d["temperature"] for d in HISTORY if d["temperature"] is not None]
    humidity_values = [d["humidity"] for d in HISTORY if d["humidity"] is not None]
    crack_values = [d["crack"] for d in HISTORY if d["crack"] is not None]

    # Count critical and warning conditions
    critical_count = 0
    warning_count = 0
    
    analysis_details = []

    # Strain analysis
    if strain_values:
        avg_strain = sum(strain_values) / len(strain_values)
        max_strain = max(strain_values)
        if max_strain > 350:
            critical_count += 1
            analysis_details.append(f"Critical strain levels detected (max: {max_strain:.1f} με)")
        elif max_strain > 250:
            warning_count += 1
            analysis_details.append(f"Elevated strain levels observed (max: {max_strain:.1f} με)")

    # Vibration analysis
    if vibration_values:
        avg_vibration = sum(vibration_values) / len(vibration_values)
        max_vibration = max(vibration_values)
        if max_vibration > 0.08:
            critical_count += 1
            analysis_details.append(f"Critical vibration detected (max: {max_vibration:.3f} g)")
        elif max_vibration > 0.05:
            warning_count += 1
            analysis_details.append(f"Elevated vibration levels (max: {max_vibration:.3f} g)")

    # Temperature analysis
    if temperature_values:
        avg_temp = sum(temperature_values) / len(temperature_values)
        max_temp = max(temperature_values)
        if max_temp > 45:
            critical_count += 1
            analysis_details.append(f"High temperature detected (max: {max_temp:.1f} °C)")
        elif max_temp > 35:
            warning_count += 1
            analysis_details.append(f"Elevated temperature (max: {max_temp:.1f} °C)")

    # Humidity analysis
    if humidity_values:
        avg_humidity = sum(humidity_values) / len(humidity_values)
        max_humidity = max(humidity_values)
        if max_humidity > 85:
            critical_count += 1
            analysis_details.append(f"Critical humidity levels (max: {max_humidity:.1f} %)")
        elif max_humidity > 70:
            warning_count += 1
            analysis_details.append(f"High humidity conditions (max: {max_humidity:.1f} %)")

    # Crack analysis
    if crack_values:
        max_crack = max(crack_values)
        if max_crack > 0.5:
            critical_count += 1
            analysis_details.append(f"Critical crack width detected (max: {max_crack:.3f} mm)")
        elif max_crack > 0.3:
            warning_count += 1
            analysis_details.append(f"Crack formation observed (max: {max_crack:.3f} mm)")

    # Determine overall status and generate detailed advice
    if critical_count > 0:
        status = "CRITICAL"
        summary = f"Severe structural issues detected: {critical_count} critical parameter(s). {'. '.join(analysis_details[:2])}"
        advice = "IMMEDIATE ACTION REQUIRED: 1) Conduct thorough structural inspection 2) Implement safety measures 3) Consult structural engineer 4) Consider load reduction 5) Continuous monitoring essential"
    elif warning_count > 2:
        status = "WARNING"
        summary = f"Multiple structural concerns identified: {warning_count} parameter(s) showing stress. {'. '.join(analysis_details[:2])}"
        advice = "PREVENTIVE ACTION NEEDED: 1) Schedule detailed inspection within 48 hours 2) Increase monitoring frequency 3) Review maintenance records 4) Check for environmental factors 5) Plan remedial measures"
    elif warning_count > 0:
        status = "WARNING"
        summary = f"Early structural degradation signs: {warning_count} parameter(s) require attention. {analysis_details[0] if analysis_details else 'Minor anomalies detected'}"
        advice = "MONITORING ADVISED: 1) Continue regular monitoring 2) Document trends 3) Schedule routine inspection 4) Review operational loads 5) Maintain observation log"
    else:
        status = "SAFE"
        summary = f"Structure performing within acceptable limits. All {len(HISTORY)} sensor readings normal."
        advice = "ROUTINE MONITORING: 1) Continue standard monitoring schedule 2) Maintain calibration of sensors 3) Regular visual inspections 4) Document baseline readings 5) Plan periodic assessments"

    return jsonify({
        "building": SESSION["building"],
        "zone": SESSION["zone"],
        "status": status,
        "summary": summary,
        "advice": advice
    })

# ==============================
# EXPORT CSV
# ==============================
# ... (rest of the code remains the same)
@app.route("/api/export/csv", methods=["GET"])
def export_csv():

    if not HISTORY:
        return jsonify({"error": "No data available"}), 400

    report = get_report().json

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["InfraSense Structural Monitoring Report"])
    writer.writerow(["Device ID", DEVICE_ID])
    writer.writerow(["Building", report["building"]])
    writer.writerow(["Zone", report["zone"]])
    writer.writerow(["Generated On", get_utc_now().isoformat()])
    writer.writerow(["Overall Status", report["status"]])
    writer.writerow(["Summary", report["summary"]])
    writer.writerow(["Advice", report["advice"]])
    writer.writerow([])

    writer.writerow(["Time", "Strain", "Vibration", "Temperature", "Humidity", "Crack"])

    for d in HISTORY:
        writer.writerow([
            d["time"],
            d["strain"],
            d["vibration"],
            d["temperature"],
            d["humidity"],
            d["crack"]
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=infrasense_report.csv"

    return response

# ==============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
