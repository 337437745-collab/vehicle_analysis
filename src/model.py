# -*- coding: utf-8 -*-
"""
04 告警识别建模与评估
=====================
实验设计
  A. 读数级多分类（同分布随机划分）：Dummy / 逻辑回归 / 随机森林 / 直方图梯度提升
  B. 跨设备泛化：留一设备交叉验证（检验告警规则是否设备无关）
  C. 无监督异常检测与告警标签的一致性（3σ / IQR 与 alarmClass 对照）
  D. 时段级预警可行性：5 分钟窗口状态特征 -> 高等级告警(C3/C4)是否出现
输出 figures/fig14 ~ fig17
运行：python src/model.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, classification_report,
                             confusion_matrix, precision_recall_fscore_support)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, ALARM_COLORS, ALARM_SHORT, ALARM_CN, vname, savefig,
                    dump_json, load_long, slug)

pd.set_option("display.width", 220)
RNG = 42

CONTEXT = ["vehicle_speed", "engine_rpm", "acceleration_x", "acceleration_y",
           "acceleration_z", "throttle_position", "ignition_status", "idling",
           "towing", "external_battery", "internal_battery",
           "calculated_engine_load", "relative_throttle_position"]


# ------------------------------------------------------------------ 数据准备
def build_readings():
    long = load_long()
    wide = pd.read_csv(DATA / "wide_ts.csv.gz")
    wide["ts"] = pd.to_datetime(wide["ts"], format="mixed")
    ctx_cols = [c for c in CONTEXT if c in wide.columns]
    ctx = wide[["deviceId", "ts"] + ctx_cols].copy()

    df = long.merge(ctx, on=["deviceId", "ts"], how="left")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    g = df.groupby("variable")["value"]
    df["value_z"] = (df["value"] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
    df["value_rank"] = g.rank(pct=True)
    df["hour"] = df["ts"].dt.hour
    df["weekday"] = df["ts"].dt.weekday
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["device"] = df["deviceId"].str[-8:]
    return df, ctx_cols


def feature_matrix(df, ctx_cols, with_variable=True):
    X = pd.DataFrame(index=df.index)
    X["value_z"] = df["value_z"]
    X["value_rank"] = df["value_rank"]
    X["value_scaled"] = df["value"] / df.groupby("variable")["value"].transform("max").abs()
    X["hour_sin"] = df["hour_sin"]
    X["hour_cos"] = df["hour_cos"]
    X["is_weekend"] = (df["weekday"] >= 5).astype(int)
    for c in ctx_cols:
        X[c] = df[c]
        X[c + "_miss"] = df[c].isna().astype(int)
    if with_variable:
        vd = pd.get_dummies(df["variable"], prefix="var", dtype=float)
        dd = pd.get_dummies(df["device"], prefix="dev", dtype=float)
        X = pd.concat([X, vd, dd], axis=1)
    return X.astype("float32")


def evaluate(name, model, Xtr, ytr, Xte, yte, out):
    t0 = time.time()
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)
    acc = accuracy_score(yte, pred)
    mf1 = f1_score(yte, pred, average="macro", zero_division=0)
    wf1 = f1_score(yte, pred, average="weighted", zero_division=0)
    p, r, f, s = precision_recall_fscore_support(yte, pred, labels=range(6),
                                                 zero_division=0)
    out[name] = {
        "acc": round(float(acc), 4), "macro_f1": round(float(mf1), 4),
        "weighted_f1": round(float(wf1), 4), "fit_seconds": round(time.time() - t0, 1),
        "per_class": {int(c): {"precision": round(float(p[c]), 3),
                               "recall": round(float(r[c]), 3),
                               "f1": round(float(f[c]), 3),
                               "support": int(s[c])} for c in range(6)},
    }
    print(f"    {name:26s} acc={acc:.4f}  macroF1={mf1:.4f}  weightedF1={wf1:.4f}"
          f"  ({out[name]['fit_seconds']}s)")
    return pred


def task_d_early_warning(df):
    """以过去 10 分钟的运行状态与告警历史，预测未来 10 分钟是否出现新的类别4告警片段"""
    state_vars = ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
                  "ACCELERATION Z", "THROTTLE POSITION", "IGNITION_STATUS"]
    long = df[["deviceId", "ts", "variable", "value", "alarm"]]
    state = (long[long["variable"].isin(state_vars)]
             .pivot_table(index=["deviceId", "ts"], columns="variable", values="value",
                          aggfunc="mean").sort_index())
    state = state.groupby(level=0).ffill(limit=12)

    # 目标事件：类别4（高速高负荷）告警片段的起点（同设备间隔 > 120s 视为新片段）
    al = long[long["alarm"] == 4].sort_values(["deviceId", "ts"])
    al = al.assign(gap=al.groupby("deviceId")["ts"].diff().dt.total_seconds())
    starts = al[(al["gap"].isna()) | (al["gap"] > 120)].groupby("deviceId")["ts"]
    starts = {k: np.sort(v.unique()).astype("datetime64[s]").astype("int64")
              for k, v in starts}
    al_any = long[long["alarm"] > 0].groupby("deviceId")["ts"]
    al_any = {k: np.sort(v.unique()).astype("datetime64[s]").astype("int64")
              for k, v in al_any}
    al_c4 = long[long["alarm"] == 4].groupby("deviceId")["ts"]
    al_c4 = {k: np.sort(v.unique()).astype("datetime64[s]").astype("int64")
             for k, v in al_c4}

    def count_between(arr, t0, t1):
        return np.searchsorted(arr, t1, "right") - np.searchsorted(arr, t0, "left")

    rows = []
    for dev, s in state.groupby(level=0):
        s = s.droplevel(0)
        s = s[s["Vehicle speed"].notna()]
        if len(s) < 50:
            continue
        r = pd.DataFrame(index=s.index)
        for col, tag in [("Vehicle speed", "speed"), ("ENGINE RPM", "rpm"),
                         ("THROTTLE POSITION", "thr")]:
            if col in s.columns:
                roll = s[col].rolling("10min", min_periods=3)
                r[tag + "_mean"] = roll.mean()
                r[tag + "_max"] = roll.max()
                r[tag + "_std"] = roll.std()
        for col, tag in [("ACCELERATION X", "ax"), ("ACCELERATION Y", "ay"),
                         ("ACCELERATION Z", "az")]:
            if col in s.columns:
                r[tag + "_absmax"] = s[col].abs().rolling("10min", min_periods=3).max()
        if "IGNITION_STATUS" in s.columns:
            r["ign_off_share"] = (s["IGNITION_STATUS"] == 0).rolling(
                "10min", min_periods=3).mean()
        r["n_obs"] = s["Vehicle speed"].rolling("10min", min_periods=3).count()
        r["speed_now"] = s["Vehicle speed"]

        t0 = r.index.values.astype("datetime64[s]").astype("int64")
        # 过去 10 分钟告警读数条数（历史特征，可用）
        a = al_any.get(dev, np.array([], dtype="int64"))
        r["alarm_cnt_past"] = count_between(a, t0 - 600, t0)
        r["c4_cnt_past"] = count_between(al_c4.get(dev, np.array([], dtype="int64")),
                                         t0 - 1800, t0)
        # 未来 10 分钟是否出现新片段起点（标签）
        e = starts.get(dev, np.array([], dtype="int64"))
        r["label"] = count_between(e, t0, t0 + 600) > 0
        r["device"] = dev[-8:]
        rows.append(r)

    d = pd.concat(rows).reset_index()
    d = d.dropna(subset=["speed_mean"]).copy()
    d["hour"] = d["ts"].dt.hour
    d["hour_sin"] = np.sin(2 * np.pi * d["hour"] / 24)
    d["hour_cos"] = np.cos(2 * np.pi * d["hour"] / 24)
    d["device_code"] = pd.factorize(d["device"])[0]
    d["label"] = d["label"].astype(int)
    fcols = [c for c in d.columns if c not in ("ts", "label", "device", "hour")]
    d = d.sort_values("ts").reset_index(drop=True)
    cut = int(len(d) * 0.7)
    Xd = d[fcols].fillna(d[fcols].median())
    yd = d["label"].values
    print(f"    样本 {len(d)} 个时刻（正类占比 {yd.mean():.1%}），特征 {len(fcols)} 个；"
          f"按时间前 70% 训练、后 30% 测试（测试集正类 {yd[cut:].mean():.1%}）")
    out, curves = {}, {}
    models = {
        "基线(多数类)": DummyClassifier(strategy="most_frequent"),
        "逻辑回归": make_pipeline(StandardScaler(),
                                 LogisticRegression(max_iter=2000, class_weight="balanced")),
        "随机森林": RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=5,
                                        class_weight="balanced_subsample", random_state=RNG,
                                        n_jobs=-1),
    }
    for name, m in models.items():
        m.fit(Xd.iloc[:cut], yd[:cut])
        pr = m.predict(Xd.iloc[cut:])
        yt = yd[cut:]
        p, r, f, _ = precision_recall_fscore_support(yt, pr, average="binary", zero_division=0)
        auc = None
        if hasattr(m, "predict_proba"):
            from sklearn.metrics import roc_auc_score, precision_recall_curve
            prob = m.predict_proba(Xd.iloc[cut:])[:, 1]
            auc = round(float(roc_auc_score(yt, prob)), 4)
            prec_c, rec_c, _ = precision_recall_curve(yt, prob)
            curves[name] = (rec_c, prec_c)
        out[name] = {"acc": round(float(accuracy_score(yt, pr)), 4),
                     "precision": round(float(p), 4), "recall": round(float(r), 4),
                     "f1": round(float(f), 4), "auc": auc, "test_n": int(len(yt))}
        print(f"    {name}: acc={out[name]['acc']}  P={p:.3f} R={r:.3f} F1={f:.3f}"
              f"  AUC={auc}")
    # 严格切分：切点前后各留 10 分钟，避免标签窗口（未来 10 分钟）跨集合
    cut_ts = d["ts"].iloc[cut]
    emb2 = pd.Timedelta(minutes=10)
    tr2 = (d["ts"] < cut_ts - emb2)
    te2 = (d["ts"] > cut_ts + emb2)
    strict = {}
    for name, m in {
        "逻辑回归": make_pipeline(StandardScaler(),
                                 LogisticRegression(max_iter=2000, class_weight="balanced")),
        "随机森林": RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=5,
                                        class_weight="balanced_subsample", random_state=RNG,
                                        n_jobs=-1),
    }.items():
        m.fit(Xd[tr2], yd[tr2.values])
        pr = m.predict(Xd[te2])
        yt = yd[te2.values]
        p, r, f, _ = precision_recall_fscore_support(yt, pr, average="binary", zero_division=0)
        from sklearn.metrics import roc_auc_score
        prob = m.predict_proba(Xd[te2])[:, 1]
        strict[name] = {"acc": round(float(accuracy_score(yt, pr)), 4),
                        "precision": round(float(p), 4), "recall": round(float(r), 4),
                        "f1": round(float(f), 4),
                        "auc": round(float(roc_auc_score(yt, prob)), 4),
                        "train_n": int(tr2.sum()), "test_n": int(te2.sum())}
        print(f"    [严格切分] {name}: acc={strict[name]['acc']} P={p:.3f} "
              f"R={r:.3f} F1={f:.3f} AUC={strict[name]['auc']}")

    rf = models["随机森林"]
    imp = pd.Series(rf.feature_importances_, index=fcols).sort_values(ascending=False).head(10)
    return out, {"curves": curves, "importance": imp.to_dict(), "n": int(len(d)),
                 "pos_rate": round(float(yd.mean()), 4),
                 "test_pos_rate": round(float(yd[cut:].mean()), 4),
                 "n_features": len(fcols), "strict": strict}


def task_a_strict(df, X, y):
    """时间分块 + 30 分钟间隔 + 告警事件不跨集合的严格评估。"""
    d = pd.DataFrame({"ts": df["ts"].values, "deviceId": df["deviceId"].values,
                      "alarm": df["alarm"].values})
    ts_sorted = np.sort(d["ts"].values)
    cut = ts_sorted[int(len(ts_sorted) * 0.7)]
    emb = np.timedelta64(30, "m")

    al = d[d["alarm"] > 0].sort_values(["deviceId", "alarm", "ts"]).copy()
    al["gap"] = al.groupby(["deviceId", "alarm"])["ts"].diff().dt.total_seconds()
    al["ep"] = ((al["gap"].isna()) | (al["gap"] > 120)).groupby(
        [al["deviceId"], al["alarm"]]).cumsum()
    al["key"] = (al["deviceId"].astype(str) + "|" + al["alarm"].astype(str)
                 + "|" + al["ep"].astype(str))
    key = pd.Series(index=d.index, dtype=object)
    key.loc[al.index] = al["key"]
    d["key"] = key
    bounds = al.groupby("key")["ts"].agg(["min", "max"])
    cross = set(bounds[(bounds["min"] < cut) & (bounds["max"] > cut)].index)
    d["cross"] = d["key"].isin(cross).fillna(False)

    tr = (d["ts"].values < cut) & (~d["cross"].values)
    te = (d["ts"].values > cut + emb) & (~d["cross"].values)
    out = {}
    models = {
        "随机森林": RandomForestClassifier(n_estimators=300, min_samples_leaf=2,
                                       class_weight="balanced_subsample",
                                       random_state=RNG, n_jobs=-1),
        "直方图梯度提升": HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1,
                                                   random_state=RNG),
    }
    for name, model in models.items():
        evaluate(name, model, X.values[tr], y[tr], X.values[te], y[te], out)
    return {
        "cut": str(pd.Timestamp(cut)), "embargo_min": 30,
        "train_n": int(tr.sum()), "test_n": int(te.sum()),
        "dropped_crossing_episodes": int(len(cross)),
        "dropped_crossing_readings": int(d["cross"].sum()),
        "models": out,
    }


def main():
    res: dict = {}
    print("=" * 78)
    print("构建读数级特征矩阵 ...")
    df, ctx_cols = build_readings()
    print(f"  样本 {len(df)} 行；上下文列 {len(ctx_cols)} 个")
    y = df["alarm"].astype(int).values

    # ---------------------------------------------------------------- A. 同分布划分
    X = feature_matrix(df, ctx_cols, with_variable=True)
    X = X.fillna(X.median(numeric_only=True)).fillna(0)
    print(f"  特征矩阵 {X.shape}，缺失值已用列中位数填充")
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=RNG, stratify=y)
    print(f"  训练 {len(Xtr)} / 测试 {len(Xte)}")

    models = {
        "基线(多数类)": DummyClassifier(strategy="most_frequent"),
        "逻辑回归": make_pipeline(StandardScaler(with_mean=True),
                                 LogisticRegression(max_iter=2000, class_weight="balanced",
                                                    )),
        "随机森林": RandomForestClassifier(n_estimators=300, min_samples_leaf=2,
                                         class_weight="balanced_subsample",
                                         random_state=RNG, n_jobs=-1),
        "直方图梯度提升": HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1,
                                                    random_state=RNG),
    }
    out_a, preds = {}, {}
    print("\n[A] 读数级多分类（随机 70/30 划分）")
    for name, m in models.items():
        preds[name] = evaluate(name, m, Xtr, ytr, Xte, yte, out_a)
    res["task_a_random_split"] = out_a

    best_name = max([k for k in out_a if k != "基线(多数类)"],
                    key=lambda k: out_a[k]["macro_f1"])
    best_pred = preds[best_name]
    cm = confusion_matrix(yte, best_pred, labels=range(6))
    res["task_a_confusion"] = {"model": best_name, "matrix": cm.tolist()}
    print(f"  最优模型: {best_name}")

    # 不使用变量身份（只看运行状态与取值大小）的对照
    Xs = feature_matrix(df, ctx_cols, with_variable=False)
    Xs = Xs.fillna(Xs.median(numeric_only=True)).fillna(0)
    Xtr2, Xte2, ytr2, yte2 = train_test_split(Xs, y, test_size=0.3, random_state=RNG, stratify=y)
    out_no = {}
    print("\n[A2] 去掉变量身份（仅数值+运行状态上下文）")
    for name in ["逻辑回归", "随机森林"]:
        m = (make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000,
                                                                class_weight="balanced"))
             if name == "逻辑回归" else
             RandomForestClassifier(n_estimators=200, min_samples_leaf=2, random_state=RNG,
                                    n_jobs=-1, class_weight="balanced_subsample"))
        evaluate(name + "(无变量身份)", m, Xtr2, ytr2, Xte2, yte2, out_no)
    res["task_a_no_variable_id"] = out_no

    # 严格评估：时间分块 + 30 分钟间隔 + 告警事件不跨集合
    print("\n[A3] 时间分块 + 事件隔离（30 分钟间隔）")
    res["task_a_time_episode"] = task_a_strict(df, X, y)

    # ---------------------------------------------------------------- B. 留一设备
    print("\n[B] 留一设备交叉验证（随机森林）")
    out_b = {}
    for dev in sorted(df["device"].unique()):
        tr = df["device"] != dev
        Xa = X.values
        m = RandomForestClassifier(n_estimators=200, min_samples_leaf=2, random_state=RNG,
                                   n_jobs=-1)
        pred = evaluate(f"训练其余→测试车-{dev}", m, Xa[tr.values], y[tr.values],
                        Xa[~tr.values], y[~tr.values], out_b)
        m2 = LogisticRegression(max_iter=1000, class_weight="balanced")
        sc = StandardScaler().fit(Xa[tr.values])
        evaluate(f"  (逻辑回归) 测试车-{dev}", m2, sc.transform(Xa[tr.values]), y[tr.values],
                 sc.transform(Xa[~tr.values]), y[~tr.values], out_b)
    res["task_b_leave_one_device"] = out_b

    # ---------------------------------------------------------------- C. 异常检测一致性
    print("\n[C] 无监督异常检测与告警标签的一致性")
    cons = {}
    for var in ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
                "ACCELERATION Z", "EXTERNAL BATTERY", "RELATIVE THROTTLE POSITION"]:
        s = df.loc[df["variable"] == var, ["value", "alarm"]].dropna(subset=["value"])
        if len(s) < 100:
            continue
        mu, sd = s["value"].mean(), s["value"].std()
        flag = ((s["value"] - mu).abs() > 3 * sd)
        lab = s["alarm"] > 0
        tp = int((flag & lab).sum())
        cons[var] = {
            "n": int(len(s)), "outliers": int(flag.sum()), "alarm_rate": round(float(lab.mean()), 3),
            "precision": round(float(tp / flag.sum()), 3) if flag.sum() else None,
            "recall": round(float(tp / lab.sum()), 3) if lab.sum() else None,
            "outlier_in_alarm_share": round(float(tp / s["alarm"].gt(0).sum()), 4) if lab.sum() else None,
        }
    res["task_c_consistency"] = cons
    for k, v in cons.items():
        print(f"    {k:32s} 3σ异常 {v['outliers']:4d}  精确率 {v['precision']}  召回率 {v['recall']}")

    # ---------------------------------------------------------------- D. 提前预警
    print("\n[D] 提前预警：过去 10 分钟运行状态 -> 未来 10 分钟是否出现 C4 告警片段")
    out_d, d_meta = task_d_early_warning(df)
    res["task_d_early_warning"] = out_d
    res["task_d_strict"] = d_meta.pop("strict", {})
    res["task_d_meta"] = {k: v for k, v in d_meta.items() if k != "curves"}

    # ---------------------------------------------------------------- 图14 模型对比
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.1),
                             gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.26})
    ax = axes[0]
    names = list(out_a.keys()) + [k for k in out_no if k not in out_a]
    metrics = ["acc", "macro_f1", "weighted_f1"]
    colors = ["#1f77b4", "#e8743b", "#2ca02c"]
    xx = np.arange(len(names))
    for i, (m, c) in enumerate(zip(metrics, colors)):
        vals = []
        for n in names:
            src = out_a.get(n) or out_no.get(n)
            vals.append(src[m])
        ax.bar(xx + (i - 1) * 0.26, vals, width=0.24, color=c,
               label={"acc": "准确率", "macro_f1": "宏平均F1", "weighted_f1": "加权F1"}[m])
    ax.set_xticks(xx)
    ax.set_xticklabels([n.replace("(", "\n(") for n in names], fontsize=8, rotation=18)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("得分")
    ax.legend(fontsize=8.5, loc="lower right")
    ax.set_title("(a) 读数级多分类：不同模型表现", loc="left", fontsize=11)

    ax = axes[1]
    devs = sorted({k.split("测试车-")[-1] for k in out_b if "测试车-" in k})
    rf = [out_b[f"训练其余→测试车-{d}"]["macro_f1"] for d in devs]
    lr = [out_b[f"  (逻辑回归) 测试车-{d}"]["macro_f1"] for d in devs]
    xx = np.arange(len(devs))
    ax.bar(xx - 0.18, rf, width=0.34, color="#2ca02c", label="随机森林")
    ax.bar(xx + 0.18, lr, width=0.34, color="#4c9be8", label="逻辑回归")
    for i, (a, b) in enumerate(zip(rf, lr)):
        ax.text(i - 0.18, a, f"{a:.2f}", ha="center", va="bottom", fontsize=8)
        ax.text(i + 0.18, b, f"{b:.2f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(xx)
    ax.set_xticklabels([f"车-{d}" for d in devs], fontsize=9)
    ax.set_ylabel("宏平均 F1")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8.5)
    ax.set_title("(b) 留一设备泛化（未见过该车辆数据）", loc="left", fontsize=11)
    fig.suptitle("图14  模型对比与泛化能力", fontsize=14, fontweight="bold")
    savefig(fig, "fig14_模型对比.png", tight=False)

    # ---------------------------------------------------------------- 图15 混淆矩阵
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.4),
                             gridspec_kw={"width_ratios": [1, 1], "wspace": 0.28})
    for ax, (mat, sub, title) in zip(axes, [
        (cm, True, f"(a) {best_name}（行=真实，列=预测，行归一化）"),
        (cm, False, f"(b) {best_name}（绝对样本数）"),
    ]):
        m = mat / mat.sum(axis=1, keepdims=True) if sub else mat
        im = ax.imshow(m, cmap="Blues", vmin=0, vmax=m.max())
        ax.set_xticks(range(6))
        ax.set_xticklabels([ALARM_SHORT[c] for c in range(6)], fontsize=8.5, rotation=18)
        ax.set_yticks(range(6))
        ax.set_yticklabels([ALARM_SHORT[c] for c in range(6)], fontsize=8.5)
        for i in range(6):
            for j in range(6):
                v = m[i, j]
                txt = f"{v:.2f}" if sub else f"{int(v)}"
                ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                        color="white" if v > m.max() * 0.55 else "#333")
        ax.grid(False)
        ax.set_xlabel("预测类别")
        ax.set_ylabel("真实类别")
        ax.set_title(title, loc="left", fontsize=10.5)
    fig.suptitle("图15  告警分类混淆矩阵", fontsize=14, fontweight="bold")
    savefig(fig, "fig15_混淆矩阵.png", tight=False)

    # ---------------------------------------------------------------- 图16 特征重要性
    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, random_state=RNG,
                                n_jobs=-1, class_weight="balanced_subsample")
    rf.fit(Xtr, ytr)
    imp = pd.Series(rf.feature_importances_, index=X.columns).sort_values(ascending=False)
    top = imp.head(18)[::-1]
    fig, ax = plt.subplots(figsize=(9.5, 7))
    ax.barh([t.replace("var_", "变量:").replace("dev_", "设备:") for t in top.index],
            top.values, color="#4c78a8")
    ax.set_xlabel("随机森林特征重要性（Gini 下降）")
    ax.set_title("图16  告警识别的关键特征 Top18", fontsize=13, fontweight="bold")
    ax.tick_params(axis="y", labelsize=8.5)
    savefig(fig, "fig16_特征重要性.png")
    res["feature_importance_top"] = {k: round(float(v), 5) for k, v in top[::-1].items()}

    # ---------------------------------------------------------------- 图17 一致性
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.9),
                             gridspec_kw={"width_ratios": [1.3, 1], "wspace": 0.25})
    ax = axes[0]
    ks = list(cons.keys())
    xx = np.arange(len(ks))
    prec = [cons[k]["precision"] or 0 for k in ks]
    rec = [cons[k]["recall"] or 0 for k in ks]
    ax.bar(xx - 0.18, prec, width=0.34, color="#e8743b", label="精确率")
    ax.bar(xx + 0.18, rec, width=0.34, color="#4c9be8", label="召回率")
    ax.set_xticks(xx)
    ax.set_xticklabels([vname(k).split("(")[0] for k in ks], fontsize=8, rotation=20)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("得分")
    ax.legend(fontsize=8.5)
    ax.set_title("(a) 3σ 异常点对告警的判别能力", loc="left", fontsize=11)

    ax = axes[1]
    var = "ACCELERATION Y"
    s = df.loc[df["variable"] == var, ["value", "alarm"]].dropna(subset=["value"])
    mu, sd = s["value"].mean(), s["value"].std()
    ax.hist(s.loc[((s["value"] - mu).abs() <= 3 * sd), "value"], bins=60, alpha=0.5,
            color="#8f9aa6", label="3σ 之内")
    ax.hist(s.loc[((s["value"] - mu).abs() > 3 * sd), "value"], bins=60, alpha=0.75,
            color="#c0392b", label="3σ 之外")
    ax.axvline(mu, color="#333", lw=0.8, ls="--")
    ax.set_xlabel(vname(var))
    ax.set_ylabel("频数")
    ax.legend(fontsize=8.5)
    ax.set_title("(b) 以横向加速度为例：3σ 边界与分布形态", loc="left", fontsize=11)
    fig.suptitle("图17  统计异常检测与告警标签的一致性检验", fontsize=14, fontweight="bold")
    savefig(fig, "fig17_异常一致性.png", tight=False)

    # ---------------------------------------------------------------- 图18 提前预警
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 4.9),
                             gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.26})
    ax = axes[0]
    for i, (name, (rec_c, prec_c)) in enumerate(d_meta["curves"].items()):
        ax.plot(rec_c, prec_c, lw=1.8, color=["#4c9be8", "#2ca02c"][i % 2],
                label=f"{name} (AUC={out_d[name]['auc']})")
    ax.axhline(d_meta["test_pos_rate"], color="#999", ls="--", lw=1,
               label=f"随机基线 ({d_meta['test_pos_rate']:.2f})")
    ax.set_xlabel("召回率")
    ax.set_ylabel("精确率")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8.5)
    ax.set_title("(a) 未来 10 分钟 C4 告警预警的 PR 曲线", loc="left", fontsize=11)

    ax = axes[1]
    imp_d = pd.Series(d_meta["importance"]).sort_values()
    ax.barh(imp_d.index, imp_d.values, color="#8e7cc3")
    ax.set_xlabel("随机森林特征重要性")
    ax.set_title("(b) 预警模型关键特征（过去 10 分钟统计量）", loc="left", fontsize=11)
    ax.tick_params(axis="y", labelsize=8.5)
    fig.suptitle("图18  提前预警模型的可行性与特征", fontsize=14, fontweight="bold")
    savefig(fig, "fig18_提前预警.png", tight=False)

    # ---------------------------------------------------------------- 类别阈值表
    ranges = {}
    for var in ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
                "ACCELERATION Z", "EXTERNAL BATTERY", "INTERNAL BATTERY"]:
        s = df.loc[df["variable"] == var, ["value", "alarm"]].dropna(subset=["value"])
        if len(s) < 100:
            continue
        ranges[var] = {int(c): {"n": int(len(g)), "min": round(float(g["value"].min()), 2),
                                "median": round(float(g["value"].median()), 2),
                                "max": round(float(g["value"].max()), 2)}
                       for c, g in s.groupby("alarm") if len(g) >= 5}
    res["class_value_ranges"] = ranges

    dump_json(res)
    print("\n04 建模完成。最优模型:", best_name)
    print(classification_report(yte, best_pred, labels=range(6),
                                target_names=[ALARM_CN[c] for c in range(6)], zero_division=0))


if __name__ == "__main__":
    main()


