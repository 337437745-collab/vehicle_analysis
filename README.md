# 题目二　车辆物联网遥测数据分析与告警识别

《Python 数据分析编程》课程项目。基于 3 台车载网关设备的上报数据（173,853 行长表、52 类变量，时间范围 2020-08-17 至 2020-08-24），完成数据清洗、长表转宽表、运行状态分析、告警事件识别与建模评估。

## 数据

课程提供的 `data/raw_vehicle_telemetry.csv`，字段如下：

- `deviceId`：设备编号
- `timestamp`：上报时间
- `value`：变量取值（定位为“纬度,经度,海拔”字符串）
- `variable`：变量名
- `alarmClass`：告警类别（0 正常，1–5 告警）

## 目录结构

```
data/                     原始数据与清洗后的中间数据
src/                      清洗、探索、告警、建模与报告脚本
figures/                  分析图表
report/                   报告、PPT 与讲解视频
output/metrics.json       全部实验指标
vehicle_analysis.py       单文件版分析脚本
improve_*.py              告警前后对比、严格评估等补充脚本
run_all.py                一键运行主流程
requirements.txt          依赖
```

## 环境

```bash
python -m pip install -r requirements.txt
```

已验证：Python 3.14、pandas 3.0、NumPy 2.4、Matplotlib 3.10、scikit-learn 1.9。

## 运行

```bash
# 全流程：清洗 -> 宽表 -> 探索 -> 告警 -> 建模 -> 报告 -> PPT
python run_all.py
```

也可以分步执行：

```bash
python src/prepare.py
python src/eda.py
python src/alarm.py
python src/model.py
python src/build_report.py
python src/build_ppt.py
```

补充分析：

```bash
python improve_4_alarm_context.py --csv data/raw_vehicle_telemetry.csv
python improve_5_strict_eval.py --csv data/raw_vehicle_telemetry.csv
```

## 分析内容

1. **清洗与规整**：解析时间字段，拆分定位复合字段，处理缺失值与越界值；长表按设备和时间戳转为宽表，并生成 1 分钟、5 分钟聚合表。
2. **运行状态分析**：还原单车单日运行过程，比较三台设备的车速、转速、工况构成与日行驶里程，分析关键信号相关性和时间规律。
3. **告警分析**：统计告警类别分布，反推各类别的触发变量与取值区间，划分告警事件并比较事件前后的车速、转速、水温变化。
4. **建模评估**：读数级六分类、特征消融、留一设备验证、统计异常一致性、提前预警；严格评估按时间切分并在切点前后留出间隔，同时剔除跨切点事件。
5. **结果输出**：报告、PPT、图表与 `output/metrics.json`，正文数字由指标文件读取。

## 主要结果

- 清洗后有效读数 83,359 条，有效定位 79,838 条；
- 时间戳级宽表 18,532 行 × 52 列，填充率 9.0%，前向填充后 19.6%；
- 三台设备分别呈现高速长途、城市起停、短途停放三种用车特征；
- 48.3% 的读数带告警，共识别 1,078 段告警事件；
- 读数级六分类随机划分宏 F1 0.992，严格时间/事件隔离后约 0.76，留一设备验证 0.53–0.66；
- 3σ 统计异常与业务告警并不一致，提前预警 AUC 约 0.73。

## 小组成员

- 周泽林（组长）：数据清洗与规整
- 郭德宇：探索分析与告警分析
- 钟子恒：建模评估与报告呈现
