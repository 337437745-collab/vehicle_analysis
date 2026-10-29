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

# %% 6. 仅用 NumPy 实现高斯朴素贝叶斯基线（无需 sklearn）
# 任务定义：根据一条传感器记录的“变量名称+数值”识别该记录的类别。
# 这不是提前预测；不使用 alarmClass 衍生特征、设备编号或告警计数。
model_df = df.loc[df.numeric_value.notna() & df.alarmClass.notna()].sort_values('time').copy()
# 全局时间切分，保证相同时间的记录不会分别进入训练与测试。
cut = model_df.time.quantile(.8)
train = model_df.loc[model_df.time < cut].copy()
test = model_df.loc[model_df.time >= cut].copy()
# 每种传感器有不同单位：只用训练集统计量进行标准化。
means = train.groupby('variable').numeric_value.mean()
stds = train.groupby('variable').numeric_value.std().replace(0, 1).fillna(1)
vocab = sorted(train.variable.unique())
def features(frame):
    z = ((frame.numeric_value-frame.variable.map(means))/frame.variable.map(stds)).fillna(0).clip(-20,20)
    onehot = np.column_stack([frame.variable.eq(v).to_numpy(dtype=float) for v in vocab])
    return np.column_stack([z.to_numpy(dtype=float), onehot])
Xtr, Xte = features(train), features(test)
ytr, yte = train.alarmClass.to_numpy(dtype=int), test.alarmClass.to_numpy(dtype=int)
classes = np.unique(ytr)
mu = np.array([Xtr[ytr==k].mean(axis=0) for k in classes])
var = np.array([Xtr[ytr==k].var(axis=0) for k in classes]) + .01
prior = np.array([(ytr==k).mean() for k in classes])
# 按类别计算，避免创建过大的三维数组。
scores = np.column_stack([np.log(prior[i])-.5*(np.log(2*np.pi*var[i])+((Xte-mu[i])**2)/var[i]).sum(axis=1) for i in range(len(classes))])
pred = classes[scores.argmax(axis=1)]
cm = np.zeros((6,6), dtype=int)
np.add.at(cm, (yte,pred), 1)
tp = np.diag(cm); support = cm.sum(axis=1)
precision = np.divide(tp, cm.sum(axis=0), out=np.zeros(6), where=cm.sum(axis=0)>0)
recall = np.divide(tp, support, out=np.zeros(6), where=support>0)
f1 = np.divide(2*precision*recall, precision+recall, out=np.zeros(6), where=precision+recall>0)
metrics = pd.DataFrame({'class':range(6),'precision':precision,'recall':recall,'f1':f1,'support':support})
metrics.to_csv(OUT / '11_model_metrics.csv', index=False)
majority = int(pd.Series(ytr).mode().iloc[0])
print('切分时间：', cut, '训练记录：', len(train), '测试记录：', len(test))
print(metrics.to_string(index=False))
print('模型准确率：', (pred==yte).mean())
print('多数类基线准确率：', (yte==majority).mean())
print('测试集中实际出现类别的 Macro-F1：', f1[support>0].mean())
print('测试集未见于训练的变量数：', test.loc[~test.variable.isin(vocab),'variable'].nunique())
fig, ax = plt.subplots(figsize=(7,6))
im = ax.imshow(cm, cmap='Blues'); fig.colorbar(im, ax=ax)
for i in range(6):
    for j in range(6): ax.text(j,i,str(cm[i,j]),ha='center',va='center',fontsize=8)
ax.set_xlabel('Predicted class'); ax.set_ylabel('True class'); savefig('06_confusion_matrix.png')
print('完成。表格和图片已保存到：', OUT.resolve())
# 注意：类别定义、标注生成规则、采样机制均未知。关联不能证明因果，
# 该基线适合课程实验，不能据此宣称能够跨车辆泛化或预测真实故障。
