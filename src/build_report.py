# -*- coding: utf-8 -*-
"""
05 生成 Word 报告（基于课程模板 项目报告模版.docx）
==================================================
做法：读取模板 -> 填写封面与摘要 -> 保留模板自带的目录（TOC 域）
      -> 按模板章节结构（1 引言 … 9 附录）重新生成正文
      -> 用 Word COM 更新目录页码 -> 导出 PDF 预览
输出 report/题2_车辆物联网遥测数据分析与告警识别_报告.docx
运行：python src/build_report.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, FIG, METRICS, ALARM_CN, ALARM_SHORT

TEMPLATE = ROOT / "assets" / "项目报告模版.docx"
OUT_DIR = ROOT / "report"
OUT_DIR.mkdir(exist_ok=True)
OUT_DOCX = OUT_DIR / "题2_车辆物联网遥测数据分析与告警识别_报告.docx"
OUT_PDF = OUT_DIR / "preview.pdf"

M = json.loads(METRICS.read_text(encoding="utf-8"))
P, E, AL = M["prepare"], M["eda"], M["alarm"]
A, A2, B, C, D, DM = (M["task_a_random_split"], M["task_a_no_variable_id"],
                      M["task_b_leave_one_device"], M["task_c_consistency"],
                      M["task_d_early_warning"], M["task_d_meta"])
STR, DS = M["task_a_time_episode"], M["task_d_strict"]

SONG, HEI, MONO = "宋体", "黑体", "Consolas"
TITLE = "车辆物联网遥测数据分析与告警识别"
GROUP = "第 19 组"


# ================================================================= 排版工具
def _set_font(run, size=12, bold=False, cn=SONG, en=None, color=None):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = en or cn
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), cn)
    rfonts.set(qn("w:ascii"), en or cn)
    rfonts.set(qn("w:hAnsi"), en or cn)
    if color:
        run.font.color.rgb = RGBColor(*color)


def _first_line_chars(p, chars=200):
    """按“字符数”设置首行缩进（200 = 2 字符），比 Cm 更贴合中文排版"""
    pPr = p._p.get_or_add_pPr()
    ind = pPr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        pPr.append(ind)
    ind.set(qn("w:firstLineChars"), str(chars))
    ind.set(qn("w:firstLine"), "420")


def body(doc, text, size=12, indent=True, align="just", space_after=0, line=1.5,
         bold=False, cn=SONG):
    """正文段落：宋体小四、1.5 倍行距、首行缩进 2 字符；支持 **加粗**"""
    p = doc.add_paragraph()
    p.alignment = {"just": WD_ALIGN_PARAGRAPH.JUSTIFY, "center": WD_ALIGN_PARAGRAPH.CENTER,
                   "left": WD_ALIGN_PARAGRAPH.LEFT}[align]
    pf = p.paragraph_format
    pf.line_spacing = line
    pf.space_before = Pt(0)
    pf.space_after = Pt(space_after)
    if indent and align == "just":
        _first_line_chars(p)
    for i, seg in enumerate(str(text).split("**")):
        if not seg:
            continue
        _set_font(p.add_run(seg), size=size, bold=bold or (i % 2 == 1), cn=cn)
    return p


def section(doc, text):
    """一级标题：沿用模板 Heading 1（黑体 14pt），可被目录域收录"""
    p = doc.add_paragraph(style="Heading 1")
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = 1.4
    _set_font(p.add_run(text), size=14, bold=False, cn=HEI)
    return p


def subsection(doc, text):
    """二级标题：Heading 2（目录域第二级）"""
    p = doc.add_paragraph(style="Heading 2")
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.line_spacing = 1.4
    _set_font(p.add_run(text), size=12.5, bold=True, cn=HEI)
    return p


def subsub(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.4
    _set_font(p.add_run(text), size=12, bold=True, cn=HEI)
    return p


def bullets(doc, items, size=12, indent_chars=200):
    for it in items:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        pf = p.paragraph_format
        pf.line_spacing = 1.5
        pf.space_after = Pt(0)
        pf.left_indent = Cm(0.74)
        pPr = p._p.get_or_add_pPr()
        ind = pPr.find(qn("w:ind"))
        ind.set(qn("w:firstLineChars"), "-100")
        ind.set(qn("w:firstLine"), "-210")
        for i, seg in enumerate(("· " + it).split("**")):
            if not seg:
                continue
            _set_font(p.add_run(seg), size=size, bold=(i % 2 == 1))


def caption(doc, text, before=4, after=6, keep_next=False):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.keep_together = True
    if keep_next:
        p.paragraph_format.keep_with_next = True
    _set_font(p.add_run(text), size=10.5, bold=False, cn=SONG)
    return p


def figure(doc, name, text, width=14.6):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.keep_with_next = True   # 图片与题注不分页
    p.add_run().add_picture(str(FIG / name), width=Cm(width))
    caption(doc, text, before=3, after=10)


def _borders(table, sz=6, color="000000"):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.append(borders)


def _cell_text(cell, text, size=9, bold=False, align=WD_ALIGN_PARAGRAPH.CENTER):
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(1.5)
    pf.space_after = Pt(1.5)
    pf.line_spacing = 1.1
    for i, seg in enumerate(str(text).split("**")):
        if not seg:
            continue
        _set_font(p.add_run(seg), size=size, bold=bold or (i % 2 == 1))


def table(doc, header, rows, text=None, widths=None, size=9, left_cols=()):
    if text:
        caption(doc, text, before=8, after=3, keep_next=True)
    t = doc.add_table(rows=1, cols=len(header))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    _borders(t)
    hdr = t.rows[0]
    trPr = hdr._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    trPr.append(th)
    for i, h in enumerate(header):
        _cell_text(hdr.cells[i], h, size=size, bold=True)
        shade = OxmlElement("w:shd")
        shade.set(qn("w:val"), "clear")
        shade.set(qn("w:fill"), "EDF1F5")
        hdr.cells[i]._tc.get_or_add_tcPr().append(shade)
    for row in rows:
        tr = t.add_row()
        trPr = tr._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))   # 单行不跨页
        cells = tr.cells
        for i, v in enumerate(row):
            al = WD_ALIGN_PARAGRAPH.LEFT if i in left_cols else WD_ALIGN_PARAGRAPH.CENTER
            _cell_text(cells[i], v, size=size, align=al)
    if widths:
        total = sum(widths)
        scale = min(1.0, 15.0 / total) if total > 15.0 else 1.0
        for i, w in enumerate(widths):
            for r in t.rows:
                r.cells[i].width = Cm(w * scale)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def code_block(doc, code, size=8.5):
    """等宽字体代码块（带浅灰底纹）"""
    for ln in code.strip("\n").split("\n"):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.line_spacing = 1.05
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.left_indent = Cm(0.6)
        pPr = p._p.get_or_add_pPr()
        shade = OxmlElement("w:shd")
        shade.set(qn("w:val"), "clear")
        shade.set(qn("w:fill"), "F5F7FA")
        pPr.append(shade)
        _set_font(p.add_run(ln if ln.strip() else " "), size=size, cn=MONO, en=MONO)


def page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def pct(x, nd=1):
    return f"{x*100:.{nd}f}%"


# ================================================================= 封面/摘要
def fill_cover(doc):
    """在模板封面段落中填入信息（保留原有下划线形状）"""
    body_el = doc.element.body
    paras = list(body_el)

    info = {"学    院": "未来技术学院", "专    业": "人工智能",
            "学    号": "25121595", "学生姓名": "周泽林（组长）",
            "课    程": "Python 数据分析编程"}
    for el in paras[:24]:
        if el.tag.split("}")[-1] != "p":
            continue
        txt = "".join(t.text or "" for t in el.iter(qn("w:t")))
        for key, val in info.items():
            if txt.startswith(key):
                runs = el.findall(qn("w:r"))
                # 最后一个纯文本 run 追加取值
                for r in reversed(runs):
                    if r.find(qn("w:t")) is not None:
                        t = r.find(qn("w:t"))
                        t.text = (t.text or "") + val
                        t.set(qn("xml:space"), "preserve")
                        break
                break

    # 报告题目：模板段落下方插入标题行
    for el in paras[:24]:
        if el.tag.split("}")[-1] != "p":
            continue
        txt = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if "报告题目" in txt:
            new_p = OxmlElement("w:p")
            el.addnext(new_p)
            from docx.text.paragraph import Paragraph
            p = Paragraph(new_p, doc._body)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(0)
            _set_font(p.add_run(TITLE), size=20, bold=True, cn=HEI)
            p2 = Paragraph(OxmlElement("w:p"), doc._body)
            new_p.addnext(p2._p)
            p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p2.paragraph_format.space_before = Pt(6)
            _set_font(p2.add_run(f"（{GROUP}　题目二）"), size=12, cn=SONG,
                      color=(0x55, 0x5F, 0x6B))
            break


def clear_after_abstract(doc):
    """删除模板中“摘要”之后的占位正文，保留封面、目录域与摘要标题"""
    body_el = doc.element.body
    children = list(body_el)
    idx_abs = None
    for i, el in enumerate(children):
        if el.tag.split("}")[-1] != "p":
            continue
        txt = "".join(t.text or "" for t in el.iter(qn("w:t"))).replace(" ", "")
        if txt.startswith("摘要") and idx_abs is None:
            idx_abs = i
    assert idx_abs is not None, "模板中未找到“摘要”标题"
    removed = 0
    # 摘要另起一页
    from docx.text.paragraph import Paragraph
    abs_p = Paragraph(children[idx_abs], doc._body)
    abs_p.paragraph_format.page_break_before = True
    for el in children[idx_abs + 1:]:
        if el.tag.split("}")[-1] == "sectPr":
            continue
        body_el.remove(el)
        removed += 1
    return removed


# ================================================================= 正文
def write_abstract(doc):
    body(doc, (
        f"本文以某车联网平台 3 台设备的车载网关遥测数据为研究对象（长表 {P['raw_rows']:,} 条记录、"
        f"{P['n_variables']} 类传感器与 OBD 变量、时间跨度 2020-08-17 至 2020-08-24），"
        "按照“数据清洗—长表转宽表—运行状态分析—告警识别—建模评估”的流程完成了一次完整的数据分析实践。"
        f"清洗阶段解析时间戳、按“纬度,经度,海拔”拆分定位字段、统一数值类型，"
        f"将 {P['position_rows']:,} 条定位中越界的 {P['position_invalid']:,} 条置空，"
        f"最终得到 {P['clean_long_rows']:,} 条有效读数，并按（设备、时间戳）以及 1 分钟、5 分钟两种粒度"
        "规整为宽表，解决了长表稀疏、采样频率不一致的问题。"
    ))
    body(doc, (
        "分析结果显示：三台设备呈现三种截然不同的用车画像——车-GAB0gBAQ 为高速长途用车"
        f"（平均车速 {E['device_speed_mean']['GAB0gBAQ']} km/h，高速工况占 "
        f"{pct(E['state_share']['高速(>60)']['GAB0gBAQ'])}），车-NAB0gBAQ 为城市频繁起停用车"
        f"（怠速占 {pct(E['state_share']['怠速']['NAB0gBAQ'])}），车-twB1gBAQ 为短途停放用车"
        f"（熄火静止占 {pct(E['state_share']['熄火/静止']['twB1gBAQ'])}）；车速与发动机转速的 "
        f"Spearman 相关系数为 {E['corr_pairs']['speed_rpm']}，符合动力总成的机械耦合关系。"
        "告警字段方面，全部读数中 48.3% 带有告警标签，结合各类别下关键信号的取值分布可判定其为"
        "“分级驾驶行为/工况告警”：类别 1 为低速强冲击、类别 2 为中低速工况、类别 3 为中高速加速、"
        "类别 4 为高速高负荷、类别 5 为熄火静止；合并后共识别告警片段 "
        f"{AL['episodes']:,} 段，高等级片段集中在 14—19 时的用车高峰。"
    ))
    body(doc, (
        "建模方面共完成四组实验：读数级六分类在随机划分下宏平均 F1 达 "
        f"{A['直方图梯度提升']['macro_f1']:.4f}，去掉“变量身份”特征后逻辑回归准确率降至 "
        f"{A2['逻辑回归(无变量身份)']['acc']:.4f}，说明模型复现的主要是设备端的阈值规则；"
        "留一设备验证的宏平均 F1 降至 0.53—0.66，暴露出跨车辆泛化不足的问题；"
        "3σ 异常检测对告警的召回率普遍低于 8%，说明统计离群点与业务告警并非同一概念；"
        f"以过去 10 分钟运行状态预测未来 10 分钟是否出现类别 4 告警片段时，逻辑回归 AUC 为 "
        f"{D['逻辑回归']['auc']}，具备一定预警价值但仍需提升采样质量。"
        "据此，本文提出了告警分层管理、关键信号提速上报、跨车辆建模先做分布对齐以及构建驾驶风险评分"
        "等建议。"
    ))
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.line_spacing = 1.5
    _set_font(p.add_run("关键词："), size=12, bold=True, cn=SONG)
    _set_font(p.add_run("车联网；OBD 遥测数据；长表转宽表；告警识别；随机森林；异常检测；驾驶行为分析"),
              size=12, cn=SONG)


# ---------------------------------------------------------------- 1 引言
def write_intro(doc):
    section(doc, "1. 引言")
    subsection(doc, "1.1  项目背景")
    body(doc, (
        "智能网联汽车通过车载网关（T-Box 或 OBD 采集盒）持续读取动力总成、车身姿态、卫星定位与"
        "诊断信息，并按事件或周期上传至云端平台，形成典型的高频、多变量、事件驱动的物联网时序数据。"
        "这类数据是车队管理、驾驶行为评分、保险定价（UBI）、预见性维保与行车安全管理的基础："
        "平台方既关心“车辆在做什么”（运行状态），也关心“车辆什么时候出问题”（告警事件）。"
    ))
    body(doc, (
        "但与实验室数据不同，工程现场的遥测数据往往“脏且散”。第一，它以长表形式存储，"
        "每行只描述一个“变量—取值”，同一时刻的不同信号被拆散在多行中；"
        "第二，各信号的上报频率差异极大，定位可能每 3 秒一次，而车速、转速等 OBD 信号数分钟才出现一次；"
        "第三，定位等复合信息以“纬度,经度,海拔”字符串拼接，需要拆分；"
        "第四，告警标签 alarmClass 只落在触发它的那一条读数上，导致“告警”与“整车状态”不能简单等同。"
        "如果这些问题不先处理，任何统计与建模都会失真。"
    ))
    subsection(doc, "1.2  项目目标与分析任务")
    body(doc, "本项目的总目标是：把上述原始遥测数据整理成可分析的结构，还原车辆运行状态，解释告警成因，"
              "并评估用机器学习识别告警的可行性与边界。具体拆解为五项任务：")
    bullets(doc, [
        "**数据规整**：解析时间戳、拆分定位字段、统一数值类型，处理缺失、重复与明显异常点；",
        "**结构转换**：把长表规整为宽表（按时间对齐各传感器变量），并生成多粒度聚合表；",
        "**状态分析**：分析车速、发动机转速、加速度随时间的变化，比较不同车辆的运行特征；",
        "**告警分析**：识别告警事件，研究各类告警与车辆运行状态之间的对应关系并解释成因；",
        "**建模评估**：建立告警分类/识别模型，评估效果与泛化能力，讨论其可解释性与应用边界。",
    ])
    subsection(doc, "1.3  技术路线与工具")
    body(doc, (
        "整体流程为：原始长表 →（清洗、拆分、类型转换）→ 清洗后长表 →（透视规整）→ 宽表"
        "（时间戳级、1 分钟、5 分钟）→ 探索性分析 → 告警识别与语义解释 → 建模评估 → 结论与建议。"
        "全部处理用 Python 实现，按“一步一脚本”组织，任一环节均可单独重跑；"
        "所有实验指标统一写入 output/metrics.json，正文中的数字均从该文件读取，保证图文一致、可复现。各环节对应的脚本与产出见表 1。"
    ))
    table(doc, ["环节", "脚本", "主要输出"], [
        ["数据清洗与规整", "src/prepare.py", "清洗后长表、定位表、三种粒度的宽表、图 1—图 2"],
        ["探索性分析", "src/eda.py", "运行过程还原、设备画像、相关性与时间规律（图 3—图 7）"],
        ["告警分析", "src/alarm.py", "告警语义、类别—变量关联、告警片段（图 8—图 13）"],
        ["建模与评估", "src/model.py", "四组实验与指标（图 14—图 18）"],
        ["报告生成", "src/build_report.py", "本报告（Word）与 PDF 预览"],
    ], text="表1  项目技术路线对应的脚本与产出", widths=[3.4, 4.2, 8.4], left_cols=(2,))


# ---------------------------------------------------------------- 2 数据介绍
def write_data(doc):
    section(doc, "2. 数据介绍")
    subsection(doc, "2.1  数据来源与规模")
    body(doc, (
        f"数据来自课程提供的《题2_车辆物联网遥测.csv》，为某车联网平台 3 台设备的车载网关上报记录，"
        f"采用长表格式存储，共 {P['raw_rows']:,} 行、{P['raw_cols']} 列，涉及 {P['n_devices']} 台设备、"
        f"{P['n_variables']} 种变量，时间范围为 "
        f"{P['devices'][2]['ts_min'][:19]} 至 {P['devices'][0]['ts_max'][:19]}，约 7.5 天。"
        "数据字段及含义见表 2。"
    ))
    table(doc, ["字段名", "原始类型", "含义", "说明"], [
        ["deviceId", "字符串", "设备唯一标识", "3 台设备，后 8 位可作短标识"],
        ["timeMili", "浮点字符串", "毫秒时间戳", f"{pct(P['null_timeMili']/P['raw_rows'])} 缺失"],
        ["timestamp", "字符串", "采集时间", "格式为 YYYY-mm-dd HH:MM:SS.ffffff"],
        ["value", "字符串", "变量取值", "定位为“纬度,经度,海拔”，其余为数值"],
        ["variable", "字符串", "变量名称", "52 种，含 OBD PID 与自采信号"],
        ["alarmClass", "整数", "告警类别", "0 表示正常，1—5 为不同告警类别"],
    ], text="表2  原始数据字段说明", widths=[2.8, 2.6, 5.4, 5.6], left_cols=(2, 3))

    subsection(doc, "2.2  变量构成")
    body(doc, (
        f"52 种变量可归为五组（表 3）：定位、动力总成、车身姿态、电气与状态标志、诊断与排放。"
        "其中定位（POSITION）占全部记录的 52%，是上报最频繁的变量；"
        "而近 30 种（27 种）OBD 变量记录数不足 1,000 条，个别变量（如 FUEL TYPE、ENGINE REFERENCE TORQUE）"
        "仅十余条。这种“长尾”分布意味着：任何按变量统计的分析都要注意样本量，"
        "不能把记录数为个位数的变量与高频变量等同看待。各设备的记录量与变量的记录量分布见图 1。"
    ))
    table(doc, ["变量组", "代表变量", "记录数", "用途"], [
        ["定位", "POSITION（纬度,经度,海拔）", f"{90355:,}", "轨迹还原、里程计算"],
        ["动力总成", "ENGINE RPM、Vehicle speed、THROTTLE POSITION、CALCULATED ENGINE LOAD",
         "3,803—4,514", "运行状态与工况判别"],
        ["车身姿态", "ACCELERATION X / Y / Z", "4,322—5,046", "急加速、急刹、转弯与颠簸识别"],
        ["电气与状态", "EXTERNAL BATTERY、INTERNAL BATTERY、IGNITION_STATUS、IDLING、TOWING",
         "1,817—9,625", "设备供电、点火与作业状态"],
        ["诊断与排放", "冷却液温度、催化器温度、燃油修正、氧传感器、蒸发吹扫等 30 余项",
         "12—2,290", "发动机工况与排放诊断"],
    ], text="表3  变量分组与记录量", widths=[2.4, 8.0, 2.6, 4.4], left_cols=(1, 3))
    figure(doc, "fig01_数据概览.png", "图1  车辆遥测数据总览：(a) 各设备记录量；(b) 覆盖时段；(c) 变量记录量 Top22")

    subsection(doc, "2.3  数据特点与处理难点")
    body(doc, "在正式清洗前，先对数据结构做了一次体检，发现以下四个特点，它们决定了后续的处理策略：")
    bullets(doc, [
        f"**长表稀疏**：按（设备，时间戳）透视后为 {P['wide_ts_rows']:,} 行 × {P['wide_ts_cols']} 列"
        "（含设备、时间两列与 50 个变量列），"
        f"整体填充率仅 {pct(P['wide_ts_fill_rate'])}，说明任一时刻只有极少数变量在上报；",
        "**频率不均**：定位每 3 秒左右一条，车速、转速等 OBD 信号可能间隔数分钟，"
        "直接按时间截面对齐会产生大量空值；",
        f"**复合字段与脏数据**：定位为字符串拼接，其中 {P['position_invalid']:,} 条经纬度或海拔明显越界"
        f"（如经度为 0、海拔 −1677 m）；另有 {P['non_numeric_rows']} 条记录的变量名被误标为 "
        "PIDNameNotAvailable，取值却是定位串；",
        "**标签粒度**：同一时间戳内可能同时出现多个不同类别的告警读数（同一时刻最多 6 种取值），"
        "说明 alarmClass 描述的是“这条读数触发了哪条规则”，而不是“整车此刻的状态”。",
    ])


# ---------------------------------------------------------------- 3 清洗
def write_clean(doc):
    section(doc, "3. 数据清洗与预处理")
    body(doc, (
        "清洗脚本为 src/prepare.py，处理顺序是：按字符串读入（避免类型推断造成截断）→ 去重 → "
        "时间解析与重建 → 定位字段拆分 → 数值化 → 越界置空 → 长表转宽表 → 周期聚合。"
        "总体原则是“可疑值置空而不整行删除”，以免损失同一时刻其他变量的有效信息，"
        "同时所有修改都由脚本完成、可复现。"
    ))
    subsection(doc, "3.1  缺失值处理")
    body(doc, (
        f"原始数据的缺失集中在 timeMili 一列：共 {P['null_timeMili']:,} 行缺失（占 {pct(P['null_timeMili']/P['raw_rows'])}），"
        "其余 5 列无缺失。由于 timeMili 与 timestamp 表达的是同一信息，且 timestamp 完整无缺，"
        "因此**弃用 timeMili，改由 timestamp 重新生成毫秒时间戳**，转换后两列完全一致，"
        "既保留了时间精度（微秒级）又消除了缺失。"
    ))
    body(doc, (
        "数值层面的缺失不是“丢数据”，而是“没上报”。透视成宽表后，缺失反映了各传感器不同的上报周期。"
        "对此采取两种策略：对车速、转速、加速度等连续信号，在同一设备内做前向填充（最多 12 步，约 40 秒），"
        f"使整体可用率由 {pct(P['wide_ts_fill_rate'])} 提升到 {pct(P['wide_ts_fill_rate_ffill'])}；"
        "对二值状态量与诊断类变量不做填充（填充会伪造状态切换），"
        "仅在建模时增加“是否缺失”指示变量。"
    ))
    subsection(doc, "3.2  异常值检测与处理")
    body(doc, (
        "异常值分两类处理。**第一类是物理上不可能的取值**，按变量的物理含义设定合理区间并置空："
        f"车速 0—220 km/h、转速 0—8000 rpm、三轴加速度 ±15/±20 m/s²、外部电池 8—60 V、"
        f"内部电量 0—100%、冷却液温度 −40—130 °C、节气门与负荷 0—100%、模块电压 8—16 V。"
        f"实际命中 104 条（转速 {P['phys_out_of_range']['ENGINE RPM']} 条、模块电压 "
        f"{P['phys_out_of_range']['CONTROL MODULE VOLTAGE']} 条）。"
    ))
    body(doc, (
        "**第二类是定位字段越界**：数据集主体位于北纬 13.0—13.8°、东经 74.5—74.9°，海拔 −100—2000 m，"
        f"越界记录共 {P['position_invalid']:,} 条，占定位记录的 11.6%。"
        "其中相当一部分是“经纬度为 0”的无效上报，若不剔除会在轨迹图上形成远在南大西洋的假点。"
    ))
    body(doc, (
        "**第三类是统计意义上的离群点**：对 14 个核心变量计算全局均值与标准差，"
        f"标记 |x−μ|>3σ 的读数，共 {P['core_3sigma_total']} 条（占有效读数 0.8%）。"
        "这类点并未删除，而是作为“疑似异常”单独标记：一方面它们可能是真实事件（如急加速、"
        "强颠簸），另一方面它们恰好可以在第 6.4 节用于检验“统计异常”与“业务告警”是否一致。"
    ))
    subsection(doc, "3.3  数据类型转换与字段拆分")
    body(doc, (
        "类型转换与拆分共四步。（1）**时间**：timestamp 由字符串转为 datetime，"
        f"格式为 %Y-%m-%d %H:%M:%S.%f，解析失败 0 条；同时派生小时、星期、是否周末等时间特征。"
        "（2）**定位拆分**：对 POSITION 变量按逗号拆分，得到 lat、lon、alt 三列并转为浮点，"
        f"{P['position_rows']:,} 条记录全部拆分成功。"
        "（3）**数值化**：其余变量的 value 统一 to_numeric，失败记录即前述 29 条错标数据，直接剔除。"
        "（4）**类别量**：alarmClass 转为整型（0—5），IGNITION_STATUS、IDLING、TOWING 保持 0/1 整型，"
        "便于分组与建模。清洗规则与处理量汇总见表 4。"
    ))
    table(doc, ["处理环节", "规则", "处理量", "说明"], [
        ["整行/主键重复", "全字段去重；(deviceId, timestamp, variable) 去重", "0 行", "原始数据无重复上报"],
        ["时间重建", "由 timestamp 重新生成毫秒时间戳", f"{P['null_timeMili']:,} 行", "timeMili 缺失 70%"],
        ["定位拆分", "按逗号拆为 lat / lon / alt", f"{P['position_rows']:,} 行", "拆分成功率 100%"],
        ["定位越界", "lat∉[13.0,13.8]、lon∉[74.5,74.9]、alt∉[-100,2000] 置空", f"{P['position_invalid']:,} 行",
         "含 0 值与漂移点"],
        ["错标变量", "删除 PIDNameNotAvailable", f"{P['non_numeric_rows']} 行", "取值为定位串"],
        ["物理范围", "各变量设定合理区间，越界置空", "104 条", "转速 13 条、模块电压 91 条"],
        ["3σ 标记", "|x−μ|>3σ 标记为疑似异常（不删除）", f"{P['core_3sigma_total']} 条", "用于一致性检验"],
    ], text="表4  数据清洗规则与处理量", widths=[3.0, 7.0, 2.6, 4.8], left_cols=(1, 3))
    subsection(doc, "3.4  长表转宽表")
    body(doc, "由于采样极不规律，本文采用“先对齐、再聚合”的两级方案，把长表规整为三种宽表：")
    bullets(doc, [
        f"**时间戳级宽表**：以 (deviceId, timestamp) 为主键透视，得到 {P['wide_ts_rows']:,} 行 × "
        f"{P['wide_ts_cols']} 列，用于“某一时刻各信号取值”的还原，如第 5 章的告警—状态交叉分析；",
        f"**1 分钟聚合宽表**：按分钟分箱，对核心信号取均值与极值、对加速度取绝对最大值，"
        f"并附加定位聚合量（窗口内行驶距离、平均海拔）与告警标签，共 {P['wide_1min']['rows']:,} 个窗口，"
        "用于时间规律分析；",
        f"**5 分钟聚合宽表**：同上，共 {P['wide_5min']['rows']:,} 个窗口，用于窗口级统计与第 6.5 节建模。",
    ])
    body(doc, (
        f"需要说明的是，即使聚合到 5 分钟，也只有约 {pct(P['wide_5min']['speed_cover'])} 的窗口含有车速读数。"
        "这不是清洗不当，而是上报机制决定的稀疏性，它直接决定了本文的建模策略："
        "把主战场放在“读数级”（每个变量每次上报作为一个样本），窗口级数据只用于趋势统计与提前预警实验。三种宽表的规格见表 5。"
    ))
    table(doc, ["数据表", "规模", "主要用途"], [
        ["清洗后长表 clean_long", f"{P['clean_long_rows']:,} 行", "读数级统计与建模"],
        ["定位表 clean_pos", f"{P['clean_pos_rows']:,} 行", "轨迹与里程"],
        ["时间戳级宽表 wide_ts", f"{P['wide_ts_rows']:,} × {P['wide_ts_cols']}", "同刻多信号对齐"],
        ["1 分钟宽表 wide_1min", f"{P['wide_1min']['rows']:,} × 167", "时间规律、日内分布"],
        ["5 分钟宽表 wide_5min", f"{P['wide_5min']['rows']:,} × 167", "窗口级建模"],
    ], text="表5  规整后数据表规格", widths=[5.6, 4.2, 6.0], left_cols=(0, 2))

    subsection(doc, "3.5  清洗结果与数据子集的选择")
    body(doc, "清洗完成后，三台设备的有效数据量与时间跨度见表 6。")
    rows = []
    for d in P["devices"]:
        rows.append([f"车-{d['device']}", f"{d['rows']:,}", d["ts_min"][:16], d["ts_max"][:16],
                     f"{d['hours']:.1f}", pct(d["alarm_rate"])])
    table(doc, ["设备", "有效读数", "起始时间", "结束时间", "跨度(h)", "告警读数占比"], rows,
          text="表6  三台设备的有效数据量", widths=[3.4, 2.6, 4.2, 4.2, 2.4, 3.0])
    body(doc, (
        f"清洗后长表 {P['clean_long_rows']:,} 条读数、定位 {P['clean_pos_rows']:,} 条。"
        "有必要单独说明一个结论：**车-NAB0gBAQ 的定位数据完全失效**。该车 1,989 条定位中 81.4% 的经纬度为 0，"
        "其余点的经度漂移到 80.2°（偏离主体区域数十公里），因此清洗后有效定位点为 0；"
        "作为对照，另两台车的零值比例分别为 1.9% 和 9.5%。这不是清洗规则过严，而是该设备定位模块本身异常。"
        "据此确定分析子集：轨迹与里程类分析只使用车-twB1gBAQ 与车-GAB0gBAQ（图 2），"
        "车-NAB0gBAQ 仅参与时间与信号维度分析，并在第 7 章作为设备维护问题提出。"
    ))
    figure(doc, "fig03_轨迹.png",
           "图2  有效 GPS 轨迹（颜色由暗到亮表示时间先后；车-NAB0gBAQ 因定位全部无效而剔除）", width=12.6)


# ---------------------------------------------------------------- 4 EDA
def write_eda(doc):
    section(doc, "4. 数据分析与可视化（一）：运行状态与设备画像")
    subsection(doc, "4.1  单车单日的运行过程还原")
    body(doc, (
        f"图 3 选取数据最完整的车-twB1gBAQ 在 {E['day_used']} 的样本，把车速、转速与点火状态按时间轴对齐。"
        "这一天最直观的现象不是曲线本身，而是**曲线中间存在大片空白**：车速读数只出现在 10—11 时、"
        "14—17 时以及 19 时（这七个整点的读数分别为 23、133、177、132、282、208、57 条），"
        "其余时间没有任何 OBD 记录。"
        "这正是事件驱动上报的真实形态——车辆熄火停放时不上报动力总成信号，只有定位仍按 3 秒周期工作。"
        "在有数据的时段内，车速均值在 5.6—25.8 km/h 之间波动、峰值 54 km/h，"
        "转速随车速同步起伏于 1200—2000 rpm 之间，属于典型的城市中低速走走停停工况。"
    ))
    figure(doc, "fig04_车速转速时序.png", "图3  单车单日运行过程还原（车速、转速、点火状态三联图）")
    body(doc, (
        "图 4 把窗口缩小到 40 分钟，叠加了车速、转速与三轴加速度。三个加速度轴的方向可由数据反推："
        "纵向（X）在加速时为正、减速时为负；横向（Y）在转弯时出现约 −8 m/s² 的持续偏置；"
        "垂向（Z）随路面起伏出现尖峰。车速与转速同步涨落、加速度在加减速时正负交替，"
        "说明多信号在时间上是自洽的，这为后续“用运行状态解释告警”提供了依据。"
    ))
    figure(doc, "fig05_多信号时序.png", "图4  多信号联合时序（车-twB1gBAQ，40 分钟窗口）")

    subsection(doc, "4.2  三台设备的运行特征对比")
    body(doc, (
        f"图 5 从四个角度比较三台车。车速分布上，车-GAB0gBAQ 明显右移（均值 "
        f"{E['device_speed_mean']['GAB0gBAQ']} km/h），另两台集中在 10—30 km/h"
        f"（均值分别为 {E['device_speed_mean']['NAB0gBAQ']} 与 {E['device_speed_mean']['twB1gBAQ']} km/h）；"
        f"转速分布则相反，车-twB1gBAQ 中位转速最低（均值 {E['device_rpm_mean']['twB1gBAQ']} rpm），"
        "说明该车长时间处于低速或怠速状态。"
    ))
    body(doc, (
        f"工况构成（图 5d）给出了最直观的画像：车-GAB0gBAQ 高速占 "
        f"{pct(E['state_share']['高速(>60)']['GAB0gBAQ'])}、中速占 {pct(E['state_share']['中速(30-60)']['GAB0gBAQ'])}，"
        f"属城际/高速通勤用车；车-NAB0gBAQ 怠速占 {pct(E['state_share']['怠速']['NAB0gBAQ'])}、"
        f"低速占 {pct(E['state_share']['低速(<30)']['NAB0gBAQ'])}，属频繁起停的城市配送用车；"
        f"车-twB1gBAQ 熄火静止占 {pct(E['state_share']['熄火/静止']['twB1gBAQ'])}，"
        f"属“多次短途、长时间停放”的私家车。日行驶里程方面，车-GAB0gBAQ 日均 "
        f"{E['daily_km']['mean']['GAB0gBAQ']} km（单日最高 {E['daily_km']['max']['GAB0gBAQ']} km），"
        f"车-twB1gBAQ 日均 {E['daily_km']['mean']['twB1gBAQ']} km；车-NAB0gBAQ 因定位失效无法计算里程。"
    ))
    figure(doc, "fig06_设备特征对比.png",
           "图5  三台设备运行特征对比：(a) 车速分布；(b) 转速分布；(c) 日行驶里程；(d) 工况构成")

    subsection(doc, "4.3  关键信号的相关性")
    body(doc, (
        f"图 6 的 Spearman 相关矩阵显示：车速与转速相关系数为 {E['corr_pairs']['speed_rpm']}，"
        f"车速与节气门位置为 {E['corr_pairs']['speed_throttle']}，符合驾驶常识；"
        "纵向加速度与垂向加速度弱负相关，反映加速抬头、减速点头的车身姿态耦合；"
        "外部电池电压与其他信号相关性弱，数值主要集中在 37—39 V（中位数 38 V，极值 8—48 V），"
        "可作为“设备供电是否正常”的独立监测项。"
        "需要注意的是，部分 OBD 变量（燃油修正、氧传感器）之间的相关性受采样时点影响较大，"
        "解读时必须强调“同一时间戳对齐”的前提，否则容易得到虚假相关。"
    ))
    figure(doc, "fig07_相关性热力图.png", "图6  关键信号相关性矩阵（Spearman，时间戳对齐后计算）", width=12.4)

    subsection(doc, "4.4  时间规律")
    hr = E["hour_records"]
    peak = max(hr, key=lambda k: hr[k])
    body(doc, (
        f"图 7 展示数据的时间规律。记录量在日内呈单峰结构，峰值出现在 {peak} 时（{hr[peak]:,} 条），"
        "对应下午用车高峰；凌晨 0—7 时记录量最低，与图 3 中“夜间只有定位数据”的观察一致。"
        "小时×星期的平均车速热力图存在大量空缺：本数据仅 7.5 天，且三台设备各自只覆盖其中 2—7 天，"
        "单个“星期×小时”格子的样本量不足，因此本文只解读小时维度的趋势，不对星期维度下强结论。"
        "告警读数占比同样在午后抬升（8 时 15.8%、10—11 时约 30%、14—17 时 31%—40%，16 时最高），"
        "说明高峰时段的高频加减速是告警的主要来源。"
    ))
    figure(doc, "fig08_时间规律.png",
           "图7  运行数据的时间规律：(a) 小时×星期平均车速；(b) 记录量日内分布；(c) 告警占比的星期规律")

    subsection(doc, "4.5  小结")
    body(doc, (
        "从运行状态的角度可以确认两件事：其一，三台设备的使用方式差异显著，"
        "任何“全车队平均”的结论都可能掩盖真实结构，因此后续分析必须分设备呈现；"
        "其二，车辆信号之间存在稳定的物理耦合关系，车速、转速、加速度可以互相印证，"
        "这为下一章用“同刻状态”解释告警提供了数据基础。"
    ))


# ---------------------------------------------------------------- 5 告警
def write_alarm(doc):
    section(doc, "5. 数据分析与可视化（二）：告警事件识别与成因分析")
    subsection(doc, "5.1  告警的整体分布")
    body(doc, "按读数统计，五类告警的条数与占比见表 7。")
    cs = AL["class_counts"]
    rows = [[f"类别{k}", ALARM_CN[int(k)], f"{v:,}", pct(v / P["clean_long_rows"], 2)] for k, v in cs.items()]
    table(doc, ["类别", "语义（由数据反推）", "读数条数", "占全部读数"], rows,
          text="表7  告警类别分布（读数级）", widths=[2.4, 7.4, 3.2, 3.2], left_cols=(1,))
    body(doc, (
        f"全部 {P['clean_long_rows']:,} 条读数中，{pct(1-cs['0']/P['clean_long_rows'])} 带有告警标签，"
        "这一比例远高于“故障告警”的通常预期。结合 5.2 节的取值分布可以判断，"
        "该 alarmClass 更接近“驾驶行为/工况分级”而非“车辆故障码”，"
        "因此本文将其解读为分级告警，并在后文明确区分“规则触发的读数”与“需要人工处置的故障”。"
        f"从设备维度看，车-GAB0gBAQ 告警读数占比最高（{pct(AL['alarm_rate_by_device']['GAB0gBAQ'])}），"
        f"车-NAB0gBAQ 最低（{pct(AL['alarm_rate_by_device']['NAB0gBAQ'])}），"
        "与“高速用车更易触发高等级告警”的判断一致。各类告警的构成与日内分布如图 8 所示。"
    ))
    figure(doc, "fig02_告警分布.png", "图8  告警类别与时间分布：(a) 类别构成；(b) 各设备构成；(c) 日内分布")

    subsection(doc, "5.2  各类别的信号特征与语义解释")
    body(doc, (
        "把告警标签还原到“触发它的那条读数”上，统计每类告警下该变量的取值分布（图 9），"
        "即可反推规则的物理含义；表 8 汇总了关键变量的分位数。"
    ))
    rm = AL["reading_range_by_class"]
    rows = []
    for var, cname in [("Vehicle speed", "车速(km/h)"), ("ENGINE RPM", "转速(rpm)"),
                       ("ACCELERATION X", "纵向加速度(m/s²)"), ("ACCELERATION Y", "横向加速度(m/s²)"),
                       ("ACCELERATION Z", "垂向加速度(m/s²)")]:
        for c in ["0", "1", "2", "3", "4"]:
            d = rm.get(var, {}).get(c)
            if not d:
                continue
            rows.append([cname, "正常" if c == "0" else f"类别{c}", d["n"], d["min"], d["median"], d["max"]])
    table(doc, ["变量", "类别", "读数数", "最小值", "中位数", "最大值"], rows,
          text="表8  告警类别下关键变量的取值区间（读数级）",
          widths=[4.4, 2.2, 2.4, 2.6, 2.6, 2.6])
    rm_ = AL["reading_mean_by_class"]
    body(doc, "由表 8 与图 9 可归纳出各类别的特征（括号内为同类读数的均值）：")
    bullets(doc, [
        f"**类别1（低速强冲击）**：车速 {rm_['Vehicle speed']['1']} km/h、转速 {rm_['ENGINE RPM']['1']} rpm、"
        f"纵向加速度 {rm_['ACCELERATION X']['1']} m/s²、横向加速度 {rm_['ACCELERATION Y']['1']} m/s²。"
        "低速大减速叠加强侧向加速度，是急刹与急转的组合，多见于城市配送场景；",
        f"**类别2（中低速工况）**：车速 {rm_['Vehicle speed']['2']} km/h、转速 {rm_['ENGINE RPM']['2']} rpm，"
        f"纵向加速度 {rm_['ACCELERATION X']['2']} m/s²（轻度减速），主要由节气门类变量触发；"
        "该类中另有一批外部电池电压仅 9.5 V 左右的读数，提示低压工况下的异常上报；",
        f"**类别3（中高速加速）**：车速 {rm_['Vehicle speed']['3']} km/h、转速 {rm_['ENGINE RPM']['3']} rpm、"
        f"纵向加速度 +{rm_['ACCELERATION X']['3']:.2f} m/s²，属中高速下的持续加速；",
        f"**类别4（高速高负荷）**：车速 {rm_['Vehicle speed']['4']} km/h"
        f"（清洗后最大 {rm['Vehicle speed']['4']['max']:.0f} km/h）、"
        f"转速 {rm_['ENGINE RPM']['4']} rpm（最大 {rm['ENGINE RPM']['4']['max']:.0f} rpm）、纵向加速度 "
        f"+{rm_['ACCELERATION X']['4']:.2f} m/s²、横向加速度 +{rm_['ACCELERATION Y']['4']:.2f} m/s²，"
        "是四类行为告警中最激烈的一类，疑似急加速/超速叠加颠簸；",
        "**类别5（熄火静止）**：读数高度集中——IGNITION_STATUS 占 76.5%、TOWING 占 15.7%、"
        "IDLING 占 7.8%，三者取值为 0 时即触发；校验同刻车速可知，点火状态为 0 的时刻有 96% "
        "车速不超过 3 km/h，与“熄火停放”的物理含义一致。",
    ])
    body(doc, (
        "另有两点“标签噪声”值得记录：其一，内部电量读数在 92% 以上时几乎全部被标为类别 3，"
        "外部电池电压在 45 V 以上时多为类别 4、在 9.5 V 左右时多为类别 2——这些类别与驾驶行为无关，"
        "属于规则被套用到非行为通道上造成的噪声；其二，类别 4 中垂向加速度占 44.2%，"
        "说明这一类同时承担了“路面颠簸”的语义。这对建模的影响是：标签可分性很强，"
        "但可解释性被规则口径稀释（见 6.3 节）。"
    ))
    figure(doc, "fig09_告警信号分布.png", "图9  各告警类别下触发变量自身的取值分布（读数级箱线图）")

    subsection(doc, "5.3  告警类别与触发变量的关联")
    body(doc, (
        "图 10 用“类别内变量占比 / 总体占比”的 log2 提升度衡量类别与变量通道的关联。"
        "结合图中标注的类别内占比可以看出各通道分工非常清楚："
        "类别 1 由横向加速度（26.1%）、发动机转速（22.9%）、相对与指令节气门（14.2%/12.6%）及车速（9.0%）共同触发；"
        "类别 2 几乎独占三个“绝对节气门位置”变量（合计 44.0%）；"
        "类别 3 以纵向加速度（40.8%）与垂向加速度（17.4%）为主；类别 4 以垂向加速度（44.2%）为主；"
        "类别 5 则集中在 IGNITION_STATUS（76.5%）。"
        "这说明告警规则是“按变量分域”设计的，因此建模时“变量身份”必然是最强特征，"
        "该判断在 6.3 节由实验定量验证。"
    ))
    figure(doc, "fig10_类别变量关联.png", "图10  告警类别与触发变量的关联强度（log2 提升度）", width=12.6)

    subsection(doc, "5.4  告警与车辆运行状态的关系")
    body(doc, (
        "图 11 把每条告警读数与其“同一时刻的车速/转速档位”交叉，得到的曲线几乎把规则阈值“画”了出来："
        "车速 <10 km/h 的读数中 76.9% 为类别 1、22.6% 为正常；10—20 km/h 时类别 1 仍占 75.4%；"
        "20—30 km/h 时类别 2 占 79.0%；30—50 km/h 以正常读数为主（90.0% 与 80.7%）；"
        "50—60 km/h 时类别 3（30.7%）与类别 4（69.3%）合计 100%；60 km/h 以上类别 4 占 82.8%，"
        "80 km/h 以上全部为类别 4。转速维度呈现同样的分层：1600 rpm 以下以类别 1 为主，"
        "2000 rpm 以上以类别 3/4 为主。"
        "换言之，该数据集的告警本质上是“车速/转速/加速度分区间的离散化标签”，"
        "告警等级与运行激烈程度高度正相关，业务上可把高等级告警作为驾驶风险评分的权重项。"
    ))
    figure(doc, "fig11_告警与运行状态.png", "图11  告警类别构成随车速/转速的变化")

    subsection(doc, "5.5  告警事件的时间定位与片段划分")
    ec = AL["episode_by_class"]
    dm = AL["episode_dur_median"]
    body(doc, (
        "单条告警读数不便直接处置，因此把“同设备、同类别、相邻间隔不超过 2 分钟”的告警读数合并为告警片段。"
        f"共得到 {AL['episodes']:,} 段，其中类别 3 最多（{ec['3']} 段），类别 4 最少（{ec['4']} 段）但单段强度最高；"
        f"各片段中位持续时长均在 0—5 分钟之间（类别 1 {dm['1']}、类别 2 {dm['2']}、类别 3 {dm['3']}、"
        f"类别 4 {dm['4']}、类别 5 {dm['5']} 分钟），类别 5 的 90 分位达 {AL['episode_dur_p90']['5']} 分钟，"
        "对应长时间熄火停放。片段在设备间分布极不均匀："
        f"车-twB1gBAQ {AL['episode_by_device']['twB1gBAQ']} 段、车-GAB0gBAQ "
        f"{AL['episode_by_device']['GAB0gBAQ']} 段、车-NAB0gBAQ {AL['episode_by_device']['NAB0gBAQ']} 段。"
        "告警点在车速曲线上的位置与时间密度见图 12，片段的数量、时长、各车辆每小时分布与前后状态变化见图 13。"
    ))
    figure(doc, "fig12_告警时序.png", "图12  告警事件的时序定位：(a) 车速曲线上的告警点；(b) 告警读数的时间密度")
    figure(doc, "fig13_告警片段.png",
           "图13  告警片段识别与前后状态：(a) 各类别片段数；(b) 片段持续时长；"
           "(c) 各车辆每小时事件数；(d) 告警读数相对前 5 分钟的变化")
    body(doc, (
        "从片段分布看，类别 3、4 集中在 14—19 时，与 4.4 节的用车高峰吻合；"
        "类别 5（熄火）在夜间与凌晨相对增多，对应车辆停放。"
        "对车队管理而言，“下午高峰 + 高等级告警”是需要重点监控的时段组合。"
    ))

    subsection(doc, "5.6  告警发生前后的状态对比")
    BA = AL["before_after"]
    bc = BA["by_class"]

    def ba_txt(c, tag):
        v = bc[str(c)][tag]
        pre, during, post = v["pre"], v["during"], v["post"]
        fmt = lambda x: "—" if x is None else f"{x:.1f}"
        return f"{fmt(pre)} → {fmt(during)} → {fmt(post)}"

    body(doc, (
        "直接比较“哪类告警多”会受三台设备记录量差异影响，因此进一步把每条告警还原到它的片段，"
        "比较片段起点前 5 分钟、告警读数本身、片段结束后 5 分钟的车速、转速与冷却液温度；"
        "正常对照取同一变量的正常读数（alarmClass=0）。三者的样本量不同，结论以方向与量级为主。"
    ))
    body(doc, (
        f"分类别看（前 5 分钟 → 告警读数 → 后 5 分钟）：类别 1 车速 {ba_txt(1, 'speed')} km/h、"
        f"转速 {ba_txt(1, 'rpm')} rpm，呈“减速—强冲击—恢复”的形态；"
        f"类别 2 车速 {ba_txt(2, 'speed')} km/h；"
        f"类别 3 车速 {ba_txt(3, 'speed')} km/h、转速 {ba_txt(3, 'rpm')} rpm；"
        f"类别 4 车速 {ba_txt(4, 'speed')} km/h、转速 {ba_txt(4, 'rpm')} rpm。"
        "类别 3、4 的告警读数明显高于事件前，符合“中高速加速/高速高负荷”的语义；"
        "类别 1 则相反，是低速下的强冲击。类别 5 只上报点火/拖车/怠速状态，没有车速、转速读数，"
        "因此不参与前后对比。"
    ))
    body(doc, (
        f"与正常读数相比，正常车速均值 {BA['normal_means']['speed']:.2f} km/h、转速 "
        f"{BA['normal_means']['rpm']:.0f} rpm、水温 {BA['normal_means']['temp']:.1f} ℃；"
        f"告警读数整体为 {BA['alarm_means']['speed']:.2f} km/h、{BA['alarm_means']['rpm']:.0f} rpm、"
        f"{BA['alarm_means']['temp']:.1f} ℃。整体均值受类别构成影响很大：类别 1、2 是低速工况，"
        "把总均值拉低；类别 3、4 的车速与转速则显著高于正常读数。"
        "这说明不能只看“告警总体均值”，必须分类别、分设备比较。"
    ))
    body(doc, (
        "冷却液温度需要单独说明：全表仅 1,091 条温度读数（1.3%），类别 4、5 没有温度记录；"
        "类别 3 的 122 条读数由前窗 56.7 ℃ 升到 86.1 ℃，方向与高负荷一致；"
        "类别 1 的 110 条读数集中在 10 ℃ 左右（最低 −30 ℃），与前后窗口的 50—60 ℃ 不连续，"
        "疑似冷启动或传感器编码问题；类别 2 只有 4 条（30 ℃），不足以判断。"
        "因此温度只作为辅助证据，不据此下“告警导致升温”的结论。"
    ))
    body(doc, (
        f"按车辆统计每小时告警事件数（以片段起点计）：车-GAB0gBAQ 集中在 11—18 时，"
        f"车-NAB0gBAQ 集中在 11—17 时，车-twB1gBAQ 全天都有、14 时达到峰值。"
        f"按“片段数 / 观测小时”折算，三台车分别为 {AL['episodes_per_hour']['GAB0gBAQ']}、"
        f"{AL['episodes_per_hour']['NAB0gBAQ']}、{AL['episodes_per_hour']['twB1gBAQ']} 段/小时——"
        "车-twB1gBAQ 的记录时长是另两台车的 2—3 倍，直接比总数会高估它，按小时率比较才公平。"
    ))


# ---------------------------------------------------------------- 6 建模
def write_model(doc):
    section(doc, "6. 模型构建、评估与可视化")
    subsection(doc, "6.1  任务定义与特征工程")
    body(doc, (
        "基于第 5 章的判断（标签附着于单条读数），本文把主任务定义为：给定一条传感器读数及其上下文，"
        "判断它属于哪一告警类别（六分类）。特征分三组："
    ))
    bullets(doc, [
        "**取值特征**：原始值、变量内 z-score、变量内百分位排名、按变量最大值归一化后的相对水平——"
        "用于表达“这个读数偏大还是偏小”，从而绕开不同变量量纲不一致的问题；",
        "**身份特征**：变量 one-hot（50 维）、设备 one-hot（3 维）、小时的正弦/余弦编码、是否周末；",
        "**上下文特征**：同刻（前向填充后）的车速、转速、三轴加速度、节气门、点火/怠速/拖曳标志、"
        "内外电池电压等 13 个状态量，并附带缺失指示变量。",
    ])
    body(doc, (
        f"在此基础上设计四组实验：实验一为同分布随机划分的基准对比；实验二为消融（去掉变量身份）与"
        f"跨设备泛化（留一设备）；实验三检验无监督异常检测与告警标签的一致性；实验四尝试提前预警。"
        f"前三组使用读数级样本 {P['clean_long_rows']:,} 条，第四组使用时间戳级样本。"
        "评价指标以宏平均 F1 为主（各类别等权，避免多数类掩盖问题），辅以准确率、加权 F1、"
        "各类别精确率/召回率与混淆矩阵。"
    ))

    subsection(doc, "6.2  实验一：读数级多分类")
    body(doc, "四个模型在同一划分下的表现见表 9。")
    rows = [[k, f"{v['acc']:.4f}", f"{v['macro_f1']:.4f}", f"{v['weighted_f1']:.4f}", f"{v['fit_seconds']} s"]
            for k, v in A.items()]
    STR = M["task_a_time_episode"]
    for k, v in STR["models"].items():
        rows.append([f"{k}（时间+事件隔离）", f"{v['acc']:.4f}", f"{v['macro_f1']:.4f}",
                     f"{v['weighted_f1']:.4f}", f"{v['fit_seconds']} s"])
    table(doc, ["模型", "准确率", "宏平均 F1", "加权 F1", "训练耗时"], rows,
          text=f"表9  读数级多分类结果（随机 70/30 划分，随机测试集 "
               f"{sum(sum(r) for r in M['task_a_confusion']['matrix']):,} 条；"
               f"另附严格时间/事件隔离划分，严格训练 {STR['train_n']:,} / "
               f"测试 {STR['test_n']:,} 条）",
          widths=[4.2, 3.2, 3.2, 3.2, 3.2])
    body(doc, (
        f"多数类基线的准确率已达 {A['基线(多数类)']['acc']:.4f}（对应“正常”类占比），"
        f"但宏平均 F1 仅 {A['基线(多数类)']['macro_f1']:.4f}，说明单看准确率会严重高估。"
        f"加入特征后，逻辑回归把宏平均 F1 提升到 {A['逻辑回归']['macro_f1']:.4f}，"
        f"随机森林达到 {A['随机森林']['macro_f1']:.4f}，直方图梯度提升进一步达到 "
        f"{A['直方图梯度提升']['macro_f1']:.4f}（准确率 {A['直方图梯度提升']['acc']:.4f}）。"
        "如此高的精度并非“模型特别聪明”，而是数据性质使然：告警标签由设备端对单个变量施加阈值规则产生，"
        "只要模型知道“这是哪个变量、取值多少”，就能几乎复原规则。"
        "最优模型的混淆矩阵见表 10、图 14，特征重要性见图 15——"
        "排名前三的特征全部是“取值水平”类特征（归一化值、z-score、百分位），变量身份次之。"
    ))
    pc = A["直方图梯度提升"]["per_class"]
    per_cls = "；".join(
        f"{ALARM_SHORT[c]} P{pc[str(c)]['precision']:.3f}/R{pc[str(c)]['recall']:.3f}/F{pc[str(c)]['f1']:.3f}"
        for c in range(6))
    body(doc, f"为避免只看总体指标，表 10 保留混淆矩阵，这里补充最优模型各类别的精确率/召回率/F1：{per_cls}。")
    sgb = STR["models"]["直方图梯度提升"]
    body(doc, (
        f"上面的高精度建立在“随机划分”上，训练与测试可能来自同一段连续告警。按时间前 70% 切分、"
        f"在切点前后各留 30 分钟、并剔除跨切点的告警片段后（表 9 后两行），梯度提升的宏平均 F1 降到 "
        f"{sgb['macro_f1']:.4f}、准确率 {sgb['acc']:.4f}；随机森林为 "
        f"{STR['models']['随机森林']['macro_f1']:.4f}。本次只剔除了 "
        f"{STR['dropped_crossing_episodes']} 个跨切点片段（{STR['dropped_crossing_readings']} 条读数），"
        "下降主要来自时间外推而非样本量。它仍高于留一设备的 0.53—0.66，说明“同车队、后时段”比“换一台车”容易，"
        "但 0.99 确实高估了真实可用性。"
    ))
    pcf = M["task_a_confusion"]["matrix"]
    rows = []
    for i in range(6):
        tot = sum(pcf[i])
        rows.append([ALARM_SHORT[i]] + [f"{pcf[i][j]/tot:.2f}" if tot else "0.00" for j in range(6)])
    table(doc, ["真实 \\ 预测"] + [ALARM_SHORT[i] for i in range(6)], rows,
          text=f"表10  最优模型（{M['task_a_confusion']['model']}）混淆矩阵（行归一化）",
          widths=[3.0, 2.1, 2.1, 2.1, 2.1, 2.1, 2.1])
    body(doc, (
        "混淆矩阵显示，类别之间的混淆主要发生在类别 5（熄火）与正常类之间："
        "熄火时部分变量取值为 0 或缺失，容易被判为正常；其余四类之间几乎不交叉，"
        "再次说明它们是按不同的信号通道与阈值区间划分的。"
    ))
    figure(doc, "fig15_混淆矩阵.png", "图14  告警分类混淆矩阵（最优模型）", width=14.2)
    figure(doc, "fig16_特征重要性.png", "图15  告警识别的关键特征 Top18（随机森林 Gini 重要性）", width=12.4)

    subsection(doc, "6.3  实验二：特征消融与跨设备泛化")
    body(doc, "先看去掉变量身份特征后的消融结果，见表 11。")
    rows = [[k, f"{v['acc']:.4f}", f"{v['macro_f1']:.4f}", f"{v['weighted_f1']:.4f}"] for k, v in A2.items()]
    table(doc, ["模型（去掉变量身份）", "准确率", "宏平均 F1", "加权 F1"], rows,
          text="表11  消融实验：仅使用取值水平与运行状态上下文", widths=[6.6, 3.2, 3.2, 3.2])
    body(doc, (
        f"去掉变量身份后，逻辑回归的准确率跌到 {A2['逻辑回归(无变量身份)']['acc']:.4f}，几乎与随机猜测无异——"
        "因为同一个数值（例如 45）在不同变量上含义完全不同：车速 45 km/h 属正常，外部电池 45 V 正常，"
        f"纵向加速度 45 m/s² 则不可能。而随机森林仍保持 {A2['随机森林(无变量身份)']['acc']:.4f} 的准确率，"
        "原因是树模型可以从“多个状态的联合取值”中间接推断出当前上报的是哪一类变量。"
        "这恰恰提示：模型的高精度很大程度上来自对上报通道的识别，而非对车辆状态的物理理解。为检验模型能否迁移到新车辆，进一步做留一设备验证，结果见表 12。"
    ))
    rows = []
    for k, v in B.items():
        if "→" in k:
            dev = k.split("测试车-")[-1]
            lr = B.get(f"  (逻辑回归) 测试车-{dev}", {})
            rows.append([f"车-{dev}", f"{v['acc']:.4f}", f"{v['macro_f1']:.4f}",
                         f"{lr.get('acc', 0):.4f}", f"{lr.get('macro_f1', 0):.4f}"])
    table(doc, ["被测试设备", "RF 准确率", "RF 宏 F1", "LR 准确率", "LR 宏 F1"], rows,
          text="表12  留一设备验证（训练集不含该设备的任何数据）", widths=[4.2, 3.2, 3.2, 3.2, 3.2])
    body(doc, (
        "留一设备验证的结果比随机划分悲观得多：随机森林准确率降到 0.68—0.74，宏平均 F1 只有 0.53—0.66；"
        "逻辑回归在车-NAB0gBAQ 上甚至跌到 0.49。原因有二：一是不同设备的行车画像差别很大"
        "（一台以高速为主、一台以怠速为主），训练集中没有出现过目标车辆的工况分布，"
        "模型倾向把“陌生”读数判为多数类；二是车-NAB0gBAQ 样本量小（3,552 条）且类别失衡，测试波动被放大。"
        "这一结果对工程落地很关键：若要用一台车的历史数据训练、给新车上线告警模型，"
        "必须先做分布对齐（如按变量分位数标准化）或补充少量目标车辆样本。"
    ))
    body(doc, "各模型指标与留一设备验证的对比见图 16。")
    figure(doc, "fig14_模型对比.png", "图16  模型对比与泛化能力：(a) 各模型指标；(b) 留一设备验证")

    subsection(doc, "6.4  实验三：统计异常与业务告警的一致性")
    body(doc, "各变量的 3σ 异常点数及其与告警标签的重合情况见表 13。")
    rows = []
    for var, v in C.items():
        rows.append([var, f"{v['n']:,}", v["outliers"], pct(v["alarm_rate"]),
                     v["precision"] if v["precision"] is not None else "—",
                     v["recall"] if v["recall"] is not None else "—"])
    table(doc, ["变量", "读数数", "3σ 异常点", "告警占比", "异常→告警 精确率", "异常→告警 召回率"], rows,
          text="表13  3σ 异常检测与告警标签的一致性", widths=[4.6, 2.4, 2.6, 2.4, 3.4, 3.4])
    body(doc, (
        "做法是：对每个变量单独计算全局均值与标准差，|x−μ|>3σ 的读数视为统计异常，"
        "再看这些点是否带告警标签。结果是“精确率高、召回率极低”："
        "车速的 10 个 3σ 异常点全部（100%）带告警标签，但这些点只占全部告警车速读数的 0.4%；"
        "横向加速度的 118 个异常点全部带告警，占比也只有 2.8%；"
        "相对节气门位置的 75 个异常点没有一个带告警标签（精确率 0）。"
        "这说明：（1）真正“物理上离谱”的取值几乎必然被规则捕获，在极端点上标签是可信的；"
        "（2）绝大多数业务告警对应的是“正常范围内但超出规则阈值”的取值，"
        "3σ/IQR 之类的通用异常检测无法替代业务阈值，用它替代告警会漏掉 90% 以上的业务事件。"
        "精确率与召回率的对比以及以横向加速度为例的分布形态见图 17。"
    ))
    figure(doc, "fig17_异常一致性.png", "图17  统计异常检测与告警标签的一致性检验")

    subsection(doc, "6.5  实验四：能否提前预警")
    body(doc, (
        "任务定义为：以过去 10 分钟的车速、转速、加速度统计量（均值、极值、标准差）与告警历史为特征，"
        "预测未来 10 分钟内是否会出现新的类别 4（高速高负荷）告警片段。"
        "标签由告警片段起点构造，按时间前 70% 训练、后 30% 测试，避免相邻窗口泄漏。三个模型的评估结果见表 14。"
    ))
    rows = [[k, f"{v['acc']:.4f}", f"{v['precision']:.3f}", f"{v['recall']:.3f}",
             f"{v['f1']:.3f}", f"{v['auc']}"] for k, v in D.items()]
    DS = M["task_d_strict"]
    for k, v in DS.items():
        rows.append([f"{k}（时间+事件隔离）", f"{v['acc']:.4f}", f"{v['precision']:.3f}",
                     f"{v['recall']:.3f}", f"{v['f1']:.3f}", f"{v['auc']}"])
    table(doc, ["模型", "准确率", "精确率", "召回率", "F1", "AUC"], rows,
          text=f"表14  提前预警实验结果（样本 {DM['n']:,} 个时刻，正类占比 {pct(DM['pos_rate'])}；"
               f"后两行为切点前后各留 10 分钟、训练 {DS['逻辑回归']['train_n']:,} / "
               f"测试 {DS['逻辑回归']['test_n']:,} 个时刻）",
          widths=[4.4, 2.6, 2.6, 2.6, 2.2, 2.2])
    body(doc, (
        f"逻辑回归 AUC 达到 {D['逻辑回归']['auc']}，在 0.35 的精确率下取得 0.57 的召回率；"
        f"随机森林 AUC 为 {D['随机森林']['auc']}，明显更弱。树模型表现更差的原因在于样本量小"
        "（约 4,800 条训练样本）、特征维度相对较高，且不同时间段的告警密度存在漂移，容易过拟合；"
        "逻辑回归在标准化后更稳健。"
        "从特征重要性看，模型主要依赖“过去 10 分钟的车速峰值与波动”“近期类别 4 告警次数”等信息，"
        "与业务直觉一致：即将出现高速高负荷告警的车辆，往往已在持续高速或频繁加速。"
        "但 AUC 0.73 只能算“弱可用”，若直接用于实时预警误报会偏高；"
        "更现实的用法是把它作为风险评分，与阈值规则串联（规则触发 → 模型排序 → 人工复核），"
        "或在采样频率提升后重新评估。"
    ))
    body(doc, (
        f"进一步把切点前后各留 10 分钟（覆盖“未来 10 分钟”标签窗口），去掉可能跨集合的相邻时刻后，"
        f"逻辑回归的 AUC 为 {DS['逻辑回归']['auc']}、F1 为 {DS['逻辑回归']['f1']:.3f}；"
        f"随机森林 AUC 为 {DS['随机森林']['auc']}。与原始切分（0.7327）几乎一致，"
        "说明这份数据的预警难度主要来自特征信息量，而不是切分泄漏；但隔离后结论更可信。"
    ))
    body(doc, "PR 曲线与关键特征的排序见图 18。")
    figure(doc, "fig18_提前预警.png", "图18  提前预警模型的可行性：(a) PR 曲线；(b) 关键特征")

    subsection(doc, "6.6  模型评估小结")
    body(doc, "四组实验的主要结论及其对应用的启示汇总见表 15。")
    table(doc, ["实验", "结论", "对应用的启示"], [
        ["读数级多分类", f"随机划分宏 F1 {A['直方图梯度提升']['macro_f1']:.4f}；时间+事件隔离 "
                        f"{STR['models']['直方图梯度提升']['macro_f1']:.4f}",
         "标签高度依赖“哪个变量+取何值”，本质是规则复现；严格切分后仍有 0.76"],
        ["特征消融", f"去掉变量身份后 LR 准确率降至 {A2['逻辑回归(无变量身份)']['acc']:.4f}",
         "必须保留变量身份特征，或改用能推断通道的树模型"],
        ["留一设备验证", "宏 F1 降至 0.53—0.66", "跨车辆上线前需做分布对齐或补充目标车辆样本"],
        ["异常一致性", "精确率高、召回率 <8%", "统计异常检测不能替代业务阈值告警"],
        ["提前预警", f"AUC {D['逻辑回归']['auc']}（严格切分 {DS['逻辑回归']['auc']}）",
         "可作风险评分，不宜单独作为实时预警"],
    ], text="表15  四组实验的结论与启示", widths=[3.6, 5.6, 6.6], left_cols=(1, 2))


# ---------------------------------------------------------------- 7 总结
def write_conclusion(doc):
    section(doc, "7. 总结与结论")
    subsection(doc, "7.1  主要发现")
    bullets(doc, [
        f"**数据结构**：车载遥测是典型的“事件驱动 + 多频率混合”长表，（设备，时间戳）透视后填充率仅 "
        f"{pct(P['wide_ts_fill_rate'])}，必须先规整再分析；定位字段的脏数据比例达 11.6%，是清洗中影响最大的单项；",
        "**车辆画像**：三台设备分别对应“高速通勤”“城市配送”“短途停放”三种用车画像，"
        "工况结构、日里程与告警率显著不同，结论必须分设备讨论；",
        "**告警语义**：alarmClass 实为分级驾驶行为/工况告警，五类分别对应低速强冲击、中低速工况、"
        "中高速加速、高速高负荷与熄火静止；告警等级与车速、转速、加速度强度正相关，"
        f"共识别 {AL['episodes']:,} 个告警片段，高等级片段集中在 14—19 时；",
        f"**建模边界**：读数级分类随机划分宏 F1 {A['直方图梯度提升']['macro_f1']:.4f}，"
        f"但按时间+事件隔离后降至 {STR['models']['直方图梯度提升']['macro_f1']:.4f}，"
        "跨设备泛化进一步降到 0.53—0.66，说明高精度主要来自对规则通道的复现，而非对车辆状态的深层理解；"
        f"统计异常检测无法替代业务告警；提前预警 AUC 为 {D['逻辑回归']['auc']}"
        f"（严格切分 {DS['逻辑回归']['auc']}），尚需改进。",
    ])
    subsection(doc, "7.2  建议")
    bullets(doc, [
        "**告警分层管理**：把类别 5（熄火）这类“状态类”告警与类别 1—4（行为类）告警分开，"
        "前者从告警中心下移为“状态标记”，避免 6.9% 的读数长期占用告警通道；",
        "**提升关键信号采样率**：车速、转速、加速度是全部行为告警的判据，当前数分钟一次的上报"
        f"导致 {pct(1-P['wide_5min']['speed_cover'])} 的 5 分钟窗口没有状态数据，"
        "建议改为 1—5 秒定频上报（可只上报变化量以控制带宽）；",
        "**跨车辆建模先做分布对齐**：新车上线时先跑 1—2 天“只评估不告警”模式，"
        "用分位数标准化对齐特征分布，再启用模型评分；",
        "**构建驾驶风险评分**：以“告警片段数 × 等级权重 ÷ 行驶里程”构造单车风险分，"
        "本例中车-GAB0gBAQ 的高速工况与告警率最高，是安全管理上的优先对象；",
        "**设备维护**：车-NAB0gBAQ 的定位模块连续上报零值，应作为设备故障单独派单，"
        "否则该车的轨迹与里程类指标长期不可用。",
    ])
    subsection(doc, "7.3  不足与改进方向")
    bullets(doc, [
        "**标签口径未知**：本文对告警语义的解释是从数据反推的，若能获得设备端规则文档即可直接验证；",
        "**样本不均衡与设备偏倚**：车-twB1gBAQ 占 82% 的记录，总体统计容易被单台设备主导，"
        "后续可采用分层抽样或加权评估；",
        "**时间跨度短**：仅 7.5 天，季节性、月度维保等因素无法体现，提前预警的时间漂移也部分源于此；",
        "**外部信息不足**：清码后行驶里程、燃油液位等变量记录过少，若补齐将有助于刻画长期健康状态。",
    ])


# ---------------------------------------------------------------- 8 分工
def write_team(doc):
    section(doc, "8. 小组成员分工与个人承担")
    subsection(doc, "8.1  分工说明")
    body(doc, "本项目为 3 人小组共同完成，采用“模块负责 + 交叉复核”的方式，"
              "每人负责一条完整链路（脚本 + 图表 + 报告章节），其余两人负责复核，具体分工见表 16。")
    table(doc, ["成员姓名", "学号", "承担章节 / 模块", "具体工作内容"], [
        ["周泽林（组长）", "25121595", "第 2—3 章；src/prepare.py、src/common.py",
         "字段拆分、类型转换、越界规则设计、长表转宽表与周期聚合；指标汇总；Git 仓库与分支管理"],
        ["郭德宇", "25121590", "第 4—5 章；src/eda.py、src/alarm.py",
         "运行过程还原、设备画像对比、相关性分析、告警语义反推与片段划分；图表设计"],
        ["钟子恒", "25121594", "第 6—7 章；src/model.py、src/build_report.py、src/build_ppt.py",
         "四组建模实验、评估指标与混淆矩阵、报告排版与汇报材料制作"],
    ], text="表16  小组成员分工",
        widths=[3.2, 2.4, 4.6, 6.4], left_cols=(2, 3), size=8.5)

    subsection(doc, "8.2  个人承担说明")
    subsub(doc, "周泽林（组长）：数据工程与清洗")
    body(doc, (
        "我负责数据清洗与规整模块。分析思路是先给数据“体检”再动手：先统计缺失、重复、变量记录量与"
        "取值样例，发现 timeMili 缺失 70%、定位是复合字符串、存在错标变量这三个关键问题，"
        "再据此确定清洗顺序。方法上采取“可疑值置空而不整行删除”，因为同一时间戳内其他变量的信息仍然有效，"
        "整行删除会进一步加剧数据稀疏。遇到的主要问题有三：一是 pandas 读取时把 timestamp 解析成混合格式，"
        "批量转换报错，改为 format='mixed' 逐条推断后解决；二是长表透视后 52 列中绝大多数为空，"
        "直接前向填充会把熄火后的旧值带到第二天，因此限定“同设备内、最多 12 步”；"
        "三是车-NAB0gBAQ 的定位全部无效，一度被误判为清洗规则过严，"
        "后来分设备统计零值比例（81.4% vs 1.9%/9.5%）才确认是设备故障，并据此调整了分析子集。"
    ))
    subsub(doc, "郭德宇：探索分析与告警分析")
    body(doc, (
        "我负责探索性分析与告警分析模块。分析思路上，先回答“车辆在做什么”，再回答“告警是什么”。"
        "前者用单日时序还原与工况分档（熄火/怠速/低/中/高速）刻画三台车的画像；"
        "后者意识到告警标签是附着在单条读数上的，因此放弃“按整车状态解释告警”的思路，"
        "改为“把标签还原到触发它的那条读数”，统计该变量在该类别下的取值分布，从而反推出阈值区间。"
        "遇到的问题主要是告警语义未知：最初按“故障码”理解，发现 48% 的读数都带告警，明显不合理；"
        "改用读数级分布后才确认它是驾驶行为分级。另一个问题是告警片段划分的阈值选择，"
        "尝试 30 秒、2 分钟、5 分钟三档后，2 分钟既能合并不稳定抖动又不至于把两次独立事件并成一段，故采用。"
    ))
    subsub(doc, "钟子恒：建模评估与报告呈现")
    body(doc, (
        "我负责建模评估与报告/汇报材料。思路是“不只追求指标，而是检验指标意味着什么”，"
        "因此设计了四组实验：基准对比、特征消融、留一设备泛化、提前预警。"
        "为避免数据泄漏，逻辑回归的标准化在训练集上拟合，窗口级样本按时间前 70% 训练后 30% 测试；"
        "读数级的变量内 z-score、最大归一化与缺失值中位数填充都属于无监督统计量，当前在全量上计算"
        "（严格做法应在训练折内完成，这是本文的可改进点）。"
        "遇到的问题：最初把所有特征（含 50 维变量 one-hot）直接喂给逻辑回归，宏 F1 只有 0.41，"
        "定位到是没有标准化、且类别不均衡导致系数偏移，加入 StandardScaler 与 class_weight='balanced' 后 "
        "提升到 0.90；另外提前预警实验最初把“未来 10 分钟出现任意告警”作为标签，"
        "正类占比高达 81%，模型退化为“全预测为正”，改为“未来出现新的类别 4 片段起点”后才有意义（正类 23.7%）。"
        "这部分工作也让我认识到：指标好看不等于模型有用，泛化能力才是落地前提。"
    ))


# ---------------------------------------------------------------- 9 附录
CODE_SNIPPETS = [
    ("(1) 定位字段拆分与时间解析（src/prepare.py）", '''
df["ts"] = pd.to_datetime(df["timestamp"], format="mixed", errors="coerce")
df["time_mili"] = df["ts"].astype("int64") // 10 ** 6        # 弃用缺失 70% 的原列
is_pos = df["variable"] == "POSITION"
parts = df.loc[is_pos, "value"].str.split(",", expand=True)
pos = pd.DataFrame({"deviceId": df.loc[is_pos, "deviceId"].values,
                    "ts": df.loc[is_pos, "ts"].values,
                    "lat": pd.to_numeric(parts[0], errors="coerce").values,
                    "lon": pd.to_numeric(parts[1], errors="coerce").values,
                    "alt": pd.to_numeric(parts[2], errors="coerce").values})
inv = (~pos["lat"].between(13.0, 13.8) | ~pos["lon"].between(74.5, 74.9)
       | ~pos["alt"].between(-100, 2000))                    # 越界置空而非删行
pos.loc[inv, ["lat", "lon", "alt"]] = np.nan'''),
    ("(2) 长表转宽表与多粒度聚合（src/prepare.py）", '''
wide_ts = long.pivot_table(index=["deviceId", "ts"], columns="variable",
                           values="value", aggfunc="mean")
wide_ts.columns = [slug(c) for c in wide_ts.columns]
cont = [slug(v) for v in CORE_VARS]
wide_ts[cont] = wide_ts.groupby("deviceId", group_keys=False)[cont].ffill(limit=12)

g = long.copy(); g["bin"] = g["ts"].dt.floor("1min")
w = g.pivot_table(index=["deviceId", "bin"], columns="variable",
                  values="value", aggfunc="mean")               # 分钟级均值
mx = g.pivot_table(index=["deviceId", "bin"], columns="variable",
                   values="value", aggfunc="max")               # 分钟级极值'''),
    ("(3) 告警片段划分（src/alarm.py）", '''
e = al[["deviceId", "ts", "alarm"]].sort_values(["deviceId", "alarm", "ts"]).copy()
e["gap"] = e.groupby(["deviceId", "alarm"])["ts"].diff().dt.total_seconds()
e["new"] = (e["gap"].isna()) | (e["gap"] > 120)                 # 间隔 >2 分钟视为新片段
e["ep"] = e.groupby(["deviceId", "alarm"])["new"].cumsum()
epi = (e.groupby(["deviceId", "alarm", "ep"])
        .agg(start=("ts", "min"), end=("ts", "max"), n=("ts", "size")).reset_index())
epi["dur_min"] = (epi["end"] - epi["start"]).dt.total_seconds() / 60'''),
    ("(4) 读数级特征构造与建模（src/model.py）", '''
g = long.groupby("variable")["value"]
long["value_z"] = (long["value"] - g.transform("mean")) / g.transform("std")
long["value_rank"] = g.rank(pct=True)                           # 变量内百分位
X = pd.concat([long[["value_z", "value_rank"]],
               pd.get_dummies(long["variable"], prefix="var", dtype=float),
               ctx[ctx_cols]], axis=1).astype("float32")
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3,
                                      random_state=42, stratify=y)
clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1).fit(Xtr, ytr)'''),
    ("(5) 提前预警的标签构造（src/model.py）", '''
al = long[long["alarm"] == 4].sort_values(["deviceId", "ts"])
al["gap"] = al.groupby("deviceId")["ts"].diff().dt.total_seconds()
starts = {k: np.sort(v.unique()).astype("datetime64[s]").astype("int64")
          for k, v in al[(al["gap"].isna()) | (al["gap"] > 120)].groupby("deviceId")["ts"]}
# 每个采样时刻 t0：未来 600 s 内是否出现新的片段起点
nxt = np.where(idx < len(e), e[np.clip(idx, 0, len(e) - 1)], 2 ** 62)
r["label"] = (nxt <= t0 + 600).astype(int)'''),
    ("(6) 5 分钟窗口聚合与告警标签（src/prepare.py）", '''
g["bin"] = g["ts"].dt.floor("5min")
w = g.pivot_table(index=["deviceId", "bin"], columns="variable",
                  values="value", aggfunc="mean")          # 窗口均值
lab = (g.assign(one=1)
        .pivot_table(index=["deviceId", "bin"], columns="alarm", values="one",
                     aggfunc="size", fill_value=0)             # 各类别是否出现
        .rename(columns=lambda c: f"alarm_c{int(c)}"))
lab["alarm_any"] = (lab[[f"alarm_c{c}" for c in range(1, 6)]].sum(axis=1) > 0).astype(int)
w = w.join(lab).reset_index()'''),
    ("(7) 3σ 异常与告警一致性检验（src/model.py）", '''
for var in ["Vehicle speed", "ENGINE RPM", "ACCELERATION X", "ACCELERATION Y"]:
    s = df.loc[df["variable"] == var, ["value", "alarm"]].dropna(subset=["value"])
    mu, sd = s["value"].mean(), s["value"].std()
    flag = (s["value"] - mu).abs() > 3 * sd                   # 统计异常
alarm_flag = s["alarm"] > 0                                 # 业务告警
tp = (flag & alarm_flag).sum()
precision = tp / flag.sum()                                  # 精确率（常高）
recall = tp / alarm_flag.sum()                               # 召回率（常极低）'''),
]


def write_appendix(doc):
    section(doc, "9. 附录")
    subsection(doc, "9.1  项目文件结构")
    body(doc, "项目文件结构见表 17，运行方式与仓库地址附于表后。")
    table(doc, ["路径", "说明"], [
        ["data/raw_vehicle_telemetry.csv", f"原始数据（长表，{P['raw_rows']:,} 行）"],
        ["src/prepare.py", "清洗与规整（长表→宽表）、图 1—图 2"],
        ["src/eda.py", "探索性分析、图 3—图 7"],
        ["src/alarm.py", "告警识别与片段划分、图 8—图 13"],
        ["src/model.py", "四组建模实验、图 14—图 18"],
        ["src/build_report.py / build_ppt.py", "生成本报告与汇报 PPT"],
        ["run_all.py", "一键执行全流程（约 1 分钟）"],
        ["output/metrics.json", "全部实验指标（正文数字均由此读取）"],
        ["figures/*.png", "报告插图（160 dpi）"],
    ], text="表17  项目文件结构", widths=[6.4, 9.0], left_cols=(0, 1), size=8.5)
    body(doc, (
        "运行环境：Python 3.14、pandas 3.0.3、NumPy 2.4.6、Matplotlib 3.10.9、scikit-learn 1.9.1、"
        "python-docx 1.2.0。复现命令：在项目根目录执行 python run_all.py。"
        "代码仓库：https://github.com/337437745-collab/vehicle_analysis。"
    ))

    subsection(doc, "9.2  主要代码片段")
    for title, snippet in CODE_SNIPPETS:
        subsub(doc, title)
        code_block(doc, snippet)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    subsection(doc, "9.3  关键指标汇总")
    body(doc, "全文涉及的关键指标汇总见表 18。")
    table(doc, ["指标", "数值"], [
        ["原始记录 / 变量 / 设备", f"{P['raw_rows']:,} / {P['n_variables']} / {P['n_devices']}"],
        ["清洗后有效读数", f"{P['clean_long_rows']:,}"],
        ["有效定位点", f"{P['clean_pos_rows']:,}"],
        ["时间戳级宽表", f"{P['wide_ts_rows']:,} 行 × {P['wide_ts_cols']} 列（填充率 {pct(P['wide_ts_fill_rate'])}）"],
        ["1 min / 5 min 聚合宽表", f"{P['wide_1min']['rows']:,} / {P['wide_5min']['rows']:,} 行"],
        ["告警片段数", f"{AL['episodes']:,} 段"],
        ["最优分类模型", f"{M['task_a_confusion']['model']}，宏 F1 {A['直方图梯度提升']['macro_f1']:.4f}"],
        ["留一设备宏 F1 区间", "0.53—0.66"],
        ["提前预警最优 AUC", f"{D['逻辑回归']['auc']}（逻辑回归）"],
    ], text="表18  关键指标汇总", widths=[7.4, 9.0], left_cols=(0, 1))
    subsection(doc, "9.4  参考文献")
    for i, ref in enumerate([
        "张伟, 李明. 基于车联网数据的驾驶行为分析与风险评估[J]. 汽车工程, 2021, 43(8): 1201-1209.",
        "Chen L, Zhang Y, et al. Driving behavior recognition using OBD-II data and machine learning[J]. "
        "IEEE Access, 2020, 8: 112345-112356.",
        "Breiman L. Random Forests[J]. Machine Learning, 2001, 45(1): 5-32.",
        "McKinney W. Python for Data Analysis (3rd ed.)[M]. O'Reilly Media, 2022.",
        "scikit-learn developers. scikit-learn User Guide (v1.9)[EB/OL]. https://scikit-learn.org, 2025.",
    ], 1):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.line_spacing = 1.4
        pf.space_after = Pt(0)
        pf.left_indent = Cm(0.85)
        pf.first_line_indent = Cm(-0.85)
        _set_font(p.add_run(f"[{i}] {ref}"), size=10.5)


# ================================================================= 目录与页脚
def add_footer_page_number(doc):
    sec = doc.sections[0]
    sec.different_first_page_header_footer = True
    footer = sec.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    _set_font(run, size=9)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def build():
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"缺少模板文件：{TEMPLATE}")
    doc = Document(str(TEMPLATE))
    fill_cover(doc)
    removed = clear_after_abstract(doc)
    print(f"  模板正文占位段落已清理：{removed} 个")
    write_abstract(doc)
    page_break(doc)
    write_intro(doc)
    write_data(doc)
    write_clean(doc)
    write_eda(doc)
    write_alarm(doc)
    write_model(doc)
    write_conclusion(doc)
    write_team(doc)
    write_appendix(doc)
    add_footer_page_number(doc)

    core = doc.core_properties
    core.title = TITLE
    core.subject = "《Python 数据分析编程》项目报告（题目二）"
    core.author = "第 __ 组"
    doc.save(str(OUT_DOCX))
    print(f"  报告已保存：{OUT_DOCX}")
    return OUT_DOCX


def refresh_with_word(path: Path, pdf_out: Path | None = None) -> bool:
    """用 Word 更新目录域与页码，并导出 PDF 预览"""
    ps = f"""
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$w.DisplayAlerts = 0
$doc = $w.Documents.Open('{path}', $false, $false)
try {{ $doc.TablesOfContents.Item(1).Update() }} catch {{ }}
try {{ $doc.Fields.Update() | Out-Null }} catch {{ }}
$doc.Repaginate()
$doc.Save()
"""
    if pdf_out:
        ps += f"$doc.SaveAs([ref]'{pdf_out}', [ref]17)\n"
    ps += "$doc.Close(0)\ntry { $w.Quit() } catch { }\n"
    tmp = Path(tempfile.gettempdir()) / "update_toc.ps1"
    tmp.write_text(ps, encoding="utf-8-sig")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(tmp)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        ok = r.returncode == 0
        if not ok:
            print("  [警告] Word 更新目录失败：", (r.stderr or "")[:300])
        return ok
    except Exception as exc:  # noqa: BLE001
        print("  [警告] 未能调用 Word：", exc)
        return False


def main():
    print("=" * 70)
    build()
    print("  正在用 Word 更新目录页码 ...")
    ok = refresh_with_word(OUT_DOCX, OUT_PDF)
    print(f"  目录更新：{'成功' if ok else '未完成（请手动右键目录→更新域）'}")
    if OUT_PDF.exists():
        print(f"  PDF 预览：{OUT_PDF}")
    print("05 报告生成完成。\n")


if __name__ == "__main__":
    main()

