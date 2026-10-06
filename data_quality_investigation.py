"""Data quality investigation (Section 2.2).

Performs diagnostic queries on energy_readings before cleaning:
- Counts planned, emitted, unique, duplicate, delayed, and missing readings
- Calculates quality metrics and percentages
- Identifies the top 5 meters with the most missing readings
"""

import psycopg2
import time

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"

PLANNED_READINGS = 10752000
EXPECTED_UNIQUE = 10643735
EXPECTED_DUPLICATES = 53113
EXPECTED_DELAYED = 213090
EXPECTED_MISSING = 108265
SLOTS_PER_METER = 28 * 96  # 2,688


def run_investigation():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = True
    cur = conn.cursor()

    print("=== Section 2.2: Data Quality Investigation ===")
    start_time = time.time()

    # 1. Emitted count
    cur.execute("SELECT count(*) FROM energy_readings;")
    emitted_count = cur.fetchone()[0]

    # 2. Duplicate extra copies and Unique readings count
    # Counting groups with cnt > 1
    print("Querying duplicate and unique pairs...")
    cur.execute("""
        SELECT 
            count(*) as unique_count,
            sum(cnt) as total_emitted,
            sum(cnt - 1) as duplicate_count
        FROM (
            SELECT meter_id, event_time, count(*) as cnt
            FROM energy_readings
            GROUP BY meter_id, event_time
        ) sub;
    """)
    unique_count, total_emitted, duplicate_count = cur.fetchone()

    # 3. Delayed unique readings (received_time > event_time + 15 min)
    print("Querying delayed unique readings...")
    cur.execute("""
        SELECT count(DISTINCT (meter_id, event_time))
        FROM energy_readings
        WHERE received_time > event_time + INTERVAL '15 minutes';
    """)
    delayed_unique_count = cur.fetchone()[0]

    unique_count = int(unique_count)
    total_emitted = int(total_emitted)
    duplicate_count = int(duplicate_count)
    delayed_unique_count = int(delayed_unique_count)
    missing_count = int(PLANNED_READINGS - unique_count)

    # 5. Calculations
    missing_pct = 100.0 * float(missing_count) / float(PLANNED_READINGS)
    duplicate_pct = 100.0 * float(duplicate_count) / float(unique_count)
    delayed_pct = 100.0 * float(delayed_unique_count) / float(unique_count)
    completeness_pct = 100.0 * float(unique_count) / float(PLANNED_READINGS)

    print("\n================== QUALITY METRICS TABLE ==================")
    print(f"{'Metric':<28} | {'Observed':<12} | {'Expected':<12} | {'Status'}")
    print("-" * 65)
    print(f"{'Planned readings':<28} | {PLANNED_READINGS:<12,d} | {PLANNED_READINGS:<12,d} | MATCH")
    print(f"{'Emitted readings':<28} | {emitted_count:<12,d} | {10696848:<12,d} | {'MATCH' if emitted_count == 10696848 else 'MISMATCH'}")
    print(f"{'Unique readings':<28} | {unique_count:<12,d} | {EXPECTED_UNIQUE:<12,d} | {'MATCH' if unique_count == EXPECTED_UNIQUE else 'MISMATCH'}")
    print(f"{'Duplicate readings':<28} | {duplicate_count:<12,d} | {EXPECTED_DUPLICATES:<12,d} | {'MATCH' if duplicate_count == EXPECTED_DUPLICATES else 'MISMATCH'}")
    print(f"{'Delayed unique readings':<28} | {delayed_unique_count:<12,d} | {EXPECTED_DELAYED:<12,d} | {'MATCH' if delayed_unique_count == EXPECTED_DELAYED else 'MISMATCH'}")
    print(f"{'Missing readings':<28} | {missing_count:<12,d} | {EXPECTED_MISSING:<12,d} | {'MATCH' if missing_count == EXPECTED_MISSING else 'MISMATCH'}")

    print("\n================== CALCULATED PERCENTAGES =================")
    print(f"Missing percentage:     {missing_pct:.4f}%")
    print(f"Duplicate percentage:   {duplicate_pct:.4f}%")
    print(f"Delayed percentage:     {delayed_pct:.4f}%")
    print(f"Completeness:           {completeness_pct:.4f}%")

    print("\n================== SANITY VERIFICATIONS ===================")
    print(f"Unique + Extra copies == Emitted? ({unique_count:,} + {duplicate_count:,} = {unique_count + duplicate_count:,}) -> {unique_count + duplicate_count == emitted_count}")
    print(f"Planned - Unique == Missing?     ({PLANNED_READINGS:,} - {unique_count:,} = {PLANNED_READINGS - unique_count:,}) -> {PLANNED_READINGS - unique_count == missing_count}")

    # Top 5 meters with the most missing readings
    print("\n=== Top 5 Meters with the Most Missing Readings ===")
    cur.execute("""
        SELECT meter_id, (%s - count(DISTINCT event_time)) as missing_slots
        FROM energy_readings
        GROUP BY meter_id
        ORDER BY missing_slots DESC
        LIMIT 5;
    """, (SLOTS_PER_METER,))
    top_missing = cur.fetchall()
    print(f"{'Meter ID':<15} | {'Missing Intervals':<18} | {'Missing Percentage'}")
    print("-" * 55)
    for m_id, m_missing in top_missing:
        m_pct = 100.0 * m_missing / SLOTS_PER_METER
        print(f"{m_id:<15} | {m_missing:<18} | {m_pct:.2f}%")

    elapsed = time.time() - start_time
    print(f"\nDiagnostic queries completed in {elapsed:.2f} seconds.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run_investigation()
