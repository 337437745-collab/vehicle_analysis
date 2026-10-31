# %% 1. 环境与参数（可整段运行，也可按 # %% 分段运行）
# 安装命令：python -m pip install numpy pandas matplotlib
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DATA_PATH = Path('题2_车辆物联网遥测.csv')  # 修改成自己的 CSV 路径
OUT = Path('vehicle_results_sensor_history')
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

# %% 6. 单标签：传感器分别建模与当前变量历史特征
# 标签为每条原始记录的alarmClass。沿用最初版数值有效记录及固定时间切片。
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score,classification_report,confusion_matrix
import hashlib,json
model_df=df.loc[df.numeric_value.notna() & df.alarmClass.notna()].sort_values('time').copy()
cut=model_df.time.quantile(.8)
train_mask=np.asarray(model_df.time<cut);test_mask=~train_mask
y=model_df.alarmClass.to_numpy(int)
variables=model_df.variable.astype(str)
print('固定切片：',cut,'；训练/测试：',train_mask.sum(),test_mask.sum(),flush=True)

# 同时刻重复记录先合成观测，再只使用严格早于当前时间的历史。
# 当前原始numeric_value不被替换。状态量冲突不合成为0.5。
STATE_VARIABLES={'IGNITION_STATUS','TOWING','IDLING'}
def own_history_features(records,targets,lookback='5min'):
    result=pd.DataFrame(index=targets.index)
    columns=['previous_value','previous2_value','gap_seconds','change','change_per_second',
             'past5_mean','past5_std','past5_min','past5_max','past5_count']
    result=pd.DataFrame(np.nan,index=targets.index,columns=columns,dtype=float)
    horizon=pd.Timedelta(lookback)
    observed=records.loc[records.numeric_value.notna()]
    for (device,variable),target in targets.groupby(['deviceId','variable'],sort=False):
        r=observed.loc[observed.deviceId.eq(device)&observed.variable.eq(variable),['time','numeric_value']]
        g=r.groupby('time').numeric_value
        if variable in STATE_VARIABLES:
            series=g.first().where(g.nunique(dropna=False).eq(1))
            series=series.where(series.isin([0,1]))
        else:series=g.median()
        series=series.sort_index()
        features=pd.DataFrame(index=series.index)
        times_series=pd.Series(series.index,index=series.index)
        age=(times_series-times_series.shift(1)).dt.total_seconds()
        features['previous_value']=series.shift(1).where(age<=horizon.total_seconds())
        age2=(times_series-times_series.shift(2)).dt.total_seconds()
        features['previous2_value']=series.shift(2).where(age2<=horizon.total_seconds())
        features['gap_seconds']=age
        # closed='left' 排除当前时刻及同时间的其他记录，历史均值不含未来观测。
        roll=series.rolling(lookback,closed='left',min_periods=1)
        features['past5_mean']=roll.mean();features['past5_std']=roll.std()
        features['past5_min']=roll.min();features['past5_max']=roll.max();features['past5_count']=roll.count().fillna(0)
        aligned=features.reindex(pd.DatetimeIndex(target.time))
        aligned.index=target.index
        aligned['change']=target.numeric_value-aligned['previous_value']
        aligned['change_per_second']=aligned['change']/aligned['gap_seconds'].where(aligned['gap_seconds']>0)
        result.loc[target.index,columns]=aligned[columns].to_numpy()
    return result.replace([np.inf,-np.inf],np.nan)

history=own_history_features(df,model_df)
numeric_frames={'current':model_df[['numeric_value']].astype(float),
                'history':pd.concat([model_df[['numeric_value']].astype(float),history],axis=1)}
# 独热编码类别仅从外层训练集取，内层筛选仍仅用当折训练行。
vocabulary=sorted(variables.loc[train_mask].unique())
onehot=pd.get_dummies(pd.Categorical(variables,categories=vocabulary),prefix='variable',dtype=float)
onehot.index=model_df.index
frames={name:pd.concat([frame,onehot],axis=1) for name,frame in numeric_frames.items()}

def encode_frame(frame,fit_mask):
    frame=frame.replace([np.inf,-np.inf],np.nan)
    keep=frame.loc[fit_mask].notna().any() & frame.loc[fit_mask].nunique().gt(1)
    selected=frame.loc[:,keep]
    if selected.shape[1]==0:return np.ones((len(frame),1),dtype=np.float32),['constant']
    med=selected.loc[fit_mask].median().fillna(0)
    matrix=np.c_[selected.fillna(med).to_numpy(float),selected.isna().to_numpy(float)].astype(np.float32)
    return matrix,list(selected.columns)+[c+'__missing' for c in selected.columns]

def six_probabilities(clf,X):
    p=np.zeros((len(X),6));p[:,clf.classes_.astype(int)]=clf.predict_proba(X)
    return p

def global_forest(frame,fit_mask,predict_mask,params):
    X,names=encode_frame(frame,fit_mask)
    clf=RandomForestClassifier(n_estimators=100,max_features='sqrt',class_weight=None,
        random_state=20261031,n_jobs=2,**params)
    clf.fit(X[fit_mask],y[fit_mask])
    return six_probabilities(clf,X[predict_mask]),pd.DataFrame({'feature':names,'importance':clf.feature_importances_})

# 传感器分类器数据门槛预先固定；不足时使用同配置统一模型，不强行训练稀有变量。
MIN_SENSOR_ROWS=200
MIN_CLASS_ROWS=20

def sensor_forest(frame,fit_mask,predict_mask,params,fallback_probability):
    probability=fallback_probability.copy();coverage=[]
    predict_positions=np.flatnonzero(predict_mask)
    for variable in sorted(variables.loc[fit_mask].unique()):
        fit=fit_mask & variables.eq(variable).to_numpy()
        predict=predict_mask & variables.eq(variable).to_numpy()
        counts=np.bincount(y[fit],minlength=6);positive_counts=counts[counts>0]
        eligible=fit.sum()>=MIN_SENSOR_ROWS and len(positive_counts)>=2 and positive_counts.min()>=MIN_CLASS_ROWS
        coverage.append(dict(variable=variable,training_rows=int(fit.sum()),training_classes=int(len(positive_counts)),
                             local_model=int(eligible),prediction_rows=int(predict.sum())))
        if not eligible or not predict.any():continue
        # 单独模型不输入变量独热编码：它在该子模型中是常数。
        X,_=encode_frame(frame,fit)
        clf=RandomForestClassifier(n_estimators=100,max_features='sqrt',class_weight=None,
            random_state=20261031,n_jobs=2,**params)
        clf.fit(X[fit],y[fit])
        probability[np.isin(predict_positions,np.flatnonzero(predict))]=six_probabilities(clf,X[predict])
    return probability,coverage

def classification_summary(actual,predicted):
    r=classification_report(actual,predicted,labels=range(6),output_dict=True,zero_division=0)
    return dict(accuracy=float(accuracy_score(actual,predicted)),macro_f1=float(r['macro avg']['f1-score']),
                weighted_f1=float(r['weighted avg']['f1-score']))

# 两段扩展时间验证，仅位于固定外层训练切片内；边界留5分钟间隔。
inner=train_mask.copy();training_times=model_df.loc[inner,'time']
q1,q2=training_times.quantile(.6),training_times.quantile(.8)
gap=pd.Timedelta('5min')
folds=[]
for start,end in [(q1,q2),(q2,cut)]:
    fit=np.asarray(model_df.time<start)&inner
    valid=np.asarray((model_df.time>=start+gap)&(model_df.time<end))&inner
    if min(fit.sum(),valid.sum())<30:raise ValueError('内部时间验证样本不足。')
    assert not np.any((fit|valid)&test_mask)
    folds.append((fit,valid))
fold_details=[dict(fold=i+1,train_rows=int(f.sum()),validation_rows=int(v.sum()),
    train_last=str(model_df.loc[f,'time'].max()),validation_first=str(model_df.loc[v,'time'].min()),
    validation_last=str(model_df.loc[v,'time'].max())) for i,(f,v) in enumerate(folds)]
pd.DataFrame(fold_details).to_csv(OUT/'11_history_validation_folds.csv',index=False)
configs=[dict(max_depth=14,min_samples_leaf=10),dict(max_depth=None,min_samples_leaf=5)]
validation_rows=[];candidate_scores={}
for feature_name in ['current','history']:
    for ci,params in enumerate(configs):
        for fi,(fit,valid) in enumerate(folds):
            gp,_=global_forest(frames[feature_name],fit,valid,params)
            sp,_=sensor_forest(numeric_frames[feature_name],fit,valid,params,gp)
            for strategy,prob in [('global',gp),('per_sensor',sp)]:
                name=f'{strategy}_{feature_name}_config{ci+1}'
                scores=classification_summary(y[valid],prob.argmax(axis=1))
                objective=.5*scores['accuracy']+.5*scores['macro_f1']
                candidate_scores.setdefault(name,[]).append(objective)
                validation_rows.append(dict(candidate=name,strategy=strategy,features=feature_name,config=ci+1,
                    fold=fi+1,objective=objective,**scores))
            print(f'特征={feature_name} 参数{ci+1}/2 内部时间验证{fi+1}/2完成',flush=True)
validation=pd.DataFrame(validation_rows);validation.to_csv(OUT/'12_history_validation_scores.csv',index=False)
selection=validation.groupby(['candidate','strategy','features','config'],sort=False)[['objective','accuracy','macro_f1']].mean().reset_index()
selection.to_csv(OUT/'13_history_candidate_means.csv',index=False)
best=selection.loc[selection.objective.idxmax()]
print('仅根据内部验证选择：',best.candidate,flush=True)
feature_name=best.features;strategy=best.strategy;params=configs[int(best.config)-1]
# 选择结束后重训全部外层训练数据，固定外层测试仅用于报告。
selected_global,importance=global_forest(frames[feature_name],train_mask,test_mask,params)
coverage=[]
if strategy=='per_sensor':
    probabilities,coverage=sensor_forest(numeric_frames[feature_name],train_mask,test_mask,params,selected_global)
else:probabilities=selected_global
# 固定上一版基线：当前变量+当前数值、100树、叶5，完全相同外层切片。
if feature_name=='current' and int(best.config)==2:
    baseline_probability=selected_global.copy()
else:baseline_probability,_=global_forest(frames['current'],train_mask,test_mask,configs[1])
# 两种新增方案均做固定配置对照，但不根据测试成绩替换已选方案。
if feature_name=='history' and int(best.config)==2:
    history_global=selected_global.copy()
else:history_global,_=global_forest(frames['history'],train_mask,test_mask,configs[1])
if feature_name=='history' and strategy=='per_sensor' and int(best.config)==2:
    sensor_history=probabilities.copy();history_coverage=coverage
else:sensor_history,history_coverage=sensor_forest(numeric_frames['history'],train_mask,test_mask,configs[1],history_global)
truth=y[test_mask];pred=probabilities.argmax(axis=1)
comparison=pd.DataFrame([dict(model=name,**classification_summary(truth,p.argmax(axis=1))) for name,p in [
    ('previous_singlelabel_baseline',baseline_probability),('global_history_fixed_config',history_global),
    ('per_sensor_history_fixed_config',sensor_history),('validation_selected',probabilities)]])
comparison.to_csv(OUT/'14_history_test_comparison.csv',index=False)
report=classification_report(truth,pred,labels=range(6),output_dict=True,zero_division=0)
pd.DataFrame([dict(alarmClass=k,**report[str(k)]) for k in range(6)]).to_csv(OUT/'15_history_class_metrics.csv',index=False)
pd.DataFrame(confusion_matrix(truth,pred,labels=range(6)),index=range(6),columns=range(6)).to_csv(OUT/'16_history_confusion.csv')
result=model_df.loc[test_mask,['deviceId','time','variable','value','alarmClass']].copy()
result.insert(0,'source_csv_row',model_df.index[test_mask]+2)
result['predicted_alarmClass']=pred;result['prediction_confidence']=probabilities.max(axis=1)
result['confidence_method']='raw_uncalibrated';result['previous_prediction']=baseline_probability.argmax(axis=1)
result['is_correct']=(pred==truth).astype(int)
for k in range(6):result[f'probability_class_{k}']=probabilities[:,k]
result.to_csv(OUT/'17_history_predictions.csv',index=False,encoding='utf-8-sig')
result.loc[result.predicted_alarmClass.ne(0)].to_csv(OUT/'18_history_alarm_records.csv',index=False,encoding='utf-8-sig')
# 单独保存固定历史分传感器对照；它的测试表现不用于反向改变验证选择。
history_result=result.copy()
history_result['predicted_alarmClass']=sensor_history.argmax(axis=1)
history_result['prediction_confidence']=sensor_history.max(axis=1)
history_result['is_correct']=(history_result.predicted_alarmClass.to_numpy()==truth).astype(int)
for k in range(6):history_result[f'probability_class_{k}']=sensor_history[:,k]
history_result.to_csv(OUT/'24_fixed_sensor_history_predictions.csv',index=False,encoding='utf-8-sig')
history_report=classification_report(truth,sensor_history.argmax(axis=1),labels=range(6),output_dict=True,zero_division=0)
pd.DataFrame([dict(alarmClass=k,**history_report[str(k)]) for k in range(6)]).to_csv(OUT/'25_fixed_sensor_history_class_metrics.csv',index=False)
pd.DataFrame(history_coverage).to_csv(OUT/'26_fixed_sensor_history_coverage.csv',index=False)

history.assign(source_csv_row=model_df.index+2).to_csv(OUT/'19_own_variable_history_features.csv',index=False)
pd.DataFrame(coverage,columns=['variable','training_rows','training_classes','local_model','prediction_rows']).to_csv(OUT/'20_sensor_model_coverage.csv',index=False)
importance.sort_values('importance',ascending=False).to_csv(OUT/'21_global_feature_importance.csv',index=False)
# 每个传感器的测试结果，便于检查总体指标是否掩盖某个变量的退化。
variable_scores=[]
for variable,g in result.groupby('variable'):
    variable_scores.append(dict(variable=variable,records=len(g),previous_accuracy=float(g.alarmClass.eq(g.previous_prediction).mean()),
        selected_accuracy=float(g.alarmClass.eq(g.predicted_alarmClass).mean())))
pd.DataFrame(variable_scores).to_csv(OUT/'22_accuracy_by_sensor.csv',index=False)
metadata=dict(selected_candidate=str(best.candidate),cut=str(cut),training_rows=int(train_mask.sum()),test_rows=int(test_mask.sum()),
    selected_accuracy=float((pred==truth).mean()),baseline_accuracy=float((baseline_probability.argmax(axis=1)==truth).mean()),
    selected_parameters=params,selection_objective='mean_over_two_time_folds_of_0.5_accuracy_plus_0.5_macro_f1',
    min_sensor_rows=MIN_SENSOR_ROWS,min_class_rows=MIN_CLASS_ROWS,history_window='5min',
    train_source_rows_sha256=hashlib.sha256((model_df.index[train_mask].to_numpy()+2).astype('<i8').tobytes()).hexdigest(),
    test_source_rows_sha256=hashlib.sha256((model_df.index[test_mask].to_numpy()+2).astype('<i8').tobytes()).hexdigest())
(OUT/'23_history_settings.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
print('\n固定外层测试切片的比较：\n',comparison.to_string(index=False))
print('\n验证选中方案各类指标：\n',pd.DataFrame([dict(alarmClass=k,**report[str(k)]) for k in range(6)]).to_string(index=False))
print('新增特征和传感器建模已实际参与验证；即使其他测试对照更高，也不反向替换已选方案。')
print('分数未经概率校准；历史测试段已在先前迭代中查看，不是新的独立盲测。')
fig,ax=plt.subplots(figsize=(10,4));comparison.set_index('model')[['accuracy','macro_f1']].plot.bar(ax=ax)
ax.set_ylim(0,1);ax.tick_params(axis='x',rotation=15);savefig('06_sensor_history_comparison.png')
cm=confusion_matrix(truth,pred,labels=range(6));fig,ax=plt.subplots(figsize=(7,6));ax.imshow(cm,cmap='Blues')
for i in range(6):
    for j in range(6):ax.text(j,i,str(cm[i,j]),ha='center',va='center')
ax.set_xticks(range(6));ax.set_yticks(range(6));ax.set_xlabel('Predicted class');ax.set_ylabel('True class');savefig('07_sensor_history_confusion.png')
