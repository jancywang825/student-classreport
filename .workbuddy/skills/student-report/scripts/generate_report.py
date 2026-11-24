#!/usr/bin/env python3
"""学员报告生成器（demo 版 · v0.4）。

数据源（demo）：
  - 小黑板: 本地 zip（--xhb-zip），由 parse_xiaoheiban.py 解析
  - 出勤/课堂表现/联席: 飞书快照 JSON（--attendance-json）
  - 主讲老师: --teacher（demo 默认取快照 _meta.teacher = "XX老师"），与联席为两个角色

设计取向（v0.4，参考「创作者周报」式 UI）：
  - 首页顶部绿色渐变头图：大号白色标题「XX学习报告」+ 副标题（课次/周期）+ 课程信息行
  - 正文白色圆角卡片 + 浅灰页面底色；指标用「大数字 + 小灰标签」白色色卡
  - 新增「小黑板正确率」指标色块
  - 小黑板分析：值得表扬的点 → 附作答正确的小黑板原图；可以提升的点 → 附作答错误/未写的原图
    （demo 数据源暂无错误作答图时，以说明文字兜底）

产出：
  reports/<类型>报告_<学员>.pdf  （美化版，老师对家长/学生的口吻）
  reports/<类型>报告_<学员>.md   （可编辑草稿）
  reports/assets/<学员>_小黑板.png

用法示例：
  python3 generate_report.py --type B --student 江涵钰 --course "L-初三二物理" \
      --course-full "L-初三二物理" --teacher "XX老师" --lianxi "蔡笑铮" \
      --session "2026-10-17 讲次" --xhb-correct 1 --xhb-total 1 \
      --xhb-zip ".../小黑板_2026-10-17 13-35-46.zip" \
      --attendance-json ../data/feishu_snapshot_2026-10-23.json --out ./reports
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_xiaoheiban import parse_zip, find_student, fix_zip_name  # noqa: E402

GRADE_LABEL = {"A": "A（优秀）", "B-A": "B-A（良好偏上）", "B": "B（继续加油）"}

# ---------- 色板（参考创作者周报：微信绿 + 柔和辅色） ----------
HEX = {
    "brand": "#0AA860",       # 主色：微信绿
    "brand_dark": "#067A45",  # 深绿（标题/强调）
    "brand_deep": "#059152",  # 渐变端色
    "brand_light": "#35C97E", # 渐变起色
    "text": "#2B2F33",        # 正文近黑
    "gray": "#8A9299",        # 次要文字
    "blue": "#4A7FB5",        # 出勤/作业等中性指标
    "green": "#0AA860",
    "lightgreen": "#E9F7EF",
    "orange": "#D08A3E",
    "lightorange": "#FBEEDD",
    "amber": "#DD9A33",
    "lightamber": "#FBF1DA",
    "page": "#F4F6F8",        # 页面底色
    "card": "#FFFFFF",        # 卡片
    "line": "#E6EAEE",
}


def C(h: str):
    from reportlab.lib import colors
    return colors.HexColor(h)


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chl(text: str, hexv: str) -> str:
    """彩色高亮一段文本（内容先转义，再包 font 标记）。"""
    return f'<font color="{hexv}">{esc(text)}</font>'


def strip_tags(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t)


# ---------- 数据读取与匹配 ----------
def load_attendance(json_path: str) -> dict:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def match_student(att: dict, name: str) -> dict | None:
    for s in att["students"]:
        if s["name"] == name:
            return s
    for s in att["students"]:
        if name in s["name"] or s["name"] in name:
            return s
    return None


def attended_sessions(sessions: dict) -> list[str]:
    out = []
    for sid, d in sorted(sessions.items(), key=lambda kv: int(kv[0])):
        if d.get("grade") or d.get("grade2") or d.get("performance") or d.get("homework") or (d.get("stars") or 0) > 0:
            out.append(sid)
    return out


# ---------- 老师口吻的文本生成 ----------
def xhb_analysis(att_student, xhb, good_imgs, bad_imgs) -> tuple[str, list[str], list[str]]:
    has = bool(good_imgs or bad_imgs)
    if good_imgs:
        summary = (
            f"本次小黑板（{esc(xhb['lecture_date'])}）{chl('已按时完成', HEX['green'])}，"
            "作答正确、书写工整、物理量图绘制规范，能按要求写出解题过程，说明课堂方法吸收到位。"
        )
    elif has:
        summary = f"本次小黑板（{esc(xhb['lecture_date'])}）已收集到作答记录，个别地方还需要订正，详见下方说明。"
    else:
        summary = "本讲暂未在本地小黑板收集中找到该学员的作答记录，建议向老师核实完成情况。"
    praise, improve = [], []
    if good_imgs:
        praise.append("小黑板按时完成且作答正确，解题步骤完整、书写工整，物理量图绘制规范，能独立完成计算并给出结论。")
    grades = [d.get("grade") for d in att_student.get("sessions", {}).values() if d.get("grade")]
    if "A" in grades:
        praise.append("小黑板对应课次课堂评级保持 A，且表现评价「优秀」，说明对当堂内容掌握扎实。")
    if bad_imgs:
        improve.append("小黑板中存在作答错误或未写完的部分，建议对照老师讲解订正一遍，并把错因写在小黑板旁。")
    comm = (att_student.get("communication") or "").strip()
    if comm and comm not in ("未回",):
        improve.append(
            f"学情沟通反馈：{esc(comm)}。下一步重点把「会做」升级为「说得清、念得准」，"
            "每完成一道题用一句话复述所用到的物理概念。"
        )
    wq = att_student.get("wrong_questions")
    if wq:
        improve.append(f"针对错题（第 {esc(wq)} 题）及时归类到错题本，并安排重做与同类题巩固。")
    if "B" in grades and "A" not in grades:
        improve.append("课堂评级还有提升空间，重点提升答题正确率与主动展示解题思路的频次。")
    if not improve:
        improve.append("本讲小黑板作答整体正确，暂无需要订正的图片；后续如收集到未完成作答，会在此附图说明。")
    return summary, praise, improve


def highlights(att_student, xhb_images) -> list[str]:
    lines = []
    att = attended_sessions(att_student.get("sessions", {}))
    if att:
        lines.append(f"出勤稳定，本阶段已记录课次（第{'、'.join(att)}讲）均正常出勤，学习节奏保持得很好。")
    grades = [d.get("grade") for d in att_student.get("sessions", {}).values() if d.get("grade")]
    if "A" in grades:
        lines.append("课堂评级保持 A，课堂互动参与积极、反应快，对当堂内容掌握扎实。")
    perfs = [d.get("performance") for d in att_student.get("sessions", {}).values() if d.get("performance")]
    if "优秀" in perfs:
        lines.append("多次获得老师「优秀」评价，答题质量与参与度得到认可。")
    if xhb_images:
        lines.append("课堂小黑板按时完成且作答正确，书写作答完整、物理量图绘制规范。")
    stars = att_student.get("total_stars")
    if stars and stars >= 30:
        lines.append(f"累计获得 {esc(str(stars))} 颗星星，学习积极性处于班级较好水平。")
    if not lines:
        lines.append("暂无足够课堂记录，建议老师补充课堂观察。")
    return lines


def directions(att_student) -> list[str]:
    lines = []
    comm = (att_student.get("communication") or "").strip()
    if comm and comm not in ("未回",):
        lines.append(f"结合课堂反馈（{esc(comm)}），下一步重点巩固概念表述的准确性，做到「会做也能说清楚」。")
    wq = att_student.get("wrong_questions")
    if wq:
        lines.append(f"针对错题（第 {esc(wq)} 题）进行错因归类，整理到错题本并安排重做。")
    grades = [d.get("grade") for d in att_student.get("sessions", {}).values() if d.get("grade")]
    if "B" in grades and "A" not in grades:
        lines.append("课堂评级还有提升空间，重点提升答题正确率与主动展示频次。")
    if not lines:
        lines.append("保持当前学习节奏，继续巩固基础概念，逐步提高综合题熟练度。")
    return lines


# ---------- 报告内容（返回 block 列表） ----------
def info_rows_b(course_full, student, teacher, lianxi, session, xhb) -> list[tuple]:
    return [
        ("课程全称", course_full), ("学员", student),
        ("主讲老师", teacher), ("联席老师", lianxi),
        ("课次", session), ("小黑板日期", xhb.get("lecture_date", "—")),
    ]


def accuracy_value(correct: int | None, total: int | None, has_imgs: bool) -> str:
    if correct is None or total is None or not total:
        return "1/1" if has_imgs else "—"
    return f"{correct}/{total}"


def gen_type_b(args, att_student, xhb, xhb_images, good_imgs, bad_imgs,
               course_full, teacher, lianxi) -> list[dict]:
    sessions = att_student.get("sessions", {})
    attended = attended_sessions(sessions)
    s1 = sessions.get(attended[-1], {}) if attended else {}

    metrics = [
        (GRADE_LABEL.get(s1.get("grade"), s1.get("grade") or "—"), "本讲评级", HEX["green"]),
        ("+" + str(s1.get("stars", 0)) if s1 else "—", "课堂星星", HEX["amber"]),
        (accuracy_value(args.xhb_correct, args.xhb_total, bool(good_imgs)), "小黑板正确率", HEX["brand"]),
        ("已提交" if s1 and s1.get("homework") == "已提交" else "待提交", "课后作业", HEX["blue"]),
        ("准时到课" if xhb_images else "待核实", "出勤", HEX["blue"]),
    ]

    summary, praise, improve = xhb_analysis(att_student, xhb, good_imgs, bad_imgs)

    blocks = [
        {"type": "band", "title": f"{args.student}学习报告",
         "sub": f"{args.session} · 单次课堂报告", "meta": course_full},
        {"type": "infocard", "rows": info_rows_b(course_full, args.student, teacher, lianxi,
                                                 args.session, xhb)},
        {"type": "metrics", "items": metrics},
        {"type": "card", "title": "一、课堂亮点", "body": [
            {"type": "bullets", "items": highlights(att_student, bool(xhb_images))},
        ]},
        {"type": "card", "title": "二、小黑板完成情况与分析", "body": [
            {"type": "para", "text": summary},
        ]},
        {"type": "praise", "title": "值得表扬的点", "items": praise,
         "images": [(p, f"作答正确的小黑板（{xhb.get('lecture_date', '')}）") for p, _ in good_imgs]},
        {"type": "improve", "title": "可以提升的点", "items": improve,
         "images": [(p, "作答错误/未写完的小黑板") for p, _ in bad_imgs]},
        {"type": "card", "title": "三、我们一起努力的方向", "body": [
            {"type": "bullets", "items": directions(att_student)},
        ]},
        {"type": "card", "title": "四、下次课小目标", "body": [
            {"type": "bullets", "items": [
                "课堂小黑板保持 A 评级，概念表述题做到「念得准、说得清」。",
                "课上主动展示 1 次解题思路，把方法讲给同学和老师听。",
            ]},
        ]},
        {"type": "footnote",
         "text": "本报告依据课堂记录与小黑板作答整理，供家长了解孩子学习情况；发送前请老师确认。"},
    ]
    return blocks


def gen_type_a(args, att_student, xhb, xhb_images, good_imgs, bad_imgs,
               course_full, teacher, lianxi) -> list[dict]:
    sessions = att_student.get("sessions", {})
    attended = attended_sessions(sessions)
    total_known = len(sessions)

    metrics = [
        (f"{len(attended)}/{total_known}", "已记录出勤", HEX["blue"]),
        (str(att_student.get("total_stars", 0)), "累计星星", HEX["amber"]),
        (accuracy_value(args.xhb_correct, args.xhb_total, bool(xhb_images)), "小黑板正确率", HEX["brand"]),
        ("完成" if xhb_images else "0/1", "小黑板提交", HEX["green"]),
    ]

    summary, praise, improve = xhb_analysis(att_student, xhb, good_imgs, bad_imgs)

    hw = [f"第{sid}讲：{d['homework']}" for sid, d in sessions.items() if d.get("homework")]
    hw_body = [{"type": "bullets", "items": hw}] if hw else [
        {"type": "para", "text": "本阶段飞书表未记录作业提交情况，建议老师补充。"}]

    blocks = [
        {"type": "band", "title": f"{args.student}学习报告",
         "sub": f"{args.range} · 阶段性学习报告", "meta": course_full},
        {"type": "infocard", "rows": [
            ("课程全称", course_full), ("学员", args.student),
            ("主讲老师", teacher), ("联席老师", lianxi),
            ("数据周期", args.range), ("小黑板日期", xhb.get("lecture_date", "—")),
        ]},
        {"type": "metrics", "items": metrics},
        {"type": "card", "title": "一、课程出勤", "body": [
            {"type": "bullets", "items": [
                f"已记录课次出勤：{chl(str(len(attended))+'/'+str(total_known), HEX['blue'])}（第{'、'.join(attended) or '—'}讲）",
                ("小黑板完成率：1/1（本阶段收集到 1 次小黑板作答）" if xhb_images else "小黑板完成率：0/1"),
            ]},
        ]},
        {"type": "card", "title": "二、课后作业总结", "body": hw_body},
        {"type": "card", "title": "三、表扬孩子 / 孩子的亮点", "body": [
            {"type": "bullets", "items": highlights(att_student, xhb_images)},
        ]},
        {"type": "card", "title": "四、我们一起努力的方向", "body": [
            {"type": "bullets", "items": directions(att_student)},
        ]},
        {"type": "card", "title": "五、接下来一周的小目标", "body": [
            {"type": "bullets", "items": [
                "课前完成预习，课上主动展示 1 次解题思路。",
                f"课堂小黑板保持 {'A' if 'A' in [d.get('grade') for d in sessions.values()] else 'B 以上'} 评级，作业按时提交。",
            ]},
        ]},
        {"type": "card", "title": "六、围绕小目标，我们可以一起做什么", "body": [
            {"type": "bullets", "items": [
                "我们（机构侧）：课前推送预习清单；课后 24 小时内反馈小黑板点评与错题归因。",
                "您（家长侧）：提醒按时上课；每天 10 分钟让孩子把当天概念「讲给您听」。",
            ]},
        ]},
        {"type": "card", "title": "七、课程学习记录", "body": [
            {"type": "table", "headers": ["课次", "课堂评级", "表现", "星星", "作业"],
             "rows": [[f"第{sid}讲",
                       GRADE_LABEL.get(d.get("grade"), d.get("grade") or "—"),
                       d.get("performance") or "—",
                       f"+{d.get('stars', 0)}",
                       "已提交" if d.get("homework") == "已提交" else "未记录"]
                      for sid, d in sorted(sessions.items(), key=lambda kv: int(kv[0]))]},
        ]},
        {"type": "card", "title": "八、最新一讲小黑板分析", "body": [
            {"type": "para", "text": summary},
        ]},
        {"type": "praise", "title": "值得表扬的点", "items": praise,
         "images": [(p, f"作答正确的小黑板（{xhb.get('lecture_date', '')}）") for p, _ in good_imgs]},
        {"type": "improve", "title": "可以提升的点", "items": improve,
         "images": [(p, "作答错误/未写完的小黑板") for p, _ in bad_imgs]},
        {"type": "footnote",
         "text": "本报告依据课堂记录与小黑板作答整理，供家长了解孩子学习情况；发送前请老师确认。"},
    ]
    return blocks


# ---------- Markdown 渲染 ----------
def block_to_md(b: dict) -> str:
    t = b["type"]
    if t == "band":
        return f"# {b['title']}\n\n_{b['sub']} · {b['meta']}_\n"
    if t == "infocard":
        return "\n".join(f"- **{k}**：{v}" for k, v in b["rows"]) + "\n"
    if t == "metrics":
        return "> " + "　|　".join(f"{v} {l}" for v, l, _ in b["items"]) + "\n"
    if t == "card":
        return f"## {b['title']}\n" + "\n".join(block_to_md(x) for x in b["body"])
    if t == "para":
        return f"{strip_tags(b['text'])}\n"
    if t == "bullets":
        return "\n".join(f"- {strip_tags(it)}" for it in b["items"]) + "\n"
    if t in ("praise", "improve"):
        head = "**值得表扬的点**" if t == "praise" else "**可以提升的点**"
        lines = head + "\n" + "\n".join(f"- {strip_tags(it)}" for it in b["items"]) + "\n"
        for p, cap in b.get("images", []):
            lines += f"\n![{cap}]({p})\n\n_{cap}_\n"
        return lines
    if t == "table":
        head = "| " + " | ".join(b["headers"]) + " |"
        sep = "|" + "|".join(["---"] * len(b["headers"])) + "|"
        rows = "\n".join("| " + " | ".join(r) + " |" for r in b["rows"])
        return head + "\n" + sep + "\n" + rows + "\n"
    if t == "footnote":
        return f"\n---\n\n_{b['text']}_\n"
    return ""


def render_markdown(blocks: list[dict]) -> str:
    return "\n".join(block_to_md(b) for b in blocks).strip() + "\n"


# ---------- PDF 渲染（reportlab · 创作者周报式版式） ----------
def render_pdf(blocks: list[dict], out_path: str, title: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
                                    NextPageTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image, KeepTogether)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    CJK = "STSong-Light"
    PW, PH = A4
    side = 16 * mm
    usable = PW - 2 * side
    HEADER_H = 44 * mm          # 首页绿色渐变头图高度
    band = next(b for b in blocks if b["type"] == "band")

    # ---- 样式 ----
    h2 = ParagraphStyle("h2", fontName=CJK, fontSize=13, leading=20,
                        textColor=C(HEX["text"]), charSpacing=1.2)
    body = ParagraphStyle("body", fontName=CJK, fontSize=10.5, leading=19,
                          textColor=C(HEX["text"]), charSpacing=0.9, alignment=TA_LEFT)
    bullet = ParagraphStyle("bullet", fontName=CJK, fontSize=10.5, leading=19,
                            textColor=C(HEX["text"]), charSpacing=0.8)
    info_lbl = ParagraphStyle("ilbl", fontName=CJK, fontSize=9, leading=13,
                              textColor=C(HEX["gray"]), charSpacing=0.5)
    info_val = ParagraphStyle("ival", fontName=CJK, fontSize=10.5, leading=15,
                              textColor=C(HEX["text"]), charSpacing=0.5)
    th_style = ParagraphStyle("th", fontName=CJK, fontSize=9.5, leading=14,
                              textColor=colors.white, charSpacing=0.5)
    td_style = ParagraphStyle("td", fontName=CJK, fontSize=9.5, leading=14,
                              textColor=C(HEX["text"]), charSpacing=0.3)
    cap_style = ParagraphStyle("cap", fontName=CJK, fontSize=9, leading=14,
                               textColor=C(HEX["gray"]), alignment=TA_CENTER, charSpacing=0.5)
    foot_style = ParagraphStyle("foot", fontName=CJK, fontSize=8.5, leading=13,
                                textColor=C(HEX["gray"]), alignment=TA_CENTER, charSpacing=0.5)
    metric_val = ParagraphStyle("mval", fontName=CJK, fontSize=14, leading=19,
                                alignment=TA_CENTER, charSpacing=0.5)
    metric_lab = ParagraphStyle("mlab", fontName=CJK, fontSize=8, leading=12,
                                textColor=C(HEX["gray"]), alignment=TA_CENTER, charSpacing=0.5)

    ROUNDED = ("ROUNDEDCORNERS", [5, 5, 5, 5])

    def card_table(inner_flowables, bg=HEX["card"], box=HEX["line"]) -> Table:
        return Table([[inner_flowables]], colWidths=[usable],
                     style=[("BACKGROUND", (0, 0), (-1, -1), C(bg)),
                            ("BOX", (0, 0), (-1, -1), 0.8, C(box)),
                            ROUNDED,
                            ("LEFTPADDING", (0, 0), (-1, -1), 12),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                            ("TOPPADDING", (0, 0), (-1, -1), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 10)])

    def make_inner(b: dict) -> list:
        t = b["type"]
        if t == "para":
            return [Paragraph(b["text"], body), Spacer(1, 2 * mm)]
        if t == "bullets":
            out = []
            for it in b["items"]:
                out.append(Paragraph(it, bullet, bulletText="•"))
                out.append(Spacer(1, 2.2 * mm))
            return out
        if t == "table":
            data = [[Paragraph(esc(h), th_style) for h in b["headers"]]]
            for r in b["rows"]:
                data.append([Paragraph(esc(c), td_style) for c in r])
            style = [("BACKGROUND", (0, 0), (-1, 0), C(HEX["brand"])),
                     ("GRID", (0, 0), (-1, -1), 0.4, C(HEX["line"])),
                     ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                     ("TOPPADDING", (0, 0), (-1, -1), 5),
                     ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                     ("LEFTPADDING", (0, 0), (-1, -1), 6)]
            for i in range(1, len(b["rows"]) + 1):
                if i % 2 == 0:
                    style.append(("BACKGROUND", (0, i), (-1, i), C(HEX["page"])))
            return [Table(data, colWidths=[28 * mm, 38 * mm, 44 * mm, 26 * mm, 34 * mm], style=style),
                    Spacer(1, 2 * mm)]
        if t == "image":
            ir = ImageReader(b["path"])
            iw, ih = ir.getSize()
            w = 130 * mm
            h = w * ih / iw
            if h > 170 * mm:
                h = 170 * mm
                w = h * iw / ih
            img = Image(b["path"], width=w, height=h)
            img.hAlign = "CENTER"
            return [Spacer(1, 2 * mm), img, Spacer(1, 1.5 * mm),
                    Paragraph(esc(b["caption"]), cap_style), Spacer(1, 2 * mm)]
        return []

    def accent_card(title, items, images, chex, lhex) -> list:
        cell = []
        for it in items:
            cell.append(Paragraph(it, bullet, bulletText="•"))
            cell.append(Spacer(1, 2.2 * mm))
        for p, cap in images:
            ir = ImageReader(p)
            iw, ih = ir.getSize()
            w = 118 * mm
            h = w * ih / iw
            if h > 150 * mm:
                h = 150 * mm
                w = h * iw / ih
            img = Image(p, width=w, height=h)
            img.hAlign = "CENTER"
            cell += [Spacer(1, 2 * mm), img, Spacer(1, 1.5 * mm),
                     Paragraph(esc(cap), cap_style), Spacer(1, 2 * mm)]
        head = Paragraph(f'<font color="{chex}"><b>{esc(title)}</b></font>', h2)
        tbl = Table([[head], [cell]], colWidths=[usable],
                    style=[("BACKGROUND", (0, 0), (-1, -1), C(lhex)),
                           ("BOX", (0, 0), (-1, -1), 0.8, C(chex)),
                           ROUNDED,
                           ("LEFTPADDING", (0, 0), (-1, -1), 12),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                           ("TOPPADDING", (0, 0), (0, 0), 9),
                           ("BOTTOMPADDING", (0, 0), (0, 0), 2),
                           ("TOPPADDING", (0, 1), (0, 1), 2),
                           ("BOTTOMPADDING", (0, 1), (0, 1), 9)])
        return [tbl, Spacer(1, 5 * mm)]

    def render_infocard(rows):
        data = []
        for i in range(0, len(rows), 2):
            left = rows[i]
            right = rows[i + 1] if i + 1 < len(rows) else ("", "")
            lc = Paragraph(f'<font color="{HEX["gray"]}">{esc(left[0])}</font><br/>'
                           f'<font color="{HEX["text"]}"><b>{esc(left[1])}</b></font>', info_val)
            rc = Paragraph(f'<font color="{HEX["gray"]}">{esc(right[0])}</font><br/>'
                           f'<font color="{HEX["text"]}"><b>{esc(right[1])}</b></font>', info_val)
            data.append([lc, rc])
        tbl = Table(data, colWidths=[usable / 2, usable / 2],
                    style=[("BACKGROUND", (0, 0), (-1, -1), C(HEX["card"])),
                           ("BOX", (0, 0), (-1, -1), 0.8, C(HEX["line"])),
                           ROUNDED,
                           ("INNERGRID", (0, 0), (-1, -1), 0.4, C(HEX["line"])),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 12)])
        return [tbl, Spacer(1, 5 * mm)]

    def render_metrics(items):
        n = len(items)
        cw = usable / n
        cells = []
        for value, label, chex in items:
            inner = Table([[Paragraph(f'<font color="{chex}"><b>{esc(value)}</b></font>', metric_val)],
                           [Paragraph(esc(label), metric_lab)]],
                          colWidths=[cw - 3 * mm])
            box = Table([[inner]], colWidths=[cw - 3 * mm], rowHeights=[15 * mm],
                        style=[("BACKGROUND", (0, 0), (-1, -1), C(HEX["card"])),
                               ("BOX", (0, 0), (-1, -1), 0.8, C(HEX["line"])),
                               ROUNDED,
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)])
            cells.append(box)
        row = Table([cells], colWidths=[cw] * n)
        row.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        return [row, Spacer(1, 5 * mm)]

    # ---- 页面装饰：底色 + 渐变头图 ----
    def lerp(c1, c2, t):
        return tuple(a + (b - a) * t for a, b in zip(c1, c2))

    def hex2rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def draw_header(c):
        h = HEADER_H
        top, bottom = hex2rgb(HEX["brand_light"]), hex2rgb(HEX["brand_deep"])
        steps = 64
        sw = PW / steps + 1
        for i in range(steps):
            col = lerp(top, bottom, i / (steps - 1))
            c.setFillColor(colors.Color(*col))
            c.rect(i * PW / steps, PH - h, sw, h, fill=1, stroke=0)
        # 半透明装饰圆（参考头图的柔和图形）
        c.setFillColor(colors.Color(1, 1, 1, alpha=0.08))
        c.circle(PW - 22 * mm, PH - 10 * mm, 16 * mm, fill=1, stroke=0)
        c.circle(PW - 45 * mm, PH - 34 * mm, 9 * mm, fill=1, stroke=0)
        c.setFillColor(colors.Color(1, 1, 1, alpha=0.10))
        c.circle(PW - 60 * mm, PH - 8 * mm, 5 * mm, fill=1, stroke=0)
        # 标题 / 副标题 / 信息行
        c.setFillColor(colors.white)
        c.setFont(CJK, 23)
        c.drawString(side, PH - 19 * mm, band["title"])
        c.setFillColor(colors.Color(1, 1, 1, alpha=0.92))
        c.setFont(CJK, 11)
        c.drawString(side, PH - 27 * mm, band["sub"])
        c.setFillColor(colors.Color(1, 1, 1, alpha=0.78))
        c.setFont(CJK, 9)
        c.drawString(side, PH - 34 * mm, band["meta"])

    def on_first(c, doc):
        c.saveState()
        c.setFillColor(C(HEX["page"]))
        c.rect(0, 0, PW, PH, fill=1, stroke=0)
        draw_header(c)
        c.restoreState()

    def on_later(c, doc):
        c.saveState()
        c.setFillColor(C(HEX["page"]))
        c.rect(0, 0, PW, PH, fill=1, stroke=0)
        c.setFillColor(C(HEX["brand"]))
        c.rect(0, PH - 5 * mm, PW, 5 * mm, fill=1, stroke=0)
        c.restoreState()

    frame_first = Frame(side, 14 * mm, usable, PH - HEADER_H - 20 * mm, id="f1",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    frame_later = Frame(side, 14 * mm, usable, PH - 26 * mm, id="f2",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    doc = BaseDocTemplate(out_path, pagesize=A4, title=title)
    doc.addPageTemplates([PageTemplate(id="first", frames=[frame_first], onPage=on_first),
                          PageTemplate(id="later", frames=[frame_later], onPage=on_later)])

    story = [NextPageTemplate("later")]
    prev_section = None
    for b in blocks:
        t = b["type"]
        if t == "band":
            continue  # 头图已在页面上绘制
        if t == "infocard":
            story.extend(render_infocard(b["rows"]))
        elif t == "metrics":
            story.extend(render_metrics(b["items"]))
        elif t == "card":
            if prev_section is not None:
                story.append(Spacer(1, 5 * mm))
            inner = []
            for sub in b["body"]:
                inner.extend(make_inner(sub))
            head = Paragraph(f'<font color="{HEX["brand"]}">▎</font>'
                             f'<font color="{HEX["text"]}"><b>{esc(b["title"])}</b></font>', h2)
            story.append(KeepTogether(card_table([head, Spacer(1, 2 * mm)] + inner)))
            prev_section = b["title"]
        elif t in ("praise", "improve"):
            story.append(Spacer(1, 1.5 * mm))
            if t == "praise":
                story.append(KeepTogether(accent_card(b["title"], b["items"], b.get("images", []),
                                                      HEX["brand_dark"], HEX["lightgreen"])))
            else:
                story.append(KeepTogether(accent_card(b["title"], b["items"], b.get("images", []),
                                                      HEX["orange"], HEX["lightorange"])))
            prev_section = None
        elif t == "footnote":
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph(esc(b["text"]), foot_style))
    doc.build(story)


# ---------- 入口 ----------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=["A", "B"], required=True, help="A=阶段性学习报告 B=单次课堂报告")
    ap.add_argument("--student", required=True)
    ap.add_argument("--course", required=True, help="课程名称（用于匹配）")
    ap.add_argument("--course-full", default=None, help="课程全称（展示用，默认同 --course）")
    ap.add_argument("--teacher", default=None, help="主讲老师（demo 默认取快照 _meta.teacher = XX老师）")
    ap.add_argument("--lianxi", default=None, help="联席老师（默认取快照 _meta.lianxi）")
    ap.add_argument("--range", default="", help="类型A：数据周期，如 第1次课~第2次课")
    ap.add_argument("--session", default="", help="类型B：课次，如 第3次课 / 2026-10-17 讲次")
    ap.add_argument("--xhb-correct", type=int, default=None, help="小黑板作答正确题数（demo 手工传入）")
    ap.add_argument("--xhb-total", type=int, default=None, help="小黑板作答总题数（demo 手工传入）")
    ap.add_argument("--xhb-bad-imgs", default="", help="zip 内作答错误/未完成的图片文件名，逗号分隔（demo）")
    ap.add_argument("--xhb-zip", required=True)
    ap.add_argument("--attendance-json", required=True)
    ap.add_argument("--out", default="./reports")
    args = ap.parse_args()

    att = load_attendance(args.attendance_json)
    att_student = match_student(att, args.student)
    if att_student is None:
        print(f"[warn] 飞书快照中未找到学员「{args.student}」，将生成仅含小黑板数据的报告。", file=sys.stderr)
        att_student = {"name": args.student, "course": args.course, "sessions": {}}

    xhb = parse_zip(args.xhb_zip)
    xhb_images = find_student(xhb, args.student)
    assets_dir = Path(args.out) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    bad_names = [s.strip() for s in args.xhb_bad_imgs.split(",") if s.strip()]
    good_imgs, bad_imgs = [], []
    with zipfile.ZipFile(xhb["zip_path"]) as zf:
        name_map = {Path(fix_zip_name(i.filename, i.flag_bits)).name: i for i in zf.infolist()}
    for img in xhb_images:
        info = name_map.get(img["file"])
        if info is None:
            continue
        dst = assets_dir / img["file"]
        with zipfile.ZipFile(xhb["zip_path"]) as zf:
            dst.write_bytes(zf.read(info))
        img["_abs"] = str(dst)
        (bad_imgs if img["file"] in bad_names else good_imgs).append((str(dst), img["file"]))

    meta = att.get("_meta", {})
    teacher = args.teacher or meta.get("teacher") or "XX老师"      # 主讲老师（demo=XX老师）
    lianxi = args.lianxi or meta.get("lianxi") or "（待补充）"      # 联席老师
    course_full = args.course_full or att_student.get("course") or args.course

    if args.type == "B":
        blocks = gen_type_b(args, att_student, xhb, xhb_images, good_imgs, bad_imgs,
                            course_full, teacher, lianxi)
        base = f"单次课堂报告_{args.student}"
    else:
        blocks = gen_type_a(args, att_student, xhb, xhb_images, good_imgs, bad_imgs,
                            course_full, teacher, lianxi)
        base = f"阶段性学习报告_{args.student}"

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{base}.md"
    pdf_path = out_dir / f"{base}.pdf"
    md_path.write_text(render_markdown(blocks), encoding="utf-8")
    render_pdf(blocks, str(pdf_path), base)
    print(f"[ok] Markdown: {md_path}")
    print(f"[ok] PDF: {pdf_path}")


if __name__ == "__main__":
    main()
