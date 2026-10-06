# Smart Energy Grid Analytics (SEGA)
## Advanced Big Data Analytics — Assignment I Technical Report
**Institution:** Adventist University of Central Africa (AUCA)  
**Academic Period:** October 2026  
**Timezone:** Africa/Kigali (CAT, UTC+02:00)  
**Dataset Scale:** 4,000 Smart Meters | 28 Days (1–28 September 2026) | 10,643,735 Cleaned Readings  

---

## Executive Summary
This report presents the end-to-end engineering, database architecture, benchmark evaluations, and analytical modeling of the **Smart Energy Grid Analytics (SEGA)** platform. The platform ingests, processes, and analyzes high-frequency electrical telemetry from 4,000 smart meters across 10 geographical regions.

Key empirical findings:
1. **Pipeline Integrity:** The MQTT ingestion pipeline and CSV bulk loader processed 10,696,848 emitted dictionaries with zero packet loss, cleanly deduplicating 53,113 redundant readings to establish a gold-standard dataset of exactly **10,643,735 unique observations**.
2. **Chunk Interval Benchmark:** Testing 3-hour, 1-day, and 1-week TimescaleDB chunk sizes demonstrated that the **1-day chunk interval** provides the optimal production balance, excelling in whole-dataset aggregations (1,125.81 ms vs. 3,903.10 ms for 3-hour) and single-meter index lookups (6.56 ms).
3. **Compression Efficiency:** TimescaleDB columnar compression reduced storage footprint by **69.42%** (from 1,435.52 MB down to 438.96 MB, a **3.27x ratio**). Meter segmentation (`segmentby meter_id`) provided superior single-meter history lookups (6.16 ms) and full scans (348.40 ms).
4. **Continuous Aggregates & Freshness:** Materialized continuous aggregates reduced whole-dashboard rendering time from **1,445.17 ms to 40.00 ms** (a **36.1x speedup**), maintaining strict numerical equivalence (0.000000 kWh error).
5. **Day-Ahead Machine Learning Forecast:** A regularized Ridge Regression model achieved a test Mean Absolute Error of **4.52 kWh** across 1,680 regional test intervals, delivering an **89.43% improvement** over the 24-hour persistence baseline (42.71 kWh MAE).

---

## 1. Build the Ingestion Pipeline

### 1.1 Environment Confirmation & System Specifications
All services were orchestrated in a containerized environment to guarantee strict reproducibility.

#### Hardware & Host Environment
- **Processor:** Intel(R) Core(TM) i7-9750H CPU @ 2.60 GHz (6 Physical Cores, 12 Logical Processors)
- **Host RAM:** 16.0 GB DDR4
- **Host Storage:** Apple APFS Solid State Drive (NVMe SSD), 29 GB available space
- **Operating System:** macOS 26.2 (Darwin 25C56)
- **Container Engine:** Docker Engine v29.8.1 (Compose v2)
- **Python Environment:** Python 3.13.5 with `psycopg2`, `paho-mqtt`, `pandas`, `scikit-learn`, `matplotlib`

#### Software & Database Services
- **MQTT Broker:** EMQX Enterprise v6.3.1 (Image: `emqx/emqx:latest`), Port 1883 (TCP), Port 18083 (HTTP Dashboard)
- **Relational Database:** PostgreSQL 16.15 (Alpine Linux 15.2.0, x86_64-pc-linux-musl)
- **Time-Series Extension:** TimescaleDB v2.30.2
- **Configured Timezone:** `Africa/Kigali` (CAT, UTC+02:00)

#### Key PostgreSQL Runtime Settings
- `shared_buffers`: **1984MB**
- `work_mem`: **5290kB**
- `max_parallel_workers_per_gather`: **6**
- `jit`: **off**
- `max_connections`: **100**

> [!NOTE]
> Timezone configuration was set at the database level (`ALTER DATABASE smartgrid SET timezone TO 'Africa/Kigali';`). Aligning database sessions to `Africa/Kigali` ensures that daily aggregations split at CAT midnight rather than UTC midnight (which would distort daily cuts by 2 hours).

---

### 1.2 Database Structures & Data Dictionary
Three primary tables were constructed in the `smartgrid` database:
1. `meters`: Metadata for 4,000 meters.
2. `energy_live`: Regular landing table for the real-time MQTT pilot.
3. `energy_readings`: TimescaleDB hypertable partitioned by `event_time` with a 1-day chunk time interval.

#### Data Dictionary
| Table | Column Name | PostgreSQL Type | Unit / Range | Description |
|---|---|---|---|---|
| `meters` | `meter_id` | `BIGINT PRIMARY KEY` | 1000000000 – 1000003999 | Unique 10-digit smart meter identifier |
| `meters` | `region` | `TEXT` | Region 1 – Region 10 | Administrative distribution grid sector |
| `meters` | `customer_type` | `TEXT` | residential, commercial, industrial | Customer tariff classification |
| `meters` | `base_power_kw` | `DOUBLE PRECISION` | kW (0.3 – 15.0 kW) | Base contracted electrical load |
| `meters` | `power_factor` | `DOUBLE PRECISION` | Dimensionless (0.95) | Ratio of active power to apparent power |
| `energy_live` / `energy_readings` | `meter_id` | `BIGINT` | 10-digit integer | Foreign reference to `meters.meter_id` |
| `energy_live` / `energy_readings` | `event_time` | `TIMESTAMPTZ` | ISO 8601 (+02:00 CAT) | Start timestamp of the 15-minute interval |
| `energy_live` / `energy_readings` | `received_time` | `TIMESTAMPTZ` | ISO 8601 (+02:00 CAT) | Telemetry arrival time at collection headend |
| `energy_live` / `energy_readings` | `power_kw` | `DOUBLE PRECISION` | kW | Average active electrical power over interval |
| `energy_live` / `energy_readings` | `voltage_v` | `DOUBLE PRECISION` | Volts (V) | Root-mean-square electrical voltage |
| `energy_live` / `energy_readings` | `current_a` | `DOUBLE PRECISION` | Amperes (A) | Measured electrical current |
| `energy_live` / `energy_readings` | `frequency_hz` | `DOUBLE PRECISION` | Hertz (Hz) | AC grid operating frequency (~50.0 Hz) |
| `energy_live` / `energy_readings` | `energy_kwh` | `DOUBLE PRECISION` | Kilowatt-hours (kWh) | Energy consumed ($P \times 0.25\text{ h}$) |

**Metadata Verification:** Loading `meter_metadata()` confirmed exactly **4,000 rows** in `meters`.

---

### 1.3 Pilot Stream Capture and MQTT Publication
A pilot stream consisting of the first 2,000 dictionaries was consumed from `smart_meter_simulator.iter_readings()`, serialized to JSON, and published to `energy/meters/{meter_id}` at MQTT QoS 1.

#### Pipeline Flow Verification Table
| Ingestion Pipeline Stage | Measured Count | Target Count | Discrepancy |
|---|---|---|---|
| Dictionaries taken from generator | **2,000** | 2,000 | 0 |
| Messages acknowledged by EMQX broker (`PUBACK`) | **2,000** | 2,000 | 0 |
| Messages received by subscriber (`on_message`) | **2,000** | 2,000 | 0 |
| Rows committed to database (`energy_live`) | **2,000** | 2,000 | 0 |

#### Troubleshooting & Pipeline Optimization
During initial pilot execution, EMQX dropped messages due to subscriber message queue saturation (`dropped_msg_due_to_mqueue_is_full, max_len=1000`). This was resolved by:
1. Reconfiguring EMQX broker parameter `mqtt.max_mqueue_len = 100000`.
2. Decoupling MQTT packet intake from PostgreSQL batch writes using a thread-safe in-memory queue and background batch worker.
3. Adding a formal `SUBACK` handshake before publisher transmission.

---

## 2. Capture and Prepare the Historical Dataset

### 2.1 Complete Stream Ingestion
The full dataset encompasses 4,000 meters monitored over 28 days (00:00 1 September to 00:00 29 September 2026 CAT).

- **Planned Readings:** $4,000 \times 28 \times 96 =$ **10,752,000 readings**.
- **Ingestion Route:** High-performance CSV generation with PostgreSQL bulk `COPY`.
- **CSV Generation Duration:** 104.34 seconds (File Size: 1,097.89 MB).
- **Database Load Duration (`COPY`):** 111.80 seconds.
- **Total Ingestion Elapsed Time:** 216.22 seconds (3.6 minutes).

#### Ingestion Verification
- Dictionaries consumed from generator: **10,696,848**
- CSV data row count: **10,696,848**
- Committed rows in `energy_readings`: **10,696,848**
- Expected emitted count: **10,696,848** (Exact Match: **True**)
- Distinct meters loaded: **4,000**
- Earliest event timestamp: `2026-09-01 00:00:00+02:00`
- Latest event timestamp: `2026-09-28 23:45:00+02:00`
- Total Hypertable Size (pre-cleaning): **1,505.26 MB** (Data: 1,004.33 MB, Indexes: 500.92 MB).

---

### 2.2 Data Quality Investigation
Prior to cleaning, diagnostic queries evaluated data completeness, delays, and duplicates:

| Metric | Observed Count | Expected Count | Status | Percentage |
|---|---|---|---|---|
| **Planned Readings** | 10,752,000 | 10,752,000 | MATCH | 100.0000% |
| **Emitted Readings** | 10,696,848 | 10,696,848 | MATCH | 99.4871% |
| **Unique Readings** | 10,643,735 | 10,643,735 | MATCH | 98.9931% |
| **Duplicate Extra Copies** | 53,113 | 53,113 | MATCH | 0.4990% |
| **Delayed Unique Readings** | 213,090 | 213,090 | MATCH | 2.0020% |
| **Missing Readings** | 108,265 | 108,265 | MATCH | 1.0069% |

#### Sanity Checks
- $\text{Unique} + \text{Duplicates} = 10,643,735 + 53,113 = 10,696,848 = \text{Emitted}$ (**Verified**)
- $\text{Planned} - \text{Unique} = 10,752,000 - 10,643,735 = 108,265 = \text{Missing}$ (**Verified**)

#### Top 5 Meters with Highest Missing Readings
| Meter ID | Region | Missing Intervals | Total Expected Intervals | Missing Percentage |
|---|---|---|---|---|
| `1000003622` | Region 3 | 47 | 2,688 | 1.75% |
| `1000000366` | Region 7 | 45 | 2,688 | 1.67% |
| `1000000106` | Region 7 | 45 | 2,688 | 1.67% |
| `1000001259` | Region 10 | 44 | 2,688 | 1.64% |
| `1000000295` | Region 6 | 43 | 2,688 | 1.60% |

---

### 2.3 Data Cleaning & Benchmark Preservation
1. **Conflict Inspection:** Confirmatory SQL checked whether duplicate `(meter_id, event_time)` records held conflicting electrical values. Exactly **0 conflicting records** were found; all duplicates were bitwise identical across all 6 measurements.
2. **Deduplication Execution:** Extra duplicate copies were purged using a temporary indexed lookup table retaining `min(ctid)`. In 20.18 seconds, exactly 53,113 duplicate rows were removed.
3. **Missing Value Handling:** Missing telemetry intervals were preserved as chronological gaps rather than filled with zeros. Imputing zero consumption would distort downstream baseline models, baseline load factors, and load forecasts.

#### Cleaning Verification Summary
| Metric | Pre-Cleaning Value | Post-Cleaning Value | Net Change |
|---|---|---|---|
| **Total Rows** | 10,696,848 | **10,643,735** | -53,113 |
| **Sum of Energy (kWh)** | 3,872,863.8932 | **3,853,704.3468** | -19,159.5464 kWh |
| **Remaining Duplicates** | 53,113 | **0** | Clean |
| **Preserved Meters** | 4,000 | **4,000** | Preserved |

---

## 3. Compare Chunk Intervals

### 3.1 Verification of Three Hypertable Configurations
Three hypertable chunk time intervals were created, loaded with the cleaned 10,643,735 rows, indexed, and analyzed:
1. `energy_readings_3h`: 3-hour chunk interval (225 chunks)
2. `energy_readings`: 1-day chunk interval (29 chunks)
3. `energy_readings_week`: 1-week chunk interval (5 chunks)

#### Configuration Verification Table
| Hypertable | Chunk Interval | Chunk Count | Total Rows | Meters | Sum Power (kW) | Sum Energy (kWh) |
|---|---|---|---|---|---|---|
| `energy_readings_3h` | 3 hours | 225 | 10,643,735 | 4,000 | 15,414,817.3878 | 3,853,704.3468 |
| `energy_readings` | 1 day | 29 | 10,643,735 | 4,000 | 15,414,817.3878 | 3,853,704.3468 |
| `energy_readings_week`| 1 week | 5 | 10,643,735 | 4,000 | 15,414,817.3878 | 3,853,704.3468 |

*Numeric consistency: Absolute differences across row count, power sum, and energy sum were 0.000000 ($< 0.001$).*

---

### 3.2 Workload Benchmarks & Results
Five representative queries were executed across all three hypertables (1 warm-up + 5 measured runs per configuration):
- **Q1:** Hourly average power across all meters on 21 September (24 rows).
- **Q2:** Top ten 15-minute peak power intervals from 22 to 28 September (10 rows).
- **Q3:** Total energy per meter across full 28-day historical period (4,000 rows).
- **Q4:** 7-day statistics (energy sum, avg power, peak power) for single meter `1000000000` (1 row).
- **Q5:** Full-table scan reporting row count, energy sum, and power stats (1 row).

#### Measured Query Durations (Median Execution Time in ms)
| Query | Workload Profile | 3-Hour Chunks | 1-Day Chunks (Baseline) | 1-Week Chunks | Fastest Chunk Configuration |
|---|---|---|---|---|---|
| **Q1** | 1-Day Network Avg Power | **70.57 ms** | 102.86 ms | 112.76 ms | **3-Hour** (Chunk exclusion) |
| **Q2** | 7-Day Top 10 Peaks | **236.87 ms** | 256.81 ms | 332.20 ms | **3-Hour** |
| **Q3** | Full Period Meter Aggregates | 3,903.10 ms | **1,125.81 ms** | 1,681.88 ms | **1-Day** (3.5x faster than 3h) |
| **Q4** | 1 Meter 7-Day History | 13.46 ms | 6.56 ms | **6.30 ms** | **1-Week / 1-Day** |
| **Q5** | Full Table Scan (10.6M rows) | 965.97 ms | 890.09 ms | **805.19 ms** | **1-Week** |

### 3.3 Explanation of Chunk Interval Results
1. **Short Time-Window Filtering (Q1 & Q2):** The 3-hour hypertable achieved the highest speed because TimescaleDB's chunk exclusion eliminated 217 out of 225 chunks for Q1, opening only 8 sub-tables. The 1-week table had to scan a 7-day chunk to service a 1-day query.
2. **Whole-Dataset Aggregation (Q3):** The 1-day table outperformed the 3-hour table by **3.5x** (1,125.81 ms vs. 3,903.10 ms). With 225 chunks, PostgreSQL incurs severe query planner overhead, appending and hashing 225 separate chunk plans.
3. **Single Meter Lookups (Q4):** The index lookup on `(meter_id, event_time)` is fastest on 1-week and 1-day chunks because the query engine only inspects 1 or 7 B-tree indexes, compared to 56 distinct chunk indexes for the 3-hour configuration.

**Conclusion:** The **1-day chunk interval** is chosen as the foundational storage strategy.

---

## 4. Compare Compression Strategies

### 4.1 Implementation of Compressed Configurations
Two compressed hypertable variants were evaluated against the 1-day uncompressed baseline:
1. `energy_readings_unsegmented`: `compress_orderby = 'event_time DESC'`
2. `energy_readings_segmented`: `compress_segmentby = 'meter_id'`, `compress_orderby = 'event_time DESC'`

All 29 chunks were compressed.

---

### 4.2 Storage and Compression Cost
| Configuration | Total Storage (MB) | Data Size (MB) | Index Size (MB) | Compression Ratio | Space Saved (%) | Compression Time |
|---|---|---|---|---|---|---|
| **Baseline (Uncompressed)** | 1,435.52 MB | 957.80 MB | 477.72 MB | 1.00x | 0.00% | 0.0 s |
| **Unsegmented** | **438.96 MB** | 14.97 MB | 1.16 MB | **3.27x** | **69.42%** | **24.65 s** |
| **Segmented (`meter_id`)** | **501.88 MB** | 26.78 MB | 5.69 MB | **2.86x** | **65.04%** | **89.16 s** |

- **Chunk Compression Completeness:** 29 out of 29 chunks (100.0%) compressed; uncompressed rows = 0.
- Unsegmented compression achieved higher density (69.42% space reduction) because columnar compression algorithms (Gorilla for floats, Delta-of-delta for timestamps) operate more efficiently on wider, unpartitioned arrays.

---

### 4.3 Query Performance on Compressed Hypertables
#### Comparison Table (Median Duration & % Improvement vs. Uncompressed Baseline)
| Query | Workload | Baseline (1-Day) | Unsegmented | Improvement (%) | Segmented (`meter_id`) | Improvement (%) |
|---|---|---|---|---|---|---|
| **Q1** | 1-Day Network Avg | 102.86 ms | 109.57 ms | -6.52% | 780.56 ms | -658.86% |
| **Q2** | 7-Day Peak Intervals | 256.81 ms | 210.47 ms | +18.04% | **109.66 ms** | **+57.30%** |
| **Q3** | Full Period Meter Totals | 1,125.81 ms | 378.33 ms | +66.39% | **231.29 ms** | **+79.46%** |
| **Q4** | Single Meter 7-Day Stats | 6.56 ms | 66.32 ms | -910.98% | **6.16 ms** | **+6.10%** |
| **Q5** | Full Scan Aggregation | 890.09 ms | 440.36 ms | +50.53% | **348.40 ms** | **+60.86%** |

### 4.4 Tradeoffs and Architectural Selection
1. **Single-Meter Workloads (Q4):** Segmenting by `meter_id` allows TimescaleDB to decompress only the segments matching that specific meter ID. In contrast, the unsegmented table suffered a **910% slowdown** (66.32 ms) because it had to scan and decompress full chunk column arrays.
2. **Meter Aggregations (Q3) and Scans (Q5):** Segmented compression won by substantial margins (+79.46% and +60.86% faster than baseline), reading significantly fewer I/O pages off disk.
3. **Time-Slice Penalty (Q1):** Because segmented data groups by meter ID, executing a single 1-hour average across all 4,000 meters requires decoding across all 4,000 segments.

**Selected Configuration:** **1-Day Chunk Interval with Segmented Compression (`meter_id`)**. The storage savings of 65.04% coupled with unmatched performance for meter queries (Q4: 6.16 ms) and billing aggregations (Q3: 231.29 ms) heavily outweighs the 89-second one-time compression cost.

---

## 5. Investigate Utility Data

### 5.1 Demand Patterns & Peak Load
- **Network Energy Consumption Trend:**
  - Week 1 (1–7 Sep): **934,199.06 kWh**
  - Week 2 (8–14 Sep): **953,714.97 kWh** (+2.09%)
  - Week 3 (15–21 Sep): **973,230.03 kWh** (+2.05%)
  - Week 4 (22–28 Sep): **992,560.29 kWh** (+1.99%)
  - Total network consumption expanded steadily by ~0.3% per day over the 28 days.
- **Top 3 15-Minute Peak Load Intervals:**
  1. `2026-09-28 13:00:00+02`: **7,993.69 kW** (Top Region: Region 8 with 1,018.82 kW)
  2. `2026-09-28 12:45:00+02`: **7,943.95 kW** (Top Region: Region 8 with 1,022.15 kW)
  3. `2026-09-28 13:15:00+02`: **7,943.64 kW** (Top Region: Region 8 with 1,011.69 kW)
- **Weekday vs. Weekend Load Profiles:**
  - Weekdays exhibit a strong bimodal profile: a morning peak (~07:30–08:30 CAT) and a sustained industrial/business plateau (~12:00–14:00 CAT), followed by an evening residential peak (~19:00 CAT).
  - Weekends show lower overall consumption with commercial activity dropping by 45%.

---

### 5.2 Regional Demand & Customer Breakdown
#### Regional Ranking by Total Consumption
| Rank | Region | Meters | Total Energy (kWh) | Dominant Customer Class | Dominant Share (%) |
|---|---|---|---|---|---|
| **1** | **Region 8** | 400 | **503,475.83** | Industrial | 67.24% |
| **2** | **Region 5** | 400 | **411,040.39** | Industrial | 62.08% |
| **3** | **Region 9** | 400 | **400,467.20** | Industrial | 60.46% |
| **4** | **Region 3** | 400 | **392,376.59** | Industrial | 60.27% |
| **5** | **Region 1** | 400 | **383,539.81** | Industrial | 55.24% |
| **6** | **Region 2** | 400 | **378,448.89** | Industrial | 56.33% |
| **7** | **Region 7** | 400 | **362,288.54** | Industrial | 54.51% |
| **8** | **Region 10** | 400 | **351,149.78** | Industrial | 52.34% |
| **9** | **Region 6** | 400 | **346,512.84** | Industrial | 51.26% |
| **10** | **Region 4** | 400 | **324,404.48** | Industrial | 50.69% |

*Across all 10 regions, industrial consumers account for over 50% to 67% of electrical demand, commercial accounts for 18% to 25%, and residential accounts for 14% to 27%.*

---

### 5.3 Reporting Quality & Telemetry Arrival Delays
- **Overall Network Completeness:** **98.9931%** (10,643,735 unique / 10,752,000 planned).
- **Regional Completeness:** Consistent across all regions, ranging between 98.9693% (Region 6) and 99.0129% (Region 8).
- **Delayed Telemetry Impact on 21 September Billing Cutoff:**
  - Timely received (arrival $\le$ 22 Sep 00:00 CAT): **149,400.54 kWh** (379,505 readings)
  - All arrivals (including delayed telemetry): **149,552.72 kWh** (380,036 readings)
  - Delayed volume: **152.18 kWh** (531 readings) = **0.1018%** of total daily demand.

---

### 5.4 Abnormal Consumption & Anomaly Detection Evaluation
- **Detection Heuristic:** A rule-based baseline deviation detector was deployed:
  $$\text{Ratio} = \frac{\text{power\_kw}}{\text{base\_power\_kw}}$$
  Readings were flagged if $\text{Ratio} > 3.0$ (severe demand spike) or $\text{Ratio} < 0.05$ (abnormal load drop).
- **Evaluation Against Ground Truth (40 Injected Events):**
  - Total Injected Abnormal Events: **40**
  - Correctly Detected Events: **24 / 40 (60.0%)**
  - Missed Events: **16 / 40 (40.0%)**
  - Total False Flags: **0**
  - False Flag Rate: **0.000000%**
  - Meters Affected by False Flags: **0 (0.00%)**
- **Analysis:** Missed events were primarily subtle load drops in commercial facilities that remained within normal diurnal boundary variations, avoiding false alarms entirely.

---

### 5.5 Management Recommendation
> [!IMPORTANT]
> **To: Executive Grid Operations Committee**  
> **Subject: Storage, Ingestion, and Forecasting Operational Roadmap**  
> 
> Based on comprehensive benchmarking of 10.64 million smart meter records, we recommend the following three core initiatives:
> 
> 1. **Adopt TimescaleDB 1-Day Chunks with Segmented Compression:**  
>    Store historical telemetry in 1-day chunks compressed with `segmentby meter_id` and ordered by `event_time DESC`. This configuration compresses database footprint by **65.04%** (saving 933.6 MB per month) while accelerating meter history queries to **6.16 ms** and bulk meter analytics by **79.46%** (231.29 ms vs. 1,125.81 ms). Compression overhead is negligible (89.16 seconds per month).
> 
> 2. **Implement Materialized Continuous Aggregates with a 30-Minute Refresh Policy:**  
>    Deploy continuous aggregates for hourly regional and daily meter metrics. This achieves a **36.1x speedup** on operational dashboards (reducing full-screen refresh from **1,445.17 ms to 40.00 ms**) while maintaining 0.000000 kWh calculation divergence. A refresh policy covering the trailing 7 days with a 1-hour lag accommodates the **0.1018% delayed telemetry** without reporting discrepancies.
> 
> 3. **Deploy the Regularized Ridge Regression Day-Ahead Demand Forecast:**  
>    Transition operational dispatch planning to our day-ahead Ridge Regression model. In out-of-sample testing across 1,680 hours, the model delivered a **Mean Absolute Error of 4.52 kWh** (compared to 42.71 kWh for naive persistence, an **89.43% error reduction**). Peak hour forecasting error dropped from 270.34 kWh to **7.90 kWh** in high-demand regions, offering dispatchers reliable capacity planning.

---

## 6. Continuous Aggregates and Real-Time Refresh

### 6.1 View Creation, Refresh Policies & Storage
Three continuous aggregates were instantiated:
1. `cagg_energy_15m_region`: 15-minute bucket, region. (Size: **4.06 MB**)
2. `cagg_energy_hourly_region`: 1-hour bucket, region. (Size: **1.22 MB**)
3. `cagg_energy_daily_meter`: 1-day bucket (aligned with CAT midnight), meter_id. (Size: **11.00 MB**)

#### Refresh Timing Summary
- 15-minute regional aggregate refresh: **33.51 seconds**
- Hourly regional aggregate refresh: **19.70 seconds**
- Daily meter aggregate refresh: **7.75 seconds**
- Total storage overhead across all 3 views is only **16.28 MB** (over **92x smaller** than raw data).

---

### 6.2 Freshness and Delayed Arrival Timeline Test
A dedicated test hypertable `test_energy_freshness` and view `cagg_test_hourly_freshness` were tested through a controlled chronological injection protocol:

- **Reference Test Time:** `2026-10-06 10:58:51 CAT`
- **Completed Hour $H$:** `2026-10-06 10:00:00 CAT`
- **Older Hour ($H - 48\text{h}$):** `2026-10-04 10:00:00 CAT`

#### Step-by-Step State Matrix
| Timeline Stage | Older Hour ($H - 48\text{h}$) Aggregate | Hour $H$ Aggregate | Explanation |
|---|---|---|---|
| **Stage 1:** Initial older-hour refresh, real-time OFF | **1.0 kWh, 1 row** | No bucket returned | Only refreshed older hour is visible in materialized table. |
| **Stage 2:** After both new inserts, before refresh, real-time OFF | **1.0 kWh, 1 row** | No bucket returned | Materialized view ignores un-refreshed raw table inserts. |
| **Stage 3:** Real-time ON, still before refresh | **1.0 kWh, 1 row** | **2.0 kWh, 1 row** | Real-time union dynamically scans un-materialized raw table for hour $H$. |
| **Stage 4:** After manual refresh of both hours | **1.5 kWh, 2 rows** | **2.0 kWh, 1 row** | All historical inserts are permanently materialized into aggregate chunks. |

*This confirms that TimescaleDB continuous aggregates correctly balance freshness and performance.*

---

## 7. Dashboards and Queries

### 7.1 Five Operational Panels (Evaluated 22–28 September CAT)
1. **Panel 1 (Network Demand Trend):** Hourly total kWh across network (168 hourly points).
2. **Panel 2 (Regional Demand Comparison):** Total 7-day kWh per region (10 rows).
3. **Panel 3 (Customer Tariff Breakdown):** Total 7-day kWh by customer class (3 rows).
4. **Panel 4 (Top Peak Load Intervals):** Top 10 15-minute intervals ranked by demand (10 rows).
5. **Panel 5 (Meter History):** Daily energy profile for meter `1000000000` (7 rows).

---

### 7.2 & 7.3 Numerical Verification (Raw vs. Continuous Aggregate)
| Dashboard Panel | Row Count | Count Match | Max Energy Difference | Compliance Status |
|---|---|---|---|---|
| **Panel 1 (Network Demand)** | 168 rows | **True** | **0.000000 kWh** | **PASS ($\le 0.001$)** |
| **Panel 2 (Regional Comparison)** | 10 rows | **True** | **0.000000 kWh** | **PASS ($\le 0.001$)** |
| **Panel 3 (Customer Breakdown)** | 3 rows | **True** | **0.000000 kWh** | **PASS ($\le 0.001$)** |
| **Panel 4 (Top Peak Intervals)** | 10 rows | **True** | **0.000000 kWh** | **PASS ($\le 0.001$)** |
| **Panel 5 (Meter History)** | 7 rows | **True** | **0.000000 kWh** | **PASS ($\le 0.001$)** |

---

### 7.4 Dashboard Benchmark & Performance Tradeoff
Measured across 1 warm-up and 10 measured refreshes per mode:

| Panel / Component | Raw Hypertable Median | Raw Min / Max | Continuous Aggregate Median | CAgg Min / Max | Measured Speedup |
|---|---|---|---|---|---|
| **Panel 1 (Network Demand)** | 337.76 ms | 325.6 / 405.9 ms | **3.51 ms** | 3.3 / 3.9 ms | **96.1x** |
| **Panel 2 (Regional Comparison)** | 424.85 ms | 417.6 / 466.1 ms | **2.76 ms** | 2.5 / 3.0 ms | **154.2x** |
| **Panel 3 (Customer Breakdown)** | 422.79 ms | 417.3 / 455.3 ms | **25.34 ms** | 24.7 / 27.0 ms | **16.7x** |
| **Panel 4 (Top Peak Intervals)** | 241.26 ms | 234.1 / 256.5 ms | **6.56 ms** | 6.3 / 7.1 ms | **36.8x** |
| **Panel 5 (Meter History)** | 3.75 ms | 3.6 / 5.7 ms | **1.67 ms** | 1.5 / 1.8 ms | **2.2x** |
| **WHOLE DASHBOARD (All 5 Panels)** | **1,445.17 ms** | 1402.6 / 1486.8 ms | **40.00 ms** | 38.7 / 42.0 ms | **36.1x Speedup** |

**Tradeoff Judgment:** The **36.1x latency reduction** (from 1.45 seconds down to 40 milliseconds) provides instantaneous user experience at an insignificant storage cost of 16.28 MB and a background refresh cycle that finishes in under 20 seconds.

---

## 8. Forecast Regional Demand for the Next Day

### 8.1 Chronological Dataset & Feature Engineering
- **Analysis Grid:** 10 regions $\times$ 28 days $\times$ 24 hours = **6,720 observations**.
- **Engineered Predictors:**
  - `lag_1`: Immediate preceding hour consumption.
  - `lag_24`: Same-hour demand on previous calendar day.
  - `lag_168`: Same-hour demand on identical day of preceding week.
  - `hour`: Hour of day (0–23).
  - `dayofweek`: Day of week (0–6).
  - Regional indicator dummy variables.
- **Dataset Partitioning:**
  - Training Period: Days 8–21 (3,360 rows).
  - Test Period: Days 22–28 (1,680 rows).

---

### 8.2 & 8.3 Model Selection & Validation Table
Two models were trained on Days 8–14 and validated on Days 15–21:

| Config ID | Model Algorithm | Hyperparameter Settings | Train Rows | Validation Rows | Validation MAE (kWh) | Validation RMSE (kWh) |
|---|---|---|---|---|---|---|
| **Config 1** | **Ridge Regression** | $\alpha = 10.0$, L2 penalty | 1,680 | 1,680 | **4.3782** | **5.5604** |
| **Config 2** | Random Forest Regressor | $N = 100$, Depth = 12 | 1,680 | 1,680 | 4.9901 | 6.3886 |

**Model Selection:** **Config 1 (Ridge Regression)** demonstrated superior generalization, avoiding overfitting on noise and achieving the lowest validation error. It was frozen for final out-of-sample testing.

---

### 8.4 Out-of-Sample Test Evaluation (1,680 Predictions)
#### Overall Forecast Metrics
| Model Method | Test MAE (kWh) | Test RMSE (kWh) | MAE Improvement (%) | RMSE Improvement (%) |
|---|---|---|---|---|
| **Naive Baseline (Lag 24)** | 42.7083 kWh | 79.4029 kWh | 0.00% | 0.00% |
| **Ridge Regression Model** | **4.5158 kWh** | **5.7448 kWh** | **+89.43%** | **+92.76%** |

#### Regional Performance Breakdown
| Region | Baseline MAE | Model MAE | Baseline RMSE | Model RMSE | Error Reduction (%) |
|---|---|---|---|---|---|
| **Region 1** | 44.87 kWh | **4.57 kWh** | 83.70 kWh | **5.72 kWh** | +89.82% |
| **Region 2** | 41.63 kWh | **4.46 kWh** | 76.81 kWh | **5.58 kWh** | +89.29% |
| **Region 3** | 42.75 kWh | **4.37 kWh** | 77.61 kWh | **5.69 kWh** | +89.77% |
| **Region 4** | 34.86 kWh | **3.99 kWh** | 64.23 kWh | **4.98 kWh** | +88.57% |
| **Region 5** | 44.40 kWh | **5.15 kWh** | 81.08 kWh | **6.53 kWh** | +88.40% |
| **Region 6** | 39.43 kWh | **3.74 kWh** | 74.77 kWh | **4.71 kWh** | +90.52% |
| **Region 7** | 39.69 kWh | **4.33 kWh** | 73.94 kWh | **5.63 kWh** | +89.08% |
| **Region 8** | 55.97 kWh | **5.73 kWh** | 102.06 kWh | **7.19 kWh** | +89.76% |
| **Region 9** | 43.77 kWh | **4.65 kWh** | 80.18 kWh | **5.71 kWh** | +89.38% |
| **Region 10** | 39.72 kWh | **4.17 kWh** | 74.28 kWh | **5.32 kWh** | +89.49% |

#### Peak-Hour Prediction Errors
| Region | Peak Test Hour (CAT) | Actual Demand | Baseline Prediction | Baseline Error | Model Prediction | Model Error |
|---|---|---|---|---|---|---|
| **Region 8** | `2026-09-25 13:00` | **1,016.55 kWh** | 1,010.53 kWh | 6.02 kWh | **1,015.01 kWh** | **1.55 kWh** |
| **Region 1** | `2026-09-28 13:00` | **835.61 kWh** | 565.27 kWh | 270.34 kWh | **827.71 kWh** | **7.90 kWh** |
| **Region 5** | `2026-09-28 13:00` | **828.23 kWh** | 577.36 kWh | 250.87 kWh | **826.54 kWh** | **1.69 kWh** |
| **Region 9** | `2026-09-23 13:00` | **808.96 kWh** | 796.89 kWh | 12.07 kWh | **805.11 kWh** | **3.85 kWh** |
| **Region 3** | `2026-09-28 13:00` | **783.00 kWh** | 554.69 kWh | 228.32 kWh | **790.50 kWh** | **7.50 kWh** |

**Discussion:** The baseline model produces errors exceeding 250 kWh during Monday peaks because it erroneously references Sunday weekend consumption (`lag 24`). The Ridge model utilizes calendar indicators to predict the weekday morning ramp with high precision, maintaining peak errors below 8 kWh.

---

## 9. Submission Artifacts & Verification Checklist
- [x] Docker environment files: [docker-compose.yml](./docker-compose.yml)
- [x] Database initializers: [init_db.py](./init_db.py)
- [x] Pilot MQTT pipeline: [publisher.py](./publisher.py) and [subscriber.py](./subscriber.py)
- [x] Historical ingestion script: [load_historical_stream.py](./load_historical_stream.py)
- [x] Data quality checks: [data_quality_investigation.py](./data_quality_investigation.py)
- [x] Cleaning script: [clean_dataset.py](./clean_dataset.py)
- [x] Chunk benchmarks: [setup_chunk_hypertables.py](./setup_chunk_hypertables.py) and [benchmark_workloads.py](./benchmark_workloads.py)
- [x] Compression experiments: [compression_experiments.py](./compression_experiments.py)
- [x] Continuous aggregates & freshness: [continuous_aggregates.py](./continuous_aggregates.py)
- [x] Dashboard benchmarks: [dashboard_benchmarks.py](./dashboard_benchmarks.py)
- [x] Machine learning forecast: [forecasting_pipeline.py](./forecasting_pipeline.py)
- [x] Visual plots generated:
  - [demand_patterns.png](./demand_patterns.png)
  - [forecast_plot.png](./forecast_plot.png)
