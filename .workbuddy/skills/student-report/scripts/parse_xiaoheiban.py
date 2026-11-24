#!/usr/bin/env python3
"""解析小黑板 zip 包（demo 数据源）。

zip 结构（示例：小黑板_2026-10-17 13-35-46.zip）：
  小黑板_2026-10-17 13-35-46/
    1_孙天奇.png            # 编号_组标记?学员名.png（课件底图 + 该学员红笔作答）
    2_cxz安禹桐.png          # cxz / D- 为组标记
    17_D-蔡景怡.png
    出勤学生名单.txt          # 每行一条 "编号_组标记学员名"

输出 JSON：
  {
    "zip_name": ..., "lecture_date": "2026-10-17",
    "images": [{"file", "num", "group", "student"}],
    "roster": ["2_cxz安禹桐", ...],
    "class_stats": {"image_count": 26, "roster_count": 25}
  }
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

NAME_RE = re.compile(r"^(?P<num>\d+)_(?P<group>cxz|D-)?(?P<name>.+?)\.png$")


def fix_zip_name(name: str, flag_bits: int) -> str:
    """macOS/Windows 中文 zip 常未设 UTF-8 标志位，zipfile 会按 CP437 解码导致乱码，这里还原为 UTF-8。"""
    if flag_bits & 0x800:
        return name
    try:
        fixed = name.encode("cp437").decode("utf-8")
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def parse_zip(zip_path: str, extract_to: str | None = None) -> dict:
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(f"小黑板 zip 不存在: {zip_path}")

    m = re.search(r"(\d{4}-\d{2}-\d{2})", zp.name)
    lecture_date = m.group(1) if m else ""

    images, roster = [], []
    with zipfile.ZipFile(zp) as zf:
        for info in zf.infolist():
            if info.is_dir() or "__MACOSX" in info.filename:
                continue
            base = Path(fix_zip_name(info.filename, info.flag_bits)).name
            if base.endswith(".txt"):
                if base == "出勤学生名单.txt":
                    text = zf.read(info).decode("utf-8", errors="replace")
                    roster = [ln.strip() for ln in text.splitlines() if ln.strip()]
                continue
            if not base.lower().endswith(".png"):
                continue
            mm = NAME_RE.match(base)
            if not mm:
                continue
            if extract_to:
                target = Path(extract_to) / base
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
            images.append({
                "file": base,
                "num": int(mm.group("num")),
                "group": mm.group("group") or "",
                "student": mm.group("name").strip(),
            })

    images.sort(key=lambda x: x["num"])
    return {
        "zip_name": zp.name,
        "zip_path": str(zp.resolve()),
        "lecture_date": lecture_date,
        "images": images,
        "roster": roster,
        "class_stats": {"image_count": len(images), "roster_count": len(roster)},
    }


def find_student(data: dict, name: str) -> list[dict]:
    """按姓名匹配（支持去掉组标记后的子串匹配，如 田甜 ↔ 尤田甜 不匹配，但 江涵钰 直接命中）。"""
    exact = [img for img in data["images"] if img["student"] == name]
    if exact:
        return exact
    return [img for img in data["images"] if name in img["student"] or img["student"] in name]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="解析小黑板 zip")
    ap.add_argument("zip_path")
    ap.add_argument("--extract-to", default=None, help="同时解压图片到该目录")
    ap.add_argument("--student", default=None, help="只输出匹配该学员的图片")
    args = ap.parse_args()

    data = parse_zip(args.zip_path, args.extract_to)
    if args.student:
        data["matched_images"] = find_student(data, args.student)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()
