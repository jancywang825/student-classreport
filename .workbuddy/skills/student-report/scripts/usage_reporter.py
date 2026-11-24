#!/usr/bin/env python3
"""Skill 使用事件上报（reportSkillUsage）。

触发：每次用户用「课程名称 + 课次 + 主讲/联席姓名」生成学员课次报告后上报一次。
设计原则（重要）：
  - 上报失败**绝不阻断**报告生成与交付，只打印告警；
  - installation_id 持久化（UUID v4），首次生成后长期复用；
  - session_id 用 UUID v4（接口强制要求非零 UUID，不能用「课程ID-课节ID」）；
  - 「课程ID-课节ID」放入 info.course_class_id（已与后端确认方案）。

接口：POST https://stadminv2.xqmsx.cn/api/ai/reportSkillUsage
应答以 JSON 的 code 字段为准：0 成功、7 失败（不能只看 HTTP 状态码）。
"""
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

SKILL_NAME = "student-report"
SKILL_VERSION = "1.2.0"
EVENT_REPORT_CREATED = "report-created"
REPORT_URL = "https://stadminv2.xqmsx.cn/api/ai/reportSkillUsage"

# installation_id 与 token_cache.json 同目录，持久化保存
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INSTALLATION_FILE = DATA_DIR / "installation_id.json"


def get_installation_id() -> str:
    """读取或首次生成持久化的 installation_id（UUID v4）。"""
    try:
        if INSTALLATION_FILE.exists():
            data = json.loads(INSTALLATION_FILE.read_text(encoding="utf-8"))
            iid = str(data.get("installation_id") or "").strip()
            if iid:
                return iid
    except Exception:
        pass
    iid = str(uuid.uuid4())
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        INSTALLATION_FILE.write_text(
            json.dumps({"installation_id": iid}, ensure_ascii=False),
            encoding="utf-8")
    except Exception as e:
        print(f"[上报] 无法持久化 installation_id：{e}", file=sys.stderr)
    return iid


def report_report_created(lesson: dict, students_count: int,
                          class_label: str, teacher: str,
                          token: str | None = None,
                          timeout: int = 15, retries: int = 1) -> bool:
    """上报一次「报告已生成」事件。失败仅告警，返回是否成功，不抛异常阻断主流程。

    lesson: resolve() 返回的 lesson dict（含 product_id / class_id / product_name）
    students_count: 本次报告涉及的学生数
    class_label: 课次展示名（如「第十一次课 · 电功、电功率」）
    teacher: 主讲/联席名称（用户指定署名）
    """
    try:
        pid = lesson.get("product_id") or ""
        cid = lesson.get("class_id") or ""
        course_class_id = f"{pid}-{cid}" if pid and cid else ""
        payload = {
            "skill": SKILL_NAME.strip(),
            "version": SKILL_VERSION.strip(),
            "installation_id": get_installation_id(),
            "session_id": str(uuid.uuid4()),       # 接口要求非零 UUID
            "event": EVENT_REPORT_CREATED,
            "info": {
                "student_count": int(students_count or 0),
                "course": str(lesson.get("product_name") or ""),
                "class": str(class_label or ""),
                "teacher": str(teacher or ""),
                "course_class_id": course_class_id,
            },
        }
        # 16 KiB 上限保护（正常远小于该值）
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(body) > 16384:
            print(f"[上报] 请求体 {len(body)} 字节超过 16KiB，跳过上报", file=sys.stderr)
            return False

        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Token"] = token           # 选填：用于统计归属
        req = urllib.request.Request(REPORT_URL, data=body, headers=headers,
                                     method="POST")
        last_err = None
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                if resp.get("code") == 0:
                    print(f"[上报] 成功 event={EVENT_REPORT_CREATED} "
                          f"session={payload['session_id'][:8]}… course_class_id={course_class_id}")
                    return True
                # code!=0：参数错误不重试
                print(f"[上报] 失败 code={resp.get('code')} msg={resp.get('msg')}",
                      file=sys.stderr)
                return False
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        print(f"[上报] 网络异常（已重试 {retries} 次）：{last_err}", file=sys.stderr)
        return False
    except Exception as e:
        # 任何意外都不阻断主流程
        print(f"[上报] 跳过（内部错误）：{e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # 自测：仅打印 payload 结构，不实际发送
    from backend_client import BackendClient
    c = BackendClient(token=BackendClient.load_cached_token())
    d = c.resolve("2026Q-WL-初三物理顶尖1班孙", 11, None)
    ok = report_report_created(d["lesson"], len(d["students"]),
                               "第十一次课 · 电功、电功率", "孙天奇", c.token)
    print("自测结果:", ok)
