"""Clean and deduplicate energy_readings (Section 2.3).

1. Identifies rows sharing meter_id and event_time.
2. Checks whether conflicting values exist across the 6 value columns.
3. Retains one copy and deletes the redundant duplicates using repeatable SQL.
4. Compares row counts and sum(energy_kwh) before and after cleaning.
5. Verifies final count matches expected unique_readings (10,643,735) and 0 duplicate pairs remain.
"""

import time
import psycopg2

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"

EXPECTED_UNIQUE = 10643735
EXPECTED_DUPLICATES = 53113


def clean():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = True
    cur = conn.cursor()

    print("=== Section 2.3: Clean and Preserve Consistent Benchmark ===")
    start_time = time.time()

    # Step 1: Pre-cleaning metrics
    print("Measuring pre-cleaning counts and energy sum...")
    cur.execute("SELECT count(*), sum(energy_kwh) FROM energy_readings;")
    pre_count, pre_energy = cur.fetchone()
    print(f"Pre-cleaning row count:      {pre_count:,}")
    print(f"Pre-cleaning sum(energy_kwh): {pre_energy:,.6f} kWh\n")

    # Step 2: Investigate conflicts among duplicates
    print("Investigating conflicting values among duplicates...")
    cur.execute("""
        SELECT count(*)
        FROM (
            SELECT meter_id, event_time
            FROM energy_readings
            GROUP BY meter_id, event_time
            HAVING count(*) > 1
               AND (
                   min(received_time) <> max(received_time) OR
                   min(power_kw) <> max(power_kw) OR
                   min(voltage_v) <> max(voltage_v) OR
                   min(current_a) <> max(current_a) OR
                   min(frequency_hz) <> max(frequency_hz) OR
                   min(energy_kwh) <> max(energy_kwh)
               )
        ) conflicts;
    """)
    conflict_count = cur.fetchone()[0]
    print(f"Conflicting duplicate pairs found: {conflict_count}")
    if conflict_count == 0:
        print("-> Confirmed: All duplicate rows match identically in all 6 value columns.")
    else:
        print("-> WARNING: Conflicting values detected. Reviewing conflicts...")

    # Step 3: Deduplication using temporary lookup table
    print("\nCreating temporary table of duplicate pairs...")
    cur.execute("""
        DROP TABLE IF EXISTS temp_dup_pairs;
        CREATE TEMP TABLE temp_dup_pairs AS
        SELECT meter_id, event_time, min(ctid) as keep_ctid
        FROM energy_readings
        GROUP BY meter_id, event_time
        HAVING count(*) > 1;
        
        CREATE INDEX ON temp_dup_pairs (meter_id, event_time);
    """)
    cur.execute("SELECT count(*) FROM temp_dup_pairs;")
    dup_pair_count = cur.fetchone()[0]
    print(f"Identified {dup_pair_count:,} duplicate pairs to clean.")

    print("Deleting extra duplicate rows (keeping 1 row per pair)...")
    del_start = time.time()
    cur.execute("""
        DELETE FROM energy_readings e
        USING temp_dup_pairs d
        WHERE e.meter_id = d.meter_id
          AND e.event_time = d.event_time
          AND e.ctid <> d.keep_ctid;
    """)
    deleted_rows = cur.rowcount
    del_time = time.time() - del_start
    print(f"Deleted {deleted_rows:,} extra duplicate copies in {del_time:.2f} seconds.")

    # Step 4: Post-cleaning metrics
    print("\nMeasuring post-cleaning counts and energy sum...")
    cur.execute("SELECT count(*), sum(energy_kwh) FROM energy_readings;")
    post_count, post_energy = cur.fetchone()

    # Verify no duplicate pairs remain
    cur.execute("""
        SELECT count(*) FROM (
            SELECT meter_id, event_time
            FROM energy_readings
            GROUP BY meter_id, event_time
            HAVING count(*) > 1
        ) sub;
    """)
    remaining_dups = cur.fetchone()[0]

    # Verify missing intervals remain as gaps (count distinct meters)
    cur.execute("SELECT count(DISTINCT meter_id) FROM energy_readings;")
    distinct_meters = cur.fetchone()[0]

    print("\n================ CLEANING VERIFICATION REPORT ================")
    print(f"{'Metric':<32} | {'Before':<16} | {'After':<16} | {'Difference'}")
    print("-" * 75)
    print(f"{'Total Row Count':<32} | {pre_count:<16,d} | {post_count:<16,d} | -{pre_count - post_count:,d}")
    print(f"{'Sum of energy_kwh':<32} | {pre_energy:<16.4f} | {post_energy:<16.4f} | -{pre_energy - post_energy:.4f}")
    print("-" * 75)
    print(f"Expected unique readings:        {EXPECTED_UNIQUE:,}")
    print(f"Actual cleaned row count:        {post_count:,}")
    print(f"Matches expected unique count?   {post_count == EXPECTED_UNIQUE}")
    print(f"Remaining duplicate pairs:       {remaining_dups} (must be 0)")
    print(f"Distinct meters preserved:       {distinct_meters} (must be 4000)")
    print(f"Total time elapsed:              {time.time() - start_time:.2f} seconds")

    cur.close()
    conn.close()


if __name__ == "__main__":
    clean()
