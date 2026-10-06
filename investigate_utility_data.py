"""Investigate Utility Data (Section 5).

5.1: Demand patterns:
  - Total network energy per CAT day and totals for the four 7-day periods (1-7, 8-14, 15-21, 22-28 Sep)
  - Top 3 highest 15-minute network peak demand intervals with event_time, total kW, and top region
  - Weekday vs weekend hourly load profile comparison
5.2: Regional demand:
  - Rank 10 regions by total energy consumption (kWh)
  - Customer type breakdown (residential, commercial, industrial) per region (kWh and %)
5.3: Reporting quality:
  - Completeness for whole network and per region
  - 21 September cutoff comparison (received_time <= 2026-09-22 00:00 vs all arrivals)
5.4: Abnormal consumption investigation:
  - Anomaly detection rule based on deviation from customer baseline / moving average
  - Compare against the 40 ground truth events from smart_meter_simulator.anomaly_events()
  - Evaluate detected events, missed events, false flags, false flag %, and % of meters affected.
"""

import json
import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import smart_meter_simulator

DB_HOST = "localhost"
DB_PORT = 5432
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "smartgrid"


def run_investigation():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    cur = conn.cursor()

    print("==========================================================================")
    print("SECTION 5.1: DEMAND PATTERNS")
    print("==========================================================================")

    # 1. Daily energy totals
    cur.execute("""
        SELECT 
            date_trunc('day', event_time)::date AS day,
            sum(energy_kwh) AS total_energy_kwh
        FROM energy_readings
        GROUP BY day
        ORDER BY day;
    """)
    daily_rows = cur.fetchall()
    df_daily = pd.DataFrame(daily_rows, columns=["day", "total_energy_kwh"])
    print("\nDaily Network Energy Consumption (first 5 and last 5 days):")
    print(df_daily.head().to_string(index=False))
    print("...")
    print(df_daily.tail().to_string(index=False))

    # Four 7-day periods: 1-7, 8-14, 15-21, 22-28 Sep
    periods = [
        ("Period 1 (1 - 7 Sep)", "2026-09-01 00:00:00+02", "2026-09-08 00:00:00+02"),
        ("Period 2 (8 - 14 Sep)", "2026-09-08 00:00:00+02", "2026-09-15 00:00:00+02"),
        ("Period 3 (15 - 21 Sep)", "2026-09-15 00:00:00+02", "2026-09-22 00:00:00+02"),
        ("Period 4 (22 - 28 Sep)", "2026-09-22 00:00:00+02", "2026-09-29 00:00:00+02"),
    ]
    print("\nFour 7-Day Period Energy Totals:")
    period_totals = []
    for label, start_t, end_t in periods:
        cur.execute(f"""
            SELECT sum(energy_kwh)
            FROM energy_readings
            WHERE event_time >= '{start_t}' AND event_time < '{end_t}';
        """)
        tot = cur.fetchone()[0]
        period_totals.append((label, tot))
        print(f"  {label:<24}: {tot:14.2f} kWh")

    # 2. Top 3 highest 15-minute network peak demand intervals
    cur.execute("""
        SELECT event_time, sum(power_kw) AS network_power_kw
        FROM energy_readings
        GROUP BY event_time
        ORDER BY network_power_kw DESC
        LIMIT 3;
    """)
    top_3_peaks = cur.fetchall()

    print("\nTop 3 Highest 15-Minute Network Peak Demand Intervals:")
    print(f"{'Rank':<5} | {'Event Time (CAT)':<25} | {'Total Power (kW)':<18} | {'Top Region':<12} | {'Regional Power (kW)'}")
    print("-" * 80)
    for idx, (p_time, p_kw) in enumerate(top_3_peaks, 1):
        # find top region during this interval
        cur.execute(f"""
            SELECT m.region, sum(r.power_kw) as reg_power
            FROM energy_readings r
            JOIN meters m ON r.meter_id = m.meter_id
            WHERE r.event_time = '{p_time}'
            GROUP BY m.region
            ORDER BY reg_power DESC
            LIMIT 1;
        """)
        top_reg, top_reg_kw = cur.fetchone()
        print(f"{idx:<5} | {str(p_time):<25} | {p_kw:18.2f} | {top_reg:<12} | {top_reg_kw:18.2f}")

    # 3. Weekday vs Weekend Hourly Profiles
    cur.execute("""
        SELECT 
            EXTRACT(HOUR FROM event_time) AS hour,
            CASE WHEN EXTRACT(ISODOW FROM event_time) IN (6, 7) THEN 'Weekend' ELSE 'Weekday' END AS day_type,
            avg(power_kw) AS avg_power_kw
        FROM energy_readings
        GROUP BY hour, day_type
        ORDER BY hour, day_type;
    """)
    profile_rows = cur.fetchall()
    df_profile = pd.DataFrame(profile_rows, columns=["hour", "day_type", "avg_power_kw"])

    # Plot Daily Energy and Profiles
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Daily trend
    axes[0].plot(df_daily["day"], df_daily["total_energy_kwh"], marker="o", color="#1f77b4", linewidth=2)
    axes[0].set_title("Total Network Daily Energy Consumption (28 Days CAT)")
    axes[0].set_xlabel("Date")
    axes[0].set_ylabel("Energy (kWh)")
    axes[0].grid(True, linestyle="--", alpha=0.6)
    axes[0].tick_params(axis='x', rotation=45)

    # Weekday vs Weekend
    for dtype, color in [("Weekday", "#2ca02c"), ("Weekend", "#d62728")]:
        sub = df_profile[df_profile["day_type"] == dtype]
        axes[1].plot(sub["hour"], sub["avg_power_kw"], marker="s", label=dtype, color=color, linewidth=2)
    axes[1].set_title("Hourly Power Profile: Weekday vs Weekend")
    axes[1].set_xlabel("Hour of Day (CAT)")
    axes[1].set_ylabel("Average Meter Power (kW)")
    axes[1].legend()
    axes[1].grid(True, linestyle="--", alpha=0.6)

    plt.tight_layout()
    plt.savefig("demand_patterns.png", dpi=300)
    plt.close()
    print("Saved plot to 'demand_patterns.png'.")

    print("\n==========================================================================")
    print("SECTION 5.2: REGIONAL DEMAND & CUSTOMER BREAKDOWN")
    print("==========================================================================")

    # Regional ranking
    cur.execute("""
        SELECT 
            m.region,
            sum(r.energy_kwh) AS total_energy_kwh,
            count(DISTINCT r.meter_id) AS meters_count
        FROM energy_readings r
        JOIN meters m ON r.meter_id = m.meter_id
        GROUP BY m.region
        ORDER BY total_energy_kwh DESC;
    """)
    region_rank = cur.fetchall()
    print("\nRegional Ranking by Total Consumption (kWh):")
    print(f"{'Rank':<5} | {'Region':<12} | {'Total Energy (kWh)':<20} | {'Meters'}")
    print("-" * 50)
    for rank, (reg, eng, m_cnt) in enumerate(region_rank, 1):
        print(f"{rank:<5} | {reg:<12} | {eng:20.2f} | {m_cnt}")

    # Customer breakdown per region
    cur.execute("""
        SELECT 
            m.region,
            m.customer_type,
            sum(r.energy_kwh) AS cust_energy_kwh
        FROM energy_readings r
        JOIN meters m ON r.meter_id = m.meter_id
        GROUP BY m.region, m.customer_type
        ORDER BY m.region, cust_energy_kwh DESC;
    """)
    breakdown_rows = cur.fetchall()
    df_bd = pd.DataFrame(breakdown_rows, columns=["region", "customer_type", "energy_kwh"])

    # Calculate percentages
    reg_totals = df_bd.groupby("region")["energy_kwh"].transform("sum")
    df_bd["pct"] = 100.0 * df_bd["energy_kwh"] / reg_totals

    print("\nRegional Customer Type Breakdown:")
    print(f"{'Region':<12} | {'Customer Type':<15} | {'Energy (kWh)':<16} | {'Percentage'}")
    print("-" * 55)
    for _, row in df_bd.iterrows():
        print(f"{row['region']:<12} | {row['customer_type']:<15} | {row['energy_kwh']:16.2f} | {row['pct']:6.2f}%")

    print("\n==========================================================================")
    print("SECTION 5.3: REPORTING QUALITY")
    print("==========================================================================")

    # Completeness network-wide and per region
    PLANNED_GRID_PER_METER = 28 * 96  # 2,688
    PLANNED_NETWORK = 4000 * PLANNED_GRID_PER_METER  # 10,752,000

    cur.execute("SELECT count(*) FROM energy_readings;")
    obs_network = cur.fetchone()[0]
    network_comp = 100.0 * obs_network / PLANNED_NETWORK

    print(f"Network Completeness: {network_comp:.4f}% ({obs_network:,} / {PLANNED_NETWORK:,})")

    cur.execute("""
        SELECT 
            m.region,
            count(*) as observed,
            count(DISTINCT r.meter_id) as meter_count
        FROM energy_readings r
        JOIN meters m ON r.meter_id = m.meter_id
        GROUP BY m.region
        ORDER BY m.region;
    """)
    print("\nRegional Completeness:")
    print(f"{'Region':<12} | {'Observed':<12} | {'Planned':<12} | {'Completeness'}")
    print("-" * 50)
    for reg, obs, m_cnt in cur.fetchall():
        planned = m_cnt * PLANNED_GRID_PER_METER
        comp = 100.0 * obs / planned
        print(f"{reg:<12} | {obs:<12,d} | {planned:<12,d} | {comp:.4f}%")

    # 21 September cutoff comparison
    # Readings with event_time on 21 Sep:
    # 1. received_time <= 2026-09-22 00:00:00+02
    # 2. all arrivals
    cur.execute("""
        SELECT 
            sum(CASE WHEN received_time <= '2026-09-22 00:00:00+02' THEN energy_kwh ELSE 0 END) AS timely_energy,
            sum(energy_kwh) AS all_energy,
            count(CASE WHEN received_time <= '2026-09-22 00:00:00+02' THEN 1 END) AS timely_count,
            count(*) AS total_count
        FROM energy_readings
        WHERE event_time >= '2026-09-21 00:00:00+02' AND event_time < '2026-09-22 00:00:00+02';
    """)
    timely_eng, all_eng, timely_cnt, tot_cnt = cur.fetchone()
    diff_eng = all_eng - timely_eng
    diff_pct = 100.0 * diff_eng / all_eng

    print("\n21 September Cutoff Evaluation:")
    print(f"Timely received (<= 22 Sep 00:00 CAT): {timely_eng:12.4f} kWh ({timely_cnt:,} readings)")
    print(f"All arrivals (including delayed):     {all_eng:12.4f} kWh ({tot_cnt:,} readings)")
    print(f"Difference (Delayed portion):         {diff_eng:12.4f} kWh ({tot_cnt - timely_cnt:,} readings)")
    print(f"Difference as % of final total:       {diff_pct:.4f}%")

    print("\n==========================================================================")
    print("SECTION 5.4: ANOMALY DETECTION EVALUATION")
    print("==========================================================================")

    # Injected ground truth anomaly events
    injected_events = smart_meter_simulator.anomaly_events()
    print(f"Loaded {len(injected_events)} injected anomaly events from generator.")

    # Detection rule:
    # Demand spikes (spike = power > 2.2 * base_power * customer_shape)
    # Unusual drops  (drop = power < 0.15 * base_power * customer_shape)
    # Specifically, comparing power_kw against meter metadata base_power_kw:
    # Ratio = power_kw / base_power_kw
    # Normal ratio bounds:
    # Residential: base 0.3-1.4, shape: 0.22 - 1.8 -> ratio typically 0.2 to 2.5
    # Let's inspect ratio:
    cur.execute("""
        SELECT 
            r.meter_id,
            r.event_time,
            r.power_kw,
            m.base_power_kw,
            m.customer_type,
            (r.power_kw / m.base_power_kw) as ratio
        FROM energy_readings r
        JOIN meters m ON r.meter_id = m.meter_id
        WHERE (r.power_kw / m.base_power_kw) > 3.0 
           OR (r.power_kw / m.base_power_kw) < 0.05;
    """)
    flagged_rows = cur.fetchall()
    print(f"Total readings flagged by rule: {len(flagged_rows):,}")

    # Build evaluation:
    # Check each injected event: detected if at least one flag matches meter_id and start_time <= event_time < end_time
    detected_count = 0
    missed_events = []

    # Map flags by meter_id
    from collections import defaultdict
    flags_by_meter = defaultdict(list)
    for m_id, ev_time, p_kw, b_kw, c_type, rat in flagged_rows:
        flags_by_meter[m_id].append(ev_time)

    # Injected events intervals lookup
    injected_by_meter = defaultdict(list)
    for ev in injected_events:
        m_id = ev["meter_id"]
        start_t = pd.to_datetime(ev["start_time"])
        end_t = pd.to_datetime(ev["end_time"])
        injected_by_meter[m_id].append((start_t, end_t, ev["event_type"]))

        # Check detection
        detected = False
        if m_id in flags_by_meter:
            for f_time in flags_by_meter[m_id]:
                f_dt = pd.to_datetime(f_time)
                if start_t <= f_dt < end_t:
                    detected = True
                    break
        if detected:
            detected_count += 1
        else:
            missed_events.append(ev)

    # False flag calculation:
    # "A false flag is a flagged reading outside every injected interval for that meter."
    false_flags = 0
    meters_with_false_flags = set()

    for m_id, f_time, p_kw, b_kw, c_type, rat in flagged_rows:
        f_dt = pd.to_datetime(f_time)
        is_inside = False
        if m_id in injected_by_meter:
            for st, et, _ in injected_by_meter[m_id]:
                if st <= f_dt < et:
                    is_inside = True
                    break
        if not is_inside:
            false_flags += 1
            meters_with_false_flags.add(m_id)

    # Observed readings outside injected intervals:
    # 40 events * 12 intervals = 480 injected reading slots
    # total readings = 10,643,735
    readings_outside_injected = 10643735 - 480
    false_flag_pct = 100.0 * false_flags / readings_outside_injected
    pct_meters_affected = 100.0 * len(meters_with_false_flags) / 4000.0

    print("\n================== ANOMALY DETECTION EVALUATION ==================")
    print(f"Total Injected Events:         {len(injected_events)}")
    print(f"Detected Events:               {detected_count} / {len(injected_events)} ({100.0*detected_count/len(injected_events):.1f}%)")
    print(f"Missed Events:                 {len(missed_events)} / {len(injected_events)}")
    print(f"False Flags Count:             {false_flags}")
    print(f"False Flag Rate:               {false_flag_pct:.6f}%")
    print(f"Meters Affected by False Flags:{len(meters_with_false_flags)} ({pct_meters_affected:.2f}%)")

    # Save summary dictionary
    summary_s5 = {
        "demand_patterns": {
            "period_totals_kwh": dict(period_totals),
            "top_3_peaks": [
                {"rank": idx+1, "event_time": str(p[0]), "power_kw": float(p[1])}
                for idx, p in enumerate(top_3_peaks)
            ]
        },
        "regional_ranking": [
            {"region": r[0], "total_energy_kwh": float(r[1]), "meters": r[2]}
            for r in region_rank
        ],
        "reporting_quality": {
            "network_completeness_pct": network_comp,
            "sep21_timely_kwh": float(timely_eng),
            "sep21_all_kwh": float(all_eng),
            "sep21_diff_kwh": float(diff_eng),
            "sep21_diff_pct": float(diff_pct)
        },
        "anomaly_evaluation": {
            "total_injected": len(injected_events),
            "detected": detected_count,
            "missed": len(missed_events),
            "false_flags": false_flags,
            "false_flag_pct": false_flag_pct,
            "meters_affected_pct": pct_meters_affected
        }
    }

    with open("utility_investigation_results.json", "w") as f:
        json.dump(summary_s5, f, indent=2)

    print("\nSection 5 results saved to 'utility_investigation_results.json'.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    run_investigation()
