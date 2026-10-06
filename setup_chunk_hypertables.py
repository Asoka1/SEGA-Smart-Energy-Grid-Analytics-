"""Create and verify three chunk configurations (Section 3.1).

1. Config 1: energy_readings (1 day chunk - already created and cleaned)
2. Config 2: energy_readings_3h (3 hours chunk)
3. Config 3: energy_readings_week (1 week chunk)

Copies cleaned 10,643,735 rows into both new hypertables, builds matching indexes,
updates planner statistics with ANALYZE, and performs cross-configuration verification.
"""

import time
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"


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

    print("=== Section 3.1: Create and Verify Three Chunk Configurations ===")
    overall_start = time.time()

    # Step 1: Ensure Indexes on energy_readings (1 day)
    print("\nEnsuring matching non-unique indexes on energy_readings (1-day)...")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_readings_1d_time 
        ON energy_readings (event_time);
        
        CREATE INDEX IF NOT EXISTS idx_readings_1d_meter_time 
        ON energy_readings (meter_id, event_time);
    """)

    # Step 2: Create energy_readings_3h
    print("\nSetting up energy_readings_3h (3 hours chunk)...")
    cur.execute("DROP TABLE IF EXISTS energy_readings_3h CASCADE;")
    cur.execute("""
        CREATE TABLE energy_readings_3h (
            meter_id BIGINT,
            event_time TIMESTAMPTZ NOT NULL,
            received_time TIMESTAMPTZ,
            power_kw DOUBLE PRECISION,
            voltage_v DOUBLE PRECISION,
            current_a DOUBLE PRECISION,
            frequency_hz DOUBLE PRECISION,
            energy_kwh DOUBLE PRECISION
        );
    """)
    cur.execute("""
        SELECT create_hypertable(
            'energy_readings_3h',
            'event_time',
            chunk_time_interval => INTERVAL '3 hours'
        );
    """)
    cur.execute("""
        CREATE INDEX idx_readings_3h_time ON energy_readings_3h (event_time);
        CREATE INDEX idx_readings_3h_meter_time ON energy_readings_3h (meter_id, event_time);
    """)

    print("Copying cleaned rows into energy_readings_3h...")
    t0 = time.time()
    cur.execute("INSERT INTO energy_readings_3h SELECT * FROM energy_readings;")
    print(f"energy_readings_3h loaded in {time.time() - t0:.2f} seconds.")

    # Step 3: Create energy_readings_week
    print("\nSetting up energy_readings_week (1 week chunk)...")
    cur.execute("DROP TABLE IF EXISTS energy_readings_week CASCADE;")
    cur.execute("""
        CREATE TABLE energy_readings_week (
            meter_id BIGINT,
            event_time TIMESTAMPTZ NOT NULL,
            received_time TIMESTAMPTZ,
            power_kw DOUBLE PRECISION,
            voltage_v DOUBLE PRECISION,
            current_a DOUBLE PRECISION,
            frequency_hz DOUBLE PRECISION,
            energy_kwh DOUBLE PRECISION
        );
    """)
    cur.execute("""
        SELECT create_hypertable(
            'energy_readings_week',
            'event_time',
            chunk_time_interval => INTERVAL '1 week'
        );
    """)
    cur.execute("""
        CREATE INDEX idx_readings_1w_time ON energy_readings_week (event_time);
        CREATE INDEX idx_readings_1w_meter_time ON energy_readings_week (meter_id, event_time);
    """)

    print("Copying cleaned rows into energy_readings_week...")
    t0 = time.time()
    cur.execute("INSERT INTO energy_readings_week SELECT * FROM energy_readings;")
    print(f"energy_readings_week loaded in {time.time() - t0:.2f} seconds.")

    # Step 4: Run ANALYZE on all three tables
    print("\nRunning ANALYZE on all three hypertables...")
    cur.execute("ANALYZE energy_readings;")
    cur.execute("ANALYZE energy_readings_3h;")
    cur.execute("ANALYZE energy_readings_week;")
    print("ANALYZE complete.")

    # Step 5: Verification Queries
    tables = [
        ("energy_readings_3h", "3 hours"),
        ("energy_readings", "1 day"),
        ("energy_readings_week", "1 week")
    ]

    print("\n" + "=" * 90)
    print("CHUNK CONFIGURATION AND DATA VERIFICATION TABLE")
    print("=" * 90)
    print(f"{'Table Name':<22} | {'Interval':<8} | {'Chunks':<6} | {'Row Count':<11} | {'Meters':<6} | {'Sum(power_kw)':<15} | {'Sum(energy_kwh)':<15}")
    print("-" * 90)

    results = {}
    for tbl, interval in tables:
        # Chunks count
        cur.execute(f"""
            SELECT count(*) FROM timescaledb_information.chunks
            WHERE hypertable_name = '{tbl}';
        """)
        chunks = cur.fetchone()[0]

        # Row count, meters, earliest, latest, sum power, sum energy
        cur.execute(f"""
            SELECT 
                count(*),
                count(DISTINCT meter_id),
                min(event_time),
                max(event_time),
                sum(power_kw),
                sum(energy_kwh)
            FROM {tbl};
        """)
        cnt, meters, min_t, max_t, sum_pow, sum_eng = cur.fetchone()
        results[tbl] = {
            "interval": interval,
            "chunks": chunks,
            "cnt": cnt,
            "meters": meters,
            "min_t": min_t,
            "max_t": max_t,
            "sum_pow": sum_pow,
            "sum_eng": sum_eng
        }
        print(f"{tbl:<22} | {interval:<8} | {chunks:<6} | {cnt:<11,d} | {meters:<6} | {sum_pow:<15.4f} | {sum_eng:<15.4f}")

    print("-" * 90)

    # Check differences against energy_readings
    base = results["energy_readings"]
    for tbl in ["energy_readings_3h", "energy_readings_week"]:
        target = results[tbl]
        pow_diff = abs(target["sum_pow"] - base["sum_pow"])
        eng_diff = abs(target["sum_eng"] - base["sum_eng"])
        print(f"\nComparing {tbl} vs energy_readings (1-day baseline):")
        print(f"  Rows match:        {target['cnt'] == base['cnt']} ({target['cnt']:,})")
        print(f"  Meters match:      {target['meters'] == base['meters']} ({target['meters']})")
        print(f"  Date range match:  {target['min_t'] == base['min_t']} and {target['max_t'] == base['max_t']}")
        print(f"  Power sum diff:    {pow_diff:.6f} (<= 0.001? {pow_diff <= 0.001})")
        print(f"  Energy sum diff:   {eng_diff:.6f} (<= 0.001? {eng_diff <= 0.001})")

    print(f"\nAll chunk configurations prepared and verified in {time.time() - overall_start:.2f} seconds.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
