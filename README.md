# 车辆物联网遥测数据分析与告警识别

基于 pandas、NumPy、scikit-learn 的车辆遥测数据分析代码。数据为 3 台设备的车载网关长表记录（173,853 行、52 类变量），分析内容包括数据清洗、长表转宽表、运行状态与告警事件分析，以及告警识别模型的严格评估。

## 数据

将课程提供的 `题2_车辆物联网遥测.csv` 放在项目根目录。字段为：

- `deviceId`：设备编号
- `timestamp`：上报时间
- `value`：变量取值（定位为“纬度,经度,海拔”字符串）
- `variable`：变量名
- `alarmClass`：告警类别（0 正常，1–5 告警）

`vehicle_analysis.py` 默认读取同名文件，也可以在脚本开头修改 `DATA_PATH`。

## 环境

```bash
python -m pip install -r requirements.txt
```

已验证：Python 3.14、pandas 3.0、NumPy 2.4、scikit-learn 1.9、Matplotlib 3.10。

## 运行

```bash
# 1) 基础流程：清洗、宽表、运行特征、告警统计与单标签分类
python vehicle_analysis.py

# 2) 告警前后状态对比、分车辆小时事件数
python improve_4_alarm_context.py --csv 题2_车辆物联网遥测.csv

# 3) 时间分块 + 事件隔离的严格评估，输出每一类的精确率/召回率/F1
python improve_5_strict_eval.py --csv 题2_车辆物联网遥测.csv
```

结果分别写入 `vehicle_results_singlelabel/`、`improve4_results/`、`improve5_results/`。

## 文件说明

| 文件 | 说明 |
|---|---|
| `vehicle_analysis.py` | 数据清洗、分钟宽表、运行特征、告警统计、事件划分与单标签分类 |
| `车辆遥测分析.ipynb` | 与 `vehicle_analysis.py` 对应的交互式分析 |
| `improve_4_alarm_context.py` | 告警前 5 分钟、告警读数、后 5 分钟的车速/转速/水温对比；分车辆小时事件数 |
| `improve_5_strict_eval.py` | 时间前 70% 切分，切点前后各留 30 分钟，跨切点事件整段剔除；输出每类指标 |

## 方法要点

- 长表按 `(deviceId, timestamp)` 保留原始读数，按分钟聚合时只对同一设备做有限前向填充，不跨车辆填充。
- 告警事件按“同设备、同类别、相邻读数间隔 ≤ 120 秒”合并；记录数、事件数、持续时长分别统计。
- 告警前后对比用原始读数计算，避免前向填充把上一段行驶状态带入熄火片段。
- 模型评估不使用随机划分：按时间切分，切点前后各留 30 分钟，并剔除跨切点的告警事件；指标保留每一类的精确率、召回率和 F1。

## 主要结果

- 共识别告警事件 1,078 段，其中类别 3 最多（491 段）。
- 类别 1 告警读数车速 7.6 km/h，类别 3 为 49.8 km/h，类别 4 为 62.1 km/h；类别 4 的转速均值明显高于其他类别。
- 每小时事件数：车-GAB0gBAQ 集中于 11–18 时，车-NAB0gBAQ 集中于 11–17 时，车-twB1gBAQ 全天有事件、14 时最多。
- 严格切分下六分类准确率约 0.81；各类别 F1 差异较大，类别 5 的 F1 约 0.48，说明仅看总体准确率会掩盖少数类的问题。
