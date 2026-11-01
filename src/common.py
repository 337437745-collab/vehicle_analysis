# -*- coding: utf-8 -*-
"""
公共配置与工具函数：路径、中文字体、变量中文名映射、告警类别语义、绘图风格。

题目二：车辆物联网遥测数据分析与告警识别
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 路径
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIG = ROOT / "figures"
OUT = ROOT / "output"
RAW_CSV = DATA / "raw_vehicle_telemetry.csv"

for _d in (DATA, FIG, OUT):
    _d.mkdir(parents=True, exist_ok=True)

CLEAN_LONG = DATA / "clean_long.csv.gz"
CLEAN_POS = DATA / "clean_pos.csv.gz"
WIDE_1MIN = DATA / "wide_1min.csv.gz"
METRICS = OUT / "metrics.json"

# ---------------------------------------------------------------- 绘图风格
plt.rcParams.update({
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "figure.dpi": 120,
    "savefig.dpi": 160,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
    "axes.edgecolor": "#8894a3",
    "axes.labelcolor": "#2b3440",
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "xtick.color": "#4a5563",
    "ytick.color": "#4a5563",
})

# 主色调：设备色 + 告警色
DEVICE_COLORS = ["#1f77b4", "#e377c2", "#2ca02c", "#ff7f0e"]
ALARM_COLORS = {
    0: "#b8c2cc",   # 正常
    1: "#4c9be8",   # 侧向冲击
    2: "#f2b134",   # 中低速工况
    3: "#e8743b",   # 急加速
    4: "#c0392b",   # 高负荷/强冲击
    5: "#7d5ba6",   # 熄火静止
}

# ---------------------------------------------------------------- 变量中文名
VAR_CN = {
    "POSITION": "定位(纬度,经度,海拔)",
    "Vehicle speed": "车速(km/h)",
    "ENGINE RPM": "发动机转速(rpm)",
    "ACCELERATION X": "纵向加速度(m/s²)",
    "ACCELERATION Y": "横向加速度(m/s²)",
    "ACCELERATION Z": "垂向加速度(m/s²)",
    "IGNITION_STATUS": "点火状态",
    "IDLING": "怠速标志",
    "TOWING": "拖曳标志",
    "EXTERNAL BATTERY": "外部电池电压(V)",
    "INTERNAL BATTERY": "设备内部电量(%)",
    "THROTTLE POSITION": "节气门位置(%)",
    "RELATIVE THROTTLE POSITION": "相对节气门位置(%)",
    "ABSOLUTE THROTTLE POSITION B": "绝对节气门位置B(%)",
    "ABSOLUTE THROTTLE POSITION D": "绝对节气门位置D(%)",
    "ABSOLUTE THROTTLE POSITION E": "绝对节气门位置E(%)",
    "COMMANDED THROTTLE ACTUATOR": "指令节气门开度(%)",
    "CALCULATED ENGINE LOAD": "计算发动机负荷(%)",
    "ABSOLUTE LOAD VALUE": "绝对负荷值(%)",
    "ENGINE COOLANT TEMPERATURE": "冷却液温度(°C)",
    "CATALYST TEMPERATURE BANK 1 SENSOR 1": "催化器温度(°C)",
    "INTAKE MANIFOLD ABSOLUTE PRESSURE": "进气歧管压力(kPa)",
    "AIR INTAKE TEMPERATURE": "进气温度(°C)",
    "AMBIENT AIR TEMPERATURE": "环境温度(°C)",
    "TIMING ADVANCE": "点火提前角(°)",
    "CONTROL MODULE VOLTAGE": "控制模块电压(V)",
    "RUN TIME SINCE ENGINE START": "发动机启动后运行时长(s)",
    "SHORT TERM FUEL TRIM BANK 1": "短期燃油修正(%)",
    "LONG TERM FUEL TRIM BANK 1": "长期燃油修正(%)",
    "OXYGEN SENSOR 2 - B: SHORT TERM FUEL TRIM": "氧传感器2短期燃油修正",
    "OXYGEN SENSOR 1 - B: SHORT TERM FUEL TRIM": "氧传感器1短期燃油修正",
    "DISTANCE TRAVELED SINCE CODES CLEARED": "清码后行驶里程(km)",
    "DISTANCE TRAVELED WITH MIL ON": "故障灯亮后行驶里程(km)",
    "WARM UPS SINCE CODES CLEARED": "清码后暖机次数",
    "FUEL TANK LEVEL INPUT": "燃油液位(%)",
    "MAF AIR FLOW RATE": "空气流量(g/s)",
    "FUEL RAIL GAUGE PRESSURE": "燃油轨压力(kPa)",
    "FUEL AIR COMMANDED EQUIVALENCE RATE": "指令空燃比当量比",
    "COMMANDED EVAPORATIVE PURGE": "蒸发排放吹扫指令(%)",
    "FUEL SYSTEM STATUS": "燃油系统状态",
    "FUEL TYPE": "燃油类型",
    "HARDWARE STATUS": "硬件状态",
    "MONITOR STATUS SINCE DTCS CLEARED": "清码后监测状态",
    "MONITOR STATUS THIS DRIVE CYCLE": "本周驾驶循环监测状态",
    "OBD STANDARDS THIS VEHICLE CONFORMS TO": "OBD标准",
    "OXYGEN SENSORS PRESENT IN 2 BANKS": "双排氧传感器配置",
    "ACTUAL ENGIN - PERCENT TORQUE": "实际发动机扭矩(%)",
    "DRIVER'S DEMAND ENGIN - PERCENT TORQUE": "驾驶员需求扭矩(%)",
    "ENGINE REFERENCE TORQUE": "发动机参考扭矩(N·m)",
    "ABSOLULTE BAROMETRIC PRESSURE": "大气压力(kPa)",
    "OXYGEN SENSOR 1 - AB: FUEL AIR EQUIVALENCE RATIO": "氧传感器1空燃比当量比",
    "PIDNameNotAvailable": "未命名PID(脏数据)",
}

# ---------------------------------------------------------------- 告警类别语义
# 语义由数据反推得到（见 report 第 5 章：各类别下关键信号的取值分布）
ALARM_CN = {
    0: "正常",
    1: "类别1 低速强冲击",
    2: "类别2 中低速工况",
    3: "类别3 中高速加速",
    4: "类别4 高速高负荷",
    5: "类别5 熄火静止",
}
ALARM_SHORT = {0: "正常", 1: "C1低速冲击", 2: "C2中低速", 3: "C3中高速加速",
               4: "C4高速高负荷", 5: "C5熄火"}

# 参与核心分析的变量
CORE_VARS = [
    "Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y",
    "ACCELERATION Z", "IGNITION_STATUS", "IDLING", "TOWING",
    "EXTERNAL BATTERY", "INTERNAL BATTERY", "THROTTLE POSITION",
    "RELATIVE THROTTLE POSITION", "CALCULATED ENGINE LOAD",
    "ENGINE COOLANT TEMPERATURE",
]

# 物理合理范围（用于标记明显异常点，超界置空而不删除原始记录）
PHYS_RANGE = {
    "Vehicle speed": (0, 220),
    "ENGINE RPM": (0, 8000),
    "ACCELERATION X": (-15, 15),
    "ACCELERATION Y": (-20, 20),
    "ACCELERATION Z": (-20, 20),
    "EXTERNAL BATTERY": (8, 60),
    "INTERNAL BATTERY": (0, 100),
    "ENGINE COOLANT TEMPERATURE": (-40, 130),
    "THROTTLE POSITION": (0, 100),
    "RELATIVE THROTTLE POSITION": (0, 100),
    "CALCULATED ENGINE LOAD": (0, 100),
    "CONTROL MODULE VOLTAGE": (8, 16),
}

DEVICE_LABEL = {}  # 运行期填充：deviceId -> 车1/车2/车3


def slug(name: str) -> str:
    """变量英文名 -> 宽表列名（小写下划线）"""
    import re
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def vname(v: str) -> str:
    """变量英文名 -> 中文名（无映射时原样返回）"""
    return VAR_CN.get(v, v)


def device_short(dev: str) -> str:
    """deviceId 后 8 位即为设备唯一短标识（如 twB1gBAQ）"""
    return "车-" + str(dev)[-8:]


def savefig(fig, name: str, tight=True):
    """保存图片并关闭"""
    path = FIG / name
    if tight:
        fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  [图] {path.name}")
    return path


def load_long():
    """读取清洗后的长表（ts 混合格式，统一转 datetime）"""
    df = pd.read_csv(CLEAN_LONG)
    df["ts"] = pd.to_datetime(df["ts"], format="mixed")
    return df


def load_pos():
    df = pd.read_csv(CLEAN_POS)
    df["ts"] = pd.to_datetime(df["ts"], format="mixed")
    return df


def dump_json(obj, path=METRICS):
    def _default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.ndarray,)):
            return o.tolist()
        if isinstance(o, (pd.Timestamp,)):
            return str(o)
        raise TypeError(str(type(o)))

    old = {}
    if Path(path).exists():
        old = json.loads(Path(path).read_text(encoding="utf-8"))
    old.update(obj)
    Path(path).write_text(json.dumps(old, ensure_ascii=False, indent=2, default=_default),
                         encoding="utf-8")
    print(f"  [指标] 已写入 {Path(path).name} (+{len(obj)} 项)")


def haversine_km(lat1, lon1, lat2, lon2):
    """两点球面距离(km)"""
    r = 6371.0088
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))
