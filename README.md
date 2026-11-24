# 学员阶段性学习报告 Skill

基于 WorkBuddy 的学员报告生成 Skill：按「课程名称 + 课次 + 主讲/联席姓名」批量产出全班单次课堂报告（自包含 HTML，按学生姓名切换），并支持单学员 PDF / Markdown 报告。

> 当前 Skill 版本：v2.0　|　需求文档版本：v2.0

## 目录结构

```
.
├── 学员阶段性学习报告_Skill需求文档.md  # 完整需求、版本历史、验收标准
└── .workbuddy/skills/student-report/
    ├── SKILL.md                     # Skill 使用说明与命令行参数速查
    ├── scripts/
    │   ├── backend_client.py        # 后台接口鉴权、取数、resolve 编排
    │   ├── report_content.py        # 数据 → 报告内容（小黑板三分类、赏识话术、总结）
    │   ├── generate_html_batch.py   # 批量 HTML 渲染 + 自动质检 + 事件上报
    │   ├── quality_check.py         # 26 项报告质检（数据正确性 + 前后逻辑一致）
    │   ├── usage_reporter.py        # report-created 使用事件上报
    │   ├── generate_report.py       # 单学员 PDF / Markdown（本地 zip + 飞书快照数据源）
    │   ├── parse_xiaoheiban.py      # 小黑板 zip 解析
    │   └── discover_class.py        # 课节探测辅助
    └── references/
        ├── report-templates.md      # 报告口吻与润色规范
        └── feishu-sheet-structure.md# 历史飞书表结构参考（内部链接，按需要删除）
```

## 快速开始

### 1. 环境
- Python 3.10+，需要 `Pillow`（小黑板原题裁切）与 `reportlab`（PDF 输出）
- 将整个 `.workbuddy/skills/student-report/` 放到项目的 `.workbuddy/skills/` 下

### 2. 鉴权
在浏览器登录后台后，F12 → Application → Local Storage 复制 `x-token`，通过 `--token` 传入，或缓存到 `scripts/../data/token_cache.json`（格式：`{"token": "..."}`）。

> 注意：`data/` 目录含令牌与安装标识，属本地私密文件，**不要提交到版本库**。

### 3. 生成全班报告

```bash
python generate_html_batch.py \
  --product-name "2026Q-WL-初三物理顶尖1班孙" \
  --class-seq 11 \
  --teacher "孙天奇" \
  --seq-label "第十一次课 · 电功、电功率" \
  --out ./reports
```

- `--class-seq`：课次。**注意有的课程后台用纯数字**（如 `11`），传「第十一次课」查不到课节，可用 `discover_class.py` 或先用接口确认。
- `--teacher`：主讲署名覆盖（仅展示，不改考勤等真实数据）。
- `--seq-label`：课次展示名覆盖（兼顾用户口径与真实课节名）。
- `--co-teacher`：联席/教务名称，用于从全班筛出特定老师的学生（不传则取全班）。
- `--no-qc` 跳过质检；`--no-report-usage` 跳过使用事件上报。

生成后自动输出：
- `批量课堂报告_{课程}_{课次}.html` — 自包含 HTML（图片 base64 内嵌，离线可看，支持按姓名切换、点击放大）
- `报告质检_{课程}_{课次}.md` — 26 项质检结果（FAIL/WARN 分级）

## 安全与隐私

- 本仓库**不包含**任何真实学生报告、姓名、小黑板照片或登录令牌；
- `reports/` 为运行时输出目录，已在 `.gitignore` 中排除；
- 报告含学员隐私，请仅在内网或受控环境分发。

## 使用事件上报

每次生成报告后默认上报一条 `report-created` 事件（POST `/api/ai/reportSkillUsage`）：
- `installation_id` 持久化 UUID v4（首次生成后复用）
- `session_id` 为 UUID v4；「课程ID-课节ID」放在 `info.course_class_id`
- `info` 另含 `student_count` / `course` / `class` / `teacher`
- 上报失败仅告警，不阻断报告生成；可用 `--no-report-usage` 关闭

## 许可

内部项目，未声明开源许可。
