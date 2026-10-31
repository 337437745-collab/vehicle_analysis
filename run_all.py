# -*- coding: utf-8 -*-
"""
一键执行：清洗 -> 探索 -> 告警分析 -> 建模 -> 报告
用法：python run_all.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = [
    ("① 数据清洗与规整", "src/prepare.py"),
    ("② 探索性分析", "src/eda.py"),
    ("③ 告警识别与分析", "src/alarm.py"),
    ("④ 建模与评估", "src/model.py"),
    ("⑤ 生成 Word 报告", "src/build_report.py"),
    ("⑥ 生成汇报 PPT", "src/build_ppt.py"),
]


def main():
    log_dir = ROOT / "output"
    log_dir.mkdir(exist_ok=True)
    t_all = time.time()
    for name, script in STEPS:
        print("=" * 70)
        print(f"{name}  ->  {script}")
        t0 = time.time()
        r = subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        (log_dir / f"log_{Path(script).stem}.txt").write_text(out, encoding="utf-8")
        print(out[-1200:] if len(out) > 1200 else out)
        if r.returncode != 0:
            print(f"!! {script} 运行失败（退出码 {r.returncode}），详见 output/log_*.txt")
            sys.exit(r.returncode)
        print(f"-- 完成，用时 {time.time() - t0:.1f}s")
    print("=" * 70)
    print(f"全流程完成，总用时 {time.time() - t_all:.1f}s")
    print("报告：report/题2_车辆物联网遥测数据分析与告警识别_报告.docx")
    print("图表：figures/*.png    指标：output/metrics.json")


if __name__ == "__main__":
    main()
