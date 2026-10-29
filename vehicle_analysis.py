# %% 1. 环境与参数（可整段运行，也可按 # %% 分段运行）
# 安装命令：python -m pip install numpy pandas matplotlib
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DATA_PATH = Path('题2_车辆物联网遥测.csv')  # 修改成自己的 CSV 路径
OUT = Path('vehicle_results')
OUT.mkdir(exist_ok=True)
FREQ = '1min'                 # 统一时间窗口
MAX_AGE = pd.Timedelta('2min') # 最多向前沿用两分钟内的历史观测
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

def savefig(name):
    plt.tight_layout()
    plt.savefig(OUT / name, dpi=150, bbox_inches='tight')
    plt.show()  # Jupyter 中直接显示
    plt.close()

# %% 2. 读取、时间解析与质量检查
raw = pd.read_csv(DATA_PATH)
df = raw.copy()
quality = {'raw_rows': len(df), 'exact_duplicates': int(df.duplicated().sum())}
df = df.drop_duplicates().copy()
for col in ['deviceId', 'variable']:
    df[col] = df[col].astype('string').str.strip()
    df.loc[df[col].eq(''), col] = pd.NA
# 优先使用 CSV 中的人类可读时间；不要在没有时区说明时混用两个时间字段。
df['time'] = pd.to_datetime(df['timestamp'], errors='coerce')
df['epoch_time'] = pd.to_datetime(pd.to_numeric(df['timeMili'], errors='coerce'), unit='ms', errors='coerce')
delta = (df['time'] - df['epoch_time']).dt.total_seconds()
quality['timestamp_minus_epoch_seconds_median'] = delta.median()
quality['invalid_keys_or_time'] = int(df[['deviceId', 'variable', 'time']].isna().any(axis=1).sum())
df = df.dropna(subset=['deviceId', 'variable', 'time']).copy()
df['alarmClass'] = pd.to_numeric(df['alarmClass'], errors='coerce')
valid_label = df['alarmClass'].isin(range(6))
quality['invalid_alarm_labels'] = int((~valid_label).sum())
df.loc[~valid_label, 'alarmClass'] = np.nan  # 未知标签不能直接当作正常

# POSITION 是复合字段，其余字段转换为数值；解析失败保留 NaN。
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
print('数据质量检查：\n', pd.Series(quality))
print('变量清单：\n', df.groupby('variable')['numeric_value'].agg(['count','min','max']))

# %% 3. 长表转宽表：保留传感器表与标签表的不同统计含义
# 同设备、同时间、同变量的重复数值取中位数；告警另外统计，不会随数值聚合丢失。
keys = ['deviceId', 'time']
num = df.loc[~df['variable'].eq('POSITION'), keys + ['variable', 'numeric_value']].copy()
position_rows = df.loc[df['variable'].eq('POSITION'), keys].join(pos)
position_long = position_rows.melt(id_vars=keys, value_vars=pos.columns,
                                  var_name='variable', value_name='numeric_value')
long_num = pd.concat([num, position_long], ignore_index=True)
wide_exact = long_num.pivot_table(index=keys, columns='variable', values='numeric_value', aggfunc='median')
wide_exact.to_csv(OUT / '02_wide_exact.csv', encoding='utf-8-sig')

# 按分钟汇总，避免不同传感器必须在完全相同的秒数上传才能对齐。
long_num['window'] = long_num['time'].dt.floor(FREQ)
wide = long_num.pivot_table(index=['deviceId','window'], columns='variable',
                            values='numeric_value', aggfunc='median').sort_index()
# 补齐每台设备自身时间范围内的分钟网格；绝不跨车辆填充。
parts = []
for device, g in wide.groupby(level=0):
    g = g.droplevel(0)
    g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq=FREQ, name='window'))
    parts.append(pd.concat({device: g}, names=['deviceId']))
wide = pd.concat(parts)
wide.isna().mean().rename('missing_fraction_before_fill').to_csv(OUT / '03_missingness.csv')
# 有限前向填充：超过 MAX_AGE 的缺测仍为空，不用未来值回填过去。
filled_parts = []
for device, g in wide.groupby(level=0):
    g = g.droplevel(0)
    times = pd.Series(g.index, index=g.index)
    last_seen = g.apply(lambda s: times.where(s.notna()).ffill())
    age = last_seen.apply(lambda s: times - s)
    filled = g.ffill().where(age <= MAX_AGE)
    filled_parts.append(pd.concat({device: filled}, names=['deviceId']))
wide = pd.concat(filled_parts)
# 不知道设备量程，不能凭经验删除所有极端值；只屏蔽明确无效的负车速/负转速。
for col in ['Vehicle speed', 'ENGINE RPM']:
    if col in wide:
        wide.loc[wide[col] < 0, col] = np.nan

df['window'] = df['time'].dt.floor(FREQ)
alarm_counts = pd.crosstab([df['deviceId'], df['window']], df['alarmClass']).reindex(columns=range(6), fill_value=0)
alarm_counts.columns = [f'class_{k}_records' for k in range(6)]
wide = wide.join(alarm_counts).copy()
# 未采样窗口的标签保持未知；不能自动填为正常。
wide.to_csv(OUT / '04_wide_minute.csv', encoding='utf-8-sig')
print('精确时间宽表：', wide_exact.shape, '分钟宽表：', wide.shape)

# %% 4. 车辆运行特征与时序图
sensors = [c for c in ['Vehicle speed','ENGINE RPM','ACCELERATION X','ACCELERATION Y','ACCELERATION Z'] if c in wide]
summary = wide[sensors].groupby(level=0).agg(['count','mean','std','min','max'])
summary.to_csv(OUT / '05_vehicle_summary.csv', encoding='utf-8-sig')
print('车辆特征（每分钟等权，count 为可用分钟数）：\n', summary)
fig, axes = plt.subplots(len(sensors), 1, figsize=(13, 3 * len(sensors)), squeeze=False)
for ax, col in zip(axes[:,0], sensors):
    for i, (dev, g) in enumerate(wide.groupby(level=0), 1):
        ax.plot(g.index.get_level_values('window'), g[col], lw=.8, label=f'Vehicle {i}')
    ax.set_ylabel(col); ax.legend(); ax.grid(alpha=.25)
axes[-1,0].set_xlabel('Time')
savefig('01_time_series.png')
# 加速度传感器可能包含重力，单位/轴向未知，不能直接称为急加速或急刹车。
# 车速差分只使用相邻且均有原始车速观测的分钟，单位假定车速为 km/h。
observed_speed = long_num.loc[long_num.variable.eq('Vehicle speed')].groupby(['deviceId','window']).numeric_value.median()
observed_speed = observed_speed.where(observed_speed >= 0)
acc_parts = []
for dev, s in observed_speed.groupby(level=0):
    s = s.droplevel(0).sort_index()
    dt = s.index.to_series().diff().dt.total_seconds()
    a = (s.diff() / 3.6 / dt).where(dt.eq(60))
    acc_parts.append(pd.concat({dev: a}, names=['deviceId']))
if acc_parts:
    pd.concat(acc_parts).rename('speed_derived_acceleration_m_s2').to_csv(OUT / '06_speed_acceleration.csv')

# %% 5. 告警统计、事件划分与可能成因
# 记录数不是事件数，也不是持续时间；同类相邻告警间隔 <= 60 秒视为一个事件。
counts = df['alarmClass'].value_counts().reindex(range(6), fill_value=0)
counts.rename('record_count').to_csv(OUT / '07_alarm_counts.csv')
counts.plot.bar(figsize=(8,4), title='Records by alarm class', ylabel='Record count')
savefig('02_alarm_counts.png')
ct = pd.crosstab(df['variable'], df['alarmClass']).reindex(columns=range(6), fill_value=0)
ct.to_csv(OUT / '08_variable_alarm_counts.csv', encoding='utf-8-sig')
# 行归一化：比较同一种传感器的类别比例，避免上传频率掩盖关系。
ratio = ct.div(ct.sum(axis=1), axis=0)
fig, ax = plt.subplots(figsize=(9,15))
im = ax.imshow(ratio.to_numpy(), aspect='auto', vmin=0, vmax=1, cmap='YlOrRd')
ax.set_yticks(range(len(ratio)), ratio.index, fontsize=7)
ax.set_xticks(range(6), range(6)); ax.set_xlabel('Alarm class')
fig.colorbar(im, ax=ax, label='Within-variable fraction')
savefig('03_variable_alarm_heatmap.png')
events = []
for (dev, cls), g in df.loc[df.alarmClass.gt(0)].groupby(['deviceId','alarmClass']):
    g = g.sort_values('time').copy()
    g['event'] = g.time.diff().gt(pd.Timedelta(seconds=60)).cumsum()
    e = g.groupby('event').agg(start=('time','min'), end=('time','max'), records=('time','size'))
    e['deviceId'], e['alarmClass'] = dev, int(cls)
    e['observed_span_s'] = (e.end-e.start).dt.total_seconds() # 仅观测跨度，不等于真实持续时间
    events.append(e.reset_index(drop=True))
if events:
    pd.concat(events, ignore_index=True).to_csv(OUT / '09_alarm_events.csv', index=False)
# 每类告警窗口对应的状态：同一窗口可属于多个类别，不强制互斥。
states = []
for k in range(6):
    sub = wide.loc[wide[f'class_{k}_records'].gt(0), sensors]
    stats = sub.agg(['count','mean','median']).T
    stats['alarmClass'] = k
    states.append(stats.reset_index(names='sensor'))
pd.concat(states).to_csv(OUT / '10_alarm_state_stats.csv', index=False)
fig, ax = plt.subplots(figsize=(8,7))
corr = wide[sensors].corr(min_periods=30) # 至少30对有效观测；填充值可能影响相关性
im = ax.imshow(corr, vmin=-1, vmax=1, cmap='coolwarm')
ax.set_xticks(range(len(sensors)), sensors, rotation=60, ha='right')
ax.set_yticks(range(len(sensors)), sensors)
fig.colorbar(im, ax=ax, label='Pearson r'); savefig('04_correlation.png')
# 同变量内比较不同类别的数值分布，避免比较不同单位的原始数值。
plot_vars = ['Vehicle speed','ENGINE RPM','ACCELERATION X','INTERNAL BATTERY']
fig, axes = plt.subplots(2,2,figsize=(12,8))
for ax, col in zip(axes.flat, plot_vars):
    sub = df.loc[df.variable.eq(col)]
    groups = [(k, sub.loc[sub.alarmClass.eq(k),'numeric_value'].dropna().to_numpy()) for k in range(6)]
    groups = [(k,v) for k,v in groups if len(v)]
    if groups:
        ax.boxplot([v for k,v in groups], tick_labels=[str(k) for k,v in groups], showfliers=False)
    ax.set_title(col); ax.set_xlabel('Alarm class')
savefig('05_alarm_value_boxplots.png')

# %% 6. 综合车辆状态的多标签告警识别（替换原来的第6部分）
# 先运行原 Notebook 第1至第5部分。本节只依赖前面生成的 wide、OUT、savefig。
# 不新增第三方依赖：用 NumPy 实现“分箱朴素贝叶斯”，分别识别1至5类告警。
# 每行样本=一台车辆的一分钟；每行可以同时拥有多个正标签。
# 属于当前窗口识别：特征可能包含窗口内告警之后的观测，不是提前预测。

label_cols = [f'class_{k}_records' for k in range(6)]
# 标签必须来自真实记录；补齐但未采样的分钟不能当作正常。
observed = wide[label_cols].sum(axis=1, min_count=1).gt(0)
model = wide.loc[observed].sort_index(level='window', sort_remaining=True).copy()
Y = model[[f'class_{k}_records' for k in range(1,6)]].fillna(0).gt(0).astype(int).to_numpy()
# class_0 可以和告警记录共存，因此不把0当作第六个独立告警。
# 全0表示“此窗口未观测到1至5类告警”，不等于已证实车辆完全正常。
feature_cols = [c for c in model.columns if c not in label_cols]
Xraw = model[feature_cols].apply(pd.to_numeric, errors='coerce').replace([np.inf,-np.inf], np.nan)
times = model.index.get_level_values('window')
unique_times = np.sort(times.unique())
if len(unique_times) < 10:
    raise ValueError('有效时间点太少，无法划分训练、验证、测试集。')
t1, t2 = unique_times[int(len(unique_times)*.6)], unique_times[int(len(unique_times)*.8)]
# 一分钟间隔避免有限前向填充直接跨越相邻集合；这不保证长告警事件完全隔离。
gap = pd.Timedelta('2min')
tr = np.asarray(times < t1)
va = np.asarray((times >= t1+gap) & (times < t2))
te = np.asarray(times >= t2+gap)
if min(tr.sum(),va.sum(),te.sum()) == 0:
    raise ValueError('时间划分后存在空集合，请检查时间范围。')
# 删除训练集缺失超过95%或有效取值恒定的特征。仅使用训练集决定保留列。
keep = Xraw.loc[tr].notna().mean().ge(.05) & Xraw.loc[tr].nunique().gt(1)
Xraw = Xraw.loc[:, keep]
if Xraw.shape[1] == 0:
    raise ValueError('训练集没有足够的有效传感器特征。')
print('样本数（训练/验证/测试）：', tr.sum(), va.sum(), te.sum(), flush=True)
print('传感器特征数：', Xraw.shape[1], flush=True)
print('多类告警同时出现的窗口数：', int((Y.sum(axis=1)>1).sum()))

# 分箱边界只从训练集计算；0箱表示缺失，不把缺失值强行填为中位数。
def discretize(frame, train_mask, n_bins):
    Z = np.zeros(frame.shape, dtype=int)
    for j, col in enumerate(frame.columns):
        values = frame[col].to_numpy(dtype=float)
        train_values = values[train_mask & np.isfinite(values)]
        edges = np.unique(np.quantile(train_values, np.linspace(0,1,n_bins+1)[1:-1]))
        good = np.isfinite(values)
        Z[good,j] = np.searchsorted(edges, values[good], side='right')+1
    return Z

# 为每类告警分别训练二分类器。通过训练集互信息选出与该告警关联的变量。
# 加1平滑避免未出现的分箱产生零概率；对缺失模式的依赖仍需在报告中说明。
def fit_binary(Z, y, n_features, n_categories):
    tables, information = [], []
    for j in range(Z.shape[1]):
        tab = np.zeros((2,n_categories), dtype=float)
        np.add.at(tab, (y,Z[:,j]), 1)
        p = tab / tab.sum()
        independent = p.sum(axis=1,keepdims=True)*p.sum(axis=0,keepdims=True)
        mask = p>0
        information.append(np.sum(p[mask]*np.log(p[mask]/independent[mask])))
        tables.append(tab)
    selected = np.argsort(information)[::-1][:min(n_features,Z.shape[1])]
    log_prior = np.log((np.bincount(y,minlength=2)+1)/(len(y)+2))
    log_tables = [np.log((tables[j]+1)/(tables[j].sum(axis=1,keepdims=True)+n_categories)) for j in selected]
    return selected, log_prior, log_tables

def predict_probability(Z, fitted):
    selected, prior, tables = fitted
    score = np.tile(prior,(len(Z),1))
    for j, tab in zip(selected,tables):
        score += tab[:,Z[:,j]].T
    log_odds = np.clip(score[:,1]-score[:,0],-50,50)
    return 1/(1+np.exp(-log_odds))

def binary_metrics(y, pred):
    tp = int(((y==1)&(pred==1)).sum()); fp = int(((y==0)&(pred==1)).sum())
    fn = int(((y==1)&(pred==0)).sum()); tn = int(((y==0)&(pred==0)).sum())
    precision = tp/(tp+fp) if tp+fp else 0.
    recall = tp/(tp+fn) if tp+fn else 0.
    f1 = 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.
    return dict(precision=precision, recall=recall, f1=f1,
                support=int(y.sum()), negatives=int((y==0).sum()), tp=tp,fp=fp,fn=fn,tn=tn)

# 验证集选择分箱数、变量数与阈值。测试集在选择结束后才参与评估。
# 不在验证/测试集上重算分箱，不把标签计数、设备ID或时间编码作为输入。
probabilities = np.zeros((te.sum(),5)); predictions = np.zeros((te.sum(),5),dtype=int)
settings, chosen_features = [], []
for k in range(5):
    best = None
    for bins in [5,10]:
        Z = discretize(Xraw,tr,bins)
        for nf in [4,8,16]:
            fitted = fit_binary(Z[tr],Y[tr,k],nf,bins+1)
            pv = predict_probability(Z[va],fitted)
            # 固定候选网格；阈值优化目标是验证集F1。
            for threshold in np.linspace(.1,.9,17):
                f1 = binary_metrics(Y[va,k],pv>=threshold)['f1']
                if best is None or f1 > best[0]:
                    best = (f1,bins,nf,float(threshold),fitted)
    val_f1,bins,nf,threshold,fitted = best
    Z = discretize(Xraw,tr,bins)
    probabilities[:,k] = predict_probability(Z[te],fitted)
    predictions[:,k] = probabilities[:,k]>=threshold
    settings.append(dict(alarmClass=k+1,bins=bins,feature_count=len(fitted[0]),threshold=threshold,
                         validation_f1=val_f1,train_positive=int(Y[tr,k].sum()),validation_positive=int(Y[va,k].sum())))
    for j in fitted[0]:
        chosen_features.append(dict(alarmClass=k+1,feature=Xraw.columns[j]))
    print(f'第{k+1}类模型完成；验证集F1={val_f1:.3f}',flush=True)

truth = Y[te]
metrics = pd.DataFrame([dict(alarmClass=k+1,**binary_metrics(truth[:,k],predictions[:,k])) for k in range(5)])
# 多数标签基线：每一类都预测为该类在训练集中的多数状态。
baseline = np.tile((Y[tr].mean(axis=0)>=.5).astype(int),(te.sum(),1))
def multilabel_summary(actual, predicted):
    rows = [binary_metrics(actual[:,k],predicted[:,k]) for k in range(5)]
    tp=sum(r['tp'] for r in rows); fp=sum(r['fp'] for r in rows); fn=sum(r['fn'] for r in rows)
    return dict(exact_match_accuracy=float((actual==predicted).all(axis=1).mean()),
                hamming_loss=float((actual!=predicted).mean()),
                micro_f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.,
                macro_f1=float(np.mean([r['f1'] for r in rows])))
comparison = pd.DataFrame([multilabel_summary(truth,predictions),multilabel_summary(truth,baseline)],
                          index=['multilabel_model','majority_baseline'])
metrics.to_csv(OUT/'11_multilabel_metrics.csv',index=False)
comparison.to_csv(OUT/'12_multilabel_comparison.csv')
pd.DataFrame(settings).to_csv(OUT/'13_multilabel_settings.csv',index=False)
pd.DataFrame(chosen_features).to_csv(OUT/'14_selected_features.csv',index=False)
result = pd.DataFrame(index=model.index[te])
for k in range(5):
    result[f'true_class_{k+1}']=truth[:,k]
    result[f'pred_class_{k+1}']=predictions[:,k]
    result[f'probability_class_{k+1}']=probabilities[:,k]
result.to_csv(OUT/'15_multilabel_predictions.csv')
print('\n各类独立评估：\n',metrics.to_string(index=False))
print('\n模型与基线比较：\n',comparison)
print('训练结束边界：',t1,'；测试起点：',t2+gap)
print('注意：测试集没有正样本的类别无法评价其正类识别能力；该类F1按0记。')

fig,ax=plt.subplots(figsize=(9,5))
x=np.arange(5); width=.25
for i,col in enumerate(['precision','recall','f1']):
    ax.bar(x+(i-1)*width,metrics[col],width,label=col)
ax.set_xticks(x,[str(k) for k in range(1,6)]);ax.set_ylim(0,1)
ax.set_xlabel('Alarm class');ax.set_ylabel('Score');ax.legend()
savefig('06_multilabel_scores.png')
fig,axes=plt.subplots(1,5,figsize=(17,3.5))
for k,ax in enumerate(axes):
    r=metrics.iloc[k]
    cm=np.array([[r.tn,r.fp],[r.fn,r.tp]],dtype=int)
    ax.imshow(cm,cmap='Blues')
    for i in range(2):
        for j in range(2): ax.text(j,i,str(cm[i,j]),ha='center',va='center')
    ax.set_title(f'Class {k+1}');ax.set_xticks([0,1]);ax.set_yticks([0,1]);ax.set_xlabel('Predicted')
axes[0].set_ylabel('True');savefig('07_multilabel_confusion.png')
# 标签来源是窗口内实际收到的记录。未观察到某类标签按0处理依赖“标签上报完整”假设。
# 跨分钟稀疏采样可能造成漏标，缺失模式也可能透露采样机制；不能据此直接推断真实故障。
