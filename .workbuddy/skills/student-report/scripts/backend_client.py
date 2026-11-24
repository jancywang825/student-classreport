#!/usr/bin/env python3
"""双提数学后台接口客户端（demo 版）。

职责：
  1. 鉴权：沙箱环境打开浏览器 → 用户输入用户名/密码 → 从页面 localStorage / cookie 提取 x-token；
     提取失败或环境不支持时，回退为「手动粘贴令牌」。令牌本地缓存，避免重复登录。
  2. 取数：封装与「单次课堂批量报告」相关的 5 个业务接口（见 business_api_selected_*.md）：
        #1 按课程查在读学生
        #2 按联席查在读学生
        #8 课节列表（用 productId + classSeq 取 classId）
        #4 课节考勤列表（按 productId + classSeq 取全班考勤）
        #9 课节小黑板分析（按 classId 取全班小黑板分析）
  3. mock 模式：无任何凭证时，返回与接口同形状的样例数据，便于本地预览 HTML。

接口基础域：https://stadminv2.xqmsx.cn （可通过 BASE 覆盖）
鉴权：所有请求在请求头 x-token 传入有效登录令牌。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://stadminv2.xqmsx.cn"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOKEN_CACHE = DATA_DIR / "token_cache.json"


def _find_workspace(start: str) -> Path:
    """向上查找同时含 .workbuddy 与 reports 的目录，作为工作区根。"""
    p = Path(start).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".workbuddy").exists() and (cand / "reports").exists():
            return cand
    return p

# 从页面常见位置探测登录令牌的键名
TOKEN_KEYS = ["x-token", "token", "X-Token", "accessToken", "access_token",
              "authToken", "Authorization", "TOKEN", "sessionToken"]

# 中文数字 → 阿拉伯数字（仅处理 0–99，覆盖课次常见范围）
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def normalize_class_seq(seq) -> int | None:
    """把课次（可能是『第五次课』『第5次课』『5』『5讲』等）归一为整数。
       无法解析时返回 None。"""
    if seq is None:
        return None
    s = str(seq).strip()
    if s == "" or s.lower() == "none":
        return None
    # 先剥离常见前后缀/空格
    s = (s.replace("第", "").replace("次", "").replace("课", "")
         .replace("讲", "").replace("节", "").strip())
    if s == "":
        return None
    # 阿拉伯数字优先
    m = re.search(r"\d+", s)
    if m:
        return int(m.group(0))
    # 中文数字
    if "十" in s:
        parts = s.split("十")
        left, right = parts[0], (parts[1] if len(parts) > 1 else "")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(s) == 1 and s in _CN_DIGITS:
        return _CN_DIGITS[s]
    val = 0
    for ch in s:
        if ch in _CN_DIGITS:
            val = val * 10 + _CN_DIGITS[ch]
    return val if val else None


class BackendClient:
    def __init__(self, base: str = BASE, token: str | None = None):
        self.base = base.rstrip("/")
        self.token = token

    # ----------------------------- 低层请求 -----------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                url += ("&" if "?" in url else "?") + qs
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url=f"{self.base}{path}", data=data,
                                     headers={**self._headers(),
                                              "Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def _headers(self) -> dict:
        return {"x-token": self.token} if self.token else {}

    # ----------------------------- 业务接口 -----------------------------
    def get_course_students(self, product_name: str) -> list:
        """#1 按课程查在读学生。返回 data.list。"""
        return self._get("/api/ai/getCourseStudents",
                         {"productName": product_name}).get("data", {}).get("list", [])

    def get_coteacher_students(self, co_teacher_name: str) -> list:
        """#2 按联席查在读学生。返回 data.list。"""
        return self._get("/api/ai/getCoTeacherStudents",
                         {"coTeacherName": co_teacher_name}).get("data", {}).get("list", [])

    def get_class_list(self, product_id: str | int, class_seq: str | int,
                       page: int = 1, page_size: int = 200) -> list:
        """#8 课节列表。用 productId + classSeq 定位具体课节。"""
        return self._get("/api/cClass/getCClassList",
                         {"productId": product_id, "classSeq": class_seq,
                          "productStatus": -1, "page": page, "pageSize": page_size}
                         ).get("data", {}).get("list", [])

    def get_attendance(self, product_id: str | int, class_seq: str | int,
                       student_name: str | None = None,
                       page: int = 1, page_size: int = 500) -> list:
        """#4 课节考勤列表。不传 studentName 时返回全班考勤。"""
        params = {"productId": product_id, "classSeq": class_seq,
                  "page": page, "pageSize": page_size}
        if student_name:
            params["studentName"] = student_name
        return self._get("/api/cClassAttend/getCClassAttendList", params
                         ).get("data", {}).get("list", [])

    def get_blackboard_analysis(self, class_id: str | int) -> list:
        """#9 课节小黑板分析。classId 为正整数，返回全班分析记录。"""
        return self._get("/api/ai/getClassBlackboardAnalysis",
                         {"classId": class_id}).get("data", {}).get("list", [])

    # ----------------------------- 编排：解析出批量所需数据 -----------------------------
    @staticmethod
    def _match_class(class_list: list, class_seq: str | int):
        """在课节列表中定位目标课次：先精确匹配，再用（阿拉伯/中文）数字兜底。找不到返回 None。"""
        if not class_list:
            return None
        cs = str(class_seq).strip()
        target = normalize_class_seq(class_seq)
        for c in class_list:
            cseq = c.get("classSeq")
            if cseq is None:
                continue
            # 精确字符串匹配
            if str(cseq).strip() == cs:
                return c
            # 数字匹配（中文/阿拉伯统一归一）
            if target is not None and normalize_class_seq(cseq) == target:
                return c
        return None

    def resolve(self, product_name: str | None, class_seq: str | int,
                co_teacher_name: str | None) -> dict:
        """给定 课程名称 + 课次 + 联席/教务名称，返回：
           {
             "students": [学生档案摘要],
             "lesson":   {class_id, class_seq, class_name, class_date, product_id, product_name},
             "attendance_map": {studentName: 考勤记录},
             "blackboard_map": {studentName: 小黑板分析记录},
           }
        """
        # 1) 学生列表：优先按联席查；查不到时回退按课程查，再按教务名过滤
        #    （输入的可能是联席或教务名称；接口#2 仅支持联席，教务名走课程列表过滤）
        students = []
        if co_teacher_name:
            students = self.get_coteacher_students(co_teacher_name)
        if not students and product_name:
            students = self.get_course_students(product_name)
            if co_teacher_name:
                by_ct = [s for s in students if s.get("classTeacherName") == co_teacher_name]
                if by_ct:
                    students = by_ct
        if product_name:
            by_course = [s for s in students if s.get("productName") == product_name]
            if by_course:
                students = by_course
        if not students:
            raise RuntimeError("未查到任何在读学生，请检查 课程名称 / 联席名称 是否正确。")

        # 2) 课程与课节：从学生档案取 productId，再用课次定位 classId
        first = students[0]
        product_id = first.get("productId") or first.get("productId")  # 同一结构字段
        product_full = first.get("productName") or product_name or ""
        class_list = self.get_class_list(product_id, class_seq)
        if not class_list:
            class_list = self.get_class_list(product_id, None)  # 不带课次，拉全部再匹配
        lesson = self._match_class(class_list, class_seq)
        if lesson is None:
            avail = "、".join(str(c.get("classSeq")) for c in class_list) or "（该课程无课节）"
            raise RuntimeError(
                f"未在课节列表中找到「{class_seq}」。该课程可选的课次有：{avail}")
        class_id = lesson.get("classId") or lesson.get("ID") or lesson.get("classinClassID")
        class_date = lesson.get("classDate") or lesson.get("startTime") or lesson.get("className") or "—"
        class_begin = lesson.get("classBeginTime") or ""      # 真实上课起止时刻
        class_end = lesson.get("classEndTime") or ""
        class_seq_num = normalize_class_seq(lesson.get("classSeq"))

        # 3) 考勤（全班）与小黑板分析（全班）
        #    考勤接口按 productId + 数字 classSeq 查询；中文「第五次课」需先归一为 5。
        attend_list = self.get_attendance(product_id, class_seq_num if class_seq_num is not None else class_seq)
        attendance_map = {a.get("studentName"): a for a in attend_list}
        bb_list = self.get_blackboard_analysis(class_id) if class_id else []
        # 一名学生有多条小黑板记录（每题一条），按姓名聚合为列表
        blackboard_map = {}
        for b in bb_list:
            blackboard_map.setdefault(b.get("studentName"), []).append(b)

        # v1.5：按轮次聚合「同课次原题索引」，供未参与作答 / 空白未提交
        # 的照片补显原题（题名 + 知识点 + 含白底题干的样图）。
        # 同一节课全班题目相同，取同轮次出现频次最高的题名作代表。
        from collections import Counter, defaultdict
        q_tmp = defaultdict(lambda: {"names": Counter(), "knowledge": Counter(), "images": []})
        for b in bb_list:
            if not isinstance(b, dict):
                continue
            try:
                rnd = int(b.get("roundNo") or 0)
            except Exception:
                rnd = 0
            raw = b.get("analysisResult")
            items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
            for p in items:
                if not isinstance(p, dict):
                    continue
                nm = (p.get("problem") or p.get("title") or "").strip()
                kn = (p.get("topic_knowledge") or p.get("topic") or "").strip()
                if nm:
                    q_tmp[rnd]["names"][nm] += 1
                if kn:
                    q_tmp[rnd]["knowledge"][kn] += 1
            if rnd and b.get("imageUrl"):
                q_tmp[rnd]["images"].append(b["imageUrl"])
        question_index = {}
        for rnd, v in q_tmp.items():
            if not rnd:
                continue
            names = [n for n, _ in v["names"].most_common()]
            knows = [k for k, _ in v["knowledge"].most_common()]
            question_index[rnd] = {
                "problem": names[0] if names else "",
                "knowledge": "、".join(knows) if knows else "",
                "images": v["images"][:12],
            }

        return {
            "students": students,
            "lesson": {
                "class_id": class_id,
                "class_seq": class_seq,
                "class_name": lesson.get("className") or product_full,
                "class_date": class_date,
                "class_begin": class_begin,
                "class_end": class_end,
                "product_id": product_id,
                "product_name": product_full,
            },
            "attendance_map": attendance_map,
            "blackboard_map": blackboard_map,
            "question_index": question_index,
        }

    # ----------------------------- 登录：沙箱浏览器 + 令牌缓存 -----------------------------
    @staticmethod
    def load_cached_token() -> str | None:
        try:
            if TOKEN_CACHE.exists():
                return json.loads(TOKEN_CACHE.read_text(encoding="utf-8")).get("token")
        except Exception:
            pass
        return None

    @staticmethod
    def save_cached_token(token: str) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({"token": token}, ensure_ascii=False),
                               encoding="utf-8")

    def login_via_browser(self, login_url: str | None = None, timeout: int = 300) -> str:
        """打开沙箱浏览器到登录页，等待用户手动输入用户名/密码登录，
        随后从页面的 localStorage / cookie 中提取 x-token。超时或失败则回退手动粘贴。
        """
        login_url = login_url or f"{self.base}/#/login"
        print(f"[登录] 正在沙箱浏览器打开登录页：{login_url}")
        print("[登录] 请在浏览器中输入用户名和密码完成登录，本程序会自动读取登录令牌……")
        try:
            subprocess.run(["agent-browser", "open", login_url, "--args", "--no-sandbox"],
                           check=False)
        except FileNotFoundError:
            print("[登录] 未检测到浏览器自动化工具（agent-browser）。", file=sys.stderr)

        deadline = time.time() + timeout
        while time.time() < deadline:
            token = self._probe_token()
            if token:
                print("[登录] 已自动获取登录令牌。")
                self.save_cached_token(token)
                self.token = token
                return token
            time.sleep(2)
        print("[登录] 未能自动获取令牌（沙箱浏览器可能不可交互或登录未完成）。", file=sys.stderr)
        print("[登录] 可改用：在本机浏览器登录后台后，按 F12 → Application → localStorage 复制 x-token，", file=sys.stderr)
        print("        然后重跑命令并加参数 --token <你的令牌>。", file=sys.stderr)
        return None

    def _probe_token(self) -> str | None:
        """通过 agent-browser 在页面上下文中读取 localStorage / sessionStorage / cookie 探测令牌。"""
        js = (
            "(function(){"
            "  var o={};"
            "  try{ for(var k in localStorage){ o['ls_'+k]=localStorage.getItem(k); } }catch(e){}"
            "  try{ for(var k in sessionStorage){ o['ss_'+k]=sessionStorage.getItem(k); } }catch(e){}"
            "  try{ o['cookie']=document.cookie; }catch(e){}"
            "  return JSON.stringify(o);"
            "})()"
        )
        try:
            res = subprocess.run(["agent-browser", "eval", js],
                                 capture_output=True, text=True, timeout=20)
            out = res.stdout.strip()
            # agent-browser eval 可能包裹成 JSON 字符串，尝试提取
            text = out
            for ch in ('"', "'"):
                if text.startswith(ch) and text.endswith(ch):
                    try:
                        text = json.loads(text)
                    except Exception:
                        pass
                    break
            if isinstance(text, str):
                try:
                    data = json.loads(text)
                except Exception:
                    data = {}
            else:
                data = text if isinstance(text, dict) else {}
            hay = " ".join(str(v) for v in data.values())
            # 1) 直接命名字段
            for k in TOKEN_KEYS:
                v = data.get("ls_" + k) or data.get("ss_" + k) or data.get(k)
                if v:
                    return self._unwrap_token(v)
            # 2) cookie 形式：x-token=xxx; ...
            import re
            for m in re.findall(r"(?:^|;)\s*(x-token|token)=([^;]+)", hay, re.I):
                return m[1].strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _unwrap_token(raw: str) -> str | None:
        """令牌可能直接是字符串，也可能被 JSON 包成 {"token":"..."} 或 {"data":{...}}。"""
        raw = raw.strip()
        if not raw:
            return None
        try:
            obj = json.loads(raw)
            if isinstance(obj, str):
                return obj.strip()
            if isinstance(obj, dict):
                for k in ("token", "x-token", "accessToken", "access_token"):
                    if obj.get(k):
                        return str(obj[k]).strip()
                # 递归一层
                for v in obj.values():
                    if isinstance(v, dict):
                        for k in ("token", "x-token", "accessToken", "access_token"):
                            if v.get(k):
                                return str(v[k]).strip()
        except Exception:
            pass
        return raw

    # ----------------------------- mock 数据（无凭证预览用） -----------------------------
    @staticmethod
    def mock_resolve(product_name: str | None, class_seq: str | int,
                     co_teacher_name: str | None) -> dict:
        """返回与 resolve() 同形状的样例数据，便于在拿到真实凭证前预览 HTML。"""
        product_full = product_name or "L-初三二物理"
        co = co_teacher_name or "蔡笑铮"
        seq = class_seq or "2026-10-17 讲次"
        students = [
            {"studentName": "江涵钰", "studentSeq": "S001", "productId": "P100",
             "productName": product_full, "classTeacherName": "XX老师", "coTeacherName": co,
             "grade": "初二", "subjectName": "物理"},
            {"studentName": "孙越", "studentSeq": "S002", "productId": "P100",
             "productName": product_full, "classTeacherName": "XX老师", "coTeacherName": co,
             "grade": "初二", "subjectName": "物理"},
            {"studentName": "周泽栋", "studentSeq": "S003", "productId": "P100",
             "productName": product_full, "classTeacherName": "XX老师", "coTeacherName": co,
             "grade": "初二", "subjectName": "物理"},
            {"studentName": "李思源", "studentSeq": "S004", "productId": "P100",
             "productName": product_full, "classTeacherName": "XX老师", "coTeacherName": co,
             "grade": "初二", "subjectName": "物理"},
        ]
        attendance_map = {
            "江涵钰": {"isAttend": True, "isPlayback": False},
            "孙越": {"isAttend": True, "isPlayback": False},
            "周泽栋": {"isAttend": False, "isPlayback": True},
            "李思源": {"isAttend": True, "isPlayback": False},
        }
        # 小黑板分析：analysisResult 为 JSON（与真实接口同形状，字段可容错）
        def bb(name, grade, stars, correct, total, homework, summary,
               praise, improve, img):
            return {
                "studentName": name, "classId": "C200", "className": product_full,
                "coTeacherName": co, "classTeacherName": "XX老师",
                "imageUrl": img,
                "analysisResult": json.dumps({
                    "grade": grade, "stars": stars, "homework": homework,
                    "correct": correct, "total": total,
                    "summary": summary, "praise": praise, "improve": improve,
                }, ensure_ascii=False),
            }

        xhb_dir = _find_workspace(__file__) / "reports" / "assets"
        img_jhy = str(xhb_dir / "江涵钰_小黑板.png") if (xhb_dir / "江涵钰_小黑板.png").exists() else ""
        blackboard_map = {
            "江涵钰": bb("江涵钰", "A", 8, 1, 1, "已提交",
                        f"本次小黑板（{seq}）已按时完成，作答正确、书写工整、物理量图绘制规范，能按要求写出解题过程，说明课堂方法吸收到位。",
                        ["小黑板按时完成且作答正确，解题步骤完整、书写工整，物理量图绘制规范，能独立完成计算并给出结论。",
                         "小黑板对应课次课堂评级保持 A，且表现评价「优秀」，说明对当堂内容掌握扎实。"],
                        ["结合课堂反馈，下一步重点巩固概念表述的准确性，做到「会做也能说清楚」。",
                         "针对综合题适当提高解题速度与稳定性。"],
                        img_jhy),
            "孙越": bb("孙越", "B-A", 6, 1, 1, "已提交",
                       f"本次小黑板（{seq}）已按时完成，整体思路正确，个别概念表述可以更精准。",
                       ["小黑板按时完成，书写工整，核心步骤完整，能画出物理量全图。"],
                       ["学情反馈：个别概念念错，建议每完成一道题用一句话复述所用到的物理概念。",
                        "课堂评级还有提升空间，重点提升答题正确率与主动展示解题思路的频次。"],
                       ""),
            "周泽栋": bb("周泽栋", "B", 4, 0, 1, "未提交",
                       f"本次小黑板（{seq}）缺席课堂（看回放），未提交当堂小黑板作答。",
                       ["课程已购买，建议尽快补齐当堂学习。",
                        "回放完成后可补交小黑板，老师会单独点评。"],
                       ["需补交本次小黑板作答；概念表述与综合题速度均需加强。"],
                       ""),
            "李思源": bb("李思源", "A", 7, 1, 1, "已提交",
                       f"本次小黑板（{seq}）已按时完成，作答正确、作图规范，课堂参与积极。",
                       ["小黑板按时完成且作答正确，物理量图绘制规范，解题过程清晰。",
                        "课堂评级保持 A，多次获得老师「优秀」评价。"],
                       ["继续保持当前节奏，逐步提高综合题解题速度。"],
                       ""),
        }
        return {
            "students": students,
            "lesson": {
                "class_id": "C200", "class_seq": seq, "class_name": product_full,
                "class_date": str(seq), "class_begin": "", "class_end": "",
                "product_id": "P100", "product_name": product_full,
            },
            "attendance_map": attendance_map,
            "blackboard_map": blackboard_map,
            "question_index": {},
        }


if __name__ == "__main__":
    # 命令行：python backend_client.py login  -> 打开浏览器登录并缓存令牌
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        c = BackendClient()
        tok = c.login_via_browser()
        print("TOKEN:", tok)
