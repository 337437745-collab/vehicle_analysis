# -*- coding: utf-8 -*-
"""告警发生前后的状态对比与分车辆小时事件数。

用法：
    python improve_4_alarm_context.py --csv 题2_车辆物联网遥测.csv

输出（improve4_results/）：
    class_state.csv   各类别告警前 5 分钟 / 告警读数 / 后 5 分钟的车速、转速、水温
    hourly_events.csv 每台车每个小时的告警事件数（按事件起点计）
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SIGNALS = {
    "speed": "Vehicle speed",
    "rpm": "ENGINE RPM",
    "temp": "ENGINE COOLANT TEMPERATURE",
}
WINDOW = pd.Timedelta(minutes=5)
GAP = 120  # 同类告警间隔超过 120 秒视为新事件


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["time"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["time"]).copy()
    df["alarm"] = pd.to_numeric(df["alarmClass"], errors="coerce").fillna(0).astype(int)
    is_pos = df["variable"].eq("POSITION")
    df["value"] = pd.to_numeric(df["value"].where(~is_pos), errors="coerce")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="题2_车辆物联网遥测.csv")
    args = ap.parse_args()
    out = Path("improve4_results")
    out.mkdir(exist_ok=True)

    df = load(Path(args.csv))
    print(f"读数 {len(df):,} 条，设备 {df.deviceId.nunique()} 台")

    # 事件：同设备、同类别，相邻告警读数间隔 > 120 秒则开新事件
    al = df[df.alarm > 0].sort_values(["deviceId", "alarm", "time"]).copy()
    al["gap"] = al.groupby(["deviceId", "alarm"])["time"].diff().dt.total_seconds()
    al["ep"] = (al["gap"].isna() | (al["gap"] > GAP)).groupby(
        [al["deviceId"], al["alarm"]]).cumsum()
    ev = (al.groupby(["deviceId", "alarm", "ep"])
          .agg(start=("time", "min"), end=("time", "max"))
          .reset_index())
    ev["device"] = ev["deviceId"].str[-8:]
    print(f"事件 {len(ev):,} 段")

    # 每台设备、每个信号一条时间序列（仅用于前后窗口）
    series = {}
    for dev, g in df.groupby("deviceId"):
        series[dev] = {
            tag: g.loc[g.variable.eq(col)].set_index("time")["value"].sort_index()
            for tag, col in SIGNALS.items()
        }

    def wmean(s: pd.Series, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
        x = s.loc[(s.index >= t0) & (s.index < t1)].dropna()
        return float(x.mean()) if len(x) else np.nan

    rows = []
    for _, r in ev.iterrows():
        s = series.get(r["deviceId"], {})
        rec = {"alarm": int(r["alarm"]), "device": r["device"]}
        for tag in SIGNALS:
            rec[f"{tag}_pre"] = wmean(s.get(tag, pd.Series(dtype=float)),
                                      r["start"] - WINDOW, r["start"])
            rec[f"{tag}_post"] = wmean(s.get(tag, pd.Series(dtype=float)),
                                       r["end"], r["end"] + WINDOW)
        rows.append(rec)
    win = pd.DataFrame(rows)

    # 告警读数本身：用该类别的触发读数均值（比事件窗口更稳定）
    class_mean = {}
    for tag, col in SIGNALS.items():
        s = df.loc[df.variable.eq(col) & df.alarm.gt(0)]
        class_mean[tag] = s.groupby("alarm")["value"].mean()

    table = []
    for c in range(1, 6):
        sub = win[win.alarm.eq(c)]
        item = {"alarm": c, "events": len(sub)}
        for tag in SIGNALS:
            item[f"{tag}_pre"] = sub[f"{tag}_pre"].mean()
            item[f"{tag}_during"] = class_mean[tag].get(c, np.nan)
            item[f"{tag}_post"] = sub[f"{tag}_post"].mean()
        table.append(item)
    state = pd.DataFrame(table).round(2)
    state.to_csv(out / "class_state.csv", index=False, encoding="utf-8-sig")
    print("\n各类别状态（前 5 分钟 / 告警读数 / 后 5 分钟）：")
    print(state.to_string(index=False))

    # 分车辆、分小时的事件数
    hourly = (ev.assign(hour=ev.start.dt.hour)
              .pivot_table(index="hour", columns="device", values="start", aggfunc="size",
                           fill_value=0)
              .reindex(range(24), fill_value=0))
    hourly.index.name = "hour"
    hourly.to_csv(out / "hourly_events.csv", encoding="utf-8-sig")
    print("\n每台车每小时事件数：")
    print(hourly.to_string())
    print(f"\n结果已写入 {out.resolve()}")


if __name__ == "__main__":
    main()
