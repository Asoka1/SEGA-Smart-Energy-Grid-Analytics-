"""Initialize smartgrid database, create extension, tables, and load meter metadata."""

import time
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import smart_meter_simulator

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"


def get_connection(dbname="postgres", max_retries=15, delay=2):
    """Wait for postgres to become available and return connection."""
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                dbname=dbname
            )
            return conn
        except psycopg2.OperationalError as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)


def setup_database():
    # 1. Connect to default postgres database to create smartgrid db
    conn = get_connection("postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    # Check if database exists
    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'smartgrid';")
    exists = cur.fetchone()
    if not exists:
        cur.execute("CREATE DATABASE smartgrid;")
        print("Created database 'smartgrid'.")
    else:
        print("Database 'smartgrid' already exists.")

    # Set timezone for smartgrid database
    cur.execute("ALTER DATABASE smartgrid SET timezone TO 'Africa/Kigali';")
    print("Set timezone of 'smartgrid' database to Africa/Kigali.")
    cur.close()
    conn.close()

    # 2. Connect to smartgrid database
    conn = get_connection("smartgrid")
    conn.autocommit = True
    cur = conn.cursor()

    # Verify timezone
    cur.execute("SHOW timezone;")
    tz = cur.fetchone()[0]
    print(f"Current session timezone: {tz}")

    # Create TimescaleDB extension
    cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    print("Ensured extension 'timescaledb' is created.")

    # Check version
    cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';")
    extver = cur.fetchone()[0]
    print(f"TimescaleDB extension version: {extver}")

    # Drop existing tables if needed or create if not exists
    # 1. meters table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meters (
            meter_id BIGINT PRIMARY KEY,
            region TEXT NOT NULL,
            customer_type TEXT NOT NULL,
            base_power_kw DOUBLE PRECISION NOT NULL,
            power_factor DOUBLE PRECISION NOT NULL
        );
    """)
    print("Table 'meters' ensured.")

    # 2. energy_live table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS energy_live (
            meter_id BIGINT,
            event_time TIMESTAMPTZ,
            received_time TIMESTAMPTZ,
            power_kw DOUBLE PRECISION,
            voltage_v DOUBLE PRECISION,
            current_a DOUBLE PRECISION,
            frequency_hz DOUBLE PRECISION,
            energy_kwh DOUBLE PRECISION
        );
    """)
    print("Table 'energy_live' ensured.")

    # 3. energy_readings hypertable
    cur.execute("""
        CREATE TABLE IF NOT EXISTS energy_readings (
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

    # Convert to hypertable if not already converted
    cur.execute("""
        SELECT count(*) FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'energy_readings';
    """)
    is_hyper = cur.fetchone()[0]
    if is_hyper == 0:
        cur.execute("""
            SELECT create_hypertable(
                'energy_readings',
                'event_time',
                chunk_time_interval => INTERVAL '1 day'
            );
        """)
        print("Created hypertable 'energy_readings' with 1-day chunks.")
    else:
        print("'energy_readings' is already a hypertable.")

    # Insert meter metadata
    cur.execute("SELECT count(*) FROM meters;")
    count = cur.fetchone()[0]
    if count == 0:
        print("Loading meter metadata from smart_meter_simulator...")
        meta = smart_meter_simulator.meter_metadata()
        records = [
            (
                m["meter_id"],
                m["region"],
                m["customer_type"],
                m["base_power_kw"],
                m["power_factor"]
            )
            for m in meta
        ]
        from psycopg2.extras import execute_batch
        execute_batch(cur, """
            INSERT INTO meters (meter_id, region, customer_type, base_power_kw, power_factor)
            VALUES (%s, %s, %s, %s, %s);
        """, records)
        print(f"Inserted {len(records)} meters into 'meters' table.")
    else:
        print(f"'meters' table already contains {count} rows.")

    # Final verify
    cur.execute("SELECT count(*) FROM meters;")
    final_count = cur.fetchone()[0]
    print(f"Verified: 'meters' table has {final_count} rows.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    setup_database()
