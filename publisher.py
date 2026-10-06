"""MQTT Publisher for pilot stream (Section 1.3).

Takes first 2,000 dictionaries from iter_readings(), converts each to JSON,
and publishes to energy/meters/{meter_id} at QoS 1.
"""

import json
import os
import time
import paho.mqtt.client as mqtt
import smart_meter_simulator

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TARGET_COUNT = 2000
READY_FLAG = "subscriber_ready.flag"


def run_publisher():
    print("Waiting for subscriber to be ready (SUBACK confirmed)...")
    wait_flag_start = time.time()
    while not os.path.exists(READY_FLAG) and (time.time() - wait_flag_start < 30):
        time.sleep(0.05)

    if not os.path.exists(READY_FLAG):
        print("Warning: Subscriber ready flag not seen within 30s. Proceeding anyway.")
    else:
        print("Subscriber is confirmed ready! Giving brief 0.2s pause.")
        time.sleep(0.2)

    generator_count = 0
    ack_count = 0

    def on_publish(client, userdata, mid, reason_code=None, properties=None):
        nonlocal ack_count
        ack_count += 1
        if ack_count % 500 == 0 or ack_count == TARGET_COUNT:
            print(f"[Publisher] Broker acknowledged {ack_count}/{TARGET_COUNT} messages.")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sega_pilot_publisher")
    client.on_publish = on_publish

    print(f"Connecting to MQTT broker at {BROKER_HOST}:{BROKER_PORT}...")
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()

    time.sleep(0.2)

    print(f"Reading first {TARGET_COUNT} dictionaries from generator and publishing...")
    start_time = time.time()

    for reading in smart_meter_simulator.iter_readings():
        generator_count += 1
        topic = f"energy/meters/{reading['meter_id']}"
        payload = json.dumps(reading)
        
        info = client.publish(topic, payload=payload, qos=1, retain=False)

        if generator_count >= TARGET_COUNT:
            break

    print(f"Generator finished yielding {generator_count} records. Waiting for broker acknowledgments...")

    wait_start = time.time()
    while ack_count < generator_count and (time.time() - wait_start < 30):
        time.sleep(0.05)

    elapsed = time.time() - start_time
    client.loop_stop()
    client.disconnect()

    print("\n--- Pilot Publisher Summary ---")
    print(f"Dictionaries taken from generator:    {generator_count}")
    print(f"Messages acknowledged by MQTT broker: {ack_count}")
    print(f"Total publishing elapsed time:        {elapsed:.2f} seconds")


if __name__ == "__main__":
    run_publisher()
