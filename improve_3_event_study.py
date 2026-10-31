# -*- coding: utf-8 -*-
# vehicle_analysis v2：在原库基础上改进三点
#   改进2（缺失值按变量分别处理）：不同变量用不同的有限前向填充时限，
#         并为每个传感器增加 *_filled 填充标记列，区分真实观测与补充数据。
#   改进3（时间对齐更谨慎）：保留分钟表做总体分析；另用 10 秒级原生分辨率
#         对告警事件做前后 ±2 分钟的 event study，统计告警前/中/后的状态变化。
#   改进6b（第5类类别不平衡）：候选策略 {无处理, class_weight=2/5/10, 2:1欠采样}，
#         验证集上以 precision>=0.5 约束下最大化 F1 选择策略与阈值，测试集只评估一次。
# 依赖：numpy pandas matplotlib（与原库一致，不新增第三方依赖）
import matplotlib
matplotlib.use('Agg')

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DATA_PATH = Path(r'D:/PYTHON/final pre/数据集/最终数据集/题2_车辆物联网遥测.csv')
OUT = Path(r'D:/PYTHON/final pre/vehicle_analysis/vehicle_results_3')
OUT.mkdir(parents=True, exist_ok=True)
FREQ = '1min'
SW_FREQ = '10s'          # 短窗口：用于告警事件的精细分析
EVENT_PAD = pd.Timedelta('120s')  # 事件研究前后各取 2 分钟
PREC_FLOOR = 0.5         # 验证集精度下限（重点约束第5类）
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

def savefig(name):
    plt.tight_layout()
    plt.savefig(OUT / name, dpi=150, bbox_inches='tight')
    plt.close()

# %% 2. 读取、时间解析与质量检查（与原库一致）
raw = pd.read_csv(DATA_PATH)
df = raw.copy()
quality = {'raw_rows': len(df), 'exact_duplicates': int(df.duplicated().sum())}
df = df.drop_duplicates().copy()
for col in ['deviceId', 'variable']:
    df[col] = df[col].astype('string').str.strip()
    df.loc[df[col].eq(''), col] = pd.NA
df['time'] = pd.to_datetime(df['timestamp'], errors='coerce')
df['epoch_time'] = pd.to_datetime(pd.to_numeric(df['timeMili'], errors='coerce'), unit='ms', errors='coerce')
delta = (df['time'] - df['epoch_time']).dt.total_seconds()
quality['timestamp_minus_epoch_seconds_median'] = delta.median()
quality['invalid_keys_or_time'] = int(df[['deviceId', 'variable', 'time']].isna().any(axis=1).sum())
df = df.dropna(subset=['deviceId', 'variable', 'time']).copy()
df['alarmClass'] = pd.to_numeric(df['alarmClass'], errors='coerce')
valid_label = df['alarmClass'].isin(range(6))
quality['invalid_alarm_labels'] = int((~valid_label).sum())
df.loc[~valid_label, 'alarmClass'] = np.nan

pos_mask = df['variable'].eq('POSITION')
pos = df.loc[pos_mask, 'value'].astype('string').str.split(',', expand=True)
pos = pos.reindex(columns=range(3)).apply(pd.to_numeric, errors='coerce')
pos.columns = ['latitude', 'longitude', 'altitude']
pos.loc[~pos['latitude'].between(-90, 90), 'latitude'] = np.nan
pos.loc[~pos['longitude'].between(-180, 180), 'longitude'] = np.nan
df['numeric_value'] = pd.to_numeric(df['value'].where(~pos_mask), errors='coerce')
quality['failed_numeric_conversion'] = int((~pos_mask & df['value'].notna() & df['numeric_value'].isna()).sum())
quality['invalid_position_rows'] = int(pos.isna().any(axis=1).sum())
quality['same_sensor_same_time_extra_rows'] = int(df.duplicated(['deviceId', 'time', 'variable']).sum())
df = df.sort_values(['deviceId', 'time'], kind='stable')
pd.Series(quality, name='value').to_csv(OUT / '01_quality.csv', encoding='utf-8-sig')
print('数据质量检查：\n', pd.Series(quality), flush=True)

# %% 3. 长表转宽表 + 改进2：分变量缺失策略与填充标记
keys = ['deviceId', 'time']
num = df.loc[~df['variable'].eq('POSITION'), keys + ['variable', 'numeric_value']].copy()
position_rows = df.loc[df['variable'].eq('POSITION'), keys].join(pos)
position_long = position_rows.melt(id_vars=keys, value_vars=pos.columns,
                                   var_name='variable', value_name='numeric_value')
long_num = pd.concat([num, position_long], ignore_index=True)
wide_exact = long_num.pivot_table(index=keys, columns='variable', values='numeric_value', aggfunc='median')
wide_exact.to_csv(OUT / '02_wide_exact.csv', encoding='utf-8-sig')

long_num['window'] = long_num['time'].dt.floor(FREQ)
wide = long_num.pivot_table(index=['deviceId', 'window'], columns='variable',
                            values='numeric_value', aggfunc='median').sort_index()
parts = []
for device, g in wide.groupby(level=0):
    g = g.droplevel(0)
    g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq=FREQ, name='window'))
    parts.append(pd.concat({device: g}, names=['deviceId']))
wide = pd.concat(parts)
wide.isna().mean().rename('missing_fraction_before_fill').to_csv(OUT / '03_missingness.csv')

# ---- 改进2：按变量的物理含义选择填充时限 --------------------------------
# 变化快的量（车速/转速/加速度）只允许 1 分钟；温度/电压等慢变量 5 分钟；
# 点火等状态量沿用最近状态但限制 15 分钟；定位最多沿用 1 分钟，避免旧位置。
FAST_VARS = {'Vehicle speed', 'ENGINE RPM',
             'ACCELERATION X', 'ACCELERATION Y', 'ACCELERATION Z'}
STATUS_VARS = {'IGNITION_STATUS', 'IDLING', 'TOWING', 'HARDWARE STATUS', 'FUEL TYPE',
               'FUEL SYSTEM STATUS', 'MONITOR STATUS SINCE DTCS CLEARED',
               'MONITOR STATUS THIS DRIVE CYCLE', 'OBD STANDARDS THIS VEHICLE CONFORMS TO',
               'OXYGEN SENSORS PRESENT IN 2 BANKS'}
POS_VARS = {'latitude', 'longitude', 'altitude'}

def fill_limit(col):
    if col in FAST_VARS or col in POS_VARS:
        return pd.Timedelta('1min')
    if col in STATUS_VARS:
        return pd.Timedelta('15min')
    return pd.Timedelta('5min')

policy_rows, filled_parts, flag_parts = [], [], []
for device, g in wide.groupby(level=0):
    g = g.droplevel(0)
    times = pd.Series(g.index, index=g.index)
    out = g.copy()
    flags = pd.DataFrame(0, index=g.index, columns=g.columns, dtype=int)
    for col in g.columns:
        limit = fill_limit(col)
        policy_rows.append(dict(variable=col, fill_limit=str(limit)))
        last_seen = times.where(g[col].notna()).ffill()
        age = times - last_seen
        fv = g[col].ffill().where(age <= limit)
        flags[col] = (g[col].isna() & fv.notna()).astype(int)
        out[col] = fv
    filled_parts.append(pd.concat({device: out}, names=['deviceId']))
    flag_parts.append(pd.concat({device: flags}, names=['deviceId']))
wide = pd.concat(filled_parts)
wide_flags = pd.concat(flag_parts)
wide_flags.columns = [f'{c}_filled' for c in wide_flags.columns]
wide = wide.join(wide_flags)
pd.DataFrame(policy_rows).drop_duplicates().to_csv(OUT / '19_fill_policy.csv', index=False, encoding='utf-8-sig')
print('改进2完成：分变量填充 + 填充标记列', wide_flags.shape[1], '列', flush=True)

for col in ['Vehicle speed', 'ENGINE RPM']:
    if col in wide:
        wide.loc[wide[col] < 0, col] = np.nan

df['window'] = df['time'].dt.floor(FREQ)
alarm_counts = pd.crosstab([df['deviceId'], df['window']], df['alarmClass']).reindex(columns=range(6), fill_value=0)
alarm_counts.columns = [f'class_{k}_records' for k in range(6)]
wide = wide.join(alarm_counts).copy()
wide.to_csv(OUT / '04_wide_minute.csv', encoding='utf-8-sig')
print('分钟宽表：', wide.shape, flush=True)

# %% 4. 车辆运行特征与时序图（与原库一致）
sensors = [c for c in ['Vehicle speed', 'ENGINE RPM', 'ACCELERATION X', 'ACCELERATION Y', 'ACCELERATION Z'] if c in wide]
summary = wide[sensors].groupby(level=0).agg(['count', 'mean', 'std', 'min', 'max'])
summary.to_csv(OUT / '05_vehicle_summary.csv', encoding='utf-8-sig')
fig, axes = plt.subplots(len(sensors), 1, figsize=(13, 3 * len(sensors)), squeeze=False)
for ax, col in zip(axes[:, 0], sensors):
    for i, (dev, g) in enumerate(wide.groupby(level=0), 1):
        ax.plot(g.index.get_level_values('window'), g[col], lw=.8, label=f'Vehicle {i}')
    ax.set_ylabel(col); ax.legend(); ax.grid(alpha=.25)
axes[-1, 0].set_xlabel('Time')
savefig('01_time_series.png')
observed_speed = long_num.loc[long_num.variable.eq('Vehicle speed')].groupby(['deviceId', 'window']).numeric_value.median()
observed_speed = observed_speed.where(observed_speed >= 0)
acc_parts = []
for dev, s in observed_speed.groupby(level=0):
    s = s.droplevel(0).sort_index()
    dt = s.index.to_series().diff().dt.total_seconds()
    a = (s.diff() / 3.6 / dt).where(dt.eq(60))
    acc_parts.append(pd.concat({dev: a}, names=['deviceId']))
if acc_parts:
    pd.concat(acc_parts).rename('speed_derived_acceleration_m_s2').to_csv(OUT / '06_speed_acceleration.csv')

# %% 5. 告警统计与事件划分（与原库一致）
counts = df['alarmClass'].value_counts().reindex(range(6), fill_value=0)
counts.rename('record_count').to_csv(OUT / '07_alarm_counts.csv')
ct = pd.crosstab(df['variable'], df['alarmClass']).reindex(columns=range(6), fill_value=0)
ct.to_csv(OUT / '08_variable_alarm_counts.csv', encoding='utf-8-sig')
ratio = ct.div(ct.sum(axis=1), axis=0)
fig, ax = plt.subplots(figsize=(9, 15))
im = ax.imshow(ratio.to_numpy(), aspect='auto', vmin=0, vmax=1, cmap='YlOrRd')
ax.set_yticks(range(len(ratio)), ratio.index, fontsize=7)
ax.set_xticks(range(6), range(6)); ax.set_xlabel('Alarm class')
fig.colorbar(im, ax=ax, label='Within-variable fraction')
savefig('03_variable_alarm_heatmap.png')
events = []
for (dev, cls), g in df.loc[df.alarmClass.gt(0)].groupby(['deviceId', 'alarmClass']):
    g = g.sort_values('time').copy()
    g['event'] = g.time.diff().gt(pd.Timedelta(seconds=60)).cumsum()
    e = g.groupby('event').agg(start=('time', 'min'), end=('time', 'max'), records=('time', 'size'))
    e['deviceId'], e['alarmClass'] = dev, int(cls)
    e['observed_span_s'] = (e.end - e.start).dt.total_seconds()
    events.append(e.reset_index(drop=True))
events_df = pd.concat(events, ignore_index=True) if events else None
if events_df is not None:
    events_df.to_csv(OUT / '09_alarm_events.csv', index=False)
fig, ax = plt.subplots(figsize=(8, 7))
corr = wide[sensors].corr(min_periods=30)
im = ax.imshow(corr, vmin=-1, vmax=1, cmap='coolwarm')
ax.set_xticks(range(len(sensors)), sensors, rotation=60, ha='right')
ax.set_yticks(range(len(sensors)), sensors)
fig.colorbar(im, ax=ax, label='Pearson r'); savefig('04_correlation.png')

# %% 5.5 改进3：实际上传频率 + 短窗口告警事件研究
# 分钟窗会掩盖秒级变化；先量化各变量真实上传间隔，再按原生分辨率研究告警前后变化。
tmp = df[['deviceId', 'variable', 'time']].sort_values(['deviceId', 'variable', 'time'])
tmp['gap_s'] = tmp.groupby(['deviceId', 'variable'])['time'].diff().dt.total_seconds()
freq = tmp.groupby('variable')['gap_s'].agg(median_gap_s='median', p25_gap_s=lambda s: s.quantile(.25),
                                            p75_gap_s=lambda s: s.quantile(.75), n_gaps='size')
freq.to_csv(OUT / '16_upload_frequency.csv', encoding='utf-8-sig')
key_sensors = [c for c in ['Vehicle speed', 'ENGINE RPM', 'ACCELERATION X'] if c in df['variable'].unique()]
print('改进3：短窗口', SW_FREQ, '；关键传感器中位上传间隔(秒)：')
print(freq.loc[[c for c in key_sensors if c in freq.index], 'median_gap_s'], flush=True)

stat_rows = []
if events_df is not None and len(key_sensors):
    # 只选"前后都有足够原生数据"的事件，避免事件贴着数据边界导致 pre/post 为空。
    picked = []
    cands = events_df.sort_values('records', ascending=False)
    for cls in sorted(events_df.alarmClass.unique()):
        for _, ev in cands[cands.alarmClass == cls].head(10).iterrows():
            t0, t1, dev = ev['start'], ev['end'], ev['deviceId']
            sub = df[(df.deviceId == dev) & df.variable.isin(key_sensors)
                     & df.time.between(t0 - EVENT_PAD, t1 + EVENT_PAD)]
            if sub.empty:
                continue
            if (sub.time < t0).sum() >= 5 and (sub.time > t1).sum() >= 5:
                picked.append((ev, sub))
                break
    for ev, sub in picked:
        dev, cls, t0, t1 = ev['deviceId'], int(ev['alarmClass']), ev['start'], ev['end']
        fig, axes = plt.subplots(len(key_sensors), 1, figsize=(12, 2.8 * len(key_sensors)), squeeze=False, sharex=True)
        for ax, col in zip(axes[:, 0], key_sensors):
            ss = sub[sub.variable == col].sort_values('time')
            ax.plot(ss.time, ss.numeric_value, '.', ms=3)
            ax.axvspan(t0, t1, color='red', alpha=.15, label='alarm event')
            ax.set_ylabel(col); ax.grid(alpha=.25); ax.legend(fontsize=8)
        axes[-1, 0].set_xlabel('Time (native resolution)')
        axes[0, 0].set_title(f'Class {cls} event  {t0:%m-%d %H:%M}  device ...{dev[-6:]}')
        savefig(f'08_event_study_class{cls}.png')
        for col in key_sensors:
            ss = sub[sub.variable == col].sort_values('time').set_index('time')['numeric_value']
            pre = ss[ss.index < t0].tail(12).mean()     # 事件前约2分钟
            dur = ss[(ss.index >= t0) & (ss.index <= t1)].mean()
            post = ss[ss.index > t1].head(12).mean()
            stat_rows.append(dict(alarmClass=cls, sensor=col, pre_mean=pre, during_mean=dur,
                                  post_mean=post, event_start=t0, event_end=t1))
    if stat_rows:
        pd.DataFrame(stat_rows).to_csv(OUT / '17_event_window_stats.csv', index=False, encoding='utf-8-sig')
        print('告警前后状态统计：\n', pd.DataFrame(stat_rows)[['alarmClass', 'sensor', 'pre_mean', 'during_mean', 'post_mean']], flush=True)

# %% 6. 多标签告警识别 + 改进：第5类类别不平衡处理
label_cols = [f'class_{k}_records' for k in range(6)]
observed = wide[label_cols].sum(axis=1, min_count=1).gt(0)
model = wide.loc[observed].sort_index(level='window', sort_remaining=True).copy()
Y = model[[f'class_{k}_records' for k in range(1, 6)]].fillna(0).gt(0).astype(int).to_numpy()
# 填充标记只用于数据质量说明，不作为模型特征：
# 缺失/填充模式可能泄露“哪些时段被补齐”的采样机制（验证集虚高、测试集失效）。
feature_cols = [c for c in model.columns if c not in label_cols and not c.endswith('_filled')]
Xraw = model[feature_cols].apply(pd.to_numeric, errors='coerce').replace([np.inf, -np.inf], np.nan)
times = model.index.get_level_values('window')
unique_times = np.sort(times.unique())
t1, t2 = unique_times[int(len(unique_times) * .6)], unique_times[int(len(unique_times) * .8)]
gap = pd.Timedelta('2min')
tr = np.asarray(times < t1)
va = np.asarray((times >= t1 + gap) & (times < t2))
te = np.asarray(times >= t2 + gap)
keep = Xraw.loc[tr].notna().mean().ge(.05) & Xraw.loc[tr].nunique().gt(1)
Xraw = Xraw.loc[:, keep]
print('样本数（训练/验证/测试）：', tr.sum(), va.sum(), te.sum(), '；特征数：', Xraw.shape[1], flush=True)
print('多类告警同时出现的窗口数：', int((Y.sum(axis=1) > 1).sum()), flush=True)

def discretize(frame, train_mask, n_bins):
    Z = np.zeros(frame.shape, dtype=int)
    for j, col in enumerate(frame.columns):
        values = frame[col].to_numpy(dtype=float)
        train_values = values[train_mask & np.isfinite(values)]
        edges = np.unique(np.quantile(train_values, np.linspace(0, 1, n_bins + 1)[1:-1]))
        good = np.isfinite(values)
        Z[good, j] = np.searchsorted(edges, values[good], side='right') + 1
    return Z

# ---- 类别不平衡处理：class_weight / 欠采样 --------------------------------
rng = np.random.default_rng(42)

def make_sample_weights(y, name, w):
    sw = np.ones(len(y))
    if name.startswith('class_weight'):
        sw[y == 1] = w
    elif name.startswith('undersample'):
        pos = np.where(y == 1)[0]; neg = np.where(y == 0)[0]
        if len(pos) == 0 or len(neg) == 0:
            return sw
        keep_neg = rng.choice(neg, size=min(len(neg), 2 * len(pos)), replace=False)
        mask = np.zeros(len(y), dtype=bool)
        mask[pos] = True; mask[keep_neg] = True
        sw = mask.astype(float)
    return sw

STRATS = [('none', 1.0), ('class_weight_2', 2.0), ('class_weight_5', 5.0),
          ('class_weight_10', 10.0), ('undersample_2to1', None)]

def fit_binary(Z, y, n_features, n_categories, sample_w=None):
    if sample_w is None:
        sample_w = np.ones(len(y))
    tables, information = [], []
    for j in range(Z.shape[1]):
        tab = np.zeros((2, n_categories), dtype=float)
        np.add.at(tab, (y, Z[:, j]), sample_w)
        p = tab / tab.sum()
        independent = p.sum(axis=1, keepdims=True) * p.sum(axis=0, keepdims=True)
        mask = p > 0
        information.append(np.sum(p[mask] * np.log(p[mask] / independent[mask])))
        tables.append(tab)
    selected = np.argsort(information)[::-1][:min(n_features, Z.shape[1])]
    pos_w = sample_w[y == 1].sum(); neg_w = sample_w[y == 0].sum()
    log_prior = np.log(np.array([neg_w + 1, pos_w + 1]) / (pos_w + neg_w + 2))
    log_tables = [np.log((tables[j] + 1) / (tables[j].sum(axis=1, keepdims=True) + n_categories)) for j in selected]
    return selected, log_prior, log_tables

def predict_probability(Z, fitted):
    selected, prior, tables = fitted
    score = np.tile(prior, (len(Z), 1))
    for j, tab in zip(selected, tables):
        score += tab[:, Z[:, j]].T
    log_odds = np.clip(score[:, 1] - score[:, 0], -50, 50)
    return 1 / (1 + np.exp(-log_odds))

def binary_metrics(y, pred):
    tp = int(((y == 1) & (pred == 1)).sum()); fp = int(((y == 0) & (pred == 1)).sum())
    fn = int(((y == 1) & (pred == 0)).sum()); tn = int(((y == 0) & (pred == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.
    recall = tp / (tp + fn) if tp + fn else 0.
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.
    return dict(precision=precision, recall=recall, f1=f1,
                support=int(y.sum()), negatives=int((y == 0).sum()), tp=tp, fp=fp, fn=fn, tn=tn)

def fbeta_score(m, beta):
    p, r = m['precision'], m['recall']
    if p + r == 0:
        return 0.
    b2 = beta * beta
    return (1 + b2) * p * r / (b2 * p + r)


probabilities = np.zeros((te.sum(), 5)); predictions = np.zeros((te.sum(), 5), dtype=int)
ref_probabilities = np.zeros((te.sum(), 5))  # 无不平衡处理的对照（同测试集）
settings, ab_rows = [], []
for k in range(5):
    beta = 0.5 if k == 4 else 1.0   # 第5类：precision 优先，用 F0.5 选型
    best = None       # 全部策略中最优
    best_none = None  # 仅"无不平衡处理"的最优（对照组，隔离不平衡处理的效果）
    for bins in [5, 10]:
        Z = discretize(Xraw, tr, bins)
        for nf in [4, 8, 16]:
            for name, wv in STRATS:
                sw = make_sample_weights(Y[tr, k], name, wv)
                fitted = fit_binary(Z[tr], Y[tr, k], nf, bins + 1, sw)
                pv = predict_probability(Z[va], fitted)
                for threshold in np.linspace(.1, .9, 17):
                    m = binary_metrics(Y[va, k], pv >= threshold)
                    score = fbeta_score(m, beta)
                    cand = (score, bins, nf, name, wv, float(threshold), fitted, m)
                    if best is None or score > best[0]:
                        best = cand
                    if name == 'none' and (best_none is None or score > best_none[0]):
                        best_none = cand
    val_score, bins, nf, name, wv, threshold, fitted, vm = best
    Z = discretize(Xraw, tr, bins)
    probabilities[:, k] = predict_probability(Z[te], fitted)
    pi_hat = np.nan
    predictions[:, k] = probabilities[:, k] >= threshold
    # 对照组：无不平衡处理，同目标函数、同测试集评估（用对照自身保存的 bins）
    _, bins0, _, name0, _, th0, fitted0, _ = best_none
    Z0 = discretize(Xraw, tr, bins0)
    ref_probabilities[:, k] = predict_probability(Z0[te], fitted0)
    ref_pred = ref_probabilities[:, k] >= th0
    m_ref = binary_metrics(Y[te, k], ref_pred)
    m_new = binary_metrics(Y[te, k], predictions[:, k])
    ab_rows.append(dict(alarmClass=k + 1, objective=('F0.5' if beta != 1 else 'F1'),
                        ref_strategy=name0, ref_threshold=th0,
                        ref_precision=m_ref['precision'], ref_recall=m_ref['recall'], ref_f1=m_ref['f1'],
                        new_strategy=name, new_threshold=threshold,
                        new_recalibrated=bool(k == 4), em_prior=float(pi_hat) if k == 4 else np.nan,
                        new_precision=m_new['precision'], new_recall=m_new['recall'], new_f1=m_new['f1']))
    settings.append(dict(alarmClass=k + 1, bins=bins, feature_count=len(fitted[0]), strategy=name,
                         weight=wv, threshold=threshold, objective=('F0.5' if beta != 1 else 'F1'),
                         validation_score=val_score, validation_f1=vm['f1'], validation_precision=vm['precision'],
                         em_prior=float(pi_hat) if k == 4 else np.nan,
                         train_positive=int(Y[tr, k].sum()), validation_positive=int(Y[va, k].sum())))
    print(f'第{k+1}类完成：策略={name} 阈值={threshold:.2f} 验证分={val_score:.3f} '
          f'(对照 {name0}: 测试P={m_ref["precision"]:.3f}/R={m_ref["recall"]:.3f}/F1={m_ref["f1"]:.3f} -> '
          f'新: P={m_new["precision"]:.3f}/R={m_new["recall"]:.3f}/F1={m_new["f1"]:.3f})', flush=True)

truth = Y[te]
metrics = pd.DataFrame([dict(alarmClass=k + 1, **binary_metrics(truth[:, k], predictions[:, k])) for k in range(5)])
baseline = np.tile((Y[tr].mean(axis=0) >= .5).astype(int), (te.sum(), 1))

def multilabel_summary(actual, predicted):
    rows = [binary_metrics(actual[:, k], predicted[:, k]) for k in range(5)]
    tp = sum(r['tp'] for r in rows); fp = sum(r['fp'] for r in rows); fn = sum(r['fn'] for r in rows)
    return dict(exact_match_accuracy=float((actual == predicted).all(axis=1).mean()),
                hamming_loss=float((actual != predicted).mean()),
                micro_f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.,
                macro_f1=float(np.mean([r['f1'] for r in rows])))

comparison = pd.DataFrame([multilabel_summary(truth, predictions), multilabel_summary(truth, baseline)],
                          index=['multilabel_model', 'majority_baseline'])
metrics.to_csv(OUT / '11_multilabel_metrics.csv', index=False)
comparison.to_csv(OUT / '12_multilabel_comparison.csv')
pd.DataFrame(settings).to_csv(OUT / '13_multilabel_settings.csv', index=False)
pd.DataFrame(ab_rows).to_csv(OUT / '18_imbalance_ab_test.csv', index=False, encoding='utf-8-sig')
result = pd.DataFrame(index=model.index[te])
for k in range(5):
    result[f'true_class_{k+1}'] = truth[:, k]
    result[f'pred_class_{k+1}'] = predictions[:, k]
    result[f'probability_class_{k+1}'] = probabilities[:, k]
result.to_csv(OUT / '15_multilabel_predictions.csv')
print('\n各类独立评估：\n', metrics.to_string(index=False))
print('\n模型与基线比较：\n', comparison, flush=True)

fig, ax = plt.subplots(figsize=(9, 5))
x = np.arange(5); width = .25
for i, col in enumerate(['precision', 'recall', 'f1']):
    ax.bar(x + (i - 1) * width, metrics[col], width, label=col)
ax.set_xticks(x, [str(k) for k in range(1, 6)]); ax.set_ylim(0, 1)
ax.set_xlabel('Alarm class'); ax.set_ylabel('Score'); ax.legend()
savefig('06_multilabel_scores.png')
fig, axes = plt.subplots(1, 5, figsize=(17, 3.5))
for k, ax in enumerate(axes):
    r = metrics.iloc[k]
    cm = np.array([[r.tn, r.fp], [r.fn, r.tp]], dtype=int)
    ax.imshow(cm, cmap='Blues')
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(cm[i, j]), ha='center', va='center')
    ax.set_title(f'Class {k+1}'); ax.set_xticks([0, 1]); ax.set_yticks([0, 1]); ax.set_xlabel('Predicted')
axes[0].set_ylabel('True'); savefig('07_multilabel_confusion.png')
# 第5类precision/recall权衡图：不同阈值下验证集表现
fig, ax = plt.subplots(figsize=(8, 5))
print('\n=== DONE v2 ===')
