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

# Building Management
ACTIVE_SESSION = None
SESSIONS = {}
BUILDINGS = {}
CURRENT_BUILDING = None
CURRENT_ZONE = None

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
    global LATEST_DATA, LAST_UPDATE_TIME, ACTIVE_SESSION, CURRENT_ZONE

    try:
        strain = float(request.args.get("value1"))
        temperature = float(request.args.get("value2"))
        crack = float(request.args.get("value3"))
        vibration = float(request.args.get("value4"))
        humidity = float(request.args.get("value5"))

        now = get_utc_now()
        now_iso = now.isoformat()

        # Use current zone from session if active, otherwise default
        zone_name = CURRENT_ZONE if ACTIVE_SESSION else ZONE

        LATEST_DATA = {
            "device_id": DEVICE_ID,
            "zone": zone_name,
            "timestamp": now_iso,
            "strain": strain,
            "vibration": vibration,
            "temperature": temperature,
            "humidity": humidity,
            "crack": crack
        }

        LAST_UPDATE_TIME = now

        # Add to session history if active session exists
        if ACTIVE_SESSION:
            session_id = ACTIVE_SESSION["session_id"]
            if session_id in SESSIONS:
                SESSIONS[session_id]["history"].append({
                    "time": now_iso,
                    "strain": strain,
                    "vibration": vibration,
                    "temperature": temperature,
                    "humidity": humidity,
                    "crack": crack
                })
                
                # Limit session history to 500 entries
                if len(SESSIONS[session_id]["history"]) > 500:
                    SESSIONS[session_id]["history"].pop(0)

        # Also add to global history for backward compatibility
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
                    alert_data = {
                        "parameter": param,
                        "severity": stat,
                        "start_time": now_iso,
                        "zone": zone_name
                    }
                    ACTIVE_ALERTS[param] = alert_data
                    
                    # Add to session alerts if active session exists
                    if ACTIVE_SESSION:
                        session_id = ACTIVE_SESSION["session_id"]
                        if session_id in SESSIONS:
                            SESSIONS[session_id]["alerts"][param] = alert_data
            else:
                if param in ACTIVE_ALERTS:
                    resolved = ACTIVE_ALERTS.pop(param)
                    resolved["end_time"] = now_iso
                    ALERT_HISTORY.append(resolved)
                    
                    # Remove from session alerts if active session exists
                    if ACTIVE_SESSION:
                        session_id = ACTIVE_SESSION["session_id"]
                        if session_id in SESSIONS and param in SESSIONS[session_id]["alerts"]:
                            del SESSIONS[session_id]["alerts"][param]

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
    """Return alerts from current session or global alerts"""
    if ACTIVE_SESSION:
        session_id = ACTIVE_SESSION["session_id"]
        if session_id in SESSIONS:
            return jsonify(list(SESSIONS[session_id]["alerts"].values()))
    
    # Fallback to global alerts
    return jsonify(list(ACTIVE_ALERTS.values()))


@app.route("/api/alert-history", methods=["GET"])
def get_alert_history():
    return jsonify(ALERT_HISTORY)


@app.route("/api/history", methods=["GET"])
def get_history():
    # Return history for current session if active
    if ACTIVE_SESSION:
        session_id = ACTIVE_SESSION["session_id"]
        if session_id in SESSIONS:
            return jsonify(SESSIONS[session_id].get("history", []))
    return jsonify(HISTORY)


# ================= BUILDING MANAGEMENT ENDPOINTS =================

@app.route("/api/session/start", methods=["POST"])
def start_session():
    """Start a new monitoring session"""
    global ACTIVE_SESSION, CURRENT_BUILDING, CURRENT_ZONE
    
    data = request.get_json()
    building_name = data.get("building_name")
    zones = data.get("zones", [])
    
    if not building_name or not zones:
        return jsonify({
            "status": "error",
            "message": "Building name and zones are required"
        }), 400
    
    # Create new session
    session_id = f"session_{int(time.time())}"
    ACTIVE_SESSION = {
        "session_id": session_id,
        "building_name": building_name,
        "zones": zones,
        "current_zone": zones[0],
        "start_time": get_utc_now().isoformat(),
        "history": [],
        "alerts": {},
        "data": {}
    }
    
    CURRENT_BUILDING = building_name
    CURRENT_ZONE = zones[0]
    
    # Store session
    SESSIONS[session_id] = ACTIVE_SESSION
    
    return jsonify({
        "status": "success",
        "session_id": session_id,
        "message": f"Session started for {building_name}"
    })

@app.route("/api/session/end", methods=["POST"])
def end_session():
    """End current session and generate reports"""
    global ACTIVE_SESSION, CURRENT_BUILDING, CURRENT_ZONE
    
    if not ACTIVE_SESSION:
        return jsonify({
            "status": "error",
            "message": "No active session to end"
        }), 400
    
    session_id = ACTIVE_SESSION["session_id"]
    session_data = SESSIONS.get(session_id, {})
    
    # Generate reports
    reports = generate_session_reports(session_data)
    
    # Clear active session
    ACTIVE_SESSION = None
    CURRENT_BUILDING = None
    CURRENT_ZONE = None
    
    return jsonify({
        "status": "success",
        "message": "Session ended successfully",
        "reports": reports
    })

@app.route("/api/session/status", methods=["GET"])
def get_session_status():
    """Get current session status"""
    if not ACTIVE_SESSION:
        return jsonify({
            "status": "no_session",
            "message": "No active session"
        })
    
    # Calculate duration
    start_time = datetime.datetime.fromisoformat(ACTIVE_SESSION["start_time"].replace("Z", "+00:00"))
    duration = str(get_utc_now() - start_time).split(".")[0]
    
    return jsonify({
        "status": "active",
        "session_id": ACTIVE_SESSION["session_id"],
        "building_name": ACTIVE_SESSION["building_name"],
        "current_zone": ACTIVE_SESSION["current_zone"],
        "zones": ACTIVE_SESSION["zones"],
        "start_time": ACTIVE_SESSION["start_time"],
        "duration": duration
    })

@app.route("/api/session/switch-zone", methods=["POST"])
def switch_zone():
    """Switch to a different zone within current session"""
    global CURRENT_ZONE
    
    if not ACTIVE_SESSION:
        return jsonify({
            "status": "error",
            "message": "No active session"
        }), 400
    
    data = request.get_json()
    zone_name = data.get("zone_name")
    
    if zone_name not in ACTIVE_SESSION["zones"]:
        return jsonify({
            "status": "error",
            "message": f"Zone {zone_name} not found in session"
        }), 400
    
    CURRENT_ZONE = zone_name
    ACTIVE_SESSION["current_zone"] = zone_name
    
    return jsonify({
        "status": "success",
        "current_zone": zone_name,
        "message": f"Switched to zone: {zone_name}"
    })

@app.route("/api/session/export", methods=["GET"])
def export_session():
    """Export current session data"""
    if not ACTIVE_SESSION:
        return jsonify({
            "status": "error",
            "message": "No active session to export"
        }), 400
    
    session_id = ACTIVE_SESSION["session_id"]
    session_data = SESSIONS.get(session_id, {})
    
    return jsonify({
        "status": "success",
        "session_data": session_data
    })


def generate_session_reports(session_data):
    """Generate CSV, Text, and PDF reports for session data"""
    if not session_data:
        return {"error": "No session data available"}
    
    building_name = session_data.get("building_name", "Unknown Building")
    session_id = session_data.get("session_id", "Unknown Session")
    start_time = session_data.get("start_time", "Unknown Time")
    zones = session_data.get("zones", [])
    history = session_data.get("history", [])
    alerts = session_data.get("alerts", {})
    
    # Generate CSV Report
    csv_report = generate_csv_report(building_name, session_id, zones, history, alerts)
    
    # Generate Text Report  
    text_report = generate_text_report(building_name, session_id, zones, history, alerts, start_time)
    
    # Generate PDF Report
    pdf_report = generate_pdf_report(building_name, session_id, zones, history, alerts, start_time)
    
    return {
        "csv": csv_report,
        "text": text_report,
        "pdf": pdf_report
    }

def generate_csv_report(building_name, session_id, zones, history, alerts):
    """Generate CSV format report"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["Building", building_name])
    writer.writerow(["Session ID", session_id])
    writer.writerow(["Start Time", session_data.get("start_time", "Unknown")])
    writer.writerow([])
    
    # Zone data
    writer.writerow(["Zone Data"])
    writer.writerow(["Zone", "Status", "Alerts Count"])
    for zone in zones:
        alert_count = len([a for a in alerts.values() if a.get("zone") == zone])
        writer.writerow([zone, "MONITORED", alert_count])
    writer.writerow([])
    
    # History data
    if history:
        writer.writerow(["Historical Data"])
        writer.writerow(["Time", "Strain", "Vibration", "Temperature", "Humidity", "Crack"])
        for entry in history:
            writer.writerow([
                entry.get("time", ""),
                entry.get("strain", ""),
                entry.get("vibration", ""),
                entry.get("temperature", ""),
                entry.get("humidity", ""),
                entry.get("crack", "")
            ])
    
    return output.getvalue()

def generate_text_report(building_name, session_id, zones, history, alerts, start_time):
    """Generate text format report"""
    report = f"""
STRUCTURAL HEALTH MONITORING REPORT
=====================================

Building: {building_name}
Session ID: {session_id}
Start Time: {start_time}
Monitored Zones: {', '.join(zones)}

EXECUTIVE SUMMARY
================
Total Monitoring Duration: {len(history)} data points recorded
Active Alerts: {len(alerts)}
Zones Monitored: {len(zones)}

ZONE STATUS
===========
"""
    
    for zone in zones:
        zone_alerts = [a for a in alerts.values() if a.get("zone") == zone]
        alert_count = len(zone_alerts)
        status = "CRITICAL" if alert_count > 2 else "WARNING" if alert_count > 0 else "SAFE"
        report += f"""
Zone: {zone}
Status: {status}
Alerts: {alert_count}
"""
    
    if history:
        latest = history[-1] if history else {}
        report += f"""
LATEST READINGS
===============
Strain: {latest.get('strain', 'N/A')} με
Vibration: {latest.get('vibration', 'N/A')} g
Temperature: {latest.get('temperature', 'N/A')} °C
Humidity: {latest.get('humidity', 'N/A')} %
Crack Width: {latest.get('crack', 'N/A')} mm
"""
    
    report += """
RECOMMENDATIONS
===============
- Continue regular monitoring
- Address any critical alerts immediately
- Schedule maintenance based on zone status
- Review historical trends for patterns

Report generated by InfraSense SHM System
"""
    return report

def generate_pdf_report(building_name, session_id, zones, history, alerts, start_time):
    """Generate PDF format report"""
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Title
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, f"Structural Health Report: {building_name}")
    
    # Session info
    p.setFont("Helvetica", 12)
    p.drawString(50, height - 80, f"Session ID: {session_id}")
    p.drawString(50, height - 100, f"Start Time: {start_time}")
    p.drawString(50, height - 120, f"Zones: {', '.join(zones)}")
    
    # Summary
    p.drawString(50, height - 160, f"Total Data Points: {len(history)}")
    p.drawString(50, height - 180, f"Active Alerts: {len(alerts)}")
    
    # Zone status
    y_position = height - 220
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, y_position, "Zone Status:")
    y_position -= 30
    
    p.setFont("Helvetica", 11)
    for zone in zones:
        zone_alerts = [a for a in alerts.values() if a.get("zone") == zone]
        alert_count = len(zone_alerts)
        status = "CRITICAL" if alert_count > 2 else "WARNING" if alert_count > 0 else "SAFE"
        p.drawString(70, y_position, f"{zone}: {status} ({alert_count} alerts)")
        y_position -= 20
    
    p.save()
    return buffer.getvalue()


@app.route("/api/report", methods=["GET"])
def get_report():
    """Generate report for current session or use existing data"""
    # If active session exists, generate report from session data
    if ACTIVE_SESSION:
        session_id = ACTIVE_SESSION["session_id"]
        session_data = SESSIONS.get(session_id, {})
        reports = generate_session_reports(session_data)
        
        return jsonify({
            "status": "SUCCESS",
            "session_id": session_id,
            "building_name": session_data.get("building_name", "Unknown"),
            "summary": f"Session report generated for {session_data.get('building_name', 'Unknown Building')}",
            "advice": "Review zone-specific alerts and historical trends",
            "reports": reports
        })
    
    # Fallback to original logic if no active session
    elif not HISTORY:
        return jsonify({
            "status": "NO DATA",
            "summary": "No monitoring data available.",
            "advice": "Start a session to generate report."
        })

    # Generate report from existing data
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
# SESSION EXPORT ENDPOINTS
# ==============================

@app.route("/api/session/export/csv", methods=["GET"])
def export_session_csv():
    """Export current session data as CSV"""
    if not ACTIVE_SESSION:
        return jsonify({"error": "No active session"}), 400
    
    session_id = ACTIVE_SESSION["session_id"]
    session_data = SESSIONS.get(session_id, {})
    reports = generate_session_reports(session_data)
    
    response = Response(reports["csv"], mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=session_{session_data.get('building_name', 'session')}_report.csv"
    return response

@app.route("/api/session/export/text", methods=["GET"])
def export_session_text():
    """Export current session data as Text"""
    if not ACTIVE_SESSION:
        return jsonify({"error": "No active session"}), 400
    
    session_id = ACTIVE_SESSION["session_id"]
    session_data = SESSIONS.get(session_id, {})
    reports = generate_session_reports(session_data)
    
    response = Response(reports["text"], mimetype="text/plain")
    response.headers["Content-Disposition"] = f"attachment; filename=session_{session_data.get('building_name', 'session')}_report.txt"
    return response

@app.route("/api/session/export/pdf", methods=["GET"])
def export_session_pdf():
    """Export current session data as PDF"""
    if not ACTIVE_SESSION:
        return jsonify({"error": "No active session"}), 400
    
    session_id = ACTIVE_SESSION["session_id"]
    session_data = SESSIONS.get(session_id, {})
    reports = generate_session_reports(session_data)
    
    response = Response(reports["pdf"], mimetype="application/pdf")
    response.headers["Content-Disposition"] = f"attachment; filename=session_{session_data.get('building_name', 'session')}_report.pdf"
    return response


# ==============================
# LEGACY EXPORT ENDPOINTS  
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
