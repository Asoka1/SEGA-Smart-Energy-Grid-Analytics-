"""Capture and load the complete historical stream (Section 2.1).

Uses the CSV bulk loading route:
1. Streams 10,696,848 emitted dictionaries from iter_readings() into readings.csv.
2. Measures generation time.
3. Loads readings.csv into energy_readings using PostgreSQL COPY.
4. Measures load time and total time.
5. Verifies counts and storage statistics.
"""

import os
import time
import psycopg2
import smart_meter_simulator

CSV_FILENAME = "readings.csv"

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"


def main():
    info = smart_meter_simulator.dataset_info()
    expected = info["expected_counts"]
    expected_emitted = expected["emitted_readings"]
    print("=== Section 2.1: Complete Stream Capture and Ingestion ===")
    print(f"Planned readings:   {info['planned_readings']:,}")
    print(f"Expected emitted:   {expected_emitted:,}")
    print(f"Expected unique:    {expected['unique_readings']:,}")
    print(f"Expected duplicates:{expected['duplicate_readings']:,}")
    print(f"Expected delayed:   {expected['delayed_unique_readings']:,}")
    print(f"Expected missing:   {expected['missing_readings']:,}\n")

    overall_start = time.time()

    # Step 1: Generate CSV
    print(f"--- Step 1: Generating CSV file '{CSV_FILENAME}' ---")
    gen_start = time.time()
    consumed_count = 0

    with open(CSV_FILENAME, "w", buffering=8 * 1024 * 1024) as f:
        for r in smart_meter_simulator.iter_readings():
            consumed_count += 1
            f.write(
                f"{r['meter_id']},{r['event_time']},{r['received_time']},"
                f"{r['power_kw']},{r['voltage_v']},{r['current_a']},"
                f"{r['frequency_hz']},{r['energy_kwh']}\n"
            )
            if consumed_count % 1000000 == 0:
                print(f"  Consumed {consumed_count:,} records ({time.time() - gen_start:.1f}s)...")

    gen_elapsed = time.time() - gen_start
    csv_file_size_mb = os.path.getsize(CSV_FILENAME) / (1024 * 1024)
    print(f"CSV Generation finished:")
    print(f"  Dictionaries consumed: {consumed_count:,}")
    print(f"  CSV file size:         {csv_file_size_mb:.2f} MB")
    print(f"  Generation elapsed:    {gen_elapsed:.2f} seconds\n")

    # Step 2: Connect to DB and Truncate energy_readings
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("TRUNCATE TABLE energy_readings;")
    print("Table 'energy_readings' truncated.")

    # Step 3: Load CSV into energy_readings using COPY
    print(f"--- Step 2: Loading CSV into 'energy_readings' hypertable ---")
    load_start = time.time()

    with open(CSV_FILENAME, "r", buffering=8 * 1024 * 1024) as f:
        copy_sql = """
            COPY energy_readings (
                meter_id, event_time, received_time, power_kw,
                voltage_v, current_a, frequency_hz, energy_kwh
            ) FROM STDIN WITH (FORMAT csv);
        """
        cur.copy_expert(copy_sql, f)

    load_elapsed = time.time() - load_start
    total_elapsed = time.time() - overall_start

    print(f"Database loading finished:")
    print(f"  Database load elapsed: {load_elapsed:.2f} seconds")
    print(f"  Total elapsed time:    {total_elapsed:.2f} seconds\n")

    # Step 4: Verification and Queries
    print("--- Step 3: Verification & Diagnostics ---")
    cur.execute("SELECT count(*) FROM energy_readings;")
    committed_count = cur.fetchone()[0]

    cur.execute("SELECT count(DISTINCT meter_id) FROM energy_readings;")
    distinct_meters = cur.fetchone()[0]

    cur.execute("SELECT min(event_time), max(event_time) FROM energy_readings;")
    min_event, max_event = cur.fetchone()

    # Query hypertable chunk count and sizes
    cur.execute("""
        SELECT count(*) FROM timescaledb_information.chunks
        WHERE hypertable_name = 'energy_readings';
    """)
    chunk_count = cur.fetchone()[0]

    cur.execute("""
        SELECT 
            pg_size_pretty(hypertable_size('energy_readings')) as total_size,
            pg_size_pretty(hypertable_size('energy_readings') - hypertable_index_size('energy_readings')) as table_data_size,
            pg_size_pretty(hypertable_index_size('energy_readings')) as index_size,
            hypertable_size('energy_readings') as total_bytes,
            hypertable_index_size('energy_readings') as index_bytes
        FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'energy_readings';
    """)
    total_sz, table_sz, idx_sz, total_bytes, idx_bytes = cur.fetchone()

    print(f"Dictionaries consumed:           {consumed_count:,}")
    print(f"CSV data row count:              {consumed_count:,}")
    print(f"Committed rows in energy_readings: {committed_count:,}")
    print(f"Expected emitted readings:       {expected_emitted:,}")
    agree = (consumed_count == committed_count == expected_emitted)
    print(f"All three agree?                 {agree}")
    print(f"Distinct meters:                 {distinct_meters}")
    print(f"Earliest event_time:             {min_event}")
    print(f"Latest event_time:               {max_event}")
    print(f"Hypertable chunks created:       {chunk_count}")
    print(f"Table Data Size:                 {table_sz}")
    print(f"Index Size:                      {idx_sz}")
    print(f"Total Size:                      {total_sz}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
