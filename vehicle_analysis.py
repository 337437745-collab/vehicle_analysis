# %% 1. 环境与参数（可整段运行，也可按 # %% 分段运行）
# 安装命令：python -m pip install numpy pandas matplotlib
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DATA_PATH = Path('题2_车辆物联网遥测.csv')  # 修改成自己的 CSV 路径
OUT = Path('vehicle_results_singlelabel')
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
# 仅显示运行反馈，不参与数据计算；百分比按步骤数/设备数计算。
from time import perf_counter as _progress_clock

def _show_progress(done, total, label):
    fraction = done / total if total else 1.0
    filled = int(24 * fraction)
    bar = '#' * filled + '-' * (24 - filled)
    print(f'[{bar}] {fraction:6.1%} ({done}/{total}) {label}', flush=True)

def _device_progress(groups, label):
    total = groups.ngroups
    _show_progress(0, total, label + '：开始')
    for number, item in enumerate(groups, 1):
        yield item
        # 在调用方完成当前设备后再更新，异常时不会虚报完成。
        _show_progress(number, total, label)

_progress_started = _progress_clock()
_show_progress(0, 7, '第3部分开始：整理长表')
# 同设备、同时间、同变量的重复数值取中位数；告警另外统计，不会随数值聚合丢失。
keys = ['deviceId', 'time']
num = df.loc[~df['variable'].eq('POSITION'), keys + ['variable', 'numeric_value']].copy()
position_rows = df.loc[df['variable'].eq('POSITION'), keys].join(pos)
position_long = position_rows.melt(id_vars=keys, value_vars=pos.columns,
                                  var_name='variable', value_name='numeric_value')
long_num = pd.concat([num, position_long], ignore_index=True)
_show_progress(1, 7, '长表整理完成；正在生成精确时间宽表')
wide_exact = long_num.pivot_table(index=keys, columns='variable', values='numeric_value', aggfunc='median')
wide_exact.to_csv(OUT / '02_wide_exact.csv', encoding='utf-8-sig')

_show_progress(2, 7, '精确时间宽表已保存；正在按分钟聚合')

# 按分钟汇总，避免不同传感器必须在完全相同的秒数上传才能对齐。
long_num['window'] = long_num['time'].dt.floor(FREQ)
wide = long_num.pivot_table(index=['deviceId','window'], columns='variable',
                            values='numeric_value', aggfunc='median').sort_index()
_show_progress(3, 7, '分钟聚合完成；正在补齐设备时间网格')
# 补齐每台设备自身时间范围内的分钟网格；绝不跨车辆填充。
parts = []
for device, g in _device_progress(wide.groupby(level=0), '补齐时间网格'):
    g = g.droplevel(0)
    g = g.reindex(pd.date_range(g.index.min(), g.index.max(), freq=FREQ, name='window'))
    parts.append(pd.concat({device: g}, names=['deviceId']))
wide = pd.concat(parts)
wide.isna().mean().rename('missing_fraction_before_fill').to_csv(OUT / '03_missingness.csv')
_show_progress(4, 7, '时间网格已补齐；正在填充历史观测')
# 有限前向填充：超过 MAX_AGE 的缺测仍为空，不用未来值回填过去。
filled_parts = []
for device, g in _device_progress(wide.groupby(level=0), '填充历史观测'):
    g = g.droplevel(0)
    times = pd.Series(g.index, index=g.index)
    last_seen = g.apply(lambda s: times.where(s.notna()).ffill())
    age = last_seen.apply(lambda s: times - s)
    filled = g.ffill().where(age <= MAX_AGE)
    filled_parts.append(pd.concat({device: filled}, names=['deviceId']))
wide = pd.concat(filled_parts)
_show_progress(5, 7, '历史观测填充完成；正在统计告警')
# 不知道设备量程，不能凭经验删除所有极端值；只屏蔽明确无效的负车速/负转速。
for col in ['Vehicle speed', 'ENGINE RPM']:
    if col in wide:
        wide.loc[wide[col] < 0, col] = np.nan

df['window'] = df['time'].dt.floor(FREQ)
alarm_counts = pd.crosstab([df['deviceId'], df['window']], df['alarmClass']).reindex(columns=range(6), fill_value=0)
alarm_counts.columns = [f'class_{k}_records' for k in range(6)]
wide = wide.join(alarm_counts).copy()
_show_progress(6, 7, '告警统计合并完成；正在保存分钟宽表')
# 未采样窗口的标签保持未知；不能自动填为正常。
wide.to_csv(OUT / '04_wide_minute.csv', encoding='utf-8-sig')
_show_progress(7, 7, f'第3部分完成，耗时 {_progress_clock() - _progress_started:.1f} 秒')
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

# %% 6. 单标签分类：每条原始记录对应一个alarmClass，不从分钟表构造新真值
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score,classification_report,confusion_matrix,log_loss

samples=df.loc[df['alarmClass'].isin(range(6))].copy().sort_values(['time','deviceId'],kind='stable')
samples['source_csv_row']=samples.index+2  # 对应原CSV行号（含表头，假设每条记录一行）
y=samples['alarmClass'].to_numpy(int)
times=samples['time'];unique_times=np.sort(times.unique())
if len(unique_times)<30:raise ValueError('有效时间点不足以分成训练/验证/校准/测试。')
b1,b2,b3=[pd.Timestamp(unique_times[int(len(unique_times)*r)]) for r in [.6,.7,.8]]
gap=pd.Timedelta('2min')
tr=np.asarray(times<b1);va=np.asarray((times>=b1+gap)&(times<b2))
ca=np.asarray((times>=b2+gap)&(times<b3));te=np.asarray(times>=b3+gap)
if min(tr.sum(),va.sum(),ca.sum(),te.sum())<20:raise ValueError('时间划分后某集合样本不足。')
print('逐记录单标签样本数（训练/验证/校准/测试）：',tr.sum(),va.sum(),ca.sum(),te.sum(),flush=True)
# 变量名称以训练集类别独热编码；测试新变量不反向改变训练特征表。
categories=sorted(samples.loc[tr,'variable'].dropna().unique())
categorical=pd.get_dummies(pd.Categorical(samples['variable'],categories=categories),prefix='variable',dtype=float)
categorical.index=samples.index
base_features=pd.concat([samples[['numeric_value']].astype(float),categorical],axis=1)

# 将每条记录与当时最近传感器观测对齐，禁止使用未来分钟或未来记录。
# 同一精确时间的其他变量视为同时可用；这是假定该时间戳的一批数据已收到后的识别。
CONTEXT_SENSORS=['Vehicle speed','ENGINE RPM','ENGINE COOLANT TEMPERATURE','CALCULATED ENGINE LOAD',
    'ACCELERATION X','ACCELERATION Y','ACCELERATION Z','CONTROL MODULE VOLTAGE',
    'EXTERNAL BATTERY','INTERNAL BATTERY','IGNITION_STATUS','TOWING','IDLING']
STATE_SENSORS=['IGNITION_STATUS','TOWING','IDLING']
def causal_context(records,targets,sensors,max_age):
    result=pd.DataFrame(index=targets.index)
    for sensor in sensors:
        sensor_values=pd.Series(np.nan,index=targets.index,dtype=float)
        sensor_age=pd.Series(np.nan,index=targets.index,dtype=float)
        for device,target in targets.groupby('deviceId',sort=False):
            observed=records.loc[records['deviceId'].eq(device)&records['variable'].eq(sensor),['time','numeric_value']].copy()
            if observed.empty:continue
            # 连续量重复时取中位数；状态重复冲突记未知，避免构造0.5。
            grouped=observed.groupby('time')['numeric_value']
            if sensor in STATE_SENSORS:
                values=grouped.first().where(grouped.nunique(dropna=False).eq(1))
                values=values.where(values.isin([0,1]))
            else:values=grouped.median()
            right=values.rename('context_value').reset_index().rename(columns={'time':'observed_time'}).sort_values('observed_time')
            left=target[['time']].copy();left['row_key']=target.index
            matched=pd.merge_asof(left.sort_values('time'),right,left_on='time',right_on='observed_time',direction='backward',tolerance=max_age)
            sensor_values.loc[matched['row_key']]=matched['context_value'].to_numpy()
            sensor_age.loc[matched['row_key']]=(matched['time']-matched['observed_time']).dt.total_seconds().to_numpy()
        result[sensor+'__last']=sensor_values
        result[sensor+'__age_seconds']=sensor_age
        print('已对齐历史状态：',sensor,flush=True)
    return result
context=causal_context(df,samples,CONTEXT_SENSORS,MAX_AGE)
feature_sets={'variable_value':base_features,'with_vehicle_context':pd.concat([base_features,context],axis=1)}
# 数值填充、缺失标记、有效特征筛选只从训练集确定，不使用验证/校准/测试统计。
def prepare_features(frame,train_mask):
    frame=frame.replace([np.inf,-np.inf],np.nan)
    keep=frame.loc[train_mask].notna().any()&frame.loc[train_mask].nunique().gt(1)
    frame=frame.loc[:,keep]
    med=frame.loc[train_mask].median().fillna(0)
    matrix=np.c_[frame.fillna(med).to_numpy(float),frame.isna().to_numpy(float)].astype(np.float32)
    return matrix,list(frame.columns)+[c+'__missing' for c in frame.columns]

configs=[dict(max_depth=14,min_samples_leaf=10),dict(max_depth=None,min_samples_leaf=5)]
validation_rows=[];best_by_features={}
for name,frame in feature_sets.items():
    X,names=prepare_features(frame,tr)
    best=None
    for ci,params in enumerate(configs):
        clf=RandomForestClassifier(n_estimators=100,max_features='sqrt',class_weight=None,
            random_state=20261031,n_jobs=2,**params)
        clf.fit(X[tr],y[tr]);pred=clf.predict(X[va])
        report=classification_report(y[va],pred,labels=range(6),output_dict=True,zero_division=0)
        acc=accuracy_score(y[va],pred);macro=report['macro avg']['f1-score']
        # 预先固定目标：准确率和Macro-F1等权，防止只预测正常类来抬高准确率。
        objective=.5*acc+.5*macro
        validation_rows.append(dict(features=name,config=ci+1,validation_accuracy=acc,validation_macro_f1=macro,objective=objective,**params))
        if best is None or objective>best['score']:
            best=dict(clf=clf,X=X,names=names,score=objective,config=ci+1,params=params,features=name)
        print(f'单标签 {name} 参数{ci+1}/2完成：验证准确率={acc:.3f}，Macro-F1={macro:.3f}',flush=True)
    best_by_features[name]=best
# 只按验证成绩选最终方案；测试不参与挑选。
selected=max(best_by_features.values(),key=lambda c:c['score'])
pd.DataFrame(validation_rows).to_csv(OUT/'11_singlelabel_validation.csv',index=False)
print('验证集选中：',selected['features'],selected['params'],flush=True)

def six_probabilities(clf,X):
    p=np.zeros((len(X),6));p[:,clf.classes_.astype(int)]=clf.predict_proba(X)
    return p
raw_cal=six_probabilities(selected['clf'],selected['X'][ca])
raw_test=six_probabilities(selected['clf'],selected['X'][te])
# 多分类温度校准：六类概率和为1；正温度不改变最大概率类别。
def temperature_scale(p,temperature):
    logits=np.log(np.clip(p,1e-12,1.))/temperature
    logits-=logits.max(axis=1,keepdims=True)
    z=np.exp(logits)
    return z/z.sum(axis=1,keepdims=True)
temperatures=np.geomspace(.25,4.,81)
calibration_losses=[log_loss(y[ca],temperature_scale(raw_cal,t),labels=range(6)) for t in temperatures]
temperature=float(temperatures[int(np.argmin(calibration_losses))])
experimental_calibrated=temperature_scale(raw_test,temperature)
# 校准样本覆盖门槛：各类至少30条，否则仅保留实验校准对照，不作为默认概率。
calibration_class_counts=np.bincount(y[ca],minlength=6)
use_calibration=bool((calibration_class_counts>=30).all())
confidence_method='temperature_calibrated' if use_calibration else 'raw_uncalibrated_insufficient_class_coverage'
probabilities=experimental_calibrated if use_calibration else raw_test.copy()
predictions=probabilities.argmax(axis=1)
assert np.array_equal(predictions,raw_test.argmax(axis=1))

truth=y[te]
model_rows=[]
for name,candidate in best_by_features.items():
    pred=candidate['clf'].predict(candidate['X'][te])
    r=classification_report(truth,pred,labels=range(6),output_dict=True,zero_division=0)
    model_rows.append(dict(model=name,accuracy=accuracy_score(truth,pred),macro_f1=r['macro avg']['f1-score'],weighted_f1=r['weighted avg']['f1-score']))
majority=int(np.bincount(y[tr],minlength=6).argmax());baseline=np.full(len(truth),majority)
r=classification_report(truth,baseline,labels=range(6),output_dict=True,zero_division=0)
model_rows.append(dict(model='majority_baseline',accuracy=accuracy_score(truth,baseline),macro_f1=r['macro avg']['f1-score'],weighted_f1=r['weighted avg']['f1-score']))
comparison=pd.DataFrame(model_rows);comparison.to_csv(OUT/'12_singlelabel_comparison.csv',index=False)
report=classification_report(truth,predictions,labels=range(6),output_dict=True,zero_division=0)
metrics=pd.DataFrame([dict(alarmClass=k,**report[str(k)]) for k in range(6)])
metrics.to_csv(OUT/'13_singlelabel_class_metrics.csv',index=False)
cm=confusion_matrix(truth,predictions,labels=range(6))
pd.DataFrame(cm,index=range(6),columns=range(6)).to_csv(OUT/'14_singlelabel_confusion.csv')
result=samples.loc[te,['source_csv_row','deviceId','time','variable','value','alarmClass']].copy()
result['predicted_alarmClass']=predictions
result['prediction_confidence']=probabilities.max(axis=1)
result['confidence_method']=confidence_method
result['is_correct']=(predictions==truth).astype(int)
for k in range(6):
    result[f'probability_class_{k}']=probabilities[:,k]
    result[f'raw_probability_class_{k}']=raw_test[:,k]
    result[f'experimental_calibrated_probability_class_{k}']=experimental_calibrated[:,k]
result.to_csv(OUT/'15_singlelabel_predictions.csv',index=False,encoding='utf-8-sig')
result.loc[result.predicted_alarmClass.ne(0)].to_csv(OUT/'16_predicted_alarm_records.csv',index=False,encoding='utf-8-sig')
phase=np.select([tr,va,ca,te],['train','validation','calibration','test'],default='gap')
labels=pd.DataFrame({'phase':phase,'alarmClass':y})
pd.crosstab(labels.phase,labels.alarmClass).reindex(columns=range(6),fill_value=0).to_csv(OUT/'17_singlelabel_class_distribution.csv')
split=samples[['source_csv_row','deviceId','time']].copy();split['phase']=phase;split.to_csv(OUT/'18_singlelabel_time_split.csv',index=False)
pd.DataFrame({'feature':selected['names'],'importance':selected['clf'].feature_importances_}).sort_values('importance',ascending=False).to_csv(OUT/'19_singlelabel_feature_importance.csv',index=False)
pd.DataFrame({'temperature':temperatures,'calibration_log_loss':calibration_losses}).to_csv(OUT/'20_temperature_selection.csv',index=False)

def probability_metrics(actual,p):
    confidence=p.max(axis=1);correct=p.argmax(axis=1)==actual
    bins=np.minimum((confidence*10).astype(int),9);ece=0.
    for k in range(10):
        m=bins==k
        if m.any():ece+=m.mean()*abs(confidence[m].mean()-correct[m].mean())
    return dict(log_loss=log_loss(actual,p,labels=range(6)),brier_multiclass=float(np.mean(np.sum((p-np.eye(6)[actual])**2,axis=1))),ece_top_label=float(ece))
cal_metrics=pd.DataFrame([dict(method='raw',**probability_metrics(truth,raw_test)),dict(method='temperature_calibrated_experimental',**probability_metrics(truth,experimental_calibrated))])
cal_metrics.to_csv(OUT/'21_singlelabel_calibration_metrics.csv',index=False)
settings=pd.DataFrame([dict(selected_features=selected['features'],**selected['params'],trees=100,temperature=temperature,calibration_applied=use_calibration,confidence_method=confidence_method,training_rows=int(tr.sum()),validation_rows=int(va.sum()),calibration_rows=int(ca.sum()),test_rows=int(te.sum()),accuracy=float((truth==predictions).mean()),macro_f1=report['macro avg']['f1-score'])])
settings.to_csv(OUT/'22_singlelabel_settings.csv',index=False)
print('\n同一批逐记录测试样本的比较：\n',comparison.to_string(index=False))
print('\n选中模型每类指标：\n',metrics.to_string(index=False))
print('\n温度校准：T=',temperature,'\n',cal_metrics.to_string(index=False))
print('各类校准样本数：',calibration_class_counts,'；默认置信分数方法：',confidence_method)
print('最终单标签匹配率/准确率：',f'{(predictions==truth).mean():.2%}')
print('原46.9%的模型未在本次相同切分上复现，不能把其与本次数字当作严格同条件比较。')

fig,ax=plt.subplots(figsize=(7,6));ax.imshow(cm,cmap='Blues')
for i in range(6):
    for j in range(6):ax.text(j,i,str(cm[i,j]),ha='center',va='center')
ax.set_xticks(range(6));ax.set_yticks(range(6));ax.set_xlabel('Predicted class');ax.set_ylabel('True class');savefig('06_singlelabel_confusion.png')
fig,ax=plt.subplots(figsize=(9,4));metrics.set_index('alarmClass')[['precision','recall','f1-score']].plot.bar(ax=ax)
ax.set_ylim(0,1);ax.set_ylabel('Score');savefig('07_singlelabel_class_scores.png')
