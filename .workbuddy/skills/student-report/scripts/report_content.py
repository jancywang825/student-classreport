#!/usr/bin/env python3
"""API 驱动的报告内容构建（单次课堂报告 · 维度与既有类型 B 保持一致）。

输入：由 backend_client.resolve() 返回的归一化数据（学生档案 + 课节 + 考勤 + 小黑板分析）。
输出：与 generate_report.py 中类型 B 相同的 block 列表（band/infocard/metrics/card/praise/improve/footnote）。
这样 HTML 渲染与（未来的）PDF 渲染都能复用同一套内容结构，保证「报告维度不变」。

小黑板 analysisResult 为 JSON，字段未知，这里做容错解析：
  优先读取 grade / stars / homework / correct / total / summary / praise / improve / highlights / directions；
  缺失时基于考勤与评级做合理回退。
"""
import json
import re
from datetime import datetime

GRADE_LABEL = {"A": "A（优秀）", "B-A": "B-A（良好偏上）", "B": "B（继续加油）"}


def format_class_time(begin: str, end: str, fallback: str = "") -> str:
    """把接口的 ISO 上课起止时间格式化为家长可读的「YYYY年M月D日 HH:MM–HH:MM」。

    无起止时刻时退化为日期（或原有日期串的日期部分），绝不显示 ISO 原始串。
    """
    def parse(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    b, e = parse(begin), parse(end)
    if b:
        wd = "一二三四五六日"[b.weekday()]
        date_txt = f"{b.year}年{b.month}月{b.day}日（周{wd}）"
        if e and (b.date() == e.date()):
            return f"{date_txt} {b.strftime('%H:%M')}–{e.strftime('%H:%M')}"
        return f"{date_txt} {b.strftime('%H:%M')}"
    # 无起止时刻：从 class_date 里只取日期部分
    if fallback:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(fallback))
        if m:
            return f"{int(m.group(1))}年{int(m.group(2))}月{int(m.group(3))}日"
        return str(fallback)
    return ""


def _try_json(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        # 有时接口会把 JSON 包成字符串，或前后带反引号
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.I).strip()
        try:
            return json.loads(s)
        except Exception:
            return {"_raw_text": raw}
    return {}


def clean_math(s) -> str:
    """清洗小黑板分析里的 LaTeX 标记（括号与 \\le \\ge 等），便于家长阅读。"""
    if not s:
        return ""
    s = str(s)
    s = s.replace(r"\(", " ").replace(r"\)", " ").replace(r"\[", " ").replace(r"\]", " ")
    s = (s.replace(r"\leq", "≤").replace(r"\geq", "≥").replace(r"\le", "≤")
         .replace(r"\ge", "≥").replace(r"\lt", "<").replace(r"\gt", ">")
         .replace(r"\times", "×"))
    s = re.sub(r"\\[a-zA-Z]+", "", s)        # 其余 LaTeX 命令去除
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _bucket(p: dict) -> str:
    """把单题分析归入三类：correct（全部正确）/ wrong（有错）/ blank（未作答）。"""
    correct = str(p.get("correct") or "").strip()
    status = str(p.get("status") or "").strip()
    errors = p.get("errors") or []
    if status == "空白未提交":
        return "blank"
    if correct == "有错" or errors or status == "部分作答":
        return "wrong"
    if correct in ("正确", "基本正确"):
        return "correct"
    if correct == "待判":
        # 待判：有完整作答按正确展示（标注待判分），否则视作未作答
        return "correct" if status == "完整作答" else "blank"
    # 未知取值：有完整作答按正确，否则按有错处理
    return "correct" if status == "完整作答" else "wrong"


def _first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return default


def _problem_text(p: dict, bucket: str, correct: str):
    """生成单题 bullet 文案，返回 (题名, 文案)。文字按题自身结果归类。"""
    prob = (p.get("problem") or p.get("title") or "").strip()
    note = clean_math(p.get("note") or "")
    know = (p.get("topic_knowledge") or p.get("topic") or "").strip()
    errors = p.get("errors") or []
    stuck = clean_math(p.get("stuck_at") or "")
    if bucket == "correct":
        bits = []
        if prob:
            bits.append(f"「{prob}」")
        if note:
            bits.append(note)
        if know:
            bits.append(f"（知识点：{know}）")
        text = "".join(bits) if bits else "本题作答正确，书写完整。"
        if correct == "待判":
            text += "（待老师判分）"
    elif bucket == "wrong":
        bits = []
        if prob:
            bits.append(f"「{prob}」")
        if note:
            bits.append(note)
        if errors:
            err_txt = "；".join(clean_math(e) for e in errors if e)
            if err_txt:
                bits.append(f"需注意：{err_txt}")
        if stuck:
            bits.append(f"卡点：{stuck}")
        text = "".join(bits) if bits else "本题作答不完整，建议结合老师讲解订正。"
    else:  # blank
        text = (f"「{prob}」仅有题干、未见作答，建议对照课堂讲解补齐订正。"
                if prob else "本轮小黑板未见作答，建议补齐。")
    return prob, text


TONE = {"correct": "#0AA860", "wrong": "#D08A3E",
        "blank": "#4A7FB5", "neutral": "#4A7FB5"}
# v1.6 逐图点评中「」题名的强调色（柔和墨绿、仅颜色、不加粗；
# generate_html_batch.py 的 .kp CSS 使用同值）。
# v1.8 起「总结模块」的「」题名改为跟随该行分区色 TONE[key]，不再用此常量。
KP_COLOR = "#0B7A55"


def _pct_color(p: int) -> str:
    """正确率配色：高绿 / 中琥珀 / 低橙红。"""
    return "#0AA860" if p >= 90 else ("#DD9A33" if p >= 70 else "#D9534F")


def _split_semantic(text: str) -> list:
    """按语义边界把一段评语切成多条短句（中文分号/句号/英文分号/换行）。

    不切英文句点，避免把 0.5、2/x、k>0 之类公式误切。
    去掉切分后残留的孤立标点与空句。
    """
    if not text:
        return []
    parts = re.split(r"[；;。\n]+", str(text))
    out = []
    for s in parts:
        s = re.sub(r"\s+", " ", s).strip().strip("，,、;；:： ").strip()
        if len(s) >= 2:
            out.append(s)
    return out


# v1.7 赏识型话术：从有错题目里挖掘「做得好的点」
# 按分句判断极性（不靠单字转折词切割，避免「一致/无误」被误切）
_POSITIVE = re.compile(r"正确|准确|规范|完整|清晰|合理|到位|无误|掌握|可行|工整|熟练|已标|已写出|已列出|已求出|已画|已写")
# 注意：不能用单字「不」（「不等式」是数学名词），须用精确负面词组
_NEGATIVE = re.compile(
    r"未|尚|错误|有误|漏|误|缺|停|应|仅|只|混乱|不清|失败|不当|"
    r"不正确|不准确|不完整|不一致|不等价|不明|不够|不统一|不合理|"
    # v1.9：描述「待补/标准答案」的分句不是孩子的优点
    r"需补|补完|补充|补全|待补|正确为|正确坐标|正确答案|标准答案|应为|改对")
# 描述标准答案 / 题目要求的分句，即使含「正确」也不算孩子的优点
_ANSWER_DESC = re.compile(r"^正确(答案|坐标|为|结果)?[是为(：:]|^应(为|得|该是)|^标准答案")


def _split_clauses(note: str) -> list:
    """把评语切成小分句（逗号/分号/句号/换行）。"""
    return [s.strip() for s in re.split(r"[，,；;。\n]+", note) if s.strip()]


def _is_positive_clause(s: str) -> bool:
    """判断分句是否是「对孩子已完成行为的肯定」（v1.9 收紧）。

    必须同时满足：含正面词、不含负面/待办词、不是标准答案描述，
    且描述的是孩子已做的动作（含「已/写出/求出/画/列/得到/做出」等）。
    """
    if _ANSWER_DESC.search(s) or _NEGATIVE.search(s):
        return False
    if not _POSITIVE.search(s):
        return False
    # 肯定须指向孩子的实际行为；纯结论式表述（如「坐标系作图（题意不明）」）不算
    return bool(re.search(
        r"正确|准确|规范|完整|清晰|到位|无误|工整|熟练|掌握|可行|"
        r"已标|已写出|已列出|已求出|已画|已写", s))


def _praise_from_wrong(p: dict, prefix: str) -> list:
    """从有错 / 部分作答的题目里提炼「做得好的点」（v1.7）。

    优先级：① note 里的正面分句（具体、来自老师原始评语）；
    ② 确实动笔但无可提炼肯定时，依据状态给「已经动手尝试」的鼓励。
    空白未提交由调用方排除，不套用鼓励。
    """
    note = clean_math(p.get("note") or "")
    status = str(p.get("status") or "").strip()
    errors = p.get("errors") or []
    out = []

    # ① note 里的正面分句
    for s in _split_clauses(note):
        if len(s) >= 4 and _is_positive_clause(s):
            val = prefix + s
            if val not in out:
                out.append(val)
    if out:
        return out[:2]                    # 每题最多 2 条，保持简洁

    # ② 无可提炼肯定，但确实动笔 → 有依据的鼓励（不引用负面原文）
    if status == "部分作答":
        out.append(prefix + "已经动笔写出部分推导，动手尝试本身就是很好的开始")
    elif errors or note:
        out.append(prefix + "已经写下自己的推导过程，敢于落笔尝试值得肯定")
    return out


def _review_lines(p: dict, bucket: str) -> tuple:
    """把单题分析拆成逐图点评的三段：(做得好的点, 错误的点, 卡点)。

    v1.6：只保留有信息量的内容，删除不携带具体信息的模板套话。
    v1.7：有错 / 部分作答时也从错误中挖掘「做得好的点」，置于首位；
          待订正段剔除已被提炼为肯定的分句，避免同一句重复出现。
    """
    prob = (p.get("problem") or p.get("title") or "").strip()
    note = clean_math(p.get("note") or "")
    know = (p.get("topic_knowledge") or p.get("topic") or "").strip()
    errors = [clean_math(e) for e in (p.get("errors") or []) if e]
    stuck = clean_math(p.get("stuck_at") or "")
    status = str(p.get("status") or "").strip()
    prefix = f"「{prob}」" if prob else ""
    good, bad, stuck_lines = [], [], []

    if bucket == "correct":
        pts = _split_semantic(note) or ["作答正确"]
        if know:
            pts[-1] += f"（知识点：{know}）"
        good += [prefix + x for x in pts]
    elif bucket == "wrong":
        # v1.7：先看见努力 —— 挖掘具体肯定 / 有依据的鼓励
        if status != "空白未提交":
            good += _praise_from_wrong(p, prefix)
        pts = []
        for e in errors:
            pts += _split_semantic(e)
        if not pts and note:
            # 只用非正面分句作为待订正内容（正面分句已在「做得好的点」）
            neg = [s for s in _split_clauses(note) if not _is_positive_clause(s)]
            for s in neg:
                s = re.sub(r"^(但|不过|然而|而)[，,]?\s*", "", s).strip()
                if len(s) >= 2:
                    pts.append(s)
            if not pts:
                pts = _split_semantic(note)
        if not pts:
            pts = ["作答有误或不完整"]
        # v1.9 保险：肯定句与待订正不得重复（errors 与 note 可能描述同一内容）
        good_cores = [g.split("」", 1)[-1] for g in good]
        dedup = []
        for x in pts:
            if any((gc and (gc in x or x in gc)) for gc in good_cores):
                continue
            dedup.append(x)
        bad += [prefix + x for x in (dedup or pts)]
        # v1.7 卡点话术：说明停在哪一步 + 可操作的下一步，不评价能力
        for s in _split_semantic(stuck):
            stuck_lines.append(prefix + f"停在「{s}」，下一步可以先复核这一步")
    else:  # blank（空白未提交）
        bad.append(prefix + "本题未作答")
    return good, bad, stuck_lines


def parse_analysis(bb, question_index: dict | None = None) -> dict:
    """把小黑板分析解析为结构化字段。

    真实接口粒度：一条记录 = 一轮小黑板照片（roundNo + 唯一 imageUrl），
    其 analysisResult 是对这张照片上 1~2 道题的分析。
    展示规则（保证「全部小黑板一张不落、且绝不重复」）：
      · 照片以「轮次」为最小单位，一张照片只出现一次；
      · 文字按「题」自身结果归入 全部正确 / 有错 / 未作答；
      · 照片按该轮整体结果归入一个分类（有错 > 未作答 > 全部正确）。

    v1.5：analysisResult 为空（右下角标「未参与作答」的黑屏图）不得判为正确，
    归入「未作答」，并从同课次原题索引补出题名/知识点/原题图候选。
    """
    question_index = question_index or {}
    records = bb if isinstance(bb, list) else ([bb] if bb else [])
    rounds, image_url = [], ""
    for rec in records:
        if not isinstance(rec, dict):
            continue
        img = rec.get("imageUrl") or ""
        if not image_url:
            image_url = img
        try:
            round_no = int(rec.get("roundNo") or 0)
        except Exception:
            round_no = 0
        raw = rec.get("analysisResult")
        items = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            for k in ("list", "items", "problems", "details"):
                if isinstance(raw.get(k), list):
                    items = raw[k]
                    break
            else:
                items = [raw]
        elif isinstance(raw, str) and raw.strip():
            parsed = _try_json(raw)
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict):
                items = [parsed]
        items = [p for p in items if isinstance(p, dict)]
        no_participation = not items          # v1.5：空 analysisResult = 未参与作答
        if no_participation:
            q = question_index.get(round_no) or {}
            items = [{
                "problem": q.get("problem") or "本轮原题",
                "topic_knowledge": q.get("knowledge") or "",
                "topic": q.get("knowledge") or "",
                "status": "空白未提交",
                "correct": "待判",
                "note": "本轮未参与作答",
                "errors": [],
                "stuck_at": "",
            }]
        rounds.append((round_no, img, items, no_participation))

    cats = {k: {"texts": [], "imgs": [], "units": []}
            for k in ("correct", "wrong", "blank")}
    n_total = n_correct = n_wrong = n_blank = 0
    seen_imgs = set()
    for rnd, img, items, no_participation in rounds:
        buckets = [_bucket(p) for p in items]
        probs, knows = [], []
        unit_good, unit_bad, unit_stuck = [], [], []
        for p, b in zip(items, buckets):
            n_total += 1
            if b == "correct":
                n_correct += 1
            elif b == "wrong":
                n_wrong += 1
            else:
                n_blank += 1
            prob, text = _problem_text(p, b, str(p.get("correct") or "").strip())
            cats[b]["texts"].append(text)
            if prob:
                probs.append(prob)
            kn = (p.get("topic_knowledge") or p.get("topic") or "").strip()
            if kn and kn not in knows:
                knows.append(kn)
            g, bad, s = _review_lines(p, b)
            unit_good += g
            unit_bad += bad
            unit_stuck += s
        # 照片归到该轮主分类：有错优先，其次未作答，最后全部正确
        if "wrong" in buckets:
            main = "wrong"
        elif "blank" in buckets:
            main = "blank"
        else:
            main = "correct"
        label = "、".join(probs) if probs else "小黑板照片"
        cap = f"第{rnd}轮 · {label}" if rnd else label
        if img and img not in seen_imgs:      # 全局去重：一张照片只挂一次
            seen_imgs.add(img)
            cats[main]["imgs"].append((img, cap))
            # v1.5 未作答单元：原题题名 + 知识点 + 原题图候选（同轮次样图）
            q = question_index.get(rnd) or {}
            question_imgs = []
            if no_participation:              # 纯黑图本身无题干，优先用同轮次样图
                question_imgs = [x for x in q.get("images", []) if x and x != img][:8]
            else:
                question_imgs = [img] + [x for x in q.get("images", []) if x and x != img][:7]
            cats[main]["units"].append({
                "img": img,
                "cap": cap,
                "good": unit_good,       # 做得好的点
                "bad": unit_bad,         # 错误的点
                "stuck": unit_stuck,      # 学生的卡点
                "is_blank": main == "blank",
                "no_participation": no_participation,
                "question": "、".join(probs) or q.get("problem") or "本轮原题",
                "knowledge": "、".join(knows) or q.get("knowledge") or "",
                "question_imgs": question_imgs,
            })

    judged = n_correct + n_wrong            # 已批改题数（正确率分母）
    return {
        "grade": _derive_grade(n_correct, judged),
        "stars": None,
        "homework": None,
        "correct": n_correct,
        "total": judged,
        "n_total": n_total,
        "n_blank": n_blank,
        "n_pending": 0,
        "cats": cats,   # {correct/wrong/blank: {texts, imgs, units[{img,cap,good,bad,stuck}]}}
        "image_url": image_url,
    }


def _derive_grade(correct: int, judged: int) -> str | None:
    """按已批改题的正确率推导本讲评级（接口未直接给）。"""
    if not judged:
        return None
    pct = correct / judged
    if pct >= 0.9:
        return "A"
    if pct >= 0.7:
        return "B-A"
    return "B"


def accuracy_value(correct, total) -> str:
    """小黑板正确率（已批改题的做对比例）。无批改数据返回 —。"""
    if correct is None or total is None or not total:
        return "—"
    return f"{round(correct * 100 / total)}%"


def build_student_record(student: dict, lesson: dict, attend: dict | None,
                         bb: dict | None, question_index: dict | None = None) -> dict:
    """把一名学生的原始数据归一化为内容构建所需的 record。"""
    an = parse_analysis(bb, question_index)
    name = student.get("studentName") or student.get("name") or "（未知学员）"
    product_full = lesson.get("product_name") or student.get("productName") or ""
    teacher = student.get("classTeacherName") or "XX老师"   # 主讲老师（接口提供）
    lianxi = student.get("coTeacherName") or "（待补充）"    # 联席老师
    seq = lesson.get("class_seq") or ""
    class_date = lesson.get("class_date") or ""
    # v1.4：家长可读的上课时间（日期 + 起止时刻），用于头图色块
    class_time = format_class_time(lesson.get("class_begin") or "",
                                   lesson.get("class_end") or "", class_date)
    info_date = class_time or "—"

    is_attend = bool(attend.get("isAttend")) if attend else False
    is_playback = bool(attend.get("isPlayback")) if attend else False
    grade = an.get("grade")
    stars = an.get("stars") or 0
    homework = an.get("homework")
    cats = an.get("cats") or {}
    highlights = an.get("highlights") or []
    correct = an.get("correct")
    total = an.get("total")
    n_total = an.get("n_total") or 0
    n_blank = an.get("n_blank") or 0
    image_url = an.get("image_url")

    # ---- 指标卡（评级按等级配色；无课堂星星则隐藏该卡）----
    grade_color = {"A": "#0AA860", "B-A": "#0AA860", "B": "#D08A3E"}.get(grade, "#4A7FB5")
    metrics = [
        (GRADE_LABEL.get(grade, grade or "—"), "本讲评级", grade_color),
    ]
    if stars:
        metrics.append(("+" + str(stars), "课堂星星", "#DD9A33"))
    metrics += [
        (accuracy_value(correct, total), "小黑板正确率", "#0AA860"),
        ("已提交" if homework == "已提交" else ("未提交" if homework else "—"), "课后作业", "#4A7FB5"),
        ("准时到课" if is_attend else ("看回放" if is_playback else "未出勤"), "出勤", "#4A7FB5"),
    ]

    # ---- 课堂亮点：必须由真实数据推导，禁止无条件套用正面话术 ----
    if not highlights:
        highlights = _derive_highlights(is_attend, is_playback, grade, stars, homework,
                                        n_total, n_blank, correct, total)
    # 亮点不足 3 条则整体隐藏（不为凑数编造套话）
    highlights_visible = len(highlights) >= 3

    # ---- 小黑板总结：按「全部正确 / 有错 / 未作答」三个模块分别总结 ----
    summary_modules = _summary_modules(cats, correct, total, n_total)

    record = {
        "name": name,
        "student_seq": student.get("studentSeq") or student.get("seq"),
        "grade": student.get("grade") or "",
        "subject_name": student.get("subjectName") or "",
        "product_full": product_full,
        "teacher": teacher,
        "lianxi": lianxi,
        "seq": seq,
        "class_date": class_date,
        "class_time": class_time,   # v1.4：家长可读的上课时间，头图色块展示
        "info_date": info_date,
        "is_attend": is_attend,
        "is_playback": is_playback,
        "metrics": metrics,
        "summary_modules": summary_modules,
        "cats": cats,          # {correct/wrong/blank: {texts, imgs, units}}
        "highlights": highlights,
        "highlights_visible": highlights_visible,
        # 供报告质检核对的原始量化数据
        "qc": {
            "n_total": n_total, "n_blank": n_blank, "n_correct": correct,
            "n_wrong": total - correct if total else 0, "total": total,
            "grade": grade, "stars": stars, "homework": homework,
        },
    }
    return record


def _derive_highlights(is_attend, is_playback, grade, stars, homework,
                        n_total, n_blank, correct, total) -> list:
    """课堂亮点：仅输出有事实依据的条目。

    v1.3 缺陷修复：不得因「有照片 + 出勤」就说「按时完成、书写作答完整」。
    只有当本次小黑板全部完成（无空白未提交）时，才允许输出正面完成结论，
    且「掌握扎实」类评级亮点同样要求全部完成，避免「1 题做对、7 题未做」却评级 A 的矛盾。
    """
    lines = []
    fully_done = n_total > 0 and n_blank == 0        # 全部完成
    if is_attend:
        lines.append("孩子准时到课，学习状态在线，课堂参与积极。")
    elif is_playback:
        lines.append("本次课未能到现场，但通过看回放完成了学习，建议结合回放笔记巩固当堂内容。")
    # 未出勤不写「亮点」（属负面信息，避免凑数）
    if grade in ("A", "B-A") and fully_done:
        lines.append(f"课堂评级保持 {GRADE_LABEL.get(grade, grade)}，对当堂内容掌握扎实。")
    if stars and stars >= 6:
        lines.append(f"本讲获得 {stars} 颗星星，学习投入度保持在很高水平。")
    if homework == "已提交":
        lines.append("课后作业已按时提交，学习节奏保持得很好。")
    if fully_done:
        lines.append("课堂小黑板按时完成，书写作答完整。")
    # 部分完成 / 无数据：不输出任何正面完成结论（交由「小黑板完成情况与分析」如实呈现）
    return lines


def _names_of(texts: list) -> str:
    """把一组题的 bullet 文案里的题名抽出来，拼成「「题A」「题B」」形式。"""
    names = []
    for t in texts:
        m = re.match(r"「(.+?)」", t)
        if m:
            names.append(f"「{m.group(1)}」")
    return "、".join(names) if names else ""


def _summary_modules(cats: dict, correct, total, n_total) -> list:
    """小黑板总结按三个模块分别写：全部正确 / 有错 / 未作答。

    v1.3：返回 dict 列表 {text, hl:[(片段, 颜色)]}，重要数据带颜色提示；
    兼容纯文本消费方（str(mod) 仍可读）。
    """
    mods = []

    def add(text, *marks):
        mods.append({"text": text, "hl": list(marks)})

    if n_total:
        pct = round(correct * 100 / total) if total else None
        if total:
            add(f"本次小黑板共 {n_total} 题（已批改 {total} 题、正确率 {pct}%）。",
                (f"{n_total} 题", TONE["neutral"]),
                (f"已批改 {total} 题", TONE["neutral"]),
                (f"正确率 {pct}%", _pct_color(pct)))
        else:
            add(f"本次小黑板共 {n_total} 题（暂未批改）。",
                (f"{n_total} 题", TONE["neutral"]))
    blocks = [
        ("correct", "全部正确"),
        ("wrong", "有错需订正"),
        ("blank", "未作答"),
    ]
    for key, label in blocks:
        texts = (cats.get(key) or {}).get("texts") or []
        if not texts:
            continue
        names = _names_of(texts)
        # v1.6：删掉「思路完整 / 详见下方说明 / 尽快补交」等无信息量的尾巴
        text = f"{label} · {len(texts)} 题：" + names
        marks = [(label, TONE[key]), (f"{len(texts)} 题", TONE[key])]
        # v1.8：「」题名与该行分类标签同色，一行一色保持视觉连贯
        for quoted in re.findall(r"「.+?」", names):
            marks.append((quoted, TONE[key]))
        add(text, *marks)
    if not mods:
        add("本讲暂未收集到该学员的小黑板作答记录，建议向老师核实完成情况。")
    return mods


CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八"]


def build_blocks(rec: dict) -> list:
    """返回与类型 B 同 schema 的 block 列表。模块编号动态生成，隐藏模块后自动顺延。"""
    seq_n = [0]

    def nxt():
        n = CN_NUM[seq_n[0]] if seq_n[0] < len(CN_NUM) else str(seq_n[0] + 1)
        seq_n[0] += 1
        return n

    # v1.4 头图：先课程名称 → 再第几次课 → 上课时间（家长可读）
    blocks = [
        {"type": "band", "title": f"{rec['name']}学习报告",
         "course": rec["product_full"],
         "sub": f"{rec['seq']} · 单次课堂报告",
         "class_time": rec.get("class_time") or "",
         "meta": rec["product_full"]},
        # v1.4 信息卡去重：课程/课次/学员/日期已在色块，仅保留双老师
        {"type": "infocard", "rows": [
            ("主讲老师", rec["teacher"]), ("联席老师", rec["lianxi"]),
        ]},
        {"type": "metrics", "items": rec["metrics"]},
    ]
    if rec.get("highlights_visible"):
        blocks.append({"type": "card", "title": f"{nxt()}、课堂亮点", "body": [
            {"type": "bullets", "items": rec["highlights"]},
        ]})
    blocks.append({"type": "card", "title": f"{nxt()}、小黑板完成情况与分析", "body": [
        {"type": "hl_bullets", "items": rec["summary_modules"]},
    ]})
    # 三类小黑板分区展示：全部正确 / 有错 / 未作答。
    # 文字按题归类；照片按轮次归类且一张只出现一次（parse_analysis 已去重）。
    cat_specs = [
        ("correct", "correct", "小黑板 · 全部正确"),
        ("wrong", "wrong", "小黑板 · 有错待订正"),
        ("blank", "blank", "小黑板 · 未作答"),
    ]
    for key, cat, title in cat_specs:
        c = rec["cats"].get(key) or {}
        texts, imgs, units = c.get("texts") or [], c.get("imgs") or [], c.get("units") or []
        if not texts and not imgs and not units:
            continue
        n_img = len(units) or len(imgs)
        suffix = f"（{n_img} 张）" if n_img else ""
        blocks.append({"type": "cat", "cat": cat, "title": title + suffix,
                       "items": texts, "images": imgs, "units": units})
    blocks.append({"type": "card", "title": f"{nxt()}、下次课小目标", "body": [
        {"type": "bullets", "items": [
            "订正本次小黑板中做错与未完成的题目，把解题思路讲一遍。",
            "课上主动展示 1 次解题思路，把方法讲给同学和老师听。",
        ]},
    ]})
    blocks.append({"type": "footnote",
                   "text": "本报告依据课堂记录与小黑板作答整理，供家长了解孩子学习情况；发送前请老师确认。"})
    return blocks
