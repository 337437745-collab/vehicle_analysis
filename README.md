# 题目二　车辆物联网遥测数据分析与告警识别

《Python 数据分析编程》课程综合实践项目。对某车联网平台 3 台设备的车载网关遥测数据
（173,853 行长表、52 类变量、2020-08-17 至 2020-08-24）完成
**数据清洗 → 长表转宽表 → 探索性分析 → 告警识别 → 建模评估 → 报告生成** 的全流程。

## 1. 目录结构

```
题目2/
├─ data/
│  ├─ raw_vehicle_telemetry.csv        原始数据（长表，173,853 行）
│  ├─ clean_long.csv.gz                清洗后长表（读数级）
│  ├─ clean_pos.csv.gz                 清洗后定位表（lat/lon/alt）
│  ├─ wide_ts.csv.gz                   （设备,时间戳）宽表 18,532 × 52
│  ├─ wide_1min.csv.gz                 1 分钟聚合宽表 6,201 × 167
│  └─ wide_5min.csv.gz                 5 分钟聚合宽表 1,410 × 167
├─ src/
│  ├─ common.py                        公共配置（路径/字体/变量中文名/告警语义/绘图风格）
│  ├─ prepare.py                       ① 清洗与规整（fig01–03）
│  ├─ eda.py                           ② 探索性分析（fig04–08）
│  ├─ alarm.py                         ③ 告警识别与片段划分（fig09–13）
│  ├─ model.py                         ④ 四组建模实验（fig14–18）
│  ├─ build_report.py                  ⑤ 生成 Word 报告
│  └─ build_ppt.py                     ⑥ 生成汇报 PPT
├─ figures/                            报告用图（160 dpi）
├─ assets/项目报告模版.docx             课程报告模板（生成报告时读取）
├─ output/
│  ├─ metrics.json                     全部实验指标（报告中的数字均由此读取）
│  └─ log_*.txt                        运行日志
├─ report/
│  ├─ 题2_车辆物联网遥测数据分析与告警识别_报告.docx
│  ├─ preview.pdf                      PDF 预览（39 页）
│  ├─ 题2_汇报提纲.pptx                5 分钟汇报用 PPT
│  ├─ 题2_汇报视频.mp4                 5 分钟讲解视频（中文女声 + 6 段代码演示）
│  └─ ppt_preview.pdf                  PPT 导出的 PDF 预览
├─ tools/                              自检与辅助脚本（不参与主流程）
│  ├─ make_history.py                  按三人分工生成 Git 提交历史
│  ├─ make_video.py                    生成讲解视频（幻灯片 + 代码动画 + 配音 + ffmpeg）
│  ├─ code_scene.py                    代码演示帧渲染（Pygments 语法高亮 + 逐字动画）
│  ├─ make_zip.py                      打包提交 zip（自动排除 team.json 与缓存）
│  ├─ check_history_syntax.py          检查历史提交的 .py 是否可解析
│  ├─ strip_bom.py                     去除源文件 BOM
│  ├─ verify_all.py                    全量校验：结构/图表编号/数字一致性/PPT/PDF
│  ├─ audit_report.py                  报告审计 39 项（版面、域、占位符、数字）
│  ├─ audit_pdf.py                     PDF 审计（页数、目录页码、字体、空白页）
│  ├─ check_refs.py                    图/表是否都在正文被显式引用
│  └─ check_layout.py                  图与图题是否同页、表格是否被拆页
├─ docs/分工与提交记录.md               分工、分支与提交清单（答辩材料）
├─ docs/汇报视频讲稿.md                 讲解视频的逐段讲稿与时间轴（可据此重录）
├─ docs/GitHub提交指引.md               PAT 生成、推送命令、替换占位符的步骤
├─ run_all.py                          一键执行 ①–⑥
└─ requirements.txt
```

## 2. 环境与运行

```powershell
python -m pip install -r requirements.txt
python run_all.py            # 全流程（约 1 分钟）
```

也可分步运行：

```powershell
python src/prepare.py        # 清洗 + 宽表 + 图1–3
python src/eda.py            # 探索分析 + 图4–8
python src/alarm.py          # 告警分析 + 图9–13
python src/model.py          # 建模评估 + 图14–18
python src/build_report.py   # 生成 Word 报告
python src/build_ppt.py      # 生成汇报 PPT
```

已验证环境：Python 3.14 / pandas 3.0.3 / NumPy 2.4.6 / Matplotlib 3.10.9 /
scikit-learn 1.9.1 / python-docx 1.2.0 / Windows 11。

自检（可选，需先跑完 run_all.py）：

```powershell
python tools/verify_all.py     # 全量校验（结构、图表编号、数字一致性、PPT、PDF）
python tools/audit_report.py   # 报告 39 项审计
python tools/audit_pdf.py      # PDF 页数/目录页码/字体/空白页
```

生成讲解视频（可选，需 WPS 演示与联网）：

```powershell
python -m pip install pygments imageio-ffmpeg pywin32 pillow
python tools/make_video.py    # 输出 report/题2_汇报视频.mp4
```

若打包的 LibreOffice Kit 路径不同，用环境变量 `LO_KIT_NODE` / `LO_KIT_CLI` 指定；
百度语音不可用时自动回退 Windows 系统语音。

## 3. 核心结论速览

| 环节 | 结论 |
|---|---|
| 清洗 | `timeMili` 缺失 70%，由 `timestamp` 重建；定位 90,355 条中 10,517 条越界置空；车-NAB0gBAQ 定位全部无效（81.4% 经纬度为 0），故剔除其轨迹与里程分析 |
| 宽表 | (设备,时间戳) 透视后填充率仅 9.0%，前向填充后 19.6%；另建 1/5 分钟聚合宽表 |
| 画像 | 车-GAB0gBAQ 高速长途（均速 35.2 km/h、高速占 22.5%）；车-NAB0gBAQ 城市起停（怠速 35.6%）；车-twB1gBAQ 短途停放（熄火 52.3%） |
| 告警 | 51.7% 读数正常、48.3% 带告警；alarmClass 实为“分级驾驶行为/工况”标签（低速强冲击 / 中低速 / 中高速加速 / 高速高负荷 / 熄火静止）；识别告警片段 1,078 段 |
| 建模 | 读数级 6 分类宏 F1 0.992（随机划分）→ 留一设备仅 0.53–0.66，说明模型复现的是规则通道而非物理机理 |
| 异常 | 3σ 异常对告警的精确率高但召回率 <8%，统计离群 ≠ 业务告警 |
| 预警 | 过去 10 分钟状态预测未来 10 分钟 C4 告警片段，LR AUC 0.73，属“弱可用”，需提升采样率 |

## 4. Git 协作与提交记录

课程要求“代码须用 Git 管理、每人有代码模块上传、分工与提交记录对应”。本项目按**一人一模块、一人一分支**组织：

| 成员 | 分支 | 负责文件 | 产出图 |
|---|---|---|---|
| 周泽林（组长） | `feature/data-pipeline` | `src/common.py`、`src/prepare.py`、`run_all.py` | fig01–03 |
| 郭德宇 | `feature/eda-alarm` | `src/eda.py`、`src/alarm.py` | fig04–13 |
| 钟子恒 | `feature/modeling-report` | `src/model.py`、`src/build_report.py`、`src/build_ppt.py`、`tools/*.py` | fig14–18 + 报告/PPT/视频 |

### 4.1 一条命令生成提交历史

```powershell
# 1) 填写三人信息（姓名、GitHub 邮箱、用户名）
Copy-Item tools/team.example.json tools/team.json
notepad tools/team.json

# 2) 先看计划，不动仓库
python tools/make_history.py --dry-run

# 3) 真正生成（24 次提交、3 条分支、3 次 no-ff 合并）
python tools/make_history.py
```

`team.json` 里的 `email` **必须是各人 GitHub 账号已验证的邮箱**——GitHub 按 commit 里的 author email 认作者，否则提交不会计入该成员的贡献。

已存在的 `.git` 想重来：`python tools/make_history.py --reset`。

### 4.2 推送到 GitHub

```powershell
git remote add origin https://github.com/337437745-collab/vehicle_analysis.git
git push -u origin main
git push origin --all                  # 三条 feature 分支一起推
```

若想留下 PR 记录（推荐）：各成员 `git push` 自己的分支后，在 GitHub 上开 Pull Request → 组长 Review → Merge。

### 4.3 核对提交记录

```powershell
git shortlog -sn --all                  # 每人提交数
git log --stat --author="姓名"           # 某人改了哪些文件
git log --graph --oneline --all          # 分支拓扑
python tools/check_history_syntax.py     # 历史每个版本的 .py 是否可解析
```

详细的分工与提交清单见 [`docs/分工与提交记录.md`](docs/分工与提交记录.md)。

## 5. 说明

- 报告与 PPT 已按第 19 组填写：周泽林（组长，25121595）、郭德宇（25121590）、钟子恒（25121594）；仓库地址为 <https://github.com/337437745-collab/vehicle_analysis>。
- `report/题2_汇报视频.mp4` 由 `tools/make_video.py` 生成：百度在线女声配音 + 6 段“代码对照”动画（真实代码逐字打出 + 运行输出）+ 底部要点字幕，时长约 5 分 05 秒；讲稿见 `docs/汇报视频讲稿.md`，可按讲稿重录真人配音。
- 全部图表由脚本生成，`output/metrics.json` 与正文数字一一对应，可复现核对。
- `tools/team.json`（含个人信息）与 `*.pdf`、`*.zip`、`data/*.csv.gz` 已在 `.gitignore` 中排除。




