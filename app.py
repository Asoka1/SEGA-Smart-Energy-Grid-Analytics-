"""Interactive Web Dashboard Server for SEGA (Smart Energy Grid Analytics).

Connects to TimescaleDB PostgreSQL database 'smartgrid' and provides:
- Live REST API for Section 7 Panels (with Raw vs CAgg mode toggle and latency tracking)
- Summary statistics, Anomaly detection, and ML Forecast visualization
- Interactive frontend on port 8080
"""

import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"
SERVER_PORT = 8080


def get_db():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )


class DashboardHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.serve_html()
        elif path.startswith("/api/"):
            self.serve_api(path, params)
        elif path in ["/demand_patterns.png", "/forecast_plot.png"]:
            self.serve_image(path[1:])
        else:
            self.send_error(404, "File Not Found")

    def serve_image(self, filename):
        if os.path.exists(filename):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            with open(filename, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "Image Not Found")

    def serve_api(self, path, params):
        mode = params.get("mode", ["cagg"])[0]
        meter_id = int(params.get("meter_id", [1000000000])[0])

        try:
            conn = get_db()
            cur = conn.cursor()

            if path == "/api/summary":
                # Storage & summary data
                cur.execute("SELECT count(*) FROM energy_readings;")
                readings_count = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM meters;")
                meters_count = cur.fetchone()[0]

                # Storage sizes
                cur.execute("SELECT hypertable_size('energy_readings'), hypertable_size('energy_readings_segmented');")
                base_bytes, seg_bytes = cur.fetchone()

                data = {
                    "total_readings": readings_count,
                    "total_meters": meters_count,
                    "period": "1 Sep 2026 - 28 Sep 2026 (28 Days CAT)",
                    "timezone": "Africa/Kigali (CAT, UTC+02)",
                    "baseline_mb": round(base_bytes / (1024 * 1024), 2),
                    "compressed_mb": round(seg_bytes / (1024 * 1024), 2),
                    "compression_saved_pct": round(100.0 * (base_bytes - seg_bytes) / base_bytes, 1),
                    "emqx_url": "http://localhost:18083"
                }
                self.send_json(data)

            elif path == "/api/panel1":
                # Network Demand Trend (22 - 28 Sep)
                t0 = time.perf_counter()
                if mode == "raw":
                    cur.execute("""
                        SELECT date_trunc('hour', event_time) AS bucket, sum(energy_kwh) AS total_energy
                        FROM energy_readings
                        WHERE event_time >= '2026-09-22 00:00:00+02' AND event_time < '2026-09-29 00:00:00+02'
                        GROUP BY bucket ORDER BY bucket;
                    """)
                else:
                    cur.execute("""
                        SELECT bucket, sum(total_energy_kwh) AS total_energy
                        FROM cagg_energy_hourly_region
                        WHERE bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
                        GROUP BY bucket ORDER BY bucket;
                    """)
                rows = cur.fetchall()
                dur_ms = (time.perf_counter() - t0) * 1000.0

                data = {
                    "mode": mode,
                    "duration_ms": round(dur_ms, 2),
                    "labels": [str(r[0])[5:16] for r in rows],
                    "values": [round(float(r[1]), 2) for r in rows]
                }
                self.send_json(data)

            elif path == "/api/panel2":
                # Regional Demand (22 - 28 Sep)
                t0 = time.perf_counter()
                if mode == "raw":
                    cur.execute("""
                        SELECT m.region, sum(r.energy_kwh) AS total_energy
                        FROM energy_readings r
                        JOIN meters m ON r.meter_id = m.meter_id
                        WHERE r.event_time >= '2026-09-22 00:00:00+02' AND r.event_time < '2026-09-29 00:00:00+02'
                        GROUP BY m.region ORDER BY m.region;
                    """)
                else:
                    cur.execute("""
                        SELECT region, sum(total_energy_kwh) AS total_energy
                        FROM cagg_energy_hourly_region
                        WHERE bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
                        GROUP BY region ORDER BY region;
                    """)
                rows = cur.fetchall()
                dur_ms = (time.perf_counter() - t0) * 1000.0

                data = {
                    "mode": mode,
                    "duration_ms": round(dur_ms, 2),
                    "labels": [r[0] for r in rows],
                    "values": [round(float(r[1]), 2) for r in rows]
                }
                self.send_json(data)

            elif path == "/api/panel3":
                # Customer Breakdown (22 - 28 Sep)
                t0 = time.perf_counter()
                if mode == "raw":
                    cur.execute("""
                        SELECT m.customer_type, sum(r.energy_kwh) AS total_energy
                        FROM energy_readings r
                        JOIN meters m ON r.meter_id = m.meter_id
                        WHERE r.event_time >= '2026-09-22 00:00:00+02' AND r.event_time < '2026-09-29 00:00:00+02'
                        GROUP BY m.customer_type ORDER BY total_energy DESC;
                    """)
                else:
                    cur.execute("""
                        SELECT m.customer_type, sum(d.total_energy_kwh) AS total_energy
                        FROM cagg_energy_daily_meter d
                        JOIN meters m ON d.meter_id = m.meter_id
                        WHERE d.bucket >= '2026-09-22 00:00:00+02' AND d.bucket < '2026-09-29 00:00:00+02'
                        GROUP BY m.customer_type ORDER BY total_energy DESC;
                    """)
                rows = cur.fetchall()
                dur_ms = (time.perf_counter() - t0) * 1000.0

                data = {
                    "mode": mode,
                    "duration_ms": round(dur_ms, 2),
                    "labels": [r[0].capitalize() for r in rows],
                    "values": [round(float(r[1]), 2) for r in rows]
                }
                self.send_json(data)

            elif path == "/api/panel4":
                # Top Peak Intervals
                t0 = time.perf_counter()
                if mode == "raw":
                    cur.execute("""
                        SELECT event_time, sum(energy_kwh) AS interval_energy
                        FROM energy_readings
                        WHERE event_time >= '2026-09-22 00:00:00+02' AND event_time < '2026-09-29 00:00:00+02'
                        GROUP BY event_time ORDER BY interval_energy DESC, event_time ASC LIMIT 10;
                    """)
                else:
                    cur.execute("""
                        SELECT bucket as event_time, sum(total_energy_kwh) AS interval_energy
                        FROM cagg_energy_15m_region
                        WHERE bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
                        GROUP BY bucket ORDER BY interval_energy DESC, bucket ASC LIMIT 10;
                    """)
                rows = cur.fetchall()
                dur_ms = (time.perf_counter() - t0) * 1000.0

                data = {
                    "mode": mode,
                    "duration_ms": round(dur_ms, 2),
                    "peaks": [{"rank": i + 1, "time": str(r[0]), "energy_kwh": round(float(r[1]), 2), "power_kw": round(float(r[1]) * 4, 2)} for i, r in enumerate(rows)]
                }
                self.send_json(data)

            elif path == "/api/panel5":
                # Meter History
                t0 = time.perf_counter()
                if mode == "raw":
                    cur.execute("""
                        SELECT date_trunc('day', event_time) AS day_bucket, sum(energy_kwh) AS total_energy
                        FROM energy_readings
                        WHERE meter_id = %s AND event_time >= '2026-09-22 00:00:00+02' AND event_time < '2026-09-29 00:00:00+02'
                        GROUP BY day_bucket ORDER BY day_bucket;
                    """, (meter_id,))
                else:
                    cur.execute("""
                        SELECT bucket AS day_bucket, total_energy_kwh AS total_energy
                        FROM cagg_energy_daily_meter
                        WHERE meter_id = %s AND bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
                        ORDER BY day_bucket;
                    """, (meter_id,))
                rows = cur.fetchall()
                dur_ms = (time.perf_counter() - t0) * 1000.0

                cur.execute("SELECT region, customer_type, base_power_kw FROM meters WHERE meter_id = %s;", (meter_id,))
                meta = cur.fetchone()

                data = {
                    "mode": mode,
                    "meter_id": meter_id,
                    "region": meta[0] if meta else "Unknown",
                    "customer_type": meta[1] if meta else "Unknown",
                    "base_power_kw": meta[2] if meta else 0,
                    "duration_ms": round(dur_ms, 2),
                    "labels": [str(r[0])[:10] for r in rows],
                    "values": [round(float(r[1]), 3) for r in rows]
                }
                self.send_json(data)

            elif path == "/api/forecast":
                if os.path.exists("forecasting_results_section8.json"):
                    with open("forecasting_results_section8.json", "r") as f:
                        f_data = json.load(f)
                    self.send_json(f_data)
                else:
                    self.send_json({"error": "Forecasting results file not found"})

            cur.close()
            conn.close()
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SEGA | Smart Energy Grid Analytics Platform</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: rgba(23, 32, 54, 0.7);
      --card-border: rgba(56, 189, 248, 0.15);
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.25);
      --success: #10b981;
      --warning: #f59e0b;
      --purple: #8b5cf6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      background-image: radial-gradient(circle at 15% 15%, rgba(56, 189, 248, 0.08) 0%, transparent 40%),
                        radial-gradient(circle at 85% 85%, rgba(139, 92, 246, 0.08) 0%, transparent 40%);
      color: var(--text);
      font-family: 'Outfit', sans-serif;
      min-height: 100vh;
      padding: 24px 32px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    .brand h1 {
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.5px;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .badge {
      background: linear-gradient(135deg, #0284c7, #38bdf8);
      color: #0b0f19;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
    }
    .brand p {
      font-size: 13px;
      color: var(--text-muted);
      margin-top: 4px;
    }
    .controls {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .mode-switch {
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 30px;
      padding: 4px;
      display: flex;
      gap: 4px;
    }
    .mode-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 8px 16px;
      border-radius: 20px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .mode-btn.active {
      background: var(--accent);
      color: #0b0f19;
      box-shadow: 0 0 12px var(--accent-glow);
    }
    .status-pill {
      display: flex;
      align-items: center;
      gap: 8px;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.3);
      padding: 8px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      color: #34d399;
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }
    
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .kpi-card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      position: relative;
      overflow: hidden;
    }
    .kpi-card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; width: 4px; height: 100%;
      background: var(--accent);
    }
    .kpi-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
    .kpi-val { font-size: 26px; font-weight: 700; margin: 6px 0 2px; }
    .kpi-sub { font-size: 12px; color: var(--text-muted); }
    
    .dashboard-grid {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 20px;
      margin-bottom: 20px;
    }
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(12px);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 22px;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }
    .card-title {
      font-size: 16px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .latency-badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 4px 10px;
      border-radius: 8px;
      background: rgba(56, 189, 248, 0.12);
      color: var(--accent);
      border: 1px solid rgba(56, 189, 248, 0.25);
    }
    .chart-container { position: relative; height: 260px; width: 100%; }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th {
      text-align: left;
      padding: 10px 12px;
      color: var(--text-muted);
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      font-weight: 500;
    }
    td {
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }
    .table-container { max-height: 260px; overflow-y: auto; }
    
    .forecast-section {
      margin-top: 24px;
    }
    .links-bar {
      display: flex;
      gap: 16px;
      margin-top: 24px;
      justify-content: flex-end;
    }
    .link-btn {
      color: var(--accent);
      text-decoration: none;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 14px;
      border: 1px solid rgba(56, 189, 248, 0.2);
      border-radius: 8px;
      background: rgba(56, 189, 248, 0.05);
      transition: all 0.2s;
    }
    .link-btn:hover { background: rgba(56, 189, 248, 0.15); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>SEGA Analytics <span class="badge">AUCA v3.0</span></h1>
      <p>Smart Energy Grid Analytics &middot; TimescaleDB &middot; EMQX Broker &middot; Timezone: Africa/Kigali (CAT, +02:00)</p>
    </div>
    <div class="controls">
      <div class="mode-switch">
        <button id="btn-cagg" class="mode-btn active" onclick="switchMode('cagg')">Continuous Aggregates (Mode B)</button>
        <button id="btn-raw" class="mode-btn" onclick="switchMode('raw')">Raw Hypertable (Mode A)</button>
      </div>
      <div class="status-pill">
        <div class="pulse-dot"></div>
        <span>Live PostgreSQL &middot; EMQX Up</span>
      </div>
    </div>
  </header>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">Cleaned Smart Readings</div>
      <div class="kpi-val" id="kpi-readings">10,643,735</div>
      <div class="kpi-sub">53,113 duplicates purged</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Active Smart Meters</div>
      <div class="kpi-val" id="kpi-meters">4,000</div>
      <div class="kpi-sub">10 Distribution Regions</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Hypertable Compression</div>
      <div class="kpi-val" id="kpi-comp">65.0%</div>
      <div class="kpi-sub" id="kpi-storage">501.9 MB / 1,435.5 MB (2.86x)</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Dashboard Speedup</div>
      <div class="kpi-val" id="kpi-speedup">36.1x</div>
      <div class="kpi-sub" id="kpi-latency">40.0 ms vs 1,445.2 ms</div>
    </div>
  </div>

  <div class="dashboard-grid">
    <!-- Panel 1: Network Demand Trend -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Panel 1: Network-wide Hourly Demand (22 &ndash; 28 Sep)</div>
        <div class="latency-badge" id="lat-p1">... ms</div>
      </div>
      <div class="chart-container">
        <canvas id="chart-p1"></canvas>
      </div>
    </div>

    <!-- Panel 3: Customer Type Breakdown -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Panel 3: Tariff Breakdown</div>
        <div class="latency-badge" id="lat-p3">... ms</div>
      </div>
      <div class="chart-container">
        <canvas id="chart-p3"></canvas>
      </div>
    </div>
  </div>

  <div class="dashboard-grid">
    <!-- Panel 2: Regional Demand Comparison -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Panel 2: Regional Total Consumption (kWh)</div>
        <div class="latency-badge" id="lat-p2">... ms</div>
      </div>
      <div class="chart-container">
        <canvas id="chart-p2"></canvas>
      </div>
    </div>

    <!-- Panel 4: Peak Demand Table -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Panel 4: Top 10 Peak Intervals</div>
        <div class="latency-badge" id="lat-p4">... ms</div>
      </div>
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Interval (CAT)</th>
              <th>Energy (kWh)</th>
              <th>Power (kW)</th>
            </tr>
          </thead>
          <tbody id="table-p4"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Panel 5 & Forecast -->
  <div class="dashboard-grid">
    <!-- Panel 5: Meter History -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Panel 5: Single Meter Daily History (Meter <span id="p5-meter-id">1000000000</span>)</div>
        <div class="latency-badge" id="lat-p5">... ms</div>
      </div>
      <p style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">Region: <span id="p5-reg" style="color:var(--text);">-</span> &middot; Type: <span id="p5-type" style="color:var(--text);">-</span> &middot; Base Load: <span id="p5-base" style="color:var(--text);">-</span> kW</p>
      <div class="chart-container">
        <canvas id="chart-p5"></canvas>
      </div>
    </div>

    <!-- Forecast summary card -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Section 8: Day-Ahead ML Forecast</div>
        <span class="badge" style="background:#10b981; color:#0b0f19;">Ridge Regression</span>
      </div>
      <div style="padding: 10px 0;">
        <p style="font-size: 13px; color: var(--text-muted); line-height: 1.6;">
          Trained on 14 days and evaluated on 1,680 regional test hours (22&ndash;28 Sep).
        </p>
        <div style="margin-top: 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
          <div style="background: rgba(255,255,255,0.03); padding: 12px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08);">
            <div style="font-size: 11px; color: var(--text-muted);">Model Test MAE</div>
            <div style="font-size: 20px; font-weight: 700; color: #38bdf8;">4.52 kWh</div>
            <div style="font-size: 11px; color: #34d399;">+89.4% vs Baseline</div>
          </div>
          <div style="background: rgba(255,255,255,0.03); padding: 12px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08);">
            <div style="font-size: 11px; color: var(--text-muted);">Baseline MAE</div>
            <div style="font-size: 20px; font-weight: 700; color: #f59e0b;">42.71 kWh</div>
            <div style="font-size: 11px; color: var(--text-muted);">Naive 24h persistence</div>
          </div>
        </div>
        <div style="margin-top: 16px;">
          <a href="/forecast_plot.png" target="_blank" class="link-btn" style="justify-content: center;">
            <span>View Full 7-Day Forecast Plot (PNG)</span> &rarr;
          </a>
        </div>
      </div>
    </div>
  </div>

  <div class="links-bar">
    <a href="http://localhost:18083" target="_blank" class="link-btn">
      <span>Open EMQX MQTT Dashboard (Port 18083)</span> &rarr;
    </a>
    <a href="/demand_patterns.png" target="_blank" class="link-btn">
      <span>View Section 5 Demand Patterns Chart</span> &rarr;
    </a>
  </div>

  <script>
    let currentMode = 'cagg';
    let chartP1 = null, chartP2 = null, chartP3 = null, chartP5 = null;

    Chart.defaults.color = '#94a3b8';
    Chart.defaults.font.family = 'Outfit';

    function switchMode(m) {
      currentMode = m;
      document.getElementById('btn-cagg').classList.toggle('active', m === 'cagg');
      document.getElementById('btn-raw').classList.toggle('active', m === 'raw');
      loadAllPanels();
    }

    async function loadSummary() {
      try {
        const res = await fetch('/api/summary');
        const d = await res.json();
        document.getElementById('kpi-readings').innerText = d.total_readings.toLocaleString();
        document.getElementById('kpi-meters').innerText = d.total_meters.toLocaleString();
        document.getElementById('kpi-comp').innerText = d.compression_saved_pct + '%';
        document.getElementById('kpi-storage').innerText = `${d.compressed_mb} MB / ${d.baseline_mb} MB (2.86x)`;
      } catch(e) { console.error(e); }
    }

    async function loadPanel1() {
      const res = await fetch(`/api/panel1?mode=${currentMode}`);
      const d = await res.json();
      document.getElementById('lat-p1').innerText = `${d.duration_ms} ms (${currentMode.toUpperCase()})`;

      const ctx = document.getElementById('chart-p1').getContext('2d');
      if (chartP1) chartP1.destroy();
      chartP1 = new Chart(ctx, {
        type: 'line',
        data: {
          labels: d.labels,
          datasets: [{
            label: 'Network Energy (kWh)',
            data: d.values,
            borderColor: '#38bdf8',
            backgroundColor: 'rgba(56, 189, 248, 0.08)',
            borderWidth: 2,
            fill: true,
            tension: 0.3,
            pointRadius: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false }, ticks: { maxTicksLimit: 7 } },
            y: { grid: { color: 'rgba(255, 255, 255, 0.05)' } }
          }
        }
      });
    }

    async function loadPanel2() {
      const res = await fetch(`/api/panel2?mode=${currentMode}`);
      const d = await res.json();
      document.getElementById('lat-p2').innerText = `${d.duration_ms} ms (${currentMode.toUpperCase()})`;

      const ctx = document.getElementById('chart-p2').getContext('2d');
      if (chartP2) chartP2.destroy();
      chartP2 = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: d.labels,
          datasets: [{
            label: 'Total Energy (kWh)',
            data: d.values,
            backgroundColor: '#8b5cf6',
            borderRadius: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false } },
            y: { grid: { color: 'rgba(255, 255, 255, 0.05)' } }
          }
        }
      });
    }

    async function loadPanel3() {
      const res = await fetch(`/api/panel3?mode=${currentMode}`);
      const d = await res.json();
      document.getElementById('lat-p3').innerText = `${d.duration_ms} ms (${currentMode.toUpperCase()})`;

      const ctx = document.getElementById('chart-p3').getContext('2d');
      if (chartP3) chartP3.destroy();
      chartP3 = new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: d.labels,
          datasets: [{
            data: d.values,
            backgroundColor: ['#38bdf8', '#8b5cf6', '#10b981'],
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } } },
          cutout: '65%'
        }
      });
    }

    async function loadPanel4() {
      const res = await fetch(`/api/panel4?mode=${currentMode}`);
      const d = await res.json();
      document.getElementById('lat-p4').innerText = `${d.duration_ms} ms (${currentMode.toUpperCase()})`;

      const tbody = document.getElementById('table-p4');
      tbody.innerHTML = d.peaks.map(p => `
        <tr>
          <td><span style="color:var(--accent); font-weight:600;">${p.rank}</span></td>
          <td style="font-family:'JetBrains Mono'; font-size:12px;">${p.time.slice(0, 19)}</td>
          <td>${p.energy_kwh.toLocaleString()}</td>
          <td style="font-weight:600;">${p.power_kw.toLocaleString()}</td>
        </tr>
      `).join('');
    }

    async function loadPanel5() {
      const res = await fetch(`/api/panel5?meter_id=1000000000&mode=${currentMode}`);
      const d = await res.json();
      document.getElementById('lat-p5').innerText = `${d.duration_ms} ms (${currentMode.toUpperCase()})`;
      document.getElementById('p5-meter-id').innerText = d.meter_id;
      document.getElementById('p5-reg').innerText = d.region;
      document.getElementById('p5-type').innerText = d.customer_type;
      document.getElementById('p5-base').innerText = d.base_power_kw;

      const ctx = document.getElementById('chart-p5').getContext('2d');
      if (chartP5) chartP5.destroy();
      chartP5 = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: d.labels,
          datasets: [{
            label: 'Daily Energy (kWh)',
            data: d.values,
            backgroundColor: '#10b981',
            borderRadius: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false } },
            y: { grid: { color: 'rgba(255, 255, 255, 0.05)' } }
          }
        }
      });
    }

    function loadAllPanels() {
      loadPanel1();
      loadPanel2();
      loadPanel3();
      loadPanel4();
      loadPanel5();
    }

    window.onload = () => {
      loadSummary();
      loadAllPanels();
    };
  </script>
</body>
</html>
"""
        self.wfile.write(html.encode("utf-8"))


def run():
    server = HTTPServer(("0.0.0.0", SERVER_PORT), DashboardHandler)
    print(f"SEGA Interactive Dashboard server started on http://localhost:{SERVER_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
