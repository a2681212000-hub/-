"""A small office automation agent with Yingdao handoff and Excel tools.

Usage: python agent.py "处理 data 文件夹里的销售报表"
"""
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parent
DEFAULT_INPUT = ROOT / "data"
YINGDAO_INBOX = ROOT / "inbox"
DEFAULT_OUTPUT = ROOT / "output"


def find_files(folder):
    return sorted([p for p in Path(folder).glob("*") if p.suffix.lower() in {".csv", ".tsv", ".xlsx"}])


def read_table(path):
    if path.suffix.lower() == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("读取 Excel 需要 openpyxl，请运行: python -m pip install openpyxl") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.values)
        if not values:
            return []
        headers = [str(x or "").strip() for x in values[0]]
        return [dict(zip(headers, row)) for row in values[1:] if any(x is not None for x in row)]
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def merge_reports(files):
    rows, seen, fields = [], set(), []
    for file in files:
        table = read_table(file)
        if table and not fields:
            fields = list(table[0].keys())
        for row in table:
            key = row.get("订单号") or row.get("order_id") or row.get("id")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            rows.append(row)
    return rows, fields


def validate(rows, fields):
    problems = []
    if not rows:
        problems.append("没有读取到数据")
    required = [x for x in ("订单号", "order_id", "id") if x in fields]
    if not required:
        problems.append("未发现订单号字段，已跳过去重")
    for i, row in enumerate(rows, 2):
        if any(v is None for v in row.values()):
            problems.append(f"第 {i} 行存在空字段")
    return problems


def write_report(rows, fields, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise RuntimeError("写入 Excel 需要 openpyxl，请运行: python -m pip install openpyxl") from exc
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(fields)
        for row in rows:
            sheet.append([row.get(field, "") for field in fields])
        workbook.save(output)
        return
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def understand(text):
    lowered = text.lower()
    folder_match = re.search(r"(?:目录|文件夹|folder)[:： ]*([^，。\s]+)", text, re.I)
    return {
        "action": "report" if any(x in text for x in ["报表", "销售", "合并", "csv", "excel"]) else "help",
        "folder": Path(folder_match.group(1)) if folder_match else DEFAULT_INPUT,
        "needs_confirmation": any(x in text for x in ["发送", "删除", "提交"]),
        "raw": text,
    }


def run(task, folder_override=None):
    intent = understand(task)
    if intent["action"] == "help":
        return "我目前支持：处理/合并销售报表、去重、检查空字段、生成汇总文件。"
    folder = Path(folder_override) if folder_override else intent["folder"]
    # A Yingdao RAP flow exports its web results into inbox, then calls this agent.
    if folder == DEFAULT_INPUT and YINGDAO_INBOX.exists() and find_files(YINGDAO_INBOX):
        folder = YINGDAO_INBOX
    files = find_files(folder)
    if not files:
        return f"未找到 CSV/TSV/XLSX 文件：{folder}"
    rows, fields = merge_reports(files)
    problems = validate(rows, fields)
    output = DEFAULT_OUTPUT / f"report_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    write_report(rows, fields, output)
    summary = {"files": [str(x) for x in files], "rows": len(rows), "output": str(output), "problems": problems}
    (DEFAULT_OUTPUT / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"处理完成：读取 {len(files)} 个文件，输出 {len(rows)} 行。\n报告：{output}\n" + ("异常：" + "；".join(problems) if problems else "校验通过")


if __name__ == "__main__":
    print(run(" ".join(sys.argv[1:]) or "帮助"))
