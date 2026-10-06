"""Dashboard comparison and query benchmarks (Section 7).

Panels (Filtered to 22 Sep 00:00 - 29 Sep 00:00 CAT):
Panel 1: Network Demand Trend (hourly consumption over 7 days)
Panel 2: Regional Demand Comparison (total energy per region)
Panel 3: Customer Type Breakdown (energy per customer type)
Panel 4: Top Peak Load Intervals (top 10 15-min intervals)
Panel 5: Meter History (daily energy for meter 1000000000)

Evaluates:
- Mode A: Raw hypertable (energy_readings)
- Mode B: Continuous aggregates (cagg_energy_hourly_region, cagg_energy_15m_region, cagg_energy_daily_meter)
- Numerical verification (energy diff <= 0.001 kWh)
- 1 warm-up + 10 measured refreshes per mode, reporting min, max, median durations in ms.
"""

import json
import statistics
import time
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"

PANEL_QUERIES = {
    "Panel 1 (Network Demand)": {
        "raw": """
            SELECT date_trunc('hour', event_time) AS bucket, sum(energy_kwh) AS total_energy, count(*) as cnt
            FROM energy_readings
            WHERE event_time >= '2026-09-22 00:00:00+02' AND event_time < '2026-09-29 00:00:00+02'
            GROUP BY bucket ORDER BY bucket;
        """,
        "cagg": """
            SELECT bucket, sum(total_energy_kwh) AS total_energy, sum(reading_count) as cnt
            FROM cagg_energy_hourly_region
            WHERE bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
            GROUP BY bucket ORDER BY bucket;
        """
    },
    "Panel 2 (Regional Comparison)": {
        "raw": """
            SELECT m.region, sum(r.energy_kwh) AS total_energy, count(*) as cnt
            FROM energy_readings r
            JOIN meters m ON r.meter_id = m.meter_id
            WHERE r.event_time >= '2026-09-22 00:00:00+02' AND r.event_time < '2026-09-29 00:00:00+02'
            GROUP BY m.region ORDER BY m.region;
        """,
        "cagg": """
            SELECT region, sum(total_energy_kwh) AS total_energy, sum(reading_count) as cnt
            FROM cagg_energy_hourly_region
            WHERE bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
            GROUP BY region ORDER BY region;
        """
    },
    "Panel 3 (Customer Breakdown)": {
        "raw": """
            SELECT m.customer_type, sum(r.energy_kwh) AS total_energy, count(*) as cnt
            FROM energy_readings r
            JOIN meters m ON r.meter_id = m.meter_id
            WHERE r.event_time >= '2026-09-22 00:00:00+02' AND r.event_time < '2026-09-29 00:00:00+02'
            GROUP BY m.customer_type ORDER BY m.customer_type;
        """,
        "cagg": """
            SELECT m.customer_type, sum(d.total_energy_kwh) AS total_energy, sum(d.reading_count) as cnt
            FROM cagg_energy_daily_meter d
            JOIN meters m ON d.meter_id = m.meter_id
            WHERE d.bucket >= '2026-09-22 00:00:00+02' AND d.bucket < '2026-09-29 00:00:00+02'
            GROUP BY m.customer_type ORDER BY m.customer_type;
        """
    },
    "Panel 4 (Top Peak Intervals)": {
        "raw": """
            SELECT event_time, sum(energy_kwh) AS interval_energy, count(*) as cnt
            FROM energy_readings
            WHERE event_time >= '2026-09-22 00:00:00+02' AND event_time < '2026-09-29 00:00:00+02'
            GROUP BY event_time ORDER BY interval_energy DESC, event_time ASC LIMIT 10;
        """,
        "cagg": """
            SELECT bucket as event_time, sum(total_energy_kwh) AS interval_energy, sum(reading_count) as cnt
            FROM cagg_energy_15m_region
            WHERE bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
            GROUP BY bucket ORDER BY interval_energy DESC, bucket ASC LIMIT 10;
        """
    },
    "Panel 5 (Meter History)": {
        "raw": """
            SELECT date_trunc('day', event_time) AS day_bucket, sum(energy_kwh) AS total_energy, count(*) as cnt
            FROM energy_readings
            WHERE meter_id = 1000000000 AND event_time >= '2026-09-22 00:00:00+02' AND event_time < '2026-09-29 00:00:00+02'
            GROUP BY day_bucket ORDER BY day_bucket;
        """,
        "cagg": """
            SELECT bucket AS day_bucket, total_energy_kwh AS total_energy, reading_count as cnt
            FROM cagg_energy_daily_meter
            WHERE meter_id = 1000000000 AND bucket >= '2026-09-22 00:00:00+02' AND bucket < '2026-09-29 00:00:00+02'
            ORDER BY day_bucket;
        """
    }
}


def main():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    cur = conn.cursor()

    print("==========================================================================")
    print("SECTION 7.3: NUMERICAL DATA VERIFICATION ACROSS FIVE PANELS")
    print("==========================================================================")

    for p_name, q_dict in PANEL_QUERIES.items():
        cur.execute(q_dict["raw"])
        raw_res = cur.fetchall()
        cur.execute(q_dict["cagg"])
        cagg_res = cur.fetchall()

        row_count_match = len(raw_res) == len(cagg_res)
        max_diff = 0.0
        for r_row, c_row in zip(raw_res, cagg_res):
            diff = abs(float(r_row[1]) - float(c_row[1]))
            if diff > max_diff:
                max_diff = diff

        print(f"{p_name:<30} | Rows: {len(raw_res):<4} | Count Match: {str(row_count_match):<5} | Max Energy Diff: {max_diff:.6f} kWh (<=0.001? {max_diff <= 0.001})")

    print("\n==========================================================================")
    print("SECTION 7.4: PERFORMANCE MEASUREMENT (1 Warm-up + 10 Measured Refreshes)")
    print("==========================================================================")

    modes = ["raw", "cagg"]
    panel_timings = {m: {p: [] for p in PANEL_QUERIES} for m in modes}
    dashboard_timings = {m: [] for m in modes}

    for mode in modes:
        mode_label = "Mode A (Raw Hypertable)" if mode == "raw" else "Mode B (Continuous Aggregates)"
        print(f"\nBenchmarking {mode_label}...")

        # Warm-up refresh of all 5 panels
        for p_name, q_dict in PANEL_QUERIES.items():
            cur.execute(q_dict[mode])
            cur.fetchall()

        # 10 Measured refreshes
        for ref_idx in range(1, 11):
            dash_start = time.perf_counter()
            for p_name, q_dict in PANEL_QUERIES.items():
                p_start = time.perf_counter()
                cur.execute(q_dict[mode])
                cur.fetchall()
                p_dur_ms = (time.perf_counter() - p_start) * 1000.0
                panel_timings[mode][p_name].append(p_dur_ms)
            dash_dur_ms = (time.perf_counter() - dash_start) * 1000.0
            dashboard_timings[mode].append(dash_dur_ms)

    print("\n" + "=" * 95)
    print("DASHBOARD TIMING SUMMARY TABLE (10 Refreshes, in ms)")
    print("=" * 95)
    print(f"{'Panel / Component':<30} | {'Raw Median':<12} | {'Raw Min/Max':<16} | {'CAgg Median':<12} | {'CAgg Min/Max':<16} | {'Speedup'}")
    print("-" * 95)

    summary_export = {}
    for p_name in PANEL_QUERIES:
        r_list = panel_timings["raw"][p_name]
        c_list = panel_timings["cagg"][p_name]
        r_med, r_min, r_max = statistics.median(r_list), min(r_list), max(r_list)
        c_med, c_min, c_max = statistics.median(c_list), min(c_list), max(c_list)
        speedup = r_med / c_med if c_med > 0 else 0

        summary_export[p_name] = {
            "raw_median": round(r_med, 2),
            "raw_min": round(r_min, 2),
            "raw_max": round(r_max, 2),
            "cagg_median": round(c_med, 2),
            "cagg_min": round(c_min, 2),
            "cagg_max": round(c_max, 2),
            "speedup": round(speedup, 1)
        }

        print(f"{p_name:<30} | {r_med:10.2f} ms | {r_min:6.1f} / {r_max:<6.1f} | {c_med:10.2f} ms | {c_min:6.1f} / {c_max:<6.1f} | {speedup:6.1f}x")

    # Dashboard total
    d_r = dashboard_timings["raw"]
    d_c = dashboard_timings["cagg"]
    d_r_med, d_r_min, d_r_max = statistics.median(d_r), min(d_r), max(d_r)
    d_c_med, d_c_min, d_c_max = statistics.median(d_c), min(d_c), max(d_c)
    dash_speedup = d_r_med / d_c_med

    summary_export["Whole Dashboard"] = {
        "raw_median": round(d_r_med, 2),
        "raw_min": round(d_r_min, 2),
        "raw_max": round(d_r_max, 2),
        "cagg_median": round(d_c_med, 2),
        "cagg_min": round(d_c_min, 2),
        "cagg_max": round(d_c_max, 2),
        "speedup": round(dash_speedup, 1)
    }

    print("-" * 95)
    print(f"{'WHOLE DASHBOARD (5 panels)':<30} | {d_r_med:10.2f} ms | {d_r_min:6.1f} / {d_r_max:<6.1f} | {d_c_med:10.2f} ms | {d_c_min:6.1f} / {d_c_max:<6.1f} | {dash_speedup:6.1f}x")
    print("=" * 95)

    with open("dashboard_benchmarks_section7.json", "w") as f:
        json.dump(summary_export, f, indent=2)

    print("\nSection 7 results saved to 'dashboard_benchmarks_section7.json'.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
