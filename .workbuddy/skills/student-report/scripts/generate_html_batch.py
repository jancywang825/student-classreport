#!/usr/bin/env python3
"""批量单次课堂报告 → 自包含 HTML（按学生姓名切换）。

数据源：双提数学后台接口（backend_client.BackendClient）。
  - 真实模式：浏览器沙箱登录拿 x-token → 调接口取全班学生/考勤/小黑板分析。
  - 演示模式：--mock，使用与接口同形状的样例数据，无需凭证即可预览。

报告维度与既有「单次课堂报告（类型 B）」完全一致：课堂亮点 / 小黑板完成情况与分析
（表扬点含正确作答图、提升点含错误/未写图）/ 我们一起努力的方向 / 下次课小目标。

输出：单个 HTML 文件，顶部绿色头图 + 姓名切换器（芯片），点击学生姓名即切换其报告；
所有小黑板图片以 base64 内嵌，离线可看。

用法：
  # 演示（无需凭证）
  python generate_html_batch.py --mock --product-name "L-初三二物理" --class-seq "2026-10-17 讲次" \
      --co-teacher "蔡笑铮" --out ./reports

  # 真实（首次会打开沙箱浏览器登录）
  python generate_html_batch.py --product-name "L-初三二物理" --class-seq 3 --co-teacher "蔡笑铮" \
      --out ./reports
"""
import argparse
import base64
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backend_client import BackendClient          # noqa: E402
from report_content import build_student_record, build_blocks  # noqa: E402

HEX = {
    "brand": "#0AA860", "brand_dark": "#067A45", "brand_light": "#35C97E",
    "brand_deep": "#059152", "text": "#2B2F33", "gray": "#8A9299",
    "blue": "#4A7FB5", "green": "#0AA860", "lightgreen": "#E9F7EF",
    "orange": "#D08A3E", "lightorange": "#FBEEDD", "page": "#F4F6F8",
    "card": "#FFFFFF", "line": "#E6EAEE",
}


def esc(t: str) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def load_image_datauri(url_or_path: str, token: str | None, max_side: int = 1280,
                       quality: int = 82) -> str | None:
    """本地文件直接读取；远程 URL 带 x-token 下载。统一压缩后转 base64，避免全部展现后体积过大。"""
    if not url_or_path:
        return None
    p = Path(url_or_path)
    if p.exists() and p.is_file():
        data = p.read_bytes()
        return _resize_and_encode(data, max_side, quality)
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        # 偶发 502/超时重试（质检会核对图片数，缺图会直接报 FAIL）
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url_or_path,
                    headers={"x-token": token} if token else {})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                return _resize_and_encode(data, max_side, quality)
            except Exception as e:
                last = e
                if attempt < 2:
                    time.sleep(0.6 * (attempt + 1))
        print(f"[warn] 小黑板图片下载失败（已重试3次）：{url_or_path} -> {last}",
              file=sys.stderr)
    return None


def _resize_and_encode(data: bytes, max_side: int, quality: int) -> str | None:
    """把图片等比缩到最长边 max_side 以内，重新编码为 JPEG base64。失败回退原图。"""
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
        if max(img.size) > max_side:
            scale = max_side / max(img.size)
            img = img.resize((int(img.size[0] * scale), int(img.size[1] * scale)),
                             Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        b = buf.getvalue()
        return "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")
    except Exception as e:
        print(f"[warn] 图片压缩失败，使用原图：{e}", file=sys.stderr)
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


_Q_CROP_CACHE: dict = {}


def _crop_light_region(data: bytes):
    """自动裁出图中白底亮区（原题题干）。纯黑图返回 None。纯 PIL，无 numpy。"""
    from io import BytesIO
    from PIL import Image
    gray = Image.open(BytesIO(data)).convert("L")
    w, h = gray.size
    if not w or not h:
        return None
    thr, ratio = 170, 0.35
    sw = min(w, 400)
    sh = max(1, int(h * sw / w))
    px = gray.resize((sw, sh)).load()
    col = [sum(1 for y in range(sh) if px[x, y] > thr) / sh for x in range(sw)]
    bright = [x for x, v in enumerate(col) if v > ratio]
    if not bright:
        return None                      # 纯黑「未参与作答」图
    segs, s, prev = [], bright[0], bright[0]
    for x in bright[1:]:
        if x - prev > 3:
            segs.append((s, prev))
            s = x
        prev = x
    segs.append((s, prev))
    a, b = max(segs, key=lambda t: t[1] - t[0])
    x0, x1 = int(a * w / sw), int(b * w / sw)
    if x1 - x0 < w * 0.08:               # 亮区太窄（可能只是水印），不算题干
        return None
    sub = gray.crop((x0, 0, x1 + 1, h))
    ssw = min(sub.width, 400)
    ssh = max(1, int(h * ssw / sub.width))
    p2 = sub.resize((ssw, ssh)).load()
    rows = [y for y in range(ssh)
            if sum(1 for x in range(ssw) if p2[x, y] > thr) / ssw > ratio]
    y0 = int(rows[0] * h / ssh) if rows else 0
    y1 = int(rows[-1] * h / ssh) if rows else h
    pad = 8
    box = (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad))
    return Image.open(BytesIO(data)).convert("RGB").crop(box)


def question_crop_datauri(url: str, token: str | None) -> str | None:
    """下载小黑板图并裁出原题题干，返回 base64。带结果缓存，纯黑图返回 None。"""
    if not url:
        return None
    if url in _Q_CROP_CACHE:
        return _Q_CROP_CACHE[url]
    result = None
    if url.startswith("http://") or url.startswith("https://"):
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"x-token": token} if token else {})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                cropped = _crop_light_region(data)
                if cropped is not None:
                    from io import BytesIO
                    buf = BytesIO()
                    cropped.save(buf, format="JPEG", quality=88, optimize=True)
                    result = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                break
            except Exception:
                if attempt == 0:
                    time.sleep(0.5)
    _Q_CROP_CACHE[url] = result
    return result


def find_question_crop(urls: list, token: str | None) -> str | None:
    """按候选顺序尝试裁出原题图（用于纯黑「未参与作答」照片）。"""
    for u in urls or []:
        uri = question_crop_datauri(u, token)
        if uri:
            return uri
    return None


# ----------------------------- 单学生 block → HTML -----------------------------
def render_blocks_html(blocks: list, token: str | None) -> str:
    parts = []

    for b in blocks:
        t = b["type"]
        if t == "band":
            # v1.4：先课程名称、再第几次课、再上课时间
            course = b.get("course") or b.get("meta") or ""
            class_time = b.get("class_time") or ""
            time_line = (f'<div class="stu-time">上课时间：{esc(class_time)}</div>'
                         if class_time else "")
            parts.append(f"""
      <div class="stu-band">
        <div class="stu-title">{esc(b['title'])}</div>
        <div class="stu-course">{esc(course)}</div>
        <div class="stu-sub">{esc(b['sub'])}</div>
        {time_line}
      </div>""")
        elif t == "infocard":
            cells = "".join(
                f'<div class="info-cell"><div class="info-lbl">{esc(k)}</div>'
                f'<div class="info-val">{esc(v)}</div></div>'
                for k, v in b["rows"])
            parts.append(f'<div class="info-card">{cells}</div>')
        elif t == "metrics":
            chips = "".join(
                f'<div class="metric"><div class="metric-val" style="color:{esc(c)}">'
                f'{esc(v)}</div><div class="metric-lbl">{esc(l)}</div></div>'
                for v, l, c in b["items"])
            parts.append(f'<div class="metrics">{chips}</div>')
        elif t == "card":
            inner = "".join(render_inner(sub, token) for sub in b["body"])
            parts.append(f"""
      <div class="card">
        <div class="card-title"><span class="bar"></span>{esc(b['title'])}</div>
        <div class="card-body">{inner}</div>
      </div>""")
        elif t == "cat":
            style = {
                "correct": (HEX["lightgreen"], HEX["brand_dark"]),
                "wrong": (HEX["lightorange"], HEX["orange"]),
                "blank": ("#EDF1F5", "#4A7FB5"),
            }.get(b.get("cat"), (HEX["lightorange"], HEX["orange"]))
            tint, chex = style
            # 逐图左右表格：左侧小黑板原图（可点击放大），右侧该图点评
            rows = ""
            for u in b.get("units", []):
                uri = load_image_datauri(u.get("img"), token)
                if uri:
                    left = (f'<div class="xhb-row-img">'
                            f'<img class="zoomable" src="{uri}" alt="{esc(u["cap"])}" '
                            f'onclick="openZoom(this)" title="点击放大查看细节"/>'
                            f'<div class="xhb-cap">{esc(u["cap"])} · 点击图片可放大</div></div>')
                else:
                    left = '<div class="xhb-row-img xhb-noimg">该轮小黑板图片暂不可用</div>'

                if u.get("is_blank"):
                    # v1.5 未作答：右侧展示「未作答」标签 + 原题 + 知识点
                    tag = "未参与作答" if u.get("no_participation") else "未作答"
                    seg = (f'<div class="blank-badge">{esc(tag)}</div>')
                    # 原题：裁出白底题干，让家长看清孩子没做哪道题
                    q_uri = find_question_crop(u.get("question_imgs", []), token)
                    if q_uri:
                        seg += (f'<div class="q-img"><div class="q-img-lbl">原题</div>'
                                f'<img src="{q_uri}" alt="原题：{esc(u.get("question",""))}" '
                                f'onclick="openZoom(this)" title="点击放大原题"/>'
                                f'<div class="q-img-cap">原题题干 · 点击可放大</div></div>')
                    seg += (f'<div class="rv-block">'
                            f'<div class="rv-title" style="color:#4A7FB5">原题题目</div>'
                            f'<div class="rv-line">• {esc(u.get("question") or "本轮原题")}</div></div>')
                    if u.get("knowledge"):
                        seg += (f'<div class="rv-block">'
                                f'<div class="rv-title" style="color:#0AA860">涉及知识点</div>'
                                f'<div class="rv-line">• {esc(u["knowledge"])}</div></div>')
                    # v1.7：混合题照片（一题未做、另一题做对）也展示真实肯定
                    if u.get("good"):
                        lis = "".join(f'<div class="rv-line">• {_emphasize_quoted(x)}</div>'
                                      for x in u["good"])
                        seg += (f'<div class="rv-block">'
                                f'<div class="rv-title" style="color:#0AA860">做得好的点</div>'
                                f'{lis}</div>')
                    # v1.6：去掉「建议对照课堂讲解补齐本题，再把解题思路讲一遍」等空泛句
                    right = f'<div class="xhb-row-review">{seg}</div>'
                else:
                    segs = ""
                    # v1.7：中性段标题；good 排在首位（先看见努力，再谈改进）
                    seg_specs = [("good", "做得好的点", "#0AA860"),
                                 ("bad", "待订正的点", "#D08A3E"),
                                 ("stuck", "卡住的地方", "#4A7FB5")]
                    for key, label, color in seg_specs:
                        lines = u.get(key) or []
                        if not lines:
                            continue
                        lis = "".join(
                            f'<div class="rv-line">• {_emphasize_quoted(x)}</div>'
                            for x in lines)
                        segs += (f'<div class="rv-block">'
                                 f'<div class="rv-title" style="color:{color}">{label}</div>'
                                 f'{lis}</div>')
                    if not segs:
                        segs = '<div class="rv-line">本轮暂无逐题点评。</div>'
                    right = f'<div class="xhb-row-review">{segs}</div>'
                rows += f'<div class="xhb-row">{left}{right}</div>'
            # 兜底：无逐图数据时沿用旧的文字+图片网格（PDF / 兼容）
            if not rows and (b.get("items") or b.get("images")):
                items = "".join(f'<div class="bullet">• {esc(it)}</div>' for it in b.get("items", []))
                imgs = ""
                for url, cap in b.get("images", []):
                    uri = load_image_datauri(url, token)
                    if uri:
                        imgs += (f'<div class="xhb-img"><img src="{uri}" alt="{esc(cap)}"/>'
                                 f'<div class="xhb-cap">{esc(cap)}</div></div>')
                rows = items + (f'<div class="xhb-grid">{imgs}</div>' if imgs else "")
            parts.append(f"""
      <div class="accent" style="background:{tint};border-color:{chex}">
        <div class="accent-title" style="color:{chex}">{esc(b['title'])}</div>
        <div class="accent-body">{rows}</div>
      </div>""")
        elif t == "footnote":
            parts.append(f'<div class="footnote">{esc(b["text"])}</div>')
    return "\n".join(parts)


def _highlight_text(text: str, marks: list) -> str:
    """把文本里的重要数据片段上色（v1.6：仅颜色，不加粗）。

    用占位符法一次替换：按片段长度降序，先把命中片段换成不含中文的占位符，
    最后统一替换成 <span class="hl">。避免重叠片段把已生成的 HTML 标签
    内部再次替换、造成嵌套错乱。
    """
    safe = esc(text)
    frags = []
    for frag, color in marks or []:
        f = esc(frag)
        if f and f in safe and f not in [x[0] for x in frags]:
            frags.append((f, color))
    frags.sort(key=lambda x: len(x[0]), reverse=True)
    holders = []
    for i, (f, color) in enumerate(frags):
        holder = f"\x00HL{i}\x00"
        if f in safe:
            safe = safe.replace(f, holder, 1)
            holders.append((holder, f, color))
    for holder, f, color in holders:
        safe = safe.replace(holder,
                            f'<span class="hl" style="color:{color}">{f}</span>')
    return safe


def _emphasize_quoted(text: str) -> str:
    """把「」包裹的知识点 / 方法名着色强调（仅颜色，不加粗）。"""
    safe = esc(text)
    return re.sub(r"「[^」]+」",
                  lambda m: f'<span class="kp">{m.group(0)}</span>', safe)


def render_inner(sub: dict, token: str | None) -> str:
    t = sub["type"]
    if t == "para":
        return f'<p class="para">{esc(sub["text"])}</p>'
    if t == "bullets":
        return "".join(f'<div class="bullet">• {esc(it)}</div>' for it in sub["items"])
    if t == "hl_bullets":
        out = []
        for it in sub["items"]:
            if isinstance(it, dict):
                out.append(f'<div class="bullet hl-bullet">• '
                           f'{_highlight_text(it["text"], it.get("hl", []))}</div>')
            else:
                out.append(f'<div class="bullet">• {esc(it)}</div>')
        return "".join(out)
    if t == "table":
        head = "".join(f"<th>{esc(h)}</th>" for h in sub["headers"])
        rows = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>"
                       for r in sub["rows"])
        return f'<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'
    if t == "image":
        uri = load_image_datauri(sub["path"], token)
        if not uri:
            return ""
        return (f'<div class="xhb-img"><img src="{uri}" alt="{esc(sub["caption"])}"/>'
                f'<div class="xhb-cap">{esc(sub["caption"])}</div></div>')
    return ""


# ----------------------------- 组装整页 HTML -----------------------------
CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#F4F6F8; color:#2B2F33;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif;
  line-height:1.8; letter-spacing:.3px; }
.topbar { background:linear-gradient(135deg,#35C97E 0%,#059152 100%);
  color:#fff; padding:26px 22px 20px; }
.topbar h1 { margin:0; font-size:24px; letter-spacing:1px; }
.topbar .sub { margin-top:6px; font-size:13px; opacity:.92; letter-spacing:.5px; }
.topbar .course { margin-top:4px; font-size:12px; opacity:.8; }
.switcher { position:sticky; top:0; z-index:20; background:#fff;
  border-bottom:1px solid #E6EAEE; padding:10px 14px; display:flex; gap:8px;
  overflow-x:auto; box-shadow:0 2px 8px rgba(0,0,0,.04); }
.switcher .chip { flex:0 0 auto; padding:7px 16px; border-radius:20px; cursor:pointer;
  font-size:14px; color:#067A45; background:#E9F7EF; border:1px solid #CDEBD9;
  white-space:nowrap; transition:all .15s; }
.switcher .chip:hover { background:#D5F0E1; }
.switcher .chip.active { background:#0AA860; color:#fff; border-color:#0AA860; font-weight:600; }
.wrap { max-width:820px; margin:0 auto; padding:20px 16px 60px; }
.student-report { display:none; }
.student-report.active { display:block; }
.stu-band { background:linear-gradient(135deg,#35C97E,#059152); color:#fff;
  border-radius:14px; padding:22px 20px; margin-bottom:18px; position:relative; overflow:hidden; }
.stu-band::after { content:""; position:absolute; right:-30px; top:-30px; width:120px; height:120px;
  background:rgba(255,255,255,.10); border-radius:50%; }
.stu-title { font-size:22px; font-weight:700; letter-spacing:1px; }
.stu-course { margin-top:8px; font-size:13px; font-weight:600; opacity:.95;
  line-height:1.6; letter-spacing:.3px; }
.stu-sub { margin-top:4px; font-size:13px; opacity:.92; }
.stu-time { margin-top:4px; font-size:12px; opacity:.9;
  display:inline-block; padding:2px 10px; border-radius:12px;
  background:rgba(255,255,255,.18); }
.stu-meta { margin-top:3px; font-size:12px; opacity:.82; }
.info-card { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:#E6EAEE;
  border:1px solid #E6EAEE; border-radius:12px; overflow:hidden; margin-bottom:16px; }
.info-cell { background:#fff; padding:10px 14px; }
.info-lbl { font-size:11px; color:#8A9299; letter-spacing:.5px; }
.info-val { font-size:15px; color:#2B2F33; font-weight:600; margin-top:2px; }
.metrics { display:flex; gap:10px; margin-bottom:18px; flex-wrap:wrap; }
.metric { flex:1 1 0; min-width:90px; background:#fff; border:1px solid #E6EAEE; border-radius:12px;
  text-align:center; padding:12px 6px; }
.metric-val { font-size:20px; font-weight:700; line-height:1.3; }
.metric-lbl { font-size:11px; color:#8A9299; margin-top:3px; letter-spacing:.5px; }
.card { background:#fff; border:1px solid #E6EAEE; border-radius:12px; padding:16px 18px;
  margin-bottom:16px; }
.card-title { font-size:16px; font-weight:700; color:#2B2F33; margin-bottom:10px;
  display:flex; align-items:center; gap:8px; }
.card-title .bar { display:inline-block; width:4px; height:16px; border-radius:2px;
  background:#0AA860; }
.card-body p.para { margin:0 0 6px; font-size:14px; }
.bullet { font-size:14px; margin:5px 0; padding-left:4px; }
.accent { border:1px solid; border-radius:12px; padding:14px 18px; margin-bottom:16px; }
.accent-title { font-size:15px; font-weight:700; margin-bottom:8px; }
.accent-body .bullet { font-size:14px; }
.xhb-img { margin:12px 0 4px; text-align:center; }
.xhb-img img { max-width:100%; max-height:520px; border-radius:8px; border:1px solid #ddd; }
.xhb-cap { font-size:12px; color:#8A9299; margin-top:6px; }
.xhb-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:12px 0 4px; }
.xhb-grid .xhb-img { margin:0; }
.xhb-grid .xhb-img img { width:100%; max-height:300px; object-fit:contain; background:#fff; }
/* 逐图左右表格：左图 + 右点评 */
.xhb-row { display:grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr);
  gap:16px; padding:14px 0; border-top:1px dashed #DCE3E8; align-items:start; }
.xhb-row:first-child { border-top:none; padding-top:2px; }
.xhb-row-img { min-width:0; }
.xhb-row-img img.zoomable { width:100%; max-height:360px; object-fit:contain; background:#fff;
  border:1px solid #D8DEE3; border-radius:8px; cursor:zoom-in; display:block;
  transition:box-shadow .15s; }
.xhb-row-img img.zoomable:hover { box-shadow:0 3px 12px rgba(0,0,0,.14); }
.xhb-row-img .xhb-cap { margin-top:6px; font-size:12px; color:#8A9299; line-height:1.6; }
.xhb-noimg { font-size:13px; color:#8A9299; padding:30px 8px; text-align:center;
  background:#F4F6F8; border:1px dashed #D8DEE3; border-radius:8px; }
.xhb-row-review { min-width:0; }
.hl-bullet { line-height:1.9; }
/* v1.6：强调只改字色，不加粗 */
.hl { font-weight:400; }
.kp { color:#0B7A55; font-weight:400; }
.rv-block { margin-bottom:10px; }
.rv-block:last-child { margin-bottom:0; }
.rv-title { font-size:13px; font-weight:700; margin-bottom:4px; letter-spacing:.3px; }
.rv-line { font-size:13px; line-height:1.75; margin:2px 0; color:#3A4046; padding-left:2px; }
/* 语义分段后的短句：竖线引导，逐条易读 */
.rv-block .rv-line { padding-left:12px; position:relative; }
.rv-block .rv-line::before { content:""; position:absolute; left:2px; top:.7em;
  width:4px; height:4px; border-radius:50%; background:#B9C2C9; }
/* v1.5 未作答：徽章 + 原题图 */
.blank-badge { display:inline-block; padding:3px 12px; border-radius:14px;
  background:#4A7FB5; color:#fff; font-size:12px; font-weight:700;
  letter-spacing:.6px; margin-bottom:10px; }
.q-img { margin:2px 0 12px; border:1px solid #D8DEE3; border-radius:8px;
  padding:8px; background:#fff; }
.q-img-lbl { display:inline-block; font-size:11px; font-weight:700; color:#4A7FB5;
  background:#EDF1F5; border-radius:6px; padding:1px 8px; margin-bottom:6px; }
.q-img img { width:100%; max-height:220px; object-fit:contain; cursor:zoom-in;
  border-radius:4px; display:block; }
.q-img img:hover { box-shadow:0 2px 10px rgba(0,0,0,.12); }
.q-img-cap { font-size:11px; color:#8A9299; margin-top:5px; }
@media (max-width:620px){
  .xhb-row { grid-template-columns:1fr; gap:10px; }
  .xhb-grid { grid-template-columns:1fr; }
}
/* 点击放大灯箱 */
.lightbox { display:none; position:fixed; inset:0; z-index:100; background:rgba(18,22,26,.90);
  align-items:center; justify-content:center; flex-direction:column; padding:24px; }
.lightbox.show { display:flex; }
.lightbox img { max-width:96vw; max-height:84vh; object-fit:contain; border-radius:6px;
  background:#fff; box-shadow:0 8px 40px rgba(0,0,0,.5); }
.lightbox .lb-cap { color:#fff; font-size:13px; margin-top:14px; opacity:.9; text-align:center;
  max-width:90vw; line-height:1.6; }
.lightbox .lb-close { position:absolute; top:18px; right:24px; color:#fff; font-size:30px;
  line-height:1; cursor:pointer; width:40px; height:40px; text-align:center;
  border:1px solid rgba(255,255,255,.5); border-radius:50%; }
.lightbox .lb-tip { position:absolute; bottom:14px; color:#fff; font-size:12px; opacity:.6; }
table { width:100%; border-collapse:collapse; font-size:13px; margin-top:4px; }
th { background:#0AA860; color:#fff; padding:7px 8px; text-align:left; }
td { border:1px solid #E6EAEE; padding:7px 8px; }
tbody tr:nth-child(even) { background:#F4F6F8; }
.footnote { text-align:center; color:#8A9299; font-size:12px; margin-top:18px;
  padding-top:14px; border-top:1px dashed #E6EAEE; line-height:1.7; }
@media print { .switcher { display:none; } .student-report { display:block !important;
  page-break-after:always; } body { background:#fff; }
  .lightbox { display:none !important; } .xhb-row { break-inside:avoid; } }
"""

JS = """
function showStudent(idx, el){
  document.querySelectorAll('.student-report').forEach(function(s){ s.classList.remove('active'); });
  document.querySelectorAll('.switcher .chip').forEach(function(c){ c.classList.remove('active'); });
  document.getElementById('stu-'+idx).classList.add('active');
  if(el) el.classList.add('active');
  closeZoom();
  window.scrollTo({top:0, behavior:'smooth'});
}
function openZoom(img){
  var lb=document.getElementById('lightbox');
  document.getElementById('lb-img').src=img.src;
  document.getElementById('lb-cap').textContent=img.alt||'小黑板原图';
  lb.classList.add('show');
  document.body.style.overflow='hidden';
}
function closeZoom(){
  var lb=document.getElementById('lightbox');
  if(lb){ lb.classList.remove('show'); }
  document.body.style.overflow='';
}
document.addEventListener('keydown',function(e){ if(e.key==='Escape') closeZoom(); });
"""

PAGE_JS_HEAD = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="topbar">
  <h1>{h1}</h1>
  <div class="sub">{sub}</div>
  <div class="course">{course}</div>
</div>
<div class="switcher">
{chips}
</div>
<div class="wrap">
{bodies}
</div>
<div class="lightbox" id="lightbox" onclick="closeZoom()">
  <span class="lb-close" onclick="closeZoom()" title="关闭">&times;</span>
  <img id="lb-img" src="" alt="放大查看" onclick="event.stopPropagation()"/>
  <div class="lb-cap" id="lb-cap"></div>
  <div class="lb-tip">点击空白处 / 右上角 × / 按 ESC 关闭</div>
</div>
<script>{js}</script>
</body>
</html>
"""


def render_html(data: dict, token: str | None, generated_at: str,
                 product_name: str, class_seq: str, co_teacher: str) -> str:
    students = data["students"]
    lesson = data["lesson"]
    att_map = data["attendance_map"]
    bb_map = data["blackboard_map"]
    q_index = data.get("question_index") or {}

    chips, bodies = [], []
    for i, stu in enumerate(students):
        name = stu.get("studentName") or stu.get("name") or f"学员{i+1}"
        chips.append(f'<div class="chip{" active" if i==0 else ""}" onclick="showStudent({i},this)">{esc(name)}</div>')
        rec = build_student_record(stu, lesson, att_map.get(name), bb_map.get(name), q_index)
        blocks = build_blocks(rec)
        body = render_blocks_html(blocks, token)
        bodies.append(f'<div class="student-report{" active" if i==0 else ""}" id="stu-{i}">{body}</div>')

    teacher_label = next((s.get("classTeacherName") for s in students
                          if s.get("classTeacherName")), "") if students else ""
    who = co_teacher or teacher_label or "—"
    return PAGE_JS_HEAD.format(
        title=f"{product_name or ''} {class_seq} · 学员课堂报告",
        css=CSS,
        h1=f"{esc(product_name or '')} · {esc(str(class_seq))} 学员课堂报告",
        sub=f"{'联席/教务' if co_teacher else '主讲'}：{esc(who)}　|　共 {len(students)} 名学员　|　生成于 {esc(generated_at)}",
        course=f"课程全称：{esc(lesson.get('product_name') or product_name or '—')}　课节：{esc(str(lesson.get('class_seq') or class_seq))}",
        chips="\n".join(chips),
        bodies="\n".join(bodies),
        js=JS,
    )


def fmt_seq(seq) -> str:
    """课次展示名：若已含 讲/课/次 直接用，否则包成『第X次课』。"""
    s = str(seq).strip()
    if "讲" in s or "课" in s or "次" in s:
        return s
    return f"第{s}次课"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product-name", default=None, help="课程名称（精确匹配）")
    ap.add_argument("--course", dest="product_name", help="--product-name 的别名")
    ap.add_argument("--class-seq", default=None, required=True, help="第几课节（classSeq）")
    ap.add_argument("--co-teacher", default=None, help="联席/教务名称（精确匹配）")
    ap.add_argument("--teacher", default=None, help="主讲署名覆盖（如用户指定的老师名；不改考勤等真实数据）")
    ap.add_argument("--seq-label", default=None, help="课次展示名覆盖（如「第10次课 · 考后总结」）")
    ap.add_argument("--token", default=None, help="x-token；缺省读取缓存或触发浏览器登录")
    ap.add_argument("--login-url", default=None, help="登录页地址（默认 基础域/login）")
    ap.add_argument("--mock", action="store_true", help="使用样例数据预览，无需凭证")
    ap.add_argument("--out", default="./reports")
    ap.add_argument("--no-qc", action="store_true", help="跳过报告质检（默认生成后自动质检）")
    ap.add_argument("--no-report-usage", action="store_true",
                    help="跳过使用事件上报（默认生成后自动上报 report-created）")
    args = ap.parse_args()

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if args.mock:
        print("[mock] 使用样例数据生成预览 HTML（不调用真实接口）。")
        data = BackendClient.mock_resolve(args.product_name, args.class_seq, args.co_teacher)
        token = None
    else:
        token = args.token or BackendClient.load_cached_token()
        client = BackendClient(token=token)
        if not token:
            token = client.login_via_browser(login_url=args.login_url)
            if not token:
                print("[错误] 未获取到登录令牌，无法调用接口。可用 --mock 预览。", file=sys.stderr)
                sys.exit(1)
        client.token = token
        data = client.resolve(args.product_name, args.class_seq, args.co_teacher)

    # 用户显式指定的署名 / 课次展示名：只覆盖展示字段，数据仍来自接口
    if args.teacher:
        for stu in data["students"]:
            stu["classTeacherName"] = args.teacher
        print(f"[署名] 主讲老师显示为：{args.teacher}")

    seq_label = args.seq_label or fmt_seq(args.class_seq)
    if args.seq_label and data["lesson"].get("class_name"):
        print(f"[课次] 展示名：{seq_label}（课节原名：{data['lesson']['class_name']}）")
    html = render_html(data, token, generated_at,
                       args.product_name or data["lesson"].get("product_name") or "",
                       seq_label, args.co_teacher or "")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_seq = str(args.class_seq).replace(" ", "_")
    out_path = out_dir / f"批量课堂报告_{args.product_name or '课程'}_{safe_seq}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[ok] HTML: {out_path}")

    # 报告质检（需求 v1.3 · 4.6）：数据正确性 + 前后逻辑一致性
    if not args.no_qc:
        from quality_check import check_report, write_quality_report
        res = check_report(data, html, args.product_name or "", seq_label)
        qc_path = write_quality_report(res, out_dir, args.product_name or "课程",
                                       args.class_seq)
        print(f"[质检] {res['verdict']}｜FAIL {res['n_fail']} / WARN {res['n_warn']}"
              f"｜{len(res['rows'])} 项检查 → {qc_path}")
        for i in res["issues"][:10]:
            if i["level"] == "FAIL":
                print(f"  [FAIL] {i['scope']} · {i['check']}：{i['detail']}",
                      file=sys.stderr)
        if res["n_fail"]:
            print("[质检] 存在 FAIL 项，请查看质检报告后修正。", file=sys.stderr)

    # 使用事件上报（report-created）：每次生成报告后上报一次，失败不阻断交付
    if not args.no_report_usage:
        try:
            from usage_reporter import report_report_created
            # 主讲署名：用户指定优先，否则取学生档案中的真实值
            display_teacher = args.teacher or next(
                (s.get("classTeacherName") for s in data["students"]
                 if s.get("classTeacherName")), "") or (args.co_teacher or "")
            report_report_created(
                lesson=data["lesson"],
                students_count=len(data["students"]),
                class_label=seq_label,
                teacher=display_teacher,
                token=token,
            )
        except Exception as e:
            print(f"[上报] 跳过（接入异常）：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
