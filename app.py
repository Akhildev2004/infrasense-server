from flask import Flask, jsonify, request, Response
from flask_cors import CORS
import datetime
import csv
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import threading
import time

app = Flask(__name__)
CORS(app)

DEVICE_ID = "INFRA-001"
ZONE = "Column_A1"

HISTORY = []
LATEST_DATA = None
LAST_UPDATE_TIME = None
OFFLINE_THRESHOLD = 15  # seconds

ACTIVE_ALERTS = {}
ALERT_HISTORY = []

# Scan functionality
SCANNING = False
SCAN_THREAD = None
SCAN_STATUS = {"status": "idle", "message": "Ready to scan"}

def get_utc_now():
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
                        "zone": ZONE
                    }
            else:
                if param in ACTIVE_ALERTS:
                    resolved = ACTIVE_ALERTS.pop(param)
                    resolved["end_time"] = now_iso
                    ALERT_HISTORY.append(resolved)

    except (TypeError, ValueError):
        return jsonify({"error": "Invalid or missing sensor values"}), 400

    return jsonify({"status": "data received"})


def trigger_device_scan():
    """Trigger actual device scan - only uses real device data"""
    global SCANNING, SCAN_STATUS, LATEST_DATA, LAST_UPDATE_TIME
    
    SCAN_STATUS["status"] = "scanning"
    SCAN_STATUS["message"] = "Waiting for device data..."
    
    # Wait for real device data (max 10 seconds)
    for i in range(10):
        if not SCANNING:
            break
            
        SCAN_STATUS["message"] = f"Waiting for device data... ({i+1}/10s)"
        time.sleep(1)
        
        # Check if we received real device data during scan
        if LATEST_DATA and LAST_UPDATE_TIME:
            time_diff = (get_utc_now() - LAST_UPDATE_TIME).total_seconds()
            if time_diff < 5:  # Got recent data
                overall, param_status = evaluate_overall(LATEST_DATA)
                SCAN_STATUS["status"] = "completed"
                SCAN_STATUS["message"] = f"Scan completed! Status: {overall}"
                break
    
    if SCANNING and SCAN_STATUS["status"] == "scanning":
        SCAN_STATUS["status"] = "failed"
        SCAN_STATUS["message"] = "No device data received. Check device connection."
    
    SCANNING = False


@app.route("/api/scan", methods=["POST"])
def start_scan():
    """Start a device scan"""
    global SCANNING, SCAN_THREAD, SCAN_STATUS
    
    if SCANNING:
        return jsonify({
            "status": "error",
            "message": "Scan already in progress"
        }), 400
    
    SCANNING = True
    SCAN_STATUS = {"status": "starting", "message": "Initializing scan..."}
    
    # Start scan in background thread
    SCAN_THREAD = threading.Thread(target=trigger_device_scan)
    SCAN_THREAD.daemon = True
    SCAN_THREAD.start()
    
    return jsonify({
        "status": "success",
        "message": "Scan started successfully"
    })


@app.route("/api/scan/status", methods=["GET"])
def get_scan_status():
    """Get current scan status"""
    return jsonify(SCAN_STATUS)


@app.route("/api/device/status", methods=["GET"])
def get_device_status():
    """Get detailed device status"""
    if LATEST_DATA is None or LAST_UPDATE_TIME is None:
        return jsonify({
            "device_status": "OFFLINE",
            "device_id": DEVICE_ID,
            "zone": ZONE,
            "last_update": None,
            "connection_status": "disconnected",
            "message": "Waiting for device data..."
        })
    
    device_status = "ONLINE"
    connection_status = "connected"
    message = "Device operating normally"
    
    if LAST_UPDATE_TIME:
        diff = (get_utc_now() - LAST_UPDATE_TIME).total_seconds()
        if diff > OFFLINE_THRESHOLD:
            device_status = "OFFLINE"
            connection_status = "disconnected"
            message = "Device not responding"
    
    overall, param_status = evaluate_overall(LATEST_DATA)
    
    return jsonify({
        "device_status": device_status,
        "device_id": DEVICE_ID,
        "zone": ZONE,
        "last_update": LAST_UPDATE_TIME.isoformat() if LAST_UPDATE_TIME else None,
        "connection_status": connection_status,
        "message": message,
        "overall_status": overall,
        "parameter_status": param_status
    })


@app.route("/api/live", methods=["GET"])
def live_data():
    if LATEST_DATA is None or LAST_UPDATE_TIME is None:
        return jsonify({
            "device_status": "OFFLINE",
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


@app.route("/api/alerts", methods=["GET"])
def get_active_alerts():
    return jsonify(list(ACTIVE_ALERTS.values()))


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


# ==============================
# EXPORT CSV
# ==============================
@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    if not HISTORY:
        return jsonify({"error": "No data available"}), 400

    report = get_report().json

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["InfraSense Structural Monitoring Report"])
    writer.writerow(["Device ID", DEVICE_ID])
    writer.writerow(["Zone", ZONE])
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
    response.headers["Content-Disposition"] = "attachment; filename=infrasense_full_report.csv"
    return response


# ==============================
# EXPORT TEXT
# ==============================
@app.route("/api/export/text", methods=["GET"])
def export_text():
    if not HISTORY:
        return jsonify({"error": "No data available"}), 400

    report = get_report().json

    content = f"""
InfraSense Structural Monitoring Report
----------------------------------------
Device ID: {DEVICE_ID}
Zone: {ZONE}
Generated On: {get_utc_now().isoformat()}

Overall Status: {report['status']}
Summary: {report['summary']}
Maintenance Advice: {report['advice']}

----------------------------------------
History Data
----------------------------------------
"""

    for d in HISTORY:
        content += f"""
Time: {d['time']}
Strain: {d['strain']}
Vibration: {d['vibration']}
Temperature: {d['temperature']}
Humidity: {d['humidity']}
Crack: {d['crack']}
----------------------------------------
"""

    response = Response(content, mimetype="text/plain")
    response.headers["Content-Disposition"] = "attachment; filename=infrasense_full_report.txt"
    return response


# ==============================
# EXPORT PDF
# ==============================
@app.route("/api/export/pdf", methods=["GET"])
def export_pdf():
    if not HISTORY:
        return jsonify({"error": "No data available"}), 400

    report = get_report().json

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)

    y = 800

    p.drawString(50, y, "InfraSense Structural Monitoring Report")
    y -= 30
    p.drawString(50, y, f"Device ID: {DEVICE_ID}")
    y -= 20
    p.drawString(50, y, f"Zone: {ZONE}")
    y -= 20
    p.drawString(50, y, f"Generated On: {get_utc_now().isoformat()}")
    y -= 30

    p.drawString(50, y, f"Overall Status: {report['status']}")
    y -= 20
    p.drawString(50, y, f"Summary: {report['summary']}")
    y -= 20
    p.drawString(50, y, f"Advice: {report['advice']}")
    y -= 30

    p.drawString(50, y, "History Data:")
    y -= 20

    for d in HISTORY:
        line = f"{d['time']} | S:{d['strain']} | V:{d['vibration']} | T:{d['temperature']} | H:{d['humidity']} | C:{d['crack']}"
        p.drawString(50, y, line)
        y -= 15

        if y < 50:
            p.showPage()
            y = 800

    p.save()
    buffer.seek(0)

    return Response(
        buffer,
        mimetype='application/pdf',
        headers={"Content-Disposition": "attachment;filename=infrasense_full_report.pdf"}
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
