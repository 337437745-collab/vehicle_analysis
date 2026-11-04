# -*- coding: utf-8 -*-
"""
01 数据清洗与规整
=================
输入：data/raw_vehicle_telemetry.csv （长表，173853 行）
输出：
  data/clean_long.csv.gz  清洗后的长表（数值型 value）
  data/clean_pos.csv.gz   定位子表（lat/lon/alt）
  data/wide_ts.csv.gz     按 (设备, 时间戳) 对齐的宽表
  data/wide_1min.csv.gz   1 分钟聚合宽表
  data/wide_5min.csv.gz   5 分钟聚合宽表（时段级建模用）
  output/metrics.json     清洗过程统计
运行：python src/prepare.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (RAW_CSV, CLEAN_LONG, CLEAN_POS, DATA, CORE_VARS, PHYS_RANGE,
                    ALARM_COLORS, ALARM_SHORT, DEVICE_COLORS, vname, savefig, dump_json,
                    haversine_km)

pd.set_option("display.width", 200)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main():
    stats: dict = {}
    print("=" * 78)
    print("读取原始数据 ...")
    raw = pd.read_csv(RAW_CSV, dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    stats["raw_rows"] = int(len(raw))
    stats["raw_cols"] = int(raw.shape[1])
    print(f"  原始形状: {raw.shape}")

    # 1. 结构体检
    stats["n_variables"] = int(raw["variable"].nunique())
    stats["n_devices"] = int(raw["deviceId"].nunique())
    stats["dup_full_rows"] = int(raw.duplicated().sum())
    stats["dup_key_rows"] = int(raw.duplicated(subset=["deviceId", "timestamp", "variable"]).sum())
    stats["null_timeMili"] = int(raw["timeMili"].isna().sum())
    stats["null_any"] = int(raw.isna().sum().sum())
    var_counts = raw["variable"].value_counts()
    stats["n_variables_lt5"] = int((var_counts < 5).sum())
    print(f"  设备 {stats['n_devices']} 台 / 变量 {stats['n_variables']} 种 / 重复行 {stats['dup_full_rows']}")
    print(f"  timeMili 缺失 {stats['null_timeMili']}（{stats['null_timeMili']/len(raw):.1%}）")

    df = raw.drop_duplicates().copy()

    # 2. 字段拆分与类型转换
    df["ts"] = pd.to_datetime(df["timestamp"], format="mixed", errors="coerce")
    stats["bad_timestamp"] = int(df["ts"].isna().sum())
    df = df.dropna(subset=["ts"])
    df["time_mili"] = df["ts"].astype("int64") // 10 ** 6
    df["alarm"] = pd.to_numeric(df["alarmClass"], errors="coerce").fillna(0).astype("int8")

    is_pos = df["variable"] == "POSITION"
    pos_parts = df.loc[is_pos, "value"].str.split(",", expand=True)
    stats["position_rows"] = int(is_pos.sum())
    stats["position_bad_parts"] = int(pos_parts.shape[1] != 3)

    pos = pd.DataFrame({
        "deviceId": df.loc[is_pos, "deviceId"].values,
        "ts": df.loc[is_pos, "ts"].values,
        "lat": pd.to_numeric(pos_parts[0], errors="coerce").values,
        "lon": pd.to_numeric(pos_parts[1], errors="coerce").values,
        "alt": pd.to_numeric(pos_parts[2], errors="coerce").values,
        "alarm": df.loc[is_pos, "alarm"].values,
    })
    inv = (pos["lat"].isna() | pos["lon"].isna()
           | ~pos["lat"].between(13.0, 13.8) | ~pos["lon"].between(74.5, 74.9)
           | ~pos["alt"].between(-100, 2000))
    stats["position_invalid"] = int(inv.sum())
    print(f"  定位记录 {len(pos)} 条，越界置空 {int(inv.sum())} 条")
    pos.loc[inv, ["lat", "lon", "alt"]] = np.nan
    pos = pos.dropna(subset=["lat", "lon"]).reset_index(drop=True)

    num = df.loc[~is_pos].copy()
    num["value"] = pd.to_numeric(num["value"], errors="coerce")
    stats["non_numeric_rows"] = int(num["value"].isna().sum())
    print("  非数值记录分布:", dict(num.loc[num["value"].isna(), "variable"].value_counts()))
    num = num[num["variable"] != "PIDNameNotAvailable"]

    # 3. 异常值处理
    clipped = {}
    for var, (lo, hi) in PHYS_RANGE.items():
        m = num["variable"] == var
        bad = m & ~num["value"].between(lo, hi)
        clipped[var] = int(bad.sum())
        num.loc[bad, "value"] = np.nan
    stats["phys_out_of_range"] = clipped
    print("  物理范围越界置空:", {k: v for k, v in clipped.items() if v})

    extreme = {}
    for var in CORE_VARS:
        s = num.loc[num["variable"] == var, "value"].dropna()
        if len(s) < 30 or s.std() == 0:
            continue
        extreme[var] = int(((s - s.mean()).abs() > 3 * s.std()).sum())
    stats["core_3sigma_outliers"] = extreme
    stats["core_3sigma_total"] = int(sum(extreme.values()))

    long = num[["deviceId", "ts", "time_mili", "variable", "value", "alarm"]].copy()
    long = long.dropna(subset=["value"])
    stats["clean_long_rows"] = int(len(long))
    stats["clean_pos_rows"] = int(len(pos))
    print(f"  清洗后长表 {len(long)} 行 / 定位 {len(pos)} 行")

    dev_stat = (long.groupby("deviceId")
                .agg(rows=("value", "size"), ts_min=("ts", "min"), ts_max=("ts", "max"))
                .reset_index())
    dev_stat["hours"] = (dev_stat["ts_max"] - dev_stat["ts_min"]).dt.total_seconds() / 3600
    dev_stat["alarm_rate"] = dev_stat["deviceId"].map(
        long.groupby("deviceId")["alarm"].apply(lambda s: (s > 0).mean()))
    stats["devices"] = [
        {"device": d["deviceId"][-8:], "rows": int(d["rows"]), "ts_min": str(d["ts_min"]),
         "ts_max": str(d["ts_max"]), "hours": float(d["hours"]),
         "alarm_rate": float(d["alarm_rate"])}
        for _, d in dev_stat.iterrows()]
    print(dev_stat.to_string(index=False))

    long.to_csv(CLEAN_LONG, index=False, compression="gzip")
    pos.to_csv(CLEAN_POS, index=False, compression="gzip")

    # 4. 长表转宽表
    print("\n长表转宽表 ...")
    wide_ts = long.pivot_table(index=["deviceId", "ts"], columns="variable",
                               values="value", aggfunc="mean")
    wide_ts.columns = [slug(c) for c in wide_ts.columns]
    wide_ts = wide_ts.reset_index().sort_values(["deviceId", "ts"])
    stats["wide_ts_rows"] = int(len(wide_ts))
    stats["wide_ts_cols"] = int(wide_ts.shape[1])
    stats["wide_ts_fill_rate"] = float(1 - wide_ts.drop(columns=["deviceId", "ts"]).isna().mean().mean())
    print(f"  (设备,时间戳) 宽表: {wide_ts.shape}，整体填充率 {stats['wide_ts_fill_rate']:.1%}")

    cont_cols = [slug(v) for v in CORE_VARS if slug(v) in wide_ts.columns]
    wide_ts[cont_cols] = wide_ts.groupby("deviceId", group_keys=False)[cont_cols].ffill(limit=12)
    wide_ts.to_csv(DATA / "wide_ts.csv.gz", index=False, compression="gzip")
    stats["wide_ts_fill_rate_ffill"] = float(
        1 - wide_ts.select_dtypes("number").isna().mean().mean())

    # 按周期聚合
    def build_wide(freq: str, fname: str):
        g = long.copy()
        g["bin"] = g["ts"].dt.floor(freq)
        w = g.pivot_table(index=["deviceId", "bin"], columns="variable", values="value",
                          aggfunc="mean")
        w.columns = [slug(c) + "_mean" for c in w.columns]
        g["absv"] = g["value"].abs()
        mx = g.pivot_table(index=["deviceId", "bin"], columns="variable", values="value",
                           aggfunc="max")
        mx.columns = [slug(c) + "_max" for c in mx.columns]
        amx = g.pivot_table(index=["deviceId", "bin"], columns="variable", values="absv",
                            aggfunc="max")
        amx.columns = [slug(c) + "_absmax" for c in amx.columns]
        nrec = g.groupby(["deviceId", "bin"]).size().rename("n_rec")
        w = w.join(mx).join(amx).join(nrec)

        lab = (g.assign(one=1)
               .pivot_table(index=["deviceId", "bin"], columns="alarm", values="one",
                            aggfunc="size", fill_value=0)
               .rename(columns=lambda c: f"alarm_c{int(c)}"))
        for c in range(6):
            if f"alarm_c{c}" not in lab.columns:
                lab[f"alarm_c{c}"] = 0
        lab["alarm_any"] = (lab[[f"alarm_c{c}" for c in range(1, 6)]].sum(axis=1) > 0).astype(int)
        w = w.join(lab)

        p = pos.copy()
        p["bin"] = p["ts"].dt.floor(freq)
        p = p.sort_values(["deviceId", "ts"])
        p["dlat"] = p.groupby("deviceId")["lat"].shift()
        p["dlon"] = p.groupby("deviceId")["lon"].shift()
        p["dgap"] = p.groupby("deviceId")["ts"].diff().dt.total_seconds()
        p["dkm"] = np.where(p["dgap"].between(0, 120),
                            haversine_km(p["dlat"], p["dlon"], p["lat"], p["lon"]), 0.0)
        pg = p.groupby(["deviceId", "bin"]).agg(dist_km=("dkm", "sum"),
                                                alt_mean=("alt", "mean"),
                                                n_pos=("lat", "size"))
        w = w.join(pg).reset_index()
        w["hour"] = w["bin"].dt.hour
        w["weekday"] = w["bin"].dt.weekday
        w["is_weekend"] = (w["weekday"] >= 5).astype(int)
        w["device"] = w["deviceId"].str[-8:]
        w.to_csv(DATA / fname, index=False, compression="gzip")
        print(f"  {freq} 宽表: {w.shape} -> {fname}")
        return w

    w1 = build_wide("1min", "wide_1min.csv.gz")
    w5 = build_wide("5min", "wide_5min.csv.gz")
    stats["wide_1min"] = {"rows": int(len(w1)),
                          "speed_cover": float(w1["vehicle_speed_mean"].notna().mean())}
    stats["wide_5min"] = {"rows": int(len(w5)),
                          "speed_cover": float(w5["vehicle_speed_mean"].notna().mean())}

    # 5. 图1 数据概览
    fig = plt.figure(figsize=(14, 8.4))
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 1.25], height_ratios=[1, 1.3],
                          hspace=0.45, wspace=0.26)
    d = dev_stat.copy()
    d["name"] = "车-" + d["deviceId"].str[-8:]

    ax = fig.add_subplot(gs[0, 0])
    bars = ax.bar(d["name"], d["rows"], color=DEVICE_COLORS[:len(d)], width=0.55)
    for b, r in zip(bars, d["rows"]):
        ax.text(b.get_x() + b.get_width() / 2, r, f"{r/10000:.1f}万", ha="center",
                va="bottom", fontsize=9)
    ax.set_title("(a) 各设备记录量")
    ax.set_ylabel("记录行数")
    ax.set_ylim(0, d["rows"].max() * 1.18)

    ax = fig.add_subplot(gs[0, 1])
    for i, (_, r) in enumerate(d.iterrows()):
        ax.barh(r["name"], (r["ts_max"] - r["ts_min"]).total_seconds() / 86400,
                left=mdates.date2num(r["ts_min"]), color=DEVICE_COLORS[i], height=0.42)
    ax.set_title("(b) 各设备数据覆盖时段")
    ax.set_xlabel("日期（2020 年）")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.grid(axis="y", visible=False)

    ax = fig.add_subplot(gs[1, :])
    vc = var_counts.head(22)[::-1]
    labels = [vname(v) for v in vc.index]
    labels = [x if len(x) <= 30 else x[:28] + "..." for x in labels]
    ax.barh(labels, vc.values, color="#4c78a8")
    ax.set_title("(c) 变量记录量 Top22（长表共 52 种变量）")
    ax.set_xlabel("记录数")
    for y, v in enumerate(vc.values):
        ax.text(v, y, f" {v}", va="center", fontsize=7.5)
    ax.tick_params(axis="y", labelsize=8)
    fig.suptitle("图1  车辆遥测数据总览", fontsize=14, fontweight="bold", y=0.96)
    savefig(fig, "fig01_数据概览.png", tight=False)

    # 6. 图2 告警分布
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.7),
                             gridspec_kw={"width_ratios": [1, 1, 1.2], "wspace": 0.28})
    ac = long["alarm"].value_counts().sort_index()
    ax = axes[0]
    wedges, *_ = ax.pie(ac.values, startangle=90, counterclock=False,
                        colors=[ALARM_COLORS[c] for c in ac.index],
                        wedgeprops=dict(width=0.42, edgecolor="white"))
    ax.legend(wedges, [f"{ALARM_SHORT[c]} {v/len(long):.1%}" for c, v in ac.items()],
              loc="center", fontsize=8.5, frameon=False, handlelength=1.1)
    ax.set_title("(a) 全体读数告警构成")
    ax.grid(False)

    ax = axes[1]
    ct = pd.crosstab(long["deviceId"].str[-8:], long["alarm"], normalize="index")
    bottom = np.zeros(len(ct))
    for c in ct.columns:
        ax.bar(["车-" + i for i in ct.index], ct[c].values, bottom=bottom,
               color=ALARM_COLORS[c], label=ALARM_SHORT[c], width=0.52)
        bottom += ct[c].values
    ax.set_title("(b) 各设备告警构成（占比）")
    ax.set_ylabel("占比")
    ax.legend(fontsize=7.5, ncol=2, loc="upper center", framealpha=0.9)
    ax.set_ylim(0, 1.45)

    ax = axes[2]
    h = long.assign(hh=long["ts"].dt.hour).groupby("hh")["alarm"].apply(lambda s: (s > 0).mean())
    ax.bar(h.index, h.values, color="#e8743b", width=0.72)
    ax.set_title("(c) 告警读数占比的日内分布")
    ax.set_xlabel("小时")
    ax.set_ylabel("告警占比")
    ax.set_xticks(range(0, 24, 2))
    fig.suptitle("图2  告警类别与时间分布", fontsize=14, fontweight="bold")
    savefig(fig, "fig02_告警分布.png", tight=False)

    # 7. 图3 GPS 轨迹
    devs_with_pos = [d for d, s in pos.groupby("deviceId") if len(s) > 0]
    fig, axes = plt.subplots(1, len(devs_with_pos), figsize=(5.2 * len(devs_with_pos), 5.2),
                             sharey=True, squeeze=False)
    axes = axes[0]
    for i, dev in enumerate(devs_with_pos):
        ax = axes[i]
        s = pos[pos["deviceId"] == dev].sort_values("ts")
        ax.scatter(s["lon"], s["lat"], c=s["ts"].astype("int64"), s=1.5, cmap="viridis",
                   alpha=0.55, linewidths=0)
        ax.set_title(f"车-{dev[-8:]}（{len(s)} 个有效定位点）", fontsize=10.5)
        ax.set_xlabel("经度")
        if i == 0:
            ax.set_ylabel("纬度")
        ax.set_aspect("equal", adjustable="datalim")
        ax.ticklabel_format(useOffset=False, style="plain")
        ax.tick_params(labelsize=8)
    fig.suptitle("图3  有效 GPS 轨迹（颜色由暗到亮表示时间先后；车-NAB0gBAQ 定位全部无效已剔除）",
                 fontsize=13, fontweight="bold")
    savefig(fig, "fig03_轨迹.png", tight=False)

    dump_json({"prepare": stats})
    print("\n01 清洗与规整完成。\n")


if __name__ == "__main__":
    main()

