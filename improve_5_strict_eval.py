# -*- coding: utf-8 -*-
"""更严格的时间/事件隔离评估。

用法：
    python improve_5_strict_eval.py --csv 题2_车辆物联网遥测.csv

做法：
    1. 按时间前 70% 切分，切点前后各留 30 分钟；
    2. 同一告警事件若跨切点，整段从训练/测试中剔除；
    3. 输出每一类的精确率、召回率、F1，而不只看准确率。
输出：
    improve5_results/per_class.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, classification_report

CTX = ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
       "ACCELERATION Z"]


def build(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str)
    raw["time"] = pd.to_datetime(raw["timestamp"], errors="coerce")
    raw = raw.dropna(subset=["time"]).copy()
    raw["alarm"] = pd.to_numeric(raw["alarmClass"], errors="coerce").fillna(0).astype(int)
    is_pos = raw["variable"].eq("POSITION")
    raw["value"] = pd.to_numeric(raw["value"].where(~is_pos), errors="coerce")
    d = raw.loc[raw.value.notna(), ["deviceId", "time", "variable", "value", "alarm"]].copy()

    g = d.groupby("variable")["value"]
    d["value_z"] = (d["value"] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
    d["value_rank"] = g.rank(pct=True)
    d["value_scaled"] = d["value"] / g.transform(lambda s: s.abs().max())
    d["hour_sin"] = np.sin(2 * np.pi * d.time.dt.hour / 24)
    d["hour_cos"] = np.cos(2 * np.pi * d.time.dt.hour / 24)
    d["is_weekend"] = (d.time.dt.weekday >= 5).astype(int)

    ctx = (d.loc[d.variable.isin(CTX)]
           .pivot_table(index=["deviceId", "time"], columns="variable",
                        values="value", aggfunc="mean")
           .sort_index())
    ctx = ctx.groupby(level=0).ffill(limit=12).reset_index()
    ctx.columns = ["deviceId", "time"] + [f"ctx_{i}" for i in range(1, len(ctx.columns) - 1)]
    d = d.merge(ctx, on=["deviceId", "time"], how="left")
    d["device"] = d["deviceId"].str[-8:]
    d = pd.concat([d, pd.get_dummies(d["variable"], prefix="var", dtype=float),
                   pd.get_dummies(d["device"], prefix="dev", dtype=float)], axis=1)
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="题2_车辆物联网遥测.csv")
    args = ap.parse_args()
    out = Path("improve5_results")
    out.mkdir(exist_ok=True)

    d = build(Path(args.csv)).sort_values("time").reset_index(drop=True)
    y = d["alarm"].astype(int).values
    ts = d["time"].values
    cut = np.sort(ts)[int(len(ts) * 0.7)]
    emb = np.timedelta64(30, "m")

    # 告警事件编号，跨切点的事件整段剔除
    al = d[d.alarm > 0].sort_values(["deviceId", "alarm", "time"]).copy()
    al["gap"] = al.groupby(["deviceId", "alarm"])["time"].diff().dt.total_seconds()
    al["ep"] = (al["gap"].isna() | (al["gap"] > 120)).groupby(
        [al["deviceId"], al["alarm"]]).cumsum()
    al["key"] = (al["deviceId"].astype(str) + "|" + al["alarm"].astype(str)
                 + "|" + al["ep"].astype(str))
    key = pd.Series(index=d.index, dtype=object)
    key.loc[al.index] = al["key"].values
    d["key"] = key
    bounds = al.groupby("key")["time"].agg(["min", "max"])
    cross = set(bounds[(bounds["min"] < cut) & (bounds["max"] > cut)].index)
    d["cross"] = d["key"].isin(cross).fillna(False)

    tr = (ts < cut) & (~d["cross"].values)
    te = (ts > cut + emb) & (~d["cross"].values)
    drop = ["time", "key", "alarm", "variable", "deviceId", "device", "cross"]
    X = d.drop(columns=drop).fillna(d.drop(columns=drop).median(numeric_only=True)).fillna(0)

    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, random_state=42)
    model.fit(X[tr], y[tr])
    pred = model.predict(X[te])
    print(f"训练 {int(tr.sum()):,} 条 / 测试 {int(te.sum()):,} 条")
    print(f"跨切点事件 {len(cross)} 个，剔除 {int(d['cross'].sum())} 条读数")
    print(f"准确率 {accuracy_score(y[te], pred):.4f}\n")
    rep = classification_report(y[te], pred, labels=range(6), output_dict=True,
                                zero_division=0)
    rows = []
    for c in range(6):
        r = rep[str(c)]
        rows.append({"class": c, "precision": r["precision"], "recall": r["recall"],
                     "f1": r["f1-score"], "support": r["support"]})
    table = pd.DataFrame(rows).round(4)
    table.to_csv(out / "per_class.csv", index=False, encoding="utf-8-sig")
    print(table.to_string(index=False))
    print(f"\n结果已写入 {out.resolve()}")


if __name__ == "__main__":
    main()
