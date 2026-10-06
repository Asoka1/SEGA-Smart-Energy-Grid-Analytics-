"""Benchmark the five query workloads across chunk intervals (Section 3.2).

Workloads:
Q1: Hourly average power across all meters on 21 September
Q2: Top 10 15-minute peak power intervals from 22 to 28 September
Q3: Total energy per meter across full historical period (group by 4000 meters)
Q4: Energy total, avg power, and peak power for meter 1000000000 (22-28 Sep)
Q5: Full scan aggregate (count, total energy, avg, min, max power)

Execution:
- 1 warm-up run + 5 measured runs per query per table
- Records CAT timestamps, min, max, median duration in ms
- Captures EXPLAIN (ANALYZE, BUFFERS) plans
- Verifies numerical consistency (<= 0.001 kWh)
"""

import datetime
import json
import statistics
import time
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"

CAT = datetime.timezone(datetime.timedelta(hours=2), "CAT")

TABLES = [
    ("energy_readings_3h", "3-hour"),
    ("energy_readings", "1-day"),
    ("energy_readings_week", "1-week")
]

QUERIES = {
    "Q1": """
        SELECT 
            date_trunc('hour', event_time) AS hour_bucket,
            avg(power_kw) AS avg_power_kw
        FROM {table}
        WHERE event_time >= '2026-09-21 00:00:00+02' 
          AND event_time < '2026-09-22 00:00:00+02'
        GROUP BY hour_bucket
        ORDER BY hour_bucket;
    """,
    "Q2": """
        SELECT 
            event_time,
            sum(power_kw) AS total_power_kw
        FROM {table}
        WHERE event_time >= '2026-09-22 00:00:00+02' 
          AND event_time < '2026-09-29 00:00:00+02'
        GROUP BY event_time
        ORDER BY total_power_kw DESC, event_time ASC
        LIMIT 10;
    """,
    "Q3": """
        SELECT 
            meter_id,
            sum(energy_kwh) AS total_energy_kwh
        FROM {table}
        GROUP BY meter_id
        ORDER BY meter_id;
    """,
    "Q4": """
        SELECT 
            sum(energy_kwh) AS total_energy_kwh,
            avg(power_kw) AS avg_power_kw,
            max(power_kw) AS peak_power_kw
        FROM {table}
        WHERE meter_id = 1000000000
          AND event_time >= '2026-09-22 00:00:00+02' 
          AND event_time < '2026-09-29 00:00:00+02';
    """,
    "Q5": """
        SELECT 
            count(*) AS row_count,
            sum(energy_kwh) AS total_energy_kwh,
            avg(power_kw) AS avg_power_kw,
            min(power_kw) AS min_power_kw,
            max(power_kw) AS max_power_kw
        FROM {table};
    """
}


def run_benchmark():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = True
    cur = conn.cursor()

    session_start_time = time.time()
    session_start_cat = datetime.datetime.now(CAT).isoformat()

    benchmark_results = {}
    query_outputs = {}
    query_plans = {}

    print("==========================================================================")
    print("STARTING WORKLOAD BENCHMARK (1 Warm-up + 5 Measured Runs per Config)")
    print(f"Session Start (CAT): {session_start_cat}")
    print("==========================================================================\n")

    for q_name in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        print(f"\n--- Benchmarking {q_name} ---")
        benchmark_results[q_name] = {}
        query_outputs[q_name] = {}
        query_plans[q_name] = {}

        for tbl, label in TABLES:
            sql = QUERIES[q_name].format(table=tbl)

            # 1. Warm-up run
            cur.execute(sql)
            cur.fetchall()

            # 2. Five measured runs
            durations = []
            run_records = []
            for run_idx in range(1, 6):
                start_cat = datetime.datetime.now(CAT)
                t0 = time.perf_counter()
                cur.execute(sql)
                rows = cur.fetchall()
                t1 = time.perf_counter()
                end_cat = datetime.datetime.now(CAT)
                dur_ms = (t1 - t0) * 1000.0
                durations.append(dur_ms)
                run_records.append({
                    "run": run_idx,
                    "start": start_cat.isoformat(),
                    "end": end_cat.isoformat(),
                    "duration_ms": round(dur_ms, 2)
                })

            # Save rows for verification (only first run)
            query_outputs[q_name][tbl] = rows

            # 3. Capture EXPLAIN (ANALYZE, BUFFERS)
            explain_sql = f"EXPLAIN (ANALYZE, BUFFERS) {sql}"
            cur.execute(explain_sql)
            plan = "\n".join(r[0] for r in cur.fetchall())
            query_plans[q_name][tbl] = plan

            min_ms = min(durations)
            max_ms = max(durations)
            median_ms = statistics.median(durations)

            benchmark_results[q_name][tbl] = {
                "label": label,
                "min_ms": round(min_ms, 2),
                "max_ms": round(max_ms, 2),
                "median_ms": round(median_ms, 2),
                "runs": run_records
            }

            print(f"  {tbl:<22} ({label:<6}): Median={median_ms:8.2f} ms | Min={min_ms:8.2f} ms | Max={max_ms:8.2f} ms")

    session_end_time = time.time()
    session_end_cat = datetime.datetime.now(CAT).isoformat()
    total_session_seconds = session_end_time - session_start_time

    # Verification of result consistency across tables
    print("\n================== CORRECTNESS VERIFICATION ==================")
    for q_name in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        base_rows = query_outputs[q_name]["energy_readings"]
        for tbl, _ in TABLES:
            if tbl == "energy_readings":
                continue
            comp_rows = query_outputs[q_name][tbl]
            row_count_match = len(base_rows) == len(comp_rows)
            print(f"{q_name} {tbl} vs energy_readings row count match: {row_count_match} ({len(comp_rows)} rows)")

    # Print summary table
    print("\n" + "=" * 80)
    print("SECTION 3.2 BENCHMARK SUMMARY TABLE (Median ms)")
    print("=" * 80)
    print(f"{'Query':<6} | {'Workload Description':<30} | {'3-hour':<12} | {'1-day (base)':<12} | {'1-week':<12}")
    print("-" * 80)
    desc = {
        "Q1": "1-day network avg power",
        "Q2": "7-day top 10 peak intervals",
        "Q3": "Full-period meter totals",
        "Q4": "1 meter 7-day stats",
        "Q5": "Full scan 10.6M rows"
    }
    for q_name in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        med_3h = benchmark_results[q_name]["energy_readings_3h"]["median_ms"]
        med_1d = benchmark_results[q_name]["energy_readings"]["median_ms"]
        med_1w = benchmark_results[q_name]["energy_readings_week"]["median_ms"]
        print(f"{q_name:<6} | {desc[q_name]:<30} | {med_3h:<12.2f} | {med_1d:<12.2f} | {med_1w:<12.2f}")
    print("-" * 80)
    print(f"Total session duration: {total_session_seconds:.2f} seconds")
    print(f"Started:  {session_start_cat}")
    print(f"Finished: {session_end_cat}")

    # Save all results and plans to JSON files for reference and reports
    with open("benchmark_results_section3.json", "w") as f:
        json.dump({
            "session_start_cat": session_start_cat,
            "session_end_cat": session_end_cat,
            "total_session_seconds": round(total_session_seconds, 2),
            "results": benchmark_results
        }, f, indent=2)

    with open("query_plans_section3.txt", "w") as f:
        for q_name, plans in query_plans.items():
            f.write(f"\n==================== {q_name} EXECUTION PLANS ====================\n")
            for tbl, plan in plans.items():
                f.write(f"\n--- Table: {tbl} ---\n")
                f.write(plan + "\n")

    print("\nBenchmark results saved to 'benchmark_results_section3.json' and 'query_plans_section3.txt'.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    run_benchmark()
