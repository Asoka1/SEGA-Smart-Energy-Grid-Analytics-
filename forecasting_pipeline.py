"""Regional Demand Forecasting Pipeline (Section 8).

8.1: Chronological dataset preparation & feature engineering:
     - 6,720 hourly regional rows (10 regions x 28 days x 24 hours)
     - Features: lag_1, lag_24, lag_168, hour, day_of_week, region one-hot encoding
8.2: 24-hour naive baseline (lag_24 persistence)
8.3: Model training & validation comparison (Ridge vs Random Forest):
     - Validation table with Config ID, train rows, val rows, val MAE, val RMSE
     - Freeze best model and generate 1,680 predictions for the 7 test days (22-28 Sep)
8.4: Evaluation & plots:
     - MAE & RMSE across all 1,680 observations and per region
     - Peak-hour error analysis per region
     - Generates 7-day forecast comparison plot (forecast_plot.png)
"""

import json
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg2
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

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
    cur = conn.cursor()

    print("==========================================================================")
    print("SECTION 8.1: PREPARE CHRONOLOGICAL DATA AND FEATURES")
    print("==========================================================================")

    # 1. Fetch hourly regional consumption from cagg_energy_hourly_region
    cur.execute("""
        SELECT 
            region,
            bucket AS event_time,
            total_energy_kwh
        FROM cagg_energy_hourly_region
        ORDER BY region, event_time;
    """)
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["region", "event_time", "energy_kwh"])
    df["event_time"] = pd.to_datetime(df["event_time"])

    print(f"Loaded {len(df):,} hourly regional observations.")

    # 2. Engineer features per region
    df_list = []
    for reg, grp in df.groupby("region"):
        grp = grp.sort_values("event_time").copy()
        grp["lag_1"] = grp["energy_kwh"].shift(1)
        grp["lag_24"] = grp["energy_kwh"].shift(24)
        grp["lag_168"] = grp["energy_kwh"].shift(168)
        grp["hour"] = grp["event_time"].dt.hour
        grp["dayofweek"] = grp["event_time"].dt.dayofweek
        df_list.append(grp)

    df_feats = pd.concat(df_list, axis=0).reset_index(drop=True)

    # One-hot encode regions
    df_encoded = pd.get_dummies(df_feats, columns=["region", "hour", "dayofweek"], drop_first=True)

    # Drop early rows that have NaN from lag_168 (first 7 days)
    # Notice: 1 Sep - 7 Sep provides lags; Days 8 - 21 (14 days) are training/validation;
    # Days 22 - 28 (7 days = 1,680 observations) are test!
    feature_cols = [c for c in df_encoded.columns if c not in ["event_time", "energy_kwh"]]

    # Split:
    # Train period: 2026-09-08 00:00 to 2026-09-22 00:00 CAT (14 days, with full lags available)
    # Validation split: Days 15 to 21 (2026-09-15 to 2026-09-22, 7 days x 24 x 10 = 1,680 rows)
    # Train split: Days 8 to 14 (2026-09-08 to 2026-09-15, 7 days x 24 x 10 = 1,680 rows)
    # Test period: 2026-09-22 00:00 to 2026-09-29 00:00 CAT (7 days x 24 x 10 = 1,680 rows)

    val_mask = (df_encoded["event_time"] >= "2026-09-15 00:00:00+02") & (df_encoded["event_time"] < "2026-09-22 00:00:00+02")
    sub_train_mask = (df_encoded["event_time"] >= "2026-09-08 00:00:00+02") & (df_encoded["event_time"] < "2026-09-15 00:00:00+02")
    full_train_mask = (df_encoded["event_time"] >= "2026-09-08 00:00:00+02") & (df_encoded["event_time"] < "2026-09-22 00:00:00+02")
    test_mask = (df_encoded["event_time"] >= "2026-09-22 00:00:00+02") & (df_encoded["event_time"] < "2026-09-29 00:00:00+02")

    X_sub_tr, y_sub_tr = df_encoded.loc[sub_train_mask, feature_cols], df_encoded.loc[sub_train_mask, "energy_kwh"]
    X_val, y_val = df_encoded.loc[val_mask, feature_cols], df_encoded.loc[val_mask, "energy_kwh"]

    X_full_tr, y_full_tr = df_encoded.loc[full_train_mask, feature_cols], df_encoded.loc[full_train_mask, "energy_kwh"]
    X_test, y_test = df_encoded.loc[test_mask, feature_cols], df_encoded.loc[test_mask, "energy_kwh"]
    df_test = df_feats.loc[test_mask].copy()

    print(f"Training split rows (Days 8-21): {len(X_full_tr):,}")
    print(f"Test split rows (Days 22-28):     {len(X_test):,}")

    print("\n==========================================================================")
    print("SECTION 8.3: MODEL TRAINING AND VALIDATION TABLE")
    print("==========================================================================")

    configs = [
        {"id": "Config 1", "name": "Ridge Regression", "model": Ridge(alpha=10.0), "settings": "alpha=10.0, L2 penalty"},
        {"id": "Config 2", "name": "Random Forest", "model": RandomForestRegressor(n_estimators=100, max_depth=12, random_state=42), "settings": "n_estimators=100, max_depth=12"}
    ]

    print(f"{'Config ID':<10} | {'Model Name':<18} | {'Settings':<32} | {'Train':<6} | {'Val':<6} | {'Val MAE (kWh)':<14} | {'Val RMSE (kWh)'}")
    print("-" * 110)

    val_metrics = []
    for cfg in configs:
        m = cfg["model"]
        m.fit(X_sub_tr, y_sub_tr)
        val_preds = m.predict(X_val)
        val_mae = mean_absolute_error(y_val, val_preds)
        val_rmse = root_mean_squared_error(y_val, val_preds)
        val_metrics.append({
            "id": cfg["id"],
            "name": cfg["name"],
            "settings": cfg["settings"],
            "train_rows": len(X_sub_tr),
            "val_rows": len(X_val),
            "val_mae": round(val_mae, 4),
            "val_rmse": round(val_rmse, 4)
        })
        print(f"{cfg['id']:<10} | {cfg['name']:<18} | {cfg['settings']:<32} | {len(X_sub_tr):<6} | {len(X_val):<6} | {val_mae:14.4f} | {val_rmse:14.4f}")

    # Select best model
    best_cfg = min(configs, key=lambda c: val_metrics[[x["id"] for x in configs].index(c["id"])]["val_mae"])
    print(f"\nSelected Model: {best_cfg['name']} ({best_cfg['id']}) with lowest Validation MAE.")

    # Refit selected model on full train period (Days 8 to 21)
    best_model = best_cfg["model"]
    best_model.fit(X_full_tr, y_full_tr)

    # Predictions
    test_preds = best_model.predict(X_test)
    df_test["model_pred"] = test_preds
    df_test["baseline_pred"] = df_test["lag_24"]

    print("\n==========================================================================")
    print("SECTION 8.4: EVALUATION & BASELINE COMPARISON (1,680 Test Observations)")
    print("==========================================================================")

    # Overall metrics
    base_mae = mean_absolute_error(df_test["energy_kwh"], df_test["baseline_pred"])
    base_rmse = root_mean_squared_error(df_test["energy_kwh"], df_test["baseline_pred"])

    model_mae = mean_absolute_error(df_test["energy_kwh"], df_test["model_pred"])
    model_rmse = root_mean_squared_error(df_test["energy_kwh"], df_test["model_pred"])

    mae_imp = 100.0 * (base_mae - model_mae) / base_mae
    rmse_imp = 100.0 * (base_rmse - model_rmse) / base_rmse

    print(f"{'Method':<20} | {'Test MAE (kWh)':<16} | {'Test RMSE (kWh)':<16} | {'MAE Improv %'}")
    print("-" * 65)
    print(f"{'Naive Baseline (lag 24)':<20} | {base_mae:16.4f} | {base_rmse:16.4f} | {'0.00%':<12}")
    print(f"{best_cfg['name']:<20} | {model_mae:16.4f} | {model_rmse:16.4f} | {mae_imp:6.2f}%")
    print("-" * 65)

    # Per-region metrics
    print("\nPer-Region Forecast Performance:")
    print(f"{'Region':<12} | {'Base MAE':<12} | {'Model MAE':<12} | {'Base RMSE':<12} | {'Model RMSE':<12} | {'MAE Improv %'}")
    print("-" * 75)
    regional_metrics = {}
    for reg, grp in df_test.groupby("region"):
        r_b_mae = mean_absolute_error(grp["energy_kwh"], grp["baseline_pred"])
        r_m_mae = mean_absolute_error(grp["energy_kwh"], grp["model_pred"])
        r_b_rmse = root_mean_squared_error(grp["energy_kwh"], grp["baseline_pred"])
        r_m_rmse = root_mean_squared_error(grp["energy_kwh"], grp["model_pred"])
        r_imp = 100.0 * (r_b_mae - r_m_mae) / r_b_mae
        regional_metrics[reg] = {
            "base_mae": round(r_b_mae, 2), "model_mae": round(r_m_mae, 2),
            "base_rmse": round(r_b_rmse, 2), "model_rmse": round(r_m_rmse, 2),
            "mae_imp": round(r_imp, 2)
        }
        print(f"{reg:<12} | {r_b_mae:12.2f} | {r_m_mae:12.2f} | {r_b_rmse:12.2f} | {r_m_rmse:12.2f} | {r_imp:6.2f}%")

    # Peak hour analysis per region
    print("\nPeak Hour Prediction Error per Region:")
    print(f"{'Region':<12} | {'Peak Hour (CAT)':<25} | {'Actual (kWh)':<14} | {'Base Pred':<12} | {'Base Err':<12} | {'Model Pred':<12} | {'Model Err'}")
    print("-" * 105)
    peak_analysis = []
    for reg, grp in df_test.groupby("region"):
        peak_row = grp.sort_values(by=["energy_kwh", "event_time"], ascending=[False, True]).iloc[0]
        act = peak_row["energy_kwh"]
        b_p = peak_row["baseline_pred"]
        m_p = peak_row["model_pred"]
        b_err = abs(act - b_p)
        m_err = abs(act - m_p)
        peak_analysis.append({
            "region": reg,
            "peak_hour": str(peak_row["event_time"]),
            "actual_kwh": round(act, 2),
            "baseline_pred": round(b_p, 2),
            "baseline_err": round(b_err, 2),
            "model_pred": round(m_p, 2),
            "model_err": round(m_err, 2)
        })
        print(f"{reg:<12} | {str(peak_row['event_time']):<25} | {act:14.2f} | {b_p:12.2f} | {b_err:12.2f} | {m_p:12.2f} | {m_err:12.2f}")

    # Plot 7-day actual vs baseline vs model for Region 8
    reg8 = df_test[df_test["region"] == "Region 8"].sort_values("event_time")
    plt.figure(figsize=(15, 6))
    plt.plot(reg8["event_time"], reg8["energy_kwh"], label="Actual Consumption", color="black", linewidth=2.0)
    plt.plot(reg8["event_time"], reg8["baseline_pred"], label="Naive Baseline (lag 24)", color="#ff7f0e", linestyle="--", alpha=0.8)
    plt.plot(reg8["event_time"], reg8["model_pred"], label=f"{best_cfg['name']} Forecast", color="#1f77b4", linewidth=1.8)
    plt.title("Region 8 Hourly Energy Consumption Forecast: 7 Test Days (22 - 28 September CAT)")
    plt.xlabel("Date & Time (CAT)")
    plt.ylabel("Hourly Energy Consumption (kWh)")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.tight_layout()
    plt.savefig("forecast_plot.png", dpi=300)
    plt.close()
    print("\nSaved 7-day forecast comparison plot to 'forecast_plot.png'.")

    # Export forecasting results
    export_s8 = {
        "validation_table": val_metrics,
        "selected_model": best_cfg["name"],
        "overall_evaluation": {
            "baseline_mae": round(base_mae, 4),
            "baseline_rmse": round(base_rmse, 4),
            "model_mae": round(model_mae, 4),
            "model_rmse": round(model_rmse, 4),
            "mae_improvement_pct": round(mae_imp, 2),
            "rmse_improvement_pct": round(rmse_imp, 2)
        },
        "regional_metrics": regional_metrics,
        "peak_analysis": peak_analysis
    }
    with open("forecasting_results_section8.json", "w") as f:
        json.dump(export_s8, f, indent=2)

    print("Section 8 completed successfully! Results saved to 'forecasting_results_section8.json'.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
