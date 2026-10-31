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

# %% 6. 多标签告警识别：自动时间划分、历史特征与联合阈值
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
# 在完整分钟网格上计算历史特征，再选有真实标签的窗口；避免跨缺测时段错误shift。
feature_cols = [c for c in wide.columns if c not in label_cols]
base = wide[feature_cols].apply(pd.to_numeric, errors='coerce').replace([np.inf,-np.inf], np.nan).sort_index()
def temporal_features(base):
    parts = []
    for device, g in base.groupby(level='deviceId', sort=False):
        g = g.droplevel('deviceId').sort_index()
        previous = g.shift(1)
        f = pd.concat([g, previous.add_suffix('__lag1'),
                       (g-previous).add_suffix('__change1'),
                       previous.rolling(5,min_periods=2).mean().add_suffix('__past5_mean'),
                       previous.rolling(5,min_periods=2).std().add_suffix('__past5_std')],axis=1)
        parts.append(pd.concat({device:f},names=['deviceId']))
    return pd.concat(parts)
Xbase = base.reindex(model.index)
Xtemporal = temporal_features(base).reindex(model.index)
Xraw = Xbase
times = model.index.get_level_values('window')
unique_times = np.sort(times.unique())
if len(unique_times) < 10:
    raise ValueError('有效时间点太少，无法划分训练、验证、测试集。')
t1, t2 = unique_times[int(len(unique_times)*.6)], unique_times[int(len(unique_times)*.8)]
# 5分钟历史特征加上2分钟沿用时间，留出7分钟间隔。长告警事件仍可能跨边界。
gap = pd.Timedelta('7min')
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

# 不需要另交验证集：原数据按全局时间分成约60%训练、20%验证、20%测试。
# 所有特征筛选、分箱只看训练数据；验证标签用于选参数；测试标签最后才用于评估。
# 两套候选：原始状态、状态+历史变化。保留旧模型作为同一切分上的对照。
def threshold_grid(p):
    return np.unique(np.r_[0., np.linspace(.01,.99,50), np.quantile(p,np.linspace(0,1,31)), 1.000001])

def choose_independent_threshold(y, p, legacy=False):
    grid = np.linspace(.1,.9,17) if legacy else threshold_grid(p)
    scores = [binary_metrics(y,p>=t)['f1'] for t in grid]
    return float(grid[int(np.argmax(scores))])

candidates = []
for name, frame in [('original',Xbase),('temporal',Xtemporal)]:
    keep = frame.loc[tr].notna().mean().ge(.05) & frame.loc[tr].nunique().gt(1)
    frame = frame.loc[:,keep]
    pv = np.zeros((va.sum(),5)); pt = np.zeros((te.sum(),5))
    thresholds = []; settings_local = []; features_local = []
    cache = {bins:discretize(frame,tr,bins) for bins in [5,10]}
    for k in range(5):
        best = None
        for bins,Z in cache.items():
            for nf in [4,8,16]:
                fitted = fit_binary(Z[tr],Y[tr,k],nf,bins+1)
                p = predict_probability(Z[va],fitted)
                threshold = choose_independent_threshold(Y[va,k],p,legacy=(name=='original'))
                score = binary_metrics(Y[va,k],p>=threshold)['f1']
                if best is None or score>best[0]:
                    best=(score,bins,nf,threshold,fitted,p)
        score,bins,nf,threshold,fitted,p=best
        pv[:,k]=p
        pt[:,k]=predict_probability(cache[bins][te],fitted)
        thresholds.append(threshold)
        settings_local.append(dict(alarmClass=k+1,bins=bins,feature_count=len(fitted[0]),
                                   threshold=threshold,validation_f1=score,
                                   train_positive=int(Y[tr,k].sum()),validation_positive=int(Y[va,k].sum())))
        features_local.extend(dict(alarmClass=k+1,feature=frame.columns[j]) for j in fitted[0])
        print(f'{name} 第{k+1}类完成，验证F1={score:.3f}',flush=True)
    candidates.append(dict(name=name,pv=pv,pt=pt,thresholds=np.array(thresholds),
                           settings=settings_local,features=features_local))

# 验证集联合选择阈值：同时关注完全匹配率与Macro-F1。
# 限制各类验证F1相对其独立阈值最多下降0.05，避免牺牲某类来抬高整体指标。
def validation_scores(actual,pred):
    f1=np.array([binary_metrics(actual[:,k],pred[:,k])['f1'] for k in range(5)])
    exact=(actual==pred).all(axis=1).mean()
    return .5*exact+.5*f1.mean(), f1

def joint_thresholds(actual,p,initial):
    thresholds=initial.copy()
    _,reference=validation_scores(actual,p>=initial)
    for _ in range(4):
        changed=False
        for k in range(5):
            best_score,_=validation_scores(actual,p>=thresholds)
            best_t=thresholds[k]
            for t in threshold_grid(p[:,k]):
                trial=thresholds.copy();trial[k]=t
                score,f1=validation_scores(actual,p>=trial)
                if np.all(f1>=reference-.05-1e-12) and score>best_score+1e-12:
                    best_score,best_t=score,t
            changed |= best_t!=thresholds[k]
            thresholds[k]=best_t
        if not changed: break
    return thresholds

# 原版原阈值也作为候选，因此是否启用新特征由验证集决定。
options=[dict(**c,mode='independent') for c in candidates]
for c in candidates:
    options.append(dict(**{**c,'thresholds':joint_thresholds(Y[va],c['pv'],c['thresholds'])},mode='joint'))
selected=max(options,key=lambda c:validation_scores(Y[va],c['pv']>=c['thresholds'])[0])
probabilities=selected['pt'];predictions=(probabilities>=selected['thresholds']).astype(int)
settings=selected['settings'];chosen_features=selected['features']
for k,row in enumerate(settings):
    row['threshold']=selected['thresholds'][k]
    row['validation_f1']=binary_metrics(Y[va,k],selected['pv'][:,k]>=selected['thresholds'][k])['f1']
    row['feature_set']=selected['name'];row['threshold_mode']=selected['mode']
print('验证集选择：',selected['name'],selected['mode'],flush=True)

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
old=candidates[0]
comparison.loc['previous_model_same_split']=multilabel_summary(truth,old['pt']>=old['thresholds'])
validation_rows=[]
for c in options:
    validation_rows.append(dict(candidate=c['name']+'_'+c['mode'],
        **multilabel_summary(Y[va],c['pv']>=c['thresholds'])))
pd.DataFrame(validation_rows).to_csv(OUT/'16_validation_candidates.csv',index=False)
split=pd.DataFrame({'set':np.select([tr,va,te],['train','validation','test'],default='gap')},index=model.index)
split.to_csv(OUT/'17_time_split.csv')
ratios=[]
for name,mask in [('train',tr),('validation',va),('test',te)]:
    ratios.append(dict(set=name,samples=int(mask.sum()),**{f'class_{k+1}_positive_fraction':float(Y[mask,k].mean()) for k in range(5)}))
pd.DataFrame(ratios).to_csv(OUT/'18_label_distribution.csv',index=False)
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

# %% 7. 随机森林对照与第5类分布检查
# 安装：python -m pip install scikit-learn
# 第1至第6部分执行后运行；每类独立训练，因此允许多个告警同时出现。
from sklearn.ensemble import RandomForestClassifier

# 分布检查使用分钟标签，而非原始长表记录数量，避免重复上传放大比例。
diagnostic = pd.DataFrame(Y, index=model.index, columns=[f'class_{k}' for k in range(1,6)]).reset_index()
diagnostic['date'] = diagnostic['window'].dt.date
by_day = diagnostic.groupby(['deviceId','date']).agg(
    observed_windows=('class_5','size'),class5_positive_windows=('class_5','sum'),
    class5_positive_fraction=('class_5','mean'))
by_day.to_csv(OUT/'20_class5_by_vehicle_day.csv',encoding='utf-8-sig')
# 原始记录层面的变量分布，仅供诊断，不作为模型特征。
variable_labels=pd.crosstab(df['variable'],df['alarmClass']).reindex(columns=range(6),fill_value=0)
variable_labels.columns=[f'class_{k}_records' for k in range(6)]
variable_labels['class5_fraction']=variable_labels['class_5_records']/variable_labels.sum(axis=1)
variable_labels.to_csv(OUT/'30_alarm_by_variable.csv',encoding='utf-8-sig')
status_rows=df.loc[df['variable'].isin(['IGNITION_STATUS','TOWING','IDLING'])].copy()
status_rows['date']=status_rows['time'].dt.date
status_rows['class5_record']=status_rows['alarmClass'].eq(5).astype(int)
status_rows.groupby(['deviceId','date','variable']).agg(
    records=('class5_record','size'),class5_records=('class5_record','sum'),
    class5_record_fraction=('class5_record','mean')).to_csv(OUT/'31_status_reporting_by_day.csv',encoding='utf-8-sig')


# 未知标签的分钟没有被纳入；未上报该类按0仍依赖标签完整性，统计不能证明真实故障率。
fig,ax=plt.subplots(figsize=(10,4))
for i,(device,g) in enumerate(by_day.groupby(level='deviceId')):
    ax.plot(g.index.get_level_values('date').astype(str),g['class5_positive_fraction'],marker='o',label=f'Vehicle {i+1}')
ax.set_ylim(0,1);ax.set_ylabel('Class 5 positive fraction');ax.tick_params(axis='x',rotation=30);ax.legend()
savefig('08_class5_by_vehicle_day.png')
# 按车辆输出训练/验证/测试阶段比例，帮助区分车辆构成变化和单车变化。
phase=np.select([tr,va,te],['train','validation','test'],default='gap')
diagnostic['phase']=phase
diagnostic.groupby(['deviceId','phase']).agg(windows=('class_5','size'),class5_fraction=('class_5','mean')).to_csv(OUT/'21_class5_by_vehicle_split.csv')

# 预先固定候选方案，完全不根据本次测试结果增删候选或调参数。
# 默认排除经纬度/海拔及其历史特征，减少路线位置捷径；设备ID和标签字段也不输入。
# 其他字段是否直接编码告警，仍需业务定义确认，不能只凭字段名认定无泄漏。
location_cols={'latitude','longitude','altitude'}
frames={
    'current':Xbase.drop(columns=list(location_cols),errors='ignore'),
    'temporal':Xtemporal.loc[:,[c for c in Xtemporal if c.split('__')[0] not in location_cols]],
}
# 每折只用当时训练数据选列、计算中位数。缺失标记帮助区分填充与正常数值。
def forest_matrix(frame,train_mask,predict_mask):
    train=frame.loc[train_mask]
    columns=train.columns[train.notna().mean().ge(.05)&train.nunique().gt(1)]
    if len(columns)==0: raise ValueError('随机森林训练折无有效特征。')
    train=train[columns];future=frame.loc[predict_mask,columns]
    med=train.median().fillna(0)
    a=np.c_[train.fillna(med).to_numpy(float),train.isna().to_numpy(float)]
    b=np.c_[future.fillna(med).to_numpy(float),future.isna().to_numpy(float)]
    names=list(columns)+[c+'__missing' for c in columns]
    return a,b,names

def forest_probabilities(frame,train_mask,predict_mask,params):
    a,b,names=forest_matrix(frame,train_mask,predict_mask)
    probs=np.zeros((len(b),5));importance=[]
    for k in range(5):
        y=Y[train_mask,k]
        if len(np.unique(y))==1:
            probs[:,k]=float(y[0]);continue
        clf=RandomForestClassifier(n_estimators=100,max_depth=params['depth'],
            min_samples_leaf=params['leaf'],class_weight=params['weight'],
            max_features='sqrt',random_state=20261031+k,n_jobs=2)
        clf.fit(a,y)
        probs[:,k]=clf.predict_proba(b)[:,list(clf.classes_).index(1)]
        importance.extend(dict(alarmClass=k+1,feature=n,importance=float(v))
                          for n,v in zip(names,clf.feature_importances_))
    return probs,importance

# 三段扩展时间验证：40%以前训练/40%-50%验证、50%以前/50%-60%、60%以前/60%-80%。
# 每个边界保留7分钟间隔；测试阶段仍为80%之后加间隔，与上一版一致。
folds=[]
for left,right in [(.4,.5),(.5,.6),(.6,.8)]:
    start=unique_times[int(len(unique_times)*left)];end=unique_times[int(len(unique_times)*right)]
    train_mask=np.asarray(times<start)
    valid_mask=np.asarray((times>=start+gap)&(times<end))
    if min(train_mask.sum(),valid_mask.sum())<20:raise ValueError('时间验证折样本太少。')
    assert not np.any(train_mask & valid_mask)
    assert not np.any(valid_mask & te)
    folds.append((train_mask,valid_mask))
fold_details=[]
for i,(tm,vm) in enumerate(folds):
    fold_details.append(dict(fold=i+1,train_n=int(tm.sum()),validation_n=int(vm.sum()),
        train_last=str(times[tm].max()),validation_first=str(times[vm].min()),validation_last=str(times[vm].max())))
pd.DataFrame(fold_details).to_csv(OUT/'22_forest_time_folds.csv',index=False)

configs=[dict(depth=8,leaf=5,weight='balanced'),dict(depth=None,leaf=3,weight='balanced'),
         dict(depth=None,leaf=3,weight=None)]
forest_candidates=[];fold_metrics=[]
for feature_name,frame in frames.items():
    for config_id,params in enumerate(configs):
        candidate_name=f'{feature_name}_config{config_id+1}'
        predictions_by_fold=[];truth_by_fold=[]
        for fold_id,(tm,vm) in enumerate(folds):
            p,_=forest_probabilities(frame,tm,vm,params)
            predictions_by_fold.append(p);truth_by_fold.append(Y[vm])
            print(f'随机森林 {candidate_name} 时间验证{fold_id+1}/3完成',flush=True)
        pooled_p=np.vstack(predictions_by_fold);pooled_y=np.vstack(truth_by_fold)
        initial=np.array([choose_independent_threshold(pooled_y[:,k],pooled_p[:,k]) for k in range(5)])
        thresholds=joint_thresholds(pooled_y,pooled_p,initial)
        objectives=[]
        for fold_id,(actual,p) in enumerate(zip(truth_by_fold,predictions_by_fold)):
            stats=multilabel_summary(actual,p>=thresholds)
            objectives.append(.5*stats['exact_match_accuracy']+.5*stats['macro_f1'])
            fold_metrics.append(dict(candidate=candidate_name,fold=fold_id+1,**stats))
        forest_candidates.append(dict(name=candidate_name,features=feature_name,params=params,
            thresholds=thresholds,score=float(np.mean(objectives)),std=float(np.std(objectives))))
# 阈值与模型共用这些验证折，因此验证成绩用于选择，不是无偏性能估计。
best_forest=max(forest_candidates,key=lambda c:c['score'])
pd.DataFrame(fold_metrics).to_csv(OUT/'23_forest_validation_folds.csv',index=False)
pd.DataFrame([dict(candidate=c['name'],mean_objective=c['score'],std_objective=c['std'],
    thresholds=','.join(f'{x:.6f}' for x in c['thresholds']),**c['params']) for c in forest_candidates]).to_csv(OUT/'24_forest_candidates.csv',index=False)
print('仅根据多段验证选出的随机森林：',best_forest['name'],flush=True)
# 最终仍只训练最早60%的窗口，与朴素贝叶斯使用相同训练/测试时间段。
# 重新fit的处理步骤均只看训练集，测试标签不用于fit、筛选、阈值选择。
forest_probability,forest_importance=forest_probabilities(frames[best_forest['features']],tr,te,best_forest['params'])
forest_prediction=(forest_probability>=best_forest['thresholds']).astype(int)
forest_metrics=pd.DataFrame([dict(alarmClass=k+1,**binary_metrics(Y[te,k],forest_prediction[:,k])) for k in range(5)])
forest_metrics.to_csv(OUT/'25_forest_metrics.csv',index=False)
forest_comparison=comparison.copy()
forest_comparison.loc['random_forest']=multilabel_summary(Y[te],forest_prediction)
forest_comparison.to_csv(OUT/'26_model_comparison.csv')
forest_result=pd.DataFrame(index=model.index[te])
for k in range(5):
    forest_result[f'true_class_{k+1}']=Y[te,k]
    forest_result[f'pred_class_{k+1}']=forest_prediction[:,k]
    forest_result[f'probability_class_{k+1}']=forest_probability[:,k]
forest_result.to_csv(OUT/'27_forest_predictions.csv')
pd.DataFrame(forest_importance).sort_values(['alarmClass','importance'],ascending=[True,False]).to_csv(OUT/'28_forest_importance.csv',index=False)
pd.DataFrame([dict(alarmClass=k+1,threshold=float(best_forest['thresholds'][k]),
    candidate=best_forest['name'],**best_forest['params']) for k in range(5)]).to_csv(OUT/'29_forest_settings.csv',index=False)
print('\n随机森林每类结果：\n',forest_metrics.to_string(index=False))
print('\n相同训练/测试时间段的模型比较：\n',forest_comparison.to_string())
fig,ax=plt.subplots(figsize=(10,4))
forest_comparison[['exact_match_accuracy','macro_f1','micro_f1']].plot.bar(ax=ax)
ax.set_ylim(0,1);ax.tick_params(axis='x',rotation=15);ax.set_ylabel('Score');savefig('09_model_comparison.png')
fig,ax=plt.subplots(figsize=(9,4))
forest_metrics.set_index('alarmClass')[['precision','recall','f1']].plot.bar(ax=ax)
ax.set_ylim(0,1);ax.set_ylabel('Score');savefig('10_forest_class_scores.png')
print('结果是已被先前版本查看过的历史测试段表现；新增数据才可提供新的独立确认。')
