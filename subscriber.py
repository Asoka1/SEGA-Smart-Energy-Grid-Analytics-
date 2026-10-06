"""MQTT Subscriber for pilot stream (Section 1.3).

Subscribes to energy/meters/# at QoS 1 and inserts readings into energy_live.
Uses a separate worker thread for batch database insertions to ensure the
MQTT network loop never blocks.
"""

import json
import os
import queue
import threading
import time
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import execute_batch

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC = "energy/meters/#"
TARGET_COUNT = 2000
READY_FLAG = "subscriber_ready.flag"

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"


def run_subscriber():
    if os.path.exists(READY_FLAG):
        os.remove(READY_FLAG)

    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    conn.autocommit = False
    cur = conn.cursor()

    # Clear energy_live table: "Begin with an empty energy_live table"
    cur.execute("TRUNCATE TABLE energy_live;")
    conn.commit()
    print("Table 'energy_live' truncated. Ready for pilot.")

    received_count = 0
    msg_queue = queue.Queue()
    stop_worker = threading.Event()

    def db_worker():
        batch = []
        inserted_total = 0
        insert_query = """
            INSERT INTO energy_live (
                meter_id, event_time, received_time, power_kw,
                voltage_v, current_a, frequency_hz, energy_kwh
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """
        while not stop_worker.is_set() or not msg_queue.empty():
            try:
                record = msg_queue.get(timeout=0.1)
                batch.append(record)
                if len(batch) >= 100:
                    execute_batch(cur, insert_query, batch)
                    conn.commit()
                    inserted_total += len(batch)
                    batch.clear()
            except queue.Empty:
                if batch:
                    execute_batch(cur, insert_query, batch)
                    conn.commit()
                    inserted_total += len(batch)
                    batch.clear()

        if batch:
            execute_batch(cur, insert_query, batch)
            conn.commit()
            inserted_total += len(batch)
            batch.clear()

    worker_thread = threading.Thread(target=db_worker, daemon=True)
    worker_thread.start()

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"Connected to MQTT broker at {BROKER_HOST}:{BROKER_PORT}.")
            res, mid = client.subscribe(TOPIC, qos=1)
            print(f"Subscribing to {TOPIC} (QoS 1), mid={mid}...")
        else:
            print(f"Connection failed with code {rc}")

    def on_subscribe(client, userdata, mid, granted_qos, properties=None):
        print(f"Subscription confirmed (SUBACK) for {TOPIC} with QoS {granted_qos}. Ready for messages.")
        with open(READY_FLAG, "w") as f:
            f.write("ready")

    def on_message(client, userdata, msg):
        nonlocal received_count
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            record = (
                payload["meter_id"],
                payload["event_time"],
                payload["received_time"],
                payload["power_kw"],
                payload["voltage_v"],
                payload["current_a"],
                payload["frequency_hz"],
                payload["energy_kwh"]
            )
            msg_queue.put(record)
            received_count += 1

            if received_count % 500 == 0 or received_count == TARGET_COUNT:
                print(f"[Subscriber] Received {received_count}/{TARGET_COUNT} messages.")
        except Exception as e:
            print(f"Error handling message: {e}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sega_pilot_subscriber")
    client.max_inflight_messages_set(2000)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()

    print(f"Subscriber waiting for {TARGET_COUNT} messages...")
    start_wait = time.time()
    while received_count < TARGET_COUNT and (time.time() - start_wait < 60):
        time.sleep(0.05)

    # Allow time for worker thread to flush all inserts to DB
    time.sleep(1.0)
    stop_worker.set()
    worker_thread.join()

    client.loop_stop()
    client.disconnect()

    if os.path.exists(READY_FLAG):
        os.remove(READY_FLAG)

    cur.execute("SELECT count(*) FROM energy_live;")
    db_rows = cur.fetchone()[0]

    print("\n--- Pilot Subscriber Summary ---")
    print(f"Total messages received by subscriber: {received_count}")
    print(f"Total rows committed in energy_live:   {db_rows}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run_subscriber()
