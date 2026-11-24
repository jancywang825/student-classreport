#!/usr/bin/env python3
"""登录后台并打印该课程的全部课节，便于确认真实的 classSeq 值。"""
import sys
sys.path.insert(0, "/Users/jancy/WorkBuddy/To家长  学员阶段性学习报告/.workbuddy/skills/student-report/scripts")
from backend_client import BackendClient

PRODUCT = "2026Q-SX-高一数学A版985层进化班日B谢胡"
COTEACHER = "周文峰"


def main() -> None:
    c = BackendClient()
    tok = c.login_via_browser()
    if not tok:
        print("LOGIN_FAILED")
        sys.exit(1)
    c.token = tok
    students = c.get_coteacher_students(COTEACHER)
    by_course = [s for s in students if s.get("productName") == PRODUCT]
    if by_course:
        students = by_course
    if not students:
        print("NO_STUDENTS")
        sys.exit(1)
    pid = students[0].get("productId")
    print(f"PRODUCT_ID={pid} STUDENT_COUNT={len(students)}")
    cls = c.get_class_list(pid, None)
    print(f"CLASS_TOTAL={len(cls)}")
    for cl in cls:
        seq = cl.get("classSeq")
        cid = cl.get("classId")
        name = cl.get("className")
        date = cl.get("classDate") or cl.get("startTime") or ""
        print(f"CLASS|seq={seq}|id={cid}|name={name}|date={date}")


if __name__ == "__main__":
    main()
