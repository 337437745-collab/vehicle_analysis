# -*- coding: utf-8 -*-
"""
02 探索性分析：时序形态、设备对比、信号相关性、时间规律
输出 figures/fig04 ~ fig08
运行：python src/eda.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CLEAN_LONG, CLEAN_POS, DATA, DEVICE_COLORS, ALARM_COLORS,
                    ALARM_SHORT, vname, savefig, dump_json, haversine_km,
                    load_long, load_pos)

pd.set_option("display.width", 220)

STATE_CN = ["熄火/静止", "怠速", "低速(<30)", "中速(30-60)", "高速(>60)"]
STATE_COLORS = ["#7d5ba6", "#f2b134", "#4c9be8", "#2ca02c", "#c0392b"]


def load():
    return load_long(), load_pos()


def pivot_ts(long, cols):
    w = long[long["variable"].isin(cols)].pivot_table(
        index=["deviceId", "ts"], columns="variable", values="value", aggfunc="mean")
    w = w.sort_index()
    w = w.groupby(level=0).ffill(limit=12)
    return w


def pick_busiest_day(long, dev):
    d = long[long["deviceId"] == dev]
    day = d["ts"].dt.date.value_counts().idxmax()
    return day


def main():
    long, pos = load()
    devices = long["deviceId"].value_counts().index.tolist()
    main_dev = devices[0]
    print("设备:", [d[-8:] for d in devices])

    # ------------------------------------------------ 图4 车速/转速 单日时序
    day = pick_busiest_day(long, main_dev)
    w = pivot_ts(long, ["Vehicle speed", "ENGINE RPM", "IGNITION_STATUS", "IDLING"])
    w = w.loc[main_dev]
    w = w[(w.index >= pd.Timestamp(day)) & (w.index < pd.Timestamp(day) + pd.Timedelta(days=1))]
    print(f"  图4 使用设备 车-{main_dev[-8:]} 的 {day} 数据，共 {len(w)} 个时间点")

    fig, axes = plt.subplots(3, 1, figsize=(14, 8), sharex=True,
                             gridspec_kw={"height_ratios": [1.1, 1, 0.55], "hspace": 0.18})
    ax = axes[0]
    ax.plot(w.index, w["Vehicle speed"], color="#1f77b4", lw=0.9, label="车速")
    ax.set_ylabel("车速 (km/h)")
    ax.fill_between(w.index, 0, w["Vehicle speed"], color="#1f77b4", alpha=0.12)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(f"(a) 车-{main_dev[-8:]} 在 {day} 的车速序列", loc="left")

    ax = axes[1]
    ax.plot(w.index, w["ENGINE RPM"], color="#e8743b", lw=0.9)
    ax.fill_between(w.index, 0, w["ENGINE RPM"], color="#e8743b", alpha=0.12)
    ax.set_ylabel("转速 (rpm)")
    ax.set_title("(b) 发动机转速序列", loc="left")

    ax = axes[2]
    ign = w["IGNITION_STATUS"].ffill()
    ax.step(w.index, ign, color="#2ca02c", lw=1.0, where="post")
    ax.set_ylim(-0.15, 1.25)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["熄火", "点火"])
    ax.set_ylabel("点火状态")
    ax.set_xlabel("时间")
    ax.set_title("(c) 点火状态（车辆是否处于上电运行）", loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    fig.suptitle(f"图4  单车单日运行过程还原（{day}）", fontsize=14, fontweight="bold")
    savefig(fig, "fig04_车速转速时序.png", tight=False)

    # ------------------------------------------------ 图5 多信号窗口
    t0 = w.index[len(w) // 3]
    win = pivot_ts(long, ["Vehicle speed", "ENGINE RPM", "ACCELERATION X",
                          "ACCELERATION Y", "ACCELERATION Z", "THROTTLE POSITION",
                          "CALCULATED ENGINE LOAD"]).loc[main_dev]
    win = win[(win.index >= t0) & (win.index <= t0 + pd.Timedelta(minutes=40))]
    show = [("Vehicle speed", "#1f77b4"), ("ENGINE RPM", "#e8743b"),
            ("ACCELERATION X", "#2ca02c"), ("ACCELERATION Y", "#9467bd"),
            ("ACCELERATION Z", "#8c564b"), ("THROTTLE POSITION", "#d62728")]
    fig, axes = plt.subplots(len(show), 1, figsize=(13, 9.6), sharex=True,
                             gridspec_kw={"hspace": 0.32})
    for ax, (col, c) in zip(axes, show):
        s = win[col]
        ax.plot(s.index, s.values, color=c, lw=1.1, marker=".", ms=2.5)
        if col.startswith("ACCELERATION"):
            ax.axhline(0, color="#999", lw=0.8)
        ax.set_ylabel(vname(col).split("(")[1].rstrip(")"), fontsize=9)
        ax.set_title(vname(col), loc="left", fontsize=10.5)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].set_xlabel("时间")
    fig.suptitle(f"图5  多信号联合时序（车-{main_dev[-8:]}，{t0:%Y-%m-%d %H:%M} 起 40 分钟）",
                 fontsize=14, fontweight="bold")
    savefig(fig, "fig05_多信号时序.png", tight=False)

    # ------------------------------------------------ 图6 设备运行特征对比
    core = pivot_ts(long, ["Vehicle speed", "ENGINE RPM", "ACCELERATION X",
                           "ACCELERATION Z", "IGNITION_STATUS"])
    core = core.reset_index()
    core["device"] = core["deviceId"].str[-8:]

    def state_of(r):
        sp = r["Vehicle speed"] if pd.notna(r["Vehicle speed"]) else np.nan
        rpm = r["ENGINE RPM"] if pd.notna(r["ENGINE RPM"]) else np.nan
        if pd.notna(r["IGNITION_STATUS"]) and r["IGNITION_STATUS"] == 0:
            return STATE_CN[0]
        if pd.isna(sp):
            return None
        if sp < 3 and (pd.isna(rpm) or rpm > 400):
            return STATE_CN[1]
        if sp < 30:
            return STATE_CN[2]
        if sp < 60:
            return STATE_CN[3]
        return STATE_CN[4]

    core["state"] = core.apply(state_of, axis=1)
    share = pd.crosstab(core["device"], core["state"], normalize="index")
    share = share.reindex(columns=[c for c in STATE_CN if c in share.columns]).fillna(0)

    fig, axes = plt.subplots(2, 2, figsize=(14, 8.6),
                             gridspec_kw={"hspace": 0.38, "wspace": 0.24})
    ax = axes[0, 0]
    for i, (dev, s) in enumerate(core.groupby("device")):
        v = s["Vehicle speed"].dropna()
        ax.hist(v, bins=np.arange(0, 130, 4), density=True, alpha=0.45,
                color=DEVICE_COLORS[i], label=f"车-{dev}（n={len(v)}）")
    ax.set_xlabel("车速 (km/h)")
    ax.set_ylabel("概率密度")
    ax.set_title("(a) 车速分布", loc="left")
    ax.legend(fontsize=8)

    ax = axes[0, 1]
    data = [core.loc[core["device"] == d, "ENGINE RPM"].dropna().values for d in share.index]
    bp = ax.boxplot(data, tick_labels=[f"车-{d}" for d in share.index], patch_artist=True,
                    showfliers=False, widths=0.5)
    for patch, c in zip(bp["boxes"], DEVICE_COLORS):
        patch.set_facecolor(c)
        patch.set_alpha(0.55)
    ax.set_ylabel("转速 (rpm)")
    ax.set_title("(b) 发动机转速分布", loc="left")

    ax = axes[1, 0]
    p = pos.copy()
    p = p.sort_values(["deviceId", "ts"])
    p["dlat"] = p.groupby("deviceId")["lat"].shift()
    p["dlon"] = p.groupby("deviceId")["lon"].shift()
    p["gap"] = p.groupby("deviceId")["ts"].diff().dt.total_seconds()
    p["dkm"] = np.where(p["gap"].between(0, 120),
                        haversine_km(p["dlat"], p["dlon"], p["lat"], p["lon"]), 0.0)
    p["date"] = p["ts"].dt.strftime("%m-%d")
    daily = p.groupby(["deviceId", "date"])["dkm"].sum().reset_index()
    daily["device"] = daily["deviceId"].str[-8:]
    dates = sorted(daily["date"].unique())
    xx = np.arange(len(dates))
    for i, (dev, s) in enumerate(daily.groupby("device")):
        s = s.set_index("date").reindex(dates)
        ax.bar(xx + (i - 1) * 0.26, s["dkm"], width=0.24, color=DEVICE_COLORS[i],
               label=f"车-{dev}")
    ax.set_xticks(xx)
    ax.set_xticklabels(dates, fontsize=8)
    ax.set_ylabel("日行驶里程 (km)")
    ax.set_title("(c) 各设备日行驶里程（GPS 相邻点球面距离累加）", loc="left")
    ax.legend(fontsize=8)

    ax = axes[1, 1]
    bottom = np.zeros(len(share))
    for c, col in zip(share.columns, STATE_COLORS):
        ax.bar([f"车-{d}" for d in share.index], share[c].values, bottom=bottom,
               color=col, label=c, width=0.5)
        bottom += share[c].values
    ax.set_ylim(0, 1)
    ax.set_ylabel("样本占比")
    ax.set_title("(d) 运行工况构成", loc="left")
    ax.legend(fontsize=8, ncol=2, loc="lower center", bbox_to_anchor=(0.5, -0.42))
    fig.suptitle("图6  三台设备运行特征对比", fontsize=14, fontweight="bold")
    savefig(fig, "fig06_设备特征对比.png", tight=False)

    # ------------------------------------------------ 图7 相关性
    cols = ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
            "ACCELERATION Z", "THROTTLE POSITION", "RELATIVE THROTTLE POSITION",
            "CALCULATED ENGINE LOAD", "ENGINE COOLANT TEMPERATURE",
            "EXTERNAL BATTERY", "INTERNAL BATTERY", "TOWING", "IDLING"]
    cm = pivot_ts(long, cols)
    keep = [c for c in cm.columns if cm[c].notna().sum() > 300]
    cm = cm[keep]
    corr = cm.corr(method="spearman")
    labels = [vname(c).split("(")[0] for c in corr.columns]
    fig, ax = plt.subplots(figsize=(10.5, 8.6))
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    for i in range(len(labels)):
        for j in range(len(labels)):
            v = corr.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7.2,
                        color="white" if abs(v) > 0.55 else "#333")
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.82, label="Spearman 相关系数")
    ax.set_title("图7  关键信号相关性矩阵（Spearman，设备内时序对齐后计算）",
                 fontsize=13, fontweight="bold")
    savefig(fig, "fig07_相关性热力图.png")

    # ------------------------------------------------ 图8 时间规律
    w1 = pd.read_csv(DATA / "wide_1min.csv.gz", parse_dates=["bin"])
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.6),
                             gridspec_kw={"width_ratios": [1.25, 1, 1], "wspace": 0.3})
    ax = axes[0]
    pv = w1.pivot_table(index="hour", columns="weekday", values="vehicle_speed_mean",
                        aggfunc="mean")
    pv = pv.reindex(index=range(24), columns=range(7))
    im = ax.imshow(pv.values.T, aspect="auto", cmap="YlOrRd", origin="lower")
    ax.set_xticks(range(0, 24, 3))
    ax.set_xticklabels([f"{h}时" for h in range(0, 24, 3)], fontsize=8)
    ax.set_yticks(range(7))
    ax.set_yticklabels(["周一", "周二", "周三", "周四", "周五", "周六", "周日"], fontsize=9)
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.9, label="平均车速 (km/h)")
    ax.set_title("(a) 小时×星期 平均车速", loc="left", fontsize=11)

    ax = axes[1]
    hr = long.assign(h=long["ts"].dt.hour).groupby("h").size()
    ax.bar(hr.index, hr.values, color="#4c78a8", width=0.72)
    ax.set_xticks(range(0, 24, 3))
    ax.set_xlabel("小时")
    ax.set_ylabel("记录数")
    ax.set_title("(b) 全量记录的日内分布", loc="left", fontsize=11)

    ax = axes[2]
    wk = long.assign(w=long["ts"].dt.weekday).groupby("w")["alarm"].apply(lambda s: (s > 0).mean())
    ax.bar(["周一", "周二", "周三", "周四", "周五", "周六", "周日"], wk.values,
           color="#e8743b", width=0.62)
    ax.set_ylabel("告警读数占比")
    ax.set_ylim(0, wk.max() * 1.25)
    ax.set_title("(c) 告警占比的星期规律", loc="left", fontsize=11)
    fig.suptitle("图8  运行数据的时间规律", fontsize=14, fontweight="bold")
    savefig(fig, "fig08_时间规律.png", tight=False)

    # ------------------------------------------------ 指标落盘
    stats = {
        "eda": {
            "day_used": str(day),
            "hour_records": hr.to_dict(),
            "state_share": share.round(4).to_dict(),
            "device_speed_mean": core.groupby("device")["Vehicle speed"].mean().round(2).to_dict(),
            "device_rpm_mean": core.groupby("device")["ENGINE RPM"].mean().round(2).to_dict(),
            "daily_km": daily.groupby("device")["dkm"].agg(["mean", "sum", "max"]).round(2).to_dict(),
            "corr_pairs": {
                "speed_rpm": round(float(corr.loc["Vehicle speed", "ENGINE RPM"]), 3)
                if "Vehicle speed" in corr.columns else None,
                "speed_throttle": round(float(corr.loc["Vehicle speed", "THROTTLE POSITION"]), 3)
                if "THROTTLE POSITION" in corr.columns else None,
                "accx_accz": round(float(corr.loc["ACCELERATION X", "ACCELERATION Z"]), 3)
                if "ACCELERATION Z" in corr.columns else None,
            },
        }
    }
    dump_json(stats)
    print("02 探索性分析完成。\n")


if __name__ == "__main__":
    main()


