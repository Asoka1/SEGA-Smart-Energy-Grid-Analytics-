"""Continuous Aggregates and Freshness Testing (Section 6).

Implements:
Section 6.1:
1. Creates 3 continuous aggregates over energy_readings:
   - cagg_energy_15m_region (15-min bucket, region)
   - cagg_energy_hourly_region (1-hour bucket, region)
   - cagg_energy_daily_meter (1-day bucket, meter_id)
   All initially unpopulated (WITH NO DATA), real-time aggregation disabled (materialized_only = true).
2. Sets automatic refresh policies on hourly and daily views (every 30m, 7 days back, 1h end_offset).
3. Manually refreshes all 3 views for full 28 days, measuring elapsed seconds and sizes.

Section 6.2:
1. Creates separate test hypertable 'test_energy_freshness' and hourly continuous aggregate 'cagg_test_hourly_freshness' (materialized_only = true).
2. Follows the exact worked timeline:
   - Identifies current CAT time, completed hour H, and older hour H - 48h.
   - Inserts 1 kWh into H - 48h, refreshes older hour, queries aggregate.
   - Inserts 0.5 kWh into H - 48h and 2 kWh into H.
   - Queries raw and aggregate with real-time off.
   - Queries aggregate with real-time on.
   - Manually refreshes both hours and queries again (verifying 1.5 kWh / 2 rows and 2 kWh / 1 row).
"""

import datetime
import json
import time
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"

CAT = datetime.timezone(datetime.timedelta(hours=2), "CAT")


def run_section_6():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = True
    cur = conn.cursor()

    print("==========================================================================")
    print("SECTION 6.1: CREATE AND REFRESH CONTINUOUS AGGREGATES")
    print("==========================================================================")

    # Clean up any existing continuous aggregates
    views = [
        "cagg_energy_15m_region",
        "cagg_energy_hourly_region",
        "cagg_energy_daily_meter"
    ]
    for v in views:
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {v} CASCADE;")

    # 1. Create cagg_energy_15m_region
    print("Creating cagg_energy_15m_region (15 minutes, region)...")
    cur.execute("""
        CREATE MATERIALIZED VIEW cagg_energy_15m_region
        WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
        SELECT 
            time_bucket('15 minutes', r.event_time) AS bucket,
            m.region,
            sum(r.energy_kwh) AS total_energy_kwh,
            count(*) AS reading_count
        FROM energy_readings r
        JOIN meters m ON r.meter_id = m.meter_id
        GROUP BY bucket, m.region
        WITH NO DATA;
    """)

    # 2. Create cagg_energy_hourly_region
    print("Creating cagg_energy_hourly_region (1 hour, region)...")
    cur.execute("""
        CREATE MATERIALIZED VIEW cagg_energy_hourly_region
        WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
        SELECT 
            time_bucket('1 hour', r.event_time) AS bucket,
            m.region,
            sum(r.energy_kwh) AS total_energy_kwh,
            count(*) AS reading_count
        FROM energy_readings r
        JOIN meters m ON r.meter_id = m.meter_id
        GROUP BY bucket, m.region
        WITH NO DATA;
    """)

    # 3. Create cagg_energy_daily_meter
    print("Creating cagg_energy_daily_meter (1 day, meter_id)...")
    cur.execute("""
        CREATE MATERIALIZED VIEW cagg_energy_daily_meter
        WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
        SELECT 
            time_bucket('1 day', event_time) AS bucket,
            meter_id,
            sum(energy_kwh) AS total_energy_kwh,
            count(*) AS reading_count
        FROM energy_readings
        GROUP BY bucket, meter_id
        WITH NO DATA;
    """)

    # Refresh policies on hourly and daily views
    print("\nAdding refresh policies to hourly and daily views...")
    cur.execute("""
        SELECT add_continuous_aggregate_policy(
            'cagg_energy_hourly_region',
            start_offset => INTERVAL '7 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '30 minutes'
        );
    """)
    cur.execute("""
        SELECT add_continuous_aggregate_policy(
            'cagg_energy_daily_meter',
            start_offset => INTERVAL '7 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '30 minutes'
        );
    """)
    print("Policies attached successfully.")

    # Measure before sizes
    def get_view_size(vname):
        cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{vname}')), pg_total_relation_size('{vname}');")
        return cur.fetchone()

    sizes_before = {v: get_view_size(v) for v in views}

    # Manual refreshes for the full 28 days
    start_history = "2026-09-01 00:00:00+02"
    end_history = "2026-09-29 00:00:00+02"

    refresh_stats = {}
    for v in views:
        print(f"\nManually refreshing {v} for full 28-day period...")
        t0 = time.time()
        cur.execute(f"CALL refresh_continuous_aggregate('{v}', '{start_history}', '{end_history}');")
        elap = time.time() - t0
        sz_after = get_view_size(v)
        refresh_stats[v] = {
            "elapsed_sec": round(elap, 2),
            "size_before": sizes_before[v][0],
            "size_after": sz_after[0]
        }
        print(f"  {v} refreshed in {elap:.2f} seconds | Size: {sizes_before[v][0]} -> {sz_after[0]}")

    print("\n" + "=" * 80)
    print("CONTINUOUS AGGREGATE REFRESH SUMMARY (Section 6.1)")
    print("=" * 80)
    print(f"{'View Name':<28} | {'Elapsed Time':<14} | {'Size Before':<14} | {'Size After'}")
    print("-" * 80)
    for v in views:
        st = refresh_stats[v]
        print(f"{v:<28} | {st['elapsed_sec']:6.2f} sec    | {st['size_before']:<14} | {st['size_after']}")
    print("-" * 80)

    print("\n==========================================================================")
    print("SECTION 6.2: TEST FRESHNESS AND DELAYED ARRIVALS")
    print("==========================================================================")

    # 1. Setup separate test hypertable
    cur.execute("DROP TABLE IF EXISTS test_energy_freshness CASCADE;")
    cur.execute("""
        CREATE TABLE test_energy_freshness (
            event_time TIMESTAMPTZ NOT NULL,
            energy_kwh DOUBLE PRECISION NOT NULL
        );
    """)
    cur.execute("""
        SELECT create_hypertable(
            'test_energy_freshness',
            'event_time',
            chunk_time_interval => INTERVAL '1 day'
        );
    """)

    cur.execute("""
        CREATE MATERIALIZED VIEW cagg_test_hourly_freshness
        WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
        SELECT 
            time_bucket('1 hour', event_time) AS bucket,
            sum(energy_kwh) AS total_energy,
            count(*) AS row_count
        FROM test_energy_freshness
        GROUP BY bucket
        WITH NO DATA;
    """)
    print("Created test_energy_freshness table and cagg_test_hourly_freshness (materialized_only = true).")

    # 2. Record timeline parameters
    now_cat = datetime.datetime.now(CAT)
    # H is start of most recent completed CAT hour
    H = now_cat.replace(minute=0, second=0, microsecond=0)
    H_minus_48 = H - datetime.timedelta(hours=48)

    t_1kwh = H_minus_48 + datetime.timedelta(minutes=10)
    t_05kwh = H_minus_48 + datetime.timedelta(minutes=25)
    t_2kwh = H + datetime.timedelta(minutes=10)

    print(f"\nActual CAT test time:           {now_cat.isoformat()}")
    print(f"H (Most recent completed hour): {H.isoformat()}")
    print(f"Older hour (H minus 48 hours):  {H_minus_48.isoformat()}")
    print(f"Insert 1: {t_1kwh.isoformat()} (1.0 kWh)")
    print(f"Insert 2: {t_05kwh.isoformat()} (0.5 kWh)")
    print(f"Insert 3: {t_2kwh.isoformat()} (2.0 kWh)")

    timeline_results = []

    # Step A: Insert 1 kWh into older hour & refresh only older hour
    print("\n--- Stage 1: Insert 1.0 kWh into older hour, refresh older hour, real-time OFF ---")
    cur.execute("INSERT INTO test_energy_freshness VALUES (%s, %s);", (t_1kwh, 1.0))
    # Refresh older hour
    next_older = H_minus_48 + datetime.timedelta(hours=1)
    cur.execute(f"CALL refresh_continuous_aggregate('cagg_test_hourly_freshness', '{H_minus_48.isoformat()}', '{next_older.isoformat()}');")

    def query_stage(stage_name):
        cur.execute(f"""
            SELECT bucket, total_energy, row_count 
            FROM cagg_test_hourly_freshness 
            WHERE bucket IN ('{H_minus_48.isoformat()}', '{H.isoformat()}')
            ORDER BY bucket;
        """)
        rows = {r[0].isoformat(): (float(r[1]), int(r[2])) for r in cur.fetchall()}
        older_res = rows.get(H_minus_48.isoformat(), ("No bucket", 0))
        h_res = rows.get(H.isoformat(), ("No bucket", 0))
        timeline_results.append({
            "stage": stage_name,
            "older_hour": f"{older_res[0]} kWh, {older_res[1]} rows",
            "hour_H": f"{h_res[0]} kWh, {h_res[1]} rows" if h_res[0] != "No bucket" else "No bucket returned"
        })
        print(f"  [{stage_name}]")
        print(f"    Older hour ({H_minus_48.strftime('%d %b %H:%M')}): {older_res[0]} kWh, {older_res[1]} rows")
        print(f"    Hour H     ({H.strftime('%d %b %H:%M')}): {h_res[0]} kWh, {h_res[1]} rows")

    query_stage("Stage 1: Initial older-hour refresh, real-time off")

    # Step B: Insert 0.5 kWh into older hour and 2.0 kWh into hour H (BEFORE refresh, real-time off)
    print("\n--- Stage 2: Insert 0.5 kWh into older hour & 2.0 kWh into hour H, BEFORE refresh, real-time OFF ---")
    cur.execute("INSERT INTO test_energy_freshness VALUES (%s, %s);", (t_05kwh, 0.5))
    cur.execute("INSERT INTO test_energy_freshness VALUES (%s, %s);", (t_2kwh, 2.0))
    query_stage("Stage 2: After both new inserts, before refresh, real-time off")

    # Step C: Enable real-time aggregation (still before refresh)
    print("\n--- Stage 3: Enable real-time aggregation, STILL BEFORE refresh ---")
    cur.execute("ALTER MATERIALIZED VIEW cagg_test_hourly_freshness SET (timescaledb.materialized_only = false);")
    query_stage("Stage 3: Real-time on, still before refresh")

    # Step D: Manually refresh both complete hours
    print("\n--- Stage 4: Manually refresh both complete hours ---")
    next_H = H + datetime.timedelta(hours=1)
    cur.execute(f"CALL refresh_continuous_aggregate('cagg_test_hourly_freshness', '{H_minus_48.isoformat()}', '{next_older.isoformat()}');")
    cur.execute(f"CALL refresh_continuous_aggregate('cagg_test_hourly_freshness', '{H.isoformat()}', '{next_H.isoformat()}');")
    query_stage("Stage 4: After manual refresh of both hours")

    print("\n" + "=" * 90)
    print("SECTION 6.2 WORKED TIMELINE VERIFICATION TABLE")
    print("=" * 90)
    print(f"{'Stage':<50} | {'Older Hour Aggregate':<20} | {'Hour H Aggregate'}")
    print("-" * 90)
    for r in timeline_results:
        print(f"{r['stage']:<50} | {r['older_hour']:<20} | {r['hour_H']}")
    print("-" * 90)

    # Save results to json
    results_s6 = {
        "cagg_refreshes": refresh_stats,
        "timeline_test": {
            "test_time_cat": now_cat.isoformat(),
            "H": H.isoformat(),
            "H_minus_48": H_minus_48.isoformat(),
            "stages": timeline_results
        }
    }
    with open("continuous_aggregates_results.json", "w") as f:
        json.dump(results_s6, f, indent=2)

    print("\nSection 6 completed successfully! Results saved to 'continuous_aggregates_results.json'.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    run_section_6()
