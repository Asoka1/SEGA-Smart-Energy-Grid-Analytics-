"""Compression experiments and comparison (Section 4).

Measures storage and query performance for:
1. Baseline: energy_readings (1-day uncompressed)
2. energy_readings_unsegmented (1-day, orderby event_time DESC, compressed)
3. energy_readings_segmented   (1-day, segmentby meter_id, orderby event_time DESC, compressed)
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


def main():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = True
    cur = conn.cursor()

    print("=== Section 4: Storage & Query Performance with Compression ===")

    # Function to query exact sizes via hypertable_detailed_size
    def get_detailed_size(tbl):
        cur.execute(f"SELECT table_bytes, index_bytes, total_bytes FROM hypertable_detailed_size('{tbl}');")
        return cur.fetchone()

    base_table, base_idx, base_total = get_detailed_size("energy_readings")
    unseg_table, unseg_idx, unseg_total = get_detailed_size("energy_readings_unsegmented")
    seg_table, seg_idx, seg_total = get_detailed_size("energy_readings_segmented")

    # Compression elapsed times measured in earlier run:
    # unseg: 24.65 seconds, seg: 89.16 seconds
    unseg_cost_sec = 24.65
    seg_cost_sec = 89.16

    unseg_ratio = base_total / unseg_total
    unseg_savings = 100.0 * (base_total - unseg_total) / base_total

    seg_ratio = base_total / seg_total
    seg_savings = 100.0 * (base_total - seg_total) / base_total

    # Chunk status check
    cur.execute("SELECT count(*) FROM timescaledb_information.chunks WHERE hypertable_name = 'energy_readings_unsegmented' AND is_compressed = true;")
    unseg_comp_chunks = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM timescaledb_information.chunks WHERE hypertable_name = 'energy_readings_segmented' AND is_compressed = true;")
    seg_comp_chunks = cur.fetchone()[0]

    print("\n" + "=" * 98)
    print("SECTION 4.2 STORAGE AND COMPRESSION METRICS")
    print("=" * 98)
    print(f"{'Configuration':<30} | {'Total (MB)':<10} | {'Data (MB)':<10} | {'Index (MB)':<10} | {'Ratio':<8} | {'Saved %':<8} | {'Chunks':<8} | {'Cost (s)'}")
    print("-" * 98)
    print(f"{'Baseline (1-day uncompressed)':<30} | {base_total/(1024*1024):10.2f} | {base_table/(1024*1024):10.2f} | {base_idx/(1024*1024):10.2f} | {'1.00x':<8} | {'0.00%':<8} | {'29/29':<8} | {'0.0s'}")
    print(f"{'Unsegmented (event_time)':<30} | {unseg_total/(1024*1024):10.2f} | {unseg_table/(1024*1024):10.2f} | {unseg_idx/(1024*1024):10.2f} | {unseg_ratio:7.2f}x | {unseg_savings:7.2f}% | {f'{unseg_comp_chunks}/29':<8} | {unseg_cost_sec:6.2f}s")
    print(f"{'Segmented (meter_id, event)':<30} | {seg_total/(1024*1024):10.2f} | {seg_table/(1024*1024):10.2f} | {seg_idx/(1024*1024):10.2f} | {seg_ratio:7.2f}x | {seg_savings:7.2f}% | {f'{seg_comp_chunks}/29':<8} | {seg_cost_sec:6.2f}s")
    print("-" * 98)

    # Benchmark Q1 - Q5 on compressed tables
    print("\nRunning Workload Benchmarks on Compressed Tables (1 Warm-up + 5 Measured Runs)...")
    comp_results = {}
    comp_plans = {}

    for q_name in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        print(f"\n--- Benchmarking {q_name} on Compressed Configurations ---")
        comp_results[q_name] = {}
        comp_plans[q_name] = {}
        for tbl, label in [
            ("energy_readings_unsegmented", "Unsegmented"),
            ("energy_readings_segmented", "Segmented")
        ]:
            sql = QUERIES[q_name].format(table=tbl)

            # Warm-up run
            cur.execute(sql)
            cur.fetchall()

            # 5 measured runs
            durations = []
            for _ in range(5):
                t0 = time.perf_counter()
                cur.execute(sql)
                rows = cur.fetchall()
                t1 = time.perf_counter()
                durations.append((t1 - t0) * 1000.0)

            # Capture EXPLAIN (ANALYZE, BUFFERS)
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")
            plan = "\n".join(r[0] for r in cur.fetchall())
            comp_plans[q_name][tbl] = plan

            med = statistics.median(durations)
            comp_results[q_name][tbl] = {
                "median_ms": round(med, 2),
                "min_ms": round(min(durations), 2),
                "max_ms": round(max(durations), 2)
            }
            print(f"  {tbl:<28} ({label:<11}): Median={med:8.2f} ms | Min={min(durations):8.2f} ms | Max={max(durations):8.2f} ms")

    # Load baseline results from Section 3
    with open("benchmark_results_section3.json", "r") as f:
        s3_data = json.load(f)["results"]

    print("\n" + "=" * 96)
    print("SECTION 4.3 COMPREHENSIVE QUERY PERFORMANCE & % IMPROVEMENT VS BASELINE")
    print("=" * 96)
    print(f"{'Query':<6} | {'1-day Base (ms)':<16} | {'Unsegmented (ms)':<17} | {'Improv %':<10} | {'Segmented (ms)':<16} | {'Improv %'}")
    print("-" * 96)

    for q_name in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        base_med = s3_data[q_name]["energy_readings"]["median_ms"]
        unseg_med = comp_results[q_name]["energy_readings_unsegmented"]["median_ms"]
        seg_med = comp_results[q_name]["energy_readings_segmented"]["median_ms"]

        unseg_imp = 100.0 * (base_med - unseg_med) / base_med
        seg_imp = 100.0 * (base_med - seg_med) / base_med

        print(f"{q_name:<6} | {base_med:16.2f} | {unseg_med:17.2f} | {unseg_imp:9.2f}% | {seg_med:16.2f} | {seg_imp:9.2f}%")
    print("-" * 96)

    # Save compression results and plans
    with open("compression_results_section4.json", "w") as f:
        json.dump({
            "storage": {
                "baseline_total_mb": round(base_total / (1024*1024), 2),
                "unseg_total_mb": round(unseg_total / (1024*1024), 2),
                "unseg_ratio": round(unseg_ratio, 2),
                "unseg_savings_pct": round(unseg_savings, 2),
                "unseg_cost_sec": unseg_cost_sec,
                "seg_total_mb": round(seg_total / (1024*1024), 2),
                "seg_ratio": round(seg_ratio, 2),
                "seg_savings_pct": round(seg_savings, 2),
                "seg_cost_sec": seg_cost_sec
            },
            "benchmarks": comp_results
        }, f, indent=2)

    with open("query_plans_section4.txt", "w") as f:
        for q_name, plans in comp_plans.items():
            f.write(f"\n==================== {q_name} COMPRESSED PLANS ====================\n")
            for tbl, plan in plans.items():
                f.write(f"\n--- Table: {tbl} ---\n{plan}\n")

    print("\nSection 4 completed successfully! Results saved to 'compression_results_section4.json' and 'query_plans_section4.txt'.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
