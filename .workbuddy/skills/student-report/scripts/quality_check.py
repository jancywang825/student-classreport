#!/usr/bin/env python3
"""报告质检（需求 v1.3 · 4.6）。

生成完成后自动执行，两个维度：
  维度 1 · 数据是否正确：学员数、照片去重/无漏、题数守恒、正确率、评级、
            出勤、点评归属、图片内嵌。
  维度 2 · 前后逻辑一致：亮点结论 vs 完成数据、亮点 vs 考勤、亮点隐藏规则、
            总结 vs 分区、分区标题张数、编号连续、结论 vs 分区、无空模块。

输出：结构化结果 + Markdown 质检报告（reports/报告质检_{课程}_{课次}.md）。
"""
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from report_content import build_student_record, build_blocks  # noqa: E402

DIM_DATA = "数据是否正确"
DIM_LOGIC = "前后逻辑一致"


class Checker:
    """收集质检项：每项按 (维度, 检查名) 汇总 通过/警告/失败。"""

    def __init__(self, n_students: int):
        self.n = n_students
        self.meta = {}          # (dim, name) -> {"desc":..., "checked":int}
        self.issues = []        # {"level","dim","check","scope","detail"}

    def define(self, dim, name, desc, checked=None):
        self.meta[(dim, name)] = {"desc": desc,
                                 "checked": self.n if checked is None else checked}

    def add(self, level, dim, check, scope, detail):
        self.issues.append({"level": level, "dim": dim, "check": check,
                             "scope": scope, "detail": detail})

    def fail(self, dim, check, scope, detail):
        self.add("FAIL", dim, check, scope, detail)

    def warn(self, dim, check, scope, detail):
        self.add("WARN", dim, check, scope, detail)

    def summary(self):
        rows = []
        for key, m in self.meta.items():
            dim, name = key
            bad = [i for i in self.issues if (i["dim"], i["check"]) == key]
            scopes = {i["scope"] for i in bad}
            fails = sum(1 for i in bad if i["level"] == "FAIL")
            warns = sum(1 for i in bad if i["level"] == "WARN")
            checked = m["checked"]
            passed = max(checked - len(scopes), 0)
            rows.append({"dim": dim, "name": name, "desc": m["desc"],
                         "checked": checked, "passed": passed,
                         "warn": warns, "fail": fails})
        return rows

    @property
    def n_fail(self):
        return sum(1 for i in self.issues if i["level"] == "FAIL")

    @property
    def n_warn(self):
        return sum(1 for i in self.issues if i["level"] == "WARN")


# ----------------------------- 逐学员检查 -----------------------------
def _metric(rec, label):
    for v, l, _c in rec["metrics"]:
        if l == label:
            return v
    return None


def _check_student(name, rec, blocks, att, bb_raw, ck, html_seg):
    q = rec["qc"]
    cats = rec["cats"]
    keys = ("correct", "wrong", "blank")
    highlights_text = "".join(rec["highlights"])

    # ---- 维度 1：数据正确性 ----
    n_cat = sum(len(cats[k]["texts"]) for k in keys)
    if n_cat != q["n_total"]:
        ck.fail(DIM_DATA, "题数守恒", name,
                f"分区题数 {n_cat} ≠ 总题数 {q['n_total']}")

    acc = _metric(rec, "小黑板正确率")
    if q["total"]:
        exp = f"{round(q['n_correct'] * 100 / q['total'])}%"
        if acc != exp:
            ck.fail(DIM_DATA, "正确率计算", name, f"指标卡 {acc} ≠ 应为 {exp}")
    elif acc != "—":
        ck.fail(DIM_DATA, "正确率计算", name, f"无批改题却显示 {acc}")

    if q["total"]:
        pct = q["n_correct"] / q["total"]
        exp_g = "A" if pct >= .9 else ("B-A" if pct >= .7 else "B")
        if q["grade"] != exp_g:
            ck.fail(DIM_DATA, "评级分档", name,
                    f"评级 {q['grade']} ≠ 正确率对应 {exp_g}")
    elif q["grade"] is not None:
        ck.fail(DIM_DATA, "评级分档", name, "无批改题却有评级")

    is_attend = bool(att.get("isAttend")) if att else False
    is_playback = bool(att.get("isPlayback")) if att else False
    exp_att = "准时到课" if is_attend else ("看回放" if is_playback else "未出勤")
    if _metric(rec, "出勤") != exp_att:
        ck.fail(DIM_DATA, "出勤状态", name,
                f"指标卡 {_metric(rec,'出勤')} ≠ 考勤记录 {exp_att}")

    # 照片：接口唯一照片 = 展示照片，且无重复
    src = [r.get("imageUrl") for r in (bb_raw or [])
           if isinstance(r, dict) and r.get("imageUrl")]
    src_unique = set(src)
    shown = [u["img"] for k in keys for u in cats[k]["units"]]
    if len(shown) != len(set(shown)):
        ck.fail(DIM_DATA, "小黑板照片", name,
                f"展示 {len(shown)} 张、唯一 {len(set(shown))} 张，存在重复")
    if set(shown) != src_unique:
        ck.fail(DIM_DATA, "小黑板照片", name,
                f"展示 {len(set(shown))} 张 ≠ 接口唯一 {len(src_unique)} 张（漏/多）")

    # 逐图点评归属：核对点评里出现的题名确实来自该照片的原始 analysisResult。
    # 接口对部分照片（如仅题干、空白未提交）不提供逐题分析，属正常，不作告警。
    photo_topics = {}
    for r in (bb_raw or []):
        if not isinstance(r, dict) or not r.get("imageUrl"):
            continue
        raw = r.get("analysisResult")
        items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        names = set()
        for it in items:
            if isinstance(it, dict):
                nm = (it.get("problem") or it.get("title") or "").strip()
                if nm:
                    names.add(nm)
        photo_topics.setdefault(r["imageUrl"], set()).update(names)
    for k in keys:
        for u in cats[k]["units"]:
            if not u["cap"]:
                ck.fail(DIM_DATA, "点评归属", name, "存在无图注的照片")
            known = photo_topics.get(u["img"], set())
            if not known:
                continue      # 接口未提供逐题题名，无法核对，跳过
            for line in (u["good"] + u["bad"] + u["stuck"]):
                mm = re.match(r"「(.+?)」", line)
                if mm and mm.group(1) not in known:
                    ck.fail(DIM_DATA, "点评归属", name,
                            f"照片「{u['cap']}」点评题名「{mm.group(1)}」不在该照片题目中")

    # ---- 维度 2：前后逻辑一致 ----
    positive = ("按时完成", "书写作答完整", "全部完成", "掌握扎实")
    hit = [w for w in positive if w in highlights_text]
    if hit:
        if not (q["n_total"] > 0 and q["n_blank"] == 0):
            ck.fail(DIM_LOGIC, "亮点结论 vs 完成数据", name,
                    f"亮点含「{'/'.join(hit)}」，但 {q['n_total']} 题中 "
                    f"{q['n_blank']} 题未作答（完成率不足）")
    if "准时到课" in highlights_text and not is_attend:
        ck.fail(DIM_LOGIC, "亮点 vs 考勤", name, "亮点写「准时到课」但考勤未到课")

    has_hl_card = any(b["type"] == "card" and "课堂亮点" in b["title"]
                      for b in blocks)
    if rec["highlights_visible"] != has_hl_card:
        ck.fail(DIM_LOGIC, "亮点隐藏规则", name,
                f"亮点 {len(rec['highlights'])} 条，应"
                f"{'显示' if rec['highlights_visible'] else '隐藏'}，"
                f"实际{'显示' if has_hl_card else '隐藏'}")
    if html_seg is not None:
        seg_has = "课堂亮点" in html_seg
        if has_hl_card != seg_has:
            ck.fail(DIM_LOGIC, "亮点隐藏规则", name,
                    "HTML 渲染与内容层隐藏结论不一致")

    # 总结分模块题数 = 各分区实际题数
    for m in rec["summary_modules"]:
        for k, label in (("correct", "全部正确"), ("wrong", "有错需订正"),
                         ("blank", "未作答")):
            if m["text"].startswith(label + " · "):
                mm = re.match(r".*?· (\d+) 题", m["text"])
                if mm and int(mm.group(1)) != len(cats[k]["texts"]):
                    ck.fail(DIM_LOGIC, "总结 vs 分区", name,
                            f"「{label}」总结 {mm.group(1)} 题 ≠ 分区 {len(cats[k]['texts'])} 题")

    # 分区标题「N 张」= 该区实际照片数；结论不矛盾
    for b in blocks:
        if b["type"] != "cat":
            continue
        units = b.get("units") or []
        mm = re.search(r"（(\d+) 张）", b["title"])
        if mm and int(mm.group(1)) != len(units):
            ck.fail(DIM_LOGIC, "分区标题张数", name,
                    f"「{b['title']}」标注 {mm.group(1)} 张 ≠ 实际 {len(units)} 张")
        for u in units:
            joined = "".join(u["good"] + u["bad"] + u["stuck"])
            if b["cat"] == "correct" and u["bad"]:
                ck.fail(DIM_LOGIC, "结论 vs 分区", name,
                        f"「全部正确」区照片「{u['cap']}」却含错误点")
            if b["cat"] == "blank" and ("全部完成" in joined or "按时完成" in joined):
                ck.fail(DIM_LOGIC, "结论 vs 分区", name,
                        f"「未作答」区照片「{u['cap']}」出现正面完成结论")

    # 模块编号自「一」连续递增
    nums = []
    for b in blocks:
        if b["type"] == "card":
            mm = re.match(r"([一二三四五六七八])、", b["title"])
            nums.append(mm.group(1) if mm else "?")
    exp_nums = ["一", "二", "三", "四", "五", "六", "七", "八"][:len(nums)]
    if nums != exp_nums:
        ck.fail(DIM_LOGIC, "编号连续", name, f"编号 {nums} 应为 {exp_nums}")

    # 无空模块
    for b in blocks:
        if b["type"] == "card":
            for sub in b["body"]:
                if sub["type"] in ("bullets", "hl_bullets") and not sub["items"]:
                    ck.fail(DIM_LOGIC, "无空模块", name, f"「{b['title']}」内容为空")

    # v1.4 头图信息层级：色块行序 + 上课时间格式 + 信息卡去重
    band = blocks[0]
    info = next((b for b in blocks if b["type"] == "infocard"), None)
    if band.get("course") != rec["product_full"]:
        ck.fail(DIM_DATA, "头图信息层级", name, "色块未包含课程名称")
    if not band.get("class_time") or "T" in band["class_time"]:
        ck.fail(DIM_DATA, "头图信息层级", name,
                f"上课时间缺失或为 ISO 原始串：{band.get('class_time')!r}")
    if html_seg is not None:
        i_course = html_seg.find("stu-course")
        i_time = html_seg.find("stu-time")
        i_title = html_seg.find("stu-title")
        if not (i_title >= 0 and i_course > i_title and i_time > i_course):
            ck.fail(DIM_LOGIC, "头图信息层级", name,
                    "色块行序不是「标题 → 课程名称 → 课次 → 上课时间」")
    if info:
        labels = [r[0] for r in info["rows"]]
        redundant = {"课程全称", "学员", "课次", "小黑板日期"} & set(labels)
        if redundant:
            ck.fail(DIM_LOGIC, "头图信息层级", name,
                    f"信息卡仍含冗余项：{'、'.join(redundant)}")
        if labels != ["主讲老师", "联席老师"]:
            ck.fail(DIM_LOGIC, "头图信息层级", name,
                    f"信息卡应为「主讲老师/联席老师」，实际 {labels}")

    # v1.5 未作答识别与展示
    # ① 空 analysisResult 的「未参与作答」照片不得落在「全部正确」分区
    empty_rounds = []
    for r in (bb_raw or []):
        if isinstance(r, dict) and not (r.get("analysisResult") or []):
            try:
                empty_rounds.append(int(r.get("roundNo") or 0))
            except Exception:
                pass
    correct_caps = [u["cap"] for u in cats["correct"]["units"]]
    for rnd in empty_rounds:
        if any(c.startswith(f"第{rnd}轮") for c in correct_caps):
            ck.fail(DIM_DATA, "未作答识别", name,
                    f"第{rnd}轮「未参与作答」照片被归入全部正确分区")
    # ② 未作答单元须有未作答标签 / 原题题名 / 知识点
    for k in ("blank",):
        for u in cats[k]["units"]:
            if not u.get("is_blank"):
                ck.fail(DIM_LOGIC, "未作答展示", name,
                        f"未作答照片「{u['cap']}」缺少未作答标记")
            if not (u.get("question") or "").strip():
                ck.fail(DIM_LOGIC, "未作答展示", name,
                        f"未作答照片「{u['cap']}」缺少原题题名")
            if not (u.get("knowledge") or "").strip() and not u.get("no_participation"):
                ck.warn(DIM_LOGIC, "未作答展示", name,
                        f"未作答照片「{u['cap']}」缺少知识点")
    if html_seg is not None and cats["blank"]["units"]:
        for kw in ("blank-badge", "原题题目"):
            if kw not in html_seg:
                ck.fail(DIM_LOGIC, "未作答展示", name,
                        f"HTML 未作答行缺少 {kw}")

    # v1.6 强调收敛与文案精简
    # ① 逐图点评不得含无实质内容的模板套话
    fillers = ("本题为部分作答", "解题步骤尚未完成", "建议结合老师讲解订正",
               "课堂内容吸收扎实", "详见下方说明", "再把解题思路讲一遍")
    for k in keys:
        for u in cats[k]["units"]:
            for line in (u["good"] + u["bad"] + u["stuck"]):
                for f in fillers:
                    if f in line:
                        ck.fail(DIM_LOGIC, "文案简洁", name,
                                f"残留无信息量套话「{f}」")
    # ② 总结只突出题名 / 数字，错误类评价词不得高亮
    err_words = ("作答正确", "存在错误", "订正", "未完成", "补交", "思路完整")
    for m in rec["summary_modules"]:
        for frag, _c in m.get("hl", []):
            if any(w == frag for w in err_words):
                ck.fail(DIM_LOGIC, "强调收敛", name,
                        f"错误类评价词「{frag}」仍被高亮")
    # ③「」知识点在总结里有颜色强调（不加粗由 HTML 层统一检查）
    has_quoted_mark = any("「" in frag for m in rec["summary_modules"]
                          for frag, _c in m.get("hl", []))
    if any("「" in x for k in keys for u in cats[k]["units"]
           for x in (u["good"] + u["bad"] + u["stuck"])) and not has_quoted_mark:
        ck.fail(DIM_LOGIC, "强调收敛", name, "「」知识点 / 方法未着色强调")

    # v1.8 总结题名配色对齐：每个分类行的「」题名须与该行标签同色
    row_labels = {"全部正确": "#0AA860", "有错需订正": "#D08A3E",
                  "未作答": "#4A7FB5"}
    for m in rec["summary_modules"]:
        label = next((frag for frag, _c in m.get("hl", [])
                      if frag in row_labels), None)
        if not label:
            continue
        expect = row_labels[label]
        for frag, color in m.get("hl", []):
            if frag.startswith("「") and color != expect:
                ck.fail(DIM_LOGIC, "题名配色对齐", name,
                        f"「{label}」行题名色 {color} ≠ 标签色 {expect}")

    # v1.7 赏识型话术
    # ① 有错 / 部分作答单元须先有「做得好的点」，且肯定具体、不与待订正重复
    empty_praise = ("继续加油", "很棒", "真棒", "表现不错", "再接再厉")
    for u in cats["wrong"]["units"]:
        if not u["good"]:
            ck.fail(DIM_LOGIC, "赏识型话术", name,
                    f"有错照片「{u['cap']}」缺少「做得好的点」")
        for g in u["good"]:
            if any(w in g for w in empty_praise):
                ck.fail(DIM_LOGIC, "赏识型话术", name,
                        f"出现空泛表扬「{g}」")
            core = g.split("」", 1)[-1]
            if len(core) > 5 and core in "".join(u["bad"]):
                ck.fail(DIM_LOGIC, "赏识型话术", name,
                        f"肯定句「{core}」与待订正内容重复")
    # ② 完全未参与作答（无动笔事实）不套用鼓励；
    #    混合题照片（一题未做、另一题做对）里的真实肯定应保留，不算违规
    for u in cats["blank"]["units"]:
        if u.get("no_participation") and u["good"]:
            ck.warn(DIM_LOGIC, "赏识型话术", name,
                    f"未参与作答照片「{u['cap']}」不应给鼓励语")
    # ③ 无全盘否定词；HTML 段标题为中性表述
    deny = ("全错", "完全不会", "毫无", "一窍不通", "一无是处")
    for k in keys:
        for u in cats[k]["units"]:
            joined = "".join(u["good"] + u["bad"] + u["stuck"])
            for w in deny:
                if w in joined:
                    ck.fail(DIM_LOGIC, "赏识型话术", name,
                            f"出现全盘否定词「{w}」")
    if html_seg is not None and cats["wrong"]["units"]:
        for old in (">错误的点</div>", ">学生的卡点</div>"):
            if old in html_seg:
                ck.fail(DIM_LOGIC, "赏识型话术", name,
                        f"HTML 仍使用旧段标题 {old}")


# ----------------------------- 全局 / HTML 检查 -----------------------------
def _check_html(html, n_students, recs, ck):
    segs = re.split(r'class="student-report(?: active)?"', html)[1:]
    ck.define(DIM_DATA, "学员数", "报告学员块数 = 接口学员数", 1)
    if len(segs) != n_students:
        ck.fail(DIM_DATA, "学员数", "全局",
                f"HTML 学员块 {len(segs)} ≠ 接口 {n_students}")

    n_units = sum(len(recs[r]["cats"][k]["units"])
                  for r in recs for k in ("correct", "wrong", "blank"))
    n_zoom = html.count('class="zoomable"')
    ck.define(DIM_DATA, "图片内嵌", "可放大图片数 = 逐图单元数", 1)
    if n_zoom != n_units:
        ck.fail(DIM_DATA, "图片内嵌", "全局",
                f"HTML 可放大图 {n_zoom} ≠ 逐图单元 {n_units}")

    ck.define(DIM_DATA, "图片去重", "每名学员报告内主图无重复", n_students)
    for i, seg in enumerate(segs):
        uris = re.findall(r'<img class="zoomable" src="data:image/jpeg;base64,([A-Za-z0-9+/=]+)"', seg)
        if len(uris) != len({hashlib.md5(x.encode()).hexdigest() for x in uris}):
            ck.fail(DIM_DATA, "图片去重", f"学员块#{i}",
                    f"{len(uris)} 张主图存在重复")

    ck.define(DIM_LOGIC, "放大交互", "灯箱 DOM/JS 齐备", 1)
    for kw in ('id="lightbox"', "function openZoom", "function closeZoom",
               "Escape", "openZoom(this)"):
        if kw not in html:
            ck.fail(DIM_LOGIC, "放大交互", "全局", f"缺少 {kw}")

    # v1.6：强调仅改字色、不加粗
    ck.define(DIM_LOGIC, "强调不加粗", "HTML 无 <b class=hl>，且 .hl / .kp 均无加粗", 1)
    if '<b class="hl"' in html or "<b class='hl'" in html:
        ck.fail(DIM_LOGIC, "强调不加粗", "全局", "仍使用 <b> 加粗强调")
    for pat in (r"\.hl\s*\{[^}]*font-weight\s*:\s*(?:bold|[5-9]00)",
                r"\.kp\s*\{[^}]*font-weight\s*:\s*(?:bold|[5-9]00)"):
        if re.search(pat, html):
            ck.fail(DIM_LOGIC, "强调不加粗", "全局", "hl/kp 样式含加粗")
    if 'class="kp"' not in html:
        ck.fail(DIM_LOGIC, "强调不加粗", "全局", "「」知识点未着色强调")

    # v1.8 HTML 渲染层：每个分类行的「」题名颜色须与该行标签一致
    for row in re.findall(r'<div class="bullet hl-bullet">• (.*?)</div>', html, re.S):
        lm = re.search(
            r'<span class="hl" style="color:(#[0-9A-Fa-f]{6})">'
            r'(全部正确|有错需订正|未作答)</span>', row)
        if not lm:
            continue
        label_color, label = lm.group(1), lm.group(2)
        for qc in re.findall(r'<span class="hl" style="color:(#[0-9A-Fa-f]{6})">「', row):
            if qc != label_color:
                ck.fail(DIM_LOGIC, "题名配色对齐", "全局",
                        f"HTML「{label}」行题名色 {qc} ≠ 标签色 {label_color}")
                break


def check_report(data, html, product_name, class_seq):
    """执行全部质检，返回结构化结果。"""
    students = data["students"]
    lesson = data["lesson"]
    am = data["attendance_map"]
    bm = data["blackboard_map"]
    q_index = data.get("question_index") or {}
    n = len(students)
    ck = Checker(n)

    # 定义检查项（学员级）
    student_checks = [
        (DIM_DATA, "题数守恒", "三类分区题数之和 = 总题数"),
        (DIM_DATA, "正确率计算", "指标卡正确率 = 做对题/已批改题"),
        (DIM_DATA, "评级分档", "评级与正确率分档规则一致"),
        (DIM_DATA, "出勤状态", "出勤指标 = 考勤接口原始值"),
        (DIM_DATA, "小黑板照片", "展示照片 = 接口唯一照片，无重复无遗漏"),
        (DIM_DATA, "点评归属", "每张照片均有图注与点评"),
        (DIM_LOGIC, "亮点结论 vs 完成数据", "含正面完成结论时必须 100% 完成"),
        (DIM_LOGIC, "亮点 vs 考勤", "「准时到课」与考勤一致"),
        (DIM_LOGIC, "亮点隐藏规则", "亮点不足 3 条整块隐藏，内容层与 HTML 一致"),
        (DIM_LOGIC, "总结 vs 分区", "分模块总结题数 = 分区实际题数"),
        (DIM_LOGIC, "分区标题张数", "标题「N 张」= 该区实际照片数"),
        (DIM_LOGIC, "结论 vs 分区", "全部正确区不含错误点；未作答区无正面完成结论"),
        (DIM_LOGIC, "编号连续", "模块编号自「一」连续递增"),
        (DIM_LOGIC, "无空模块", "各展示模块内容非空"),
        (DIM_LOGIC, "头图信息层级", "色块先课程后课次+上课时间；信息卡无冗余项"),
        (DIM_DATA, "未作答识别", "空 analysisResult 归入未作答，不得判为全部正确"),
        (DIM_LOGIC, "未作答展示", "未作答单元含未作答标签、原题题名与知识点"),
        (DIM_LOGIC, "文案简洁", "逐图点评无「部分作答/步骤尚未完成」等模板套话"),
        (DIM_LOGIC, "强调收敛", "只突出「」知识点与数字，错误类评价词不高亮"),
        (DIM_LOGIC, "赏识型话术", "有错单元先有具体肯定、无空泛表扬/全盘否定、标题中性"),
        (DIM_LOGIC, "题名配色对齐", "总结各分类行的「」题名与该行标签同色"),
    ]
    for dim, name, desc in student_checks:
        ck.define(dim, name, desc, n)

    segs = re.split(r'class="student-report(?: active)?"', html)[1:]
    recs = {}
    for i, s in enumerate(students):
        name = s.get("studentName") or s.get("name") or f"学员{i+1}"
        rec = build_student_record(s, lesson, am.get(name), bm.get(name), q_index)
        recs[name] = rec
        seg = segs[i] if i < len(segs) else None
        _check_student(name, rec, build_blocks(rec), am.get(name),
                       bm.get(name), ck, seg)

    _check_html(html, n, recs, ck)

    verdict = "失败" if ck.n_fail else ("警告" if ck.n_warn else "通过")
    return {
        "product": product_name, "class_seq": class_seq,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_students": n, "rows": ck.summary(), "issues": ck.issues,
        "n_fail": ck.n_fail, "n_warn": ck.n_warn, "verdict": verdict,
    }


def render_quality_md(res: dict) -> str:
    icon = {"通过": "✅", "警告": "⚠️", "失败": "❌"}[res["verdict"]]
    lines = [
        f"# 报告质检 · {res['product']} {res['class_seq']}",
        "",
        f"- 总体结论：**{icon} {res['verdict']}**",
        f"- 学员数：{res['n_students']}　检查项：{len(res['rows'])}",
        f"- 失败（FAIL）：{res['n_fail']}　警告（WARN）：{res['n_warn']}",
        f"- 质检时间：{res['generated_at']}",
        "",
        "## 一、逐项结果",
        "",
        "| 维度 | 检查项 | 判定说明 | 检查对象 | 通过 | 警告 | 失败 |",
        "|------|--------|----------|----------|------|------|------|",
    ]
    for r in res["rows"]:
        status = "✅" if not (r["warn"] or r["fail"]) else ("❌" if r["fail"] else "⚠️")
        lines.append(f"| {r['dim']} | {status} {r['name']} | {r['desc']} | "
                     f"{r['checked']} | {r['passed']} | {r['warn']} | {r['fail']} |")

    lines += ["", "## 二、问题明细", ""]
    if not res["issues"]:
        lines.append("全部检查项通过，无问题明细。")
    else:
        by_scope = {}
        for i in res["issues"]:
            by_scope.setdefault(i["scope"], []).append(i)
        for scope, items in by_scope.items():
            lines.append(f"### {scope}")
            lines.append("")
            for i in items:
                tag = "❌ FAIL" if i["level"] == "FAIL" else "⚠️ WARN"
                lines.append(f"- {tag} · [{i['dim']} / {i['check']}] {i['detail']}")
            lines.append("")
    lines += [
        "---",
        "",
        "> 维度说明：①数据是否正确 = 与后台原始数据、计算公式逐项核对；"
        "②前后逻辑一致 = 报告各模块的结论与数据、彼此之间不得矛盾。",
    ]
    return "\n".join(lines)


def write_quality_report(res: dict, out_dir, product_name, class_seq) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_seq = str(class_seq).replace(" ", "_")
    p = out_dir / f"报告质检_{product_name or '课程'}_{safe_seq}.md"
    p.write_text(render_quality_md(res), encoding="utf-8")
    return p


if __name__ == "__main__":
    # 单独运行时的自检说明
    print("本模块由 generate_html_batch.py 调用：check_report() / write_quality_report()")
