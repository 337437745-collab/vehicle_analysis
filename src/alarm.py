# -*- coding: utf-8 -*-
"""
03 告警事件识别与成因分析
=========================
告警标签 alarmClass 附着在"单条传感器读数"上，因此本节以读数级为主视角：
  · 每类告警下，触发变量自身的取值分布（反推阈值）
  · 每类告警与哪些变量强相关（log2 提升度）
  · 每类告警发生时车辆处于什么工况（车速/转速分档）
  · 告警片段（episode）识别与时间规律
输出 figures/fig09 ~ fig13
运行：python src/alarm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (ALARM_COLORS, ALARM_CN, ALARM_SHORT, vname, savefig, dump_json,
                    load_long, slug)

pd.set_option("display.width", 220)

KEY_VARS = ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
            "ACCELERATION Z", "EXTERNAL BATTERY"]
STATE_VARS = ["Vehicle speed", "ENGINE RPM", "IGNITION_STATUS",
              "ENGINE COOLANT TEMPERATURE"]
SPEED_BINS = [-0.1, 10, 20, 30, 40, 50, 60, 80, 300]
SPEED_LABELS = ["<10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-80", ">80"]
RPM_BINS = [-0.1, 800, 1200, 1600, 2000, 2500, 3000, 8000]
RPM_LABELS = ["<800", "800-1200", "1200-1600", "1600-2000", "2000-2500",
              "2500-3000", ">3000"]


def state_ts(long):
    """每台设备每个时间戳的车辆状态（前向填充，最多 12 步）"""
    w = (long[long["variable"].isin(STATE_VARS)]
         .pivot_table(index=["deviceId", "ts"], columns="variable", values="value",
                      aggfunc="mean").sort_index())
    w.columns = [slug(c) for c in w.columns]
    w = w.groupby(level=0).ffill(limit=12)
    return w.reset_index()


def main():
    long = load_long()
    st = state_ts(long)
    # 把同刻状态挂到每条读数上（读数自身的变量不参与覆盖，保留原始 value）
    m = long.merge(st, on=["deviceId", "ts"], how="left")
    m["device"] = m["deviceId"].str[-8:]
    print("读数级分析表:", m.shape)

    # ---------------------------------------------------------- 图9 各类别取值分布
    fig, axes = plt.subplots(2, 3, figsize=(14.5, 8.4),
                             gridspec_kw={"hspace": 0.44, "wspace": 0.24})
    stats, ranges = {}, {}
    for ax, var in zip(axes.ravel(), KEY_VARS):
        s = m[(m["variable"] == var) & m["value"].notna()]
        grp, pos, ns = [], [], {}
        for c in range(6):
            v = s.loc[s["alarm"] == c, "value"].values
            ns[c] = len(v)
            grp.append(v if len(v) >= 5 else np.array([np.nan]))
        bp = ax.boxplot(grp, tick_labels=[ALARM_SHORT[c] for c in range(6)],
                        patch_artist=True, showfliers=False, widths=0.62)
        for patch, c in zip(bp["boxes"], range(6)):
            patch.set_facecolor(ALARM_COLORS[c])
            patch.set_alpha(0.75)
        for i, g in enumerate(grp):
            if len(g) >= 5:
                ax.text(i + 1, np.nanmax(g), f"n={ns[i]}", ha="center", va="bottom",
                        fontsize=6.5, color="#555")
        ax.set_title(vname(var), fontsize=10.5, loc="left")
        ax.tick_params(axis="x", labelsize=7.5, rotation=22)
        stats[var] = {int(c): (round(float(np.nanmean(grp[c])), 2) if ns[c] >= 5 else None)
                      for c in range(6)}
        ranges[var] = {int(c): {"n": ns[c],
                               "min": round(float(s.loc[s["alarm"] == c, "value"].min()), 2),
                               "median": round(float(s.loc[s["alarm"] == c, "value"].median()), 2),
                               "max": round(float(s.loc[s["alarm"] == c, "value"].max()), 2)}
                       for c in range(6) if ns[c] >= 5}
    fig.suptitle("图9  各告警类别下触发变量自身的取值分布（读数级）", fontsize=13.5,
                 fontweight="bold")
    savefig(fig, "fig09_告警信号分布.png", tight=False)

    # ---------------------------------------------------------- 图10 类别-变量关联
    al = long[long["alarm"] > 0]
    share_all = al["variable"].value_counts(normalize=True)
    ratio = pd.DataFrame(index=share_all.index)
    for c in [1, 2, 3, 4, 5]:
        s = al.loc[al["alarm"] == c, "variable"].value_counts(normalize=True)
        ratio[f"C{c}"] = np.log2((s.reindex(share_all.index).fillna(1e-4)) / share_all)
    order = ratio.abs().max(axis=1).sort_values(ascending=False).index[:18]
    ratio = ratio.loc[order]
    labels = [vname(i) if len(vname(i)) <= 22 else vname(i)[:20] + "..." for i in ratio.index]

    fig, ax = plt.subplots(figsize=(8.8, 8))
    im = ax.imshow(ratio.values, cmap="RdBu_r", vmin=-1.6, vmax=1.6, aspect="auto")
    ax.set_xticks(range(ratio.shape[1]))
    ax.set_xticklabels(ratio.columns, fontsize=10)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8.5)
    for i in range(ratio.shape[0]):
        for j in range(ratio.shape[1]):
            v = ratio.values[i, j]
            ax.text(j, i, f"{v:.1f}", ha="center", va="center", fontsize=7.5,
                    color="white" if abs(v) > 1.0 else "#333")
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.7, label="log2(类别内变量占比 / 总体占比)")
    ax.set_title("图10  告警类别与触发变量的关联强度\n（红色=该变量在类别中出现得比平均更多）",
                 fontsize=12.5, fontweight="bold")
    savefig(fig, "fig10_类别变量关联.png")

    # ---------------------------------------------------------- 图11 工况构成
    m["speed_bin"] = pd.cut(m["vehicle_speed"], SPEED_BINS, labels=SPEED_LABELS)
    m["rpm_bin"] = pd.cut(m["engine_rpm"], RPM_BINS, labels=RPM_LABELS)
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.2),
                             gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.24})
    for ax, (col, labs, xlab, title) in zip(axes, [
        ("speed_bin", SPEED_LABELS, "同刻车速分档 (km/h)", "(a) 各车速区间内告警类别构成"),
        ("rpm_bin", RPM_LABELS, "同刻转速分档 (rpm)", "(b) 各转速区间内告警类别构成"),
    ]):
        ct = pd.crosstab(m[col], m["alarm"], normalize="index")
        ct = ct.reindex(columns=range(6), fill_value=0)
        bottom = np.zeros(len(ct))
        for c in range(6):
            ax.bar(range(len(ct)), ct[c].values, bottom=bottom, color=ALARM_COLORS[c],
                   label=ALARM_SHORT[c], width=0.72)
            bottom += ct[c].values
        ax.set_xticks(range(len(ct)))
        ax.set_xticklabels(ct.index.astype(str), fontsize=8.5, rotation=18)
        ax.set_ylim(0, 1)
        ax.set_xlabel(xlab)
        ax.set_ylabel("读数占比")
        ax.set_title(title, loc="left", fontsize=11)
    axes[1].legend(fontsize=8, ncol=2, loc="lower right", framealpha=0.92)
    fig.suptitle("图11  告警类别构成随车速/转速的变化", fontsize=14, fontweight="bold")
    savefig(fig, "fig11_告警与运行状态.png", tight=False)

    # ---------------------------------------------------------- 图12 告警时序定位
    dev = long["deviceId"].value_counts().index[0]
    day = long.loc[long["deviceId"] == dev, "ts"].dt.date.value_counts().idxmax()
    dd = long[(long["deviceId"] == dev) & (long["ts"].dt.date == day)]
    sp = dd[dd["variable"] == "Vehicle speed"].sort_values("ts")
    ala = dd[(dd["alarm"] > 0) & (dd["variable"].isin(KEY_VARS + STATE_VARS))]

    fig, axes = plt.subplots(2, 1, figsize=(14, 7.8), sharex=True,
                             gridspec_kw={"height_ratios": [1.3, 1], "hspace": 0.16})
    ax = axes[0]
    ax.plot(sp["ts"], sp["value"], color="#3c5a78", lw=0.9, zorder=1, label="车速")
    spm = sp.set_index("ts")["value"]
    for c in range(1, 6):
        s = ala[ala["alarm"] == c]
        if s.empty:
            continue
        y = spm.reindex(s["ts"], method="ffill").fillna(0).values
        ax.scatter(s["ts"], y, s=10, color=ALARM_COLORS[c], alpha=0.85,
                   label=ALARM_CN[c], linewidths=0)
    ax.set_ylabel("车速 (km/h)")
    ax.set_title(f"(a) 车-{dev[-8:]} 于 {day} 的车速曲线与告警发生时刻（点=告警读数）",
                 loc="left")
    ax.legend(fontsize=8, ncol=6, loc="upper center", markerscale=1.8)

    ax = axes[1]
    cnt = (ala.assign(h=ala["ts"].dt.floor("2min"))
           .pivot_table(index="h", columns="alarm", values="value", aggfunc="size",
                        fill_value=0))
    bottom = np.zeros(len(cnt))
    for c in cnt.columns:
        ax.bar(cnt.index, cnt[c].values, bottom=bottom, width=0.0016,
               color=ALARM_COLORS[int(c)], label=ALARM_SHORT[int(c)])
        bottom += cnt[c].values
    ax.set_ylabel("2 分钟告警读数条数")
    ax.set_xlabel("时间")
    ax.set_title("(b) 告警读数的时间密度（按类别堆叠）", loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    fig.suptitle("图12  告警事件的时序定位", fontsize=14, fontweight="bold")
    savefig(fig, "fig12_告警时序.png", tight=False)

    # ---------------------------------------------------------- 图13 告警片段
    e = al[["deviceId", "ts", "alarm", "variable"]].sort_values(
        ["deviceId", "alarm", "ts"]).copy()
    e["gap"] = e.groupby(["deviceId", "alarm"])["ts"].diff().dt.total_seconds()
    e["new"] = (e["gap"].isna()) | (e["gap"] > 120)
    e["ep"] = e.groupby(["deviceId", "alarm"])["new"].cumsum()
    epi = (e.groupby(["deviceId", "alarm", "ep"])
           .agg(start=("ts", "min"), end=("ts", "max"), n=("ts", "size")).reset_index())
    epi["dur_min"] = (epi["end"] - epi["start"]).dt.total_seconds() / 60

    # ---------------------------------------------------------- 告警前后状态对比
    # 用原始读数（不做前向填充）统计每段告警前 5 分钟、告警期间、后 5 分钟；
    # 避免熄火时把上一段行驶速度“带”进片段，导致 C5 出现虚假车速。
    raw_col = {"speed": "Vehicle speed", "rpm": "ENGINE RPM",
               "temp": "ENGINE COOLANT TEMPERATURE"}
    raw_by_dev = {}
    for dev, g in long.groupby("deviceId"):
        raw_by_dev[dev] = {
            tag: g.loc[g["variable"].eq(col)].set_index("ts")["value"].sort_index()
            for tag, col in raw_col.items()}

    def wmean(series, t0, t1):
        if series is None or len(series) == 0:
            return np.nan
        s = series.loc[(series.index >= t0) & (series.index < t1)].dropna()
        return float(s.mean()) if len(s) else np.nan

    ba_rows = []
    for _, r in epi.iterrows():
        f = raw_by_dev.get(r["deviceId"])
        if f is None:
            continue
        rec = {"alarm": int(r["alarm"]), "device": r["deviceId"][-8:]}
        for tag in raw_col:
            rec[f"{tag}_pre"] = wmean(f[tag], r["start"] - pd.Timedelta(minutes=5), r["start"])
            rec[f"{tag}_during"] = wmean(f[tag], r["start"], r["end"] + pd.Timedelta(seconds=1))
            rec[f"{tag}_post"] = wmean(f[tag], r["end"], r["end"] + pd.Timedelta(minutes=5))
        ba_rows.append(rec)
    ba = pd.DataFrame(ba_rows)

    # 正常读数 vs 告警读数（同一变量的读数级对照）
    normal_means, alarm_means = {}, {}
    for tag, col in raw_col.items():
        s = long.loc[long["variable"].eq(col), ["value", "alarm"]].dropna()
        normal_means[tag] = float(s.loc[s["alarm"] == 0, "value"].mean()) if len(s) else None
        alarm_means[tag] = float(s.loc[s["alarm"] > 0, "value"].mean()) if len(s) else None
    sig_std = {tag: float(long.loc[long["variable"].eq(col), "value"].std())
               for tag, col in raw_col.items()}

    # 各类别“触发读数”自身的均值（读数级），作为告警发生时的状态
    class_means = {}
    for tag, col in raw_col.items():
        s = long.loc[long["variable"].eq(col), ["value", "alarm"]].dropna()
        class_means[tag] = {str(c): (float(s.loc[s["alarm"] == c, "value"].mean())
                                     if (s["alarm"] == c).any() else None)
                            for c in range(6)}

    def fnum(x):
        return None if x is None or not np.isfinite(x) else round(float(x), 3)

    by_class = {}
    for c in range(1, 6):
        sub = ba[ba["alarm"] == c] if len(ba) else ba
        item = {"n": int(len(sub))}
        for tag in raw_col:
            pre = sub[f"{tag}_pre"].mean() if len(sub) else np.nan
            during = class_means[tag][str(c)]
            post = sub[f"{tag}_post"].mean() if len(sub) else np.nan
            item[tag] = {"pre": fnum(pre), "during": fnum(during), "post": fnum(post),
                         "delta": fnum(during - pre) if (during is not None and np.isfinite(pre)) else None}
        by_class[str(c)] = item

    delta_sigma = {}
    for c in range(1, 6):
        delta_sigma[str(c)] = {}
        for tag in raw_col:
            item = by_class[str(c)][tag]
            base = item["during"]
            if item["pre"] is not None and base is not None and sig_std[tag]:
                delta_sigma[str(c)][tag] = round((base - item["pre"]) / sig_std[tag], 2)
            else:
                delta_sigma[str(c)][tag] = None

    # 各车辆每小时的告警事件数（按片段起点计），以及每观测小时的告警率
    epi["hour"] = epi["start"].dt.hour
    devs = sorted(epi["deviceId"].str[-8:].unique())
    hourly = {d: [0] * 24 for d in devs}
    for (d, h), n in epi.groupby([epi["deviceId"].str[-8:], "hour"]).size().items():
        hourly[d][int(h)] = int(n)
    span_h = (long.groupby(long["deviceId"].str[-8:])["ts"]
              .agg(lambda s: (s.max() - s.min()).total_seconds() / 3600))
    per_hour = {d: round(len(epi[epi["deviceId"].str[-8:] == d]) / float(span_h[d]), 3)
                for d in devs}

    # ---------------------------------------------------------- 图13 告警片段
    fig, axes = plt.subplots(2, 2, figsize=(15, 9.6),
                             gridspec_kw={"hspace": 0.38, "wspace": 0.26})
    ax = axes[0, 0]
    cnt = epi["alarm"].value_counts().sort_index()
    ax.bar([ALARM_SHORT[c] for c in cnt.index], cnt.values,
           color=[ALARM_COLORS[c] for c in cnt.index], width=0.6)
    for x, v in enumerate(cnt.values):
        ax.text(x, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("片段数")
    ax.set_title("(a) 各类别告警片段数", loc="left")
    ax.tick_params(axis="x", labelsize=7.5, rotation=20)

    ax = axes[0, 1]
    for c in range(1, 6):
        s = epi.loc[epi["alarm"] == c, "dur_min"].clip(0, 120)
        if len(s):
            ax.hist(s, bins=np.arange(0, 125, 5), histtype="step", lw=1.6,
                    color=ALARM_COLORS[c], label=f"{ALARM_SHORT[c]}（中位 {s.median():.0f} 分）")
    ax.set_xlabel("片段持续时长 (分钟)")
    ax.set_ylabel("片段数")
    ax.set_title("(b) 告警片段持续时长", loc="left")
    ax.legend(fontsize=7.5)

    ax = axes[1, 0]
    mat = np.array([hourly[d] for d in devs], dtype=float)
    im = ax.imshow(mat, aspect="auto", cmap="Blues")
    ax.set_yticks(range(len(devs)))
    ax.set_yticklabels([f"车-{d}" for d in devs], fontsize=8)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels(range(0, 24, 2), fontsize=8)
    ax.set_xlabel("小时")
    ax.set_title("(c) 各车辆每小时的告警事件数", loc="left")
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            if mat[i, j] > 0:
                ax.text(j, i, int(mat[i, j]), ha="center", va="center", fontsize=6.5,
                        color="white" if mat[i, j] > mat.max() * 0.6 else "#334155")
    fig.colorbar(im, ax=ax, shrink=0.8, label="片段数")

    ax = axes[1, 1]
    tags = ["speed", "rpm", "temp"]
    zmat = np.array([[delta_sigma[str(c)][t] if delta_sigma[str(c)][t] is not None else np.nan
                      for t in tags] for c in range(1, 6)], dtype=float)
    im = ax.imshow(zmat, aspect="auto", cmap="RdBu_r", vmin=-2, vmax=2)
    ax.set_xticks(range(3))
    ax.set_xticklabels(["车速", "转速", "水温"], fontsize=9)
    ax.set_yticks(range(5))
    ax.set_yticklabels([ALARM_SHORT[c] for c in range(1, 6)], fontsize=8)
    ax.set_title("(d) 告警读数相对前 5 分钟的变化（全局标准差）", loc="left")
    for i in range(5):
        for j in range(3):
            v = zmat[i, j]
            ax.text(j, i, "—" if np.isnan(v) else f"{v:+.2f}", ha="center", va="center",
                    fontsize=8, color="white" if (not np.isnan(v) and abs(v) > 1.2) else "#1f2937")
    ax.grid(False)
    fig.colorbar(im, ax=ax, shrink=0.8, label="Δ / σ")

    fig.suptitle("图13  告警片段识别与前后状态：片段数、时长、车辆时段分布与信号变化",
                 fontsize=14, fontweight="bold")
    savefig(fig, "fig13_告警片段.png", tight=False)

    # ---------------------------------------------------------- 指标
    cnt_all = long["alarm"].value_counts().sort_index()
    top_var = {c: ratio[c].idxmax() for c in ratio.columns}
    res = {
        "alarm": {
            "class_counts": {int(k): int(v) for k, v in cnt_all.items()},
            "class_share": {int(k): round(float(v) / len(long), 4) for k, v in cnt_all.items()},
            "episodes": int(len(epi)),
            "episode_by_class": {int(k): int(v) for k, v in epi["alarm"].value_counts().items()},
            "episode_dur_median": {int(k): round(float(v), 1) for k, v in
                                   epi.groupby("alarm")["dur_min"].median().items()},
            "episode_dur_p90": {int(k): round(float(v), 1) for k, v in
                                epi.groupby("alarm")["dur_min"].quantile(0.9).items()},
            "episode_by_device": {str(k): int(v) for k, v in
                                  epi.groupby(epi["deviceId"].str[-8:]).size().items()},
            "reading_mean_by_class": stats,
            "reading_range_by_class": ranges,
            "top_var_per_class": top_var,
            "alarm_rate_by_device": {str(k): round(float(v), 3) for k, v in
                                     long.groupby(long["deviceId"].str[-8:])["alarm"]
                                     .apply(lambda s: (s > 0).mean()).items()},
            "before_after": {
                "window_min": 5,
                "by_class": by_class,
                "normal_means": {k: fnum(v) for k, v in normal_means.items()},
                "alarm_means": {k: fnum(v) for k, v in alarm_means.items()},
                "delta_sigma": delta_sigma,
            },
            "hourly_events": hourly,
            "episodes_per_hour": per_hour,
        }
    }
    dump_json(res)
    print(f"  告警片段 {len(epi)} 段；类别计数 {dict(cnt_all)}")
    print("  各类别读数均值(车速):", stats.get("Vehicle speed"))
    print("03 告警分析完成。\n")


if __name__ == "__main__":
    main()
