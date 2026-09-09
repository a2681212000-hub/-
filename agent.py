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

FIELD_ALIASES = {
    "order_id": {"订单号", "订单编号", "order_id", "orderid", "id"},
    "date": {"日期", "下单日期", "date", "order_date"},
    "store": {"店铺", "店铺名称", "store", "shop"},
    "sales": {"销售额", "销售金额", "成交金额", "gmv", "sales", "amount"},
}


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


def normalize_rows(rows):
    """Map common Chinese/English headers to stable fields for templates."""
    normalized = []
    for row in rows:
        item = dict(row)
        for canonical, aliases in FIELD_ALIASES.items():
            match = next((key for key in row if str(key).strip().lower() in {x.lower() for x in aliases}), None)
            if match and canonical not in item:
                item[canonical] = row.get(match)
        normalized.append(item)
    return normalized


def merge_reports(files):
    rows, seen, fields = [], set(), []
    for file in files:
        table = normalize_rows(read_table(file))
        if table and not fields:
            fields = list(table[0].keys())
        for row in table:
            for field in row:
                if field not in fields:
                    fields.append(field)
        for row in table:
            key = row.get("order_id") or row.get("订单号") or row.get("id")
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
    required = [x for x in ("order_id", "订单号", "id") if x in fields]
    if not required:
        problems.append("未发现订单号字段，已跳过去重")
    for i, row in enumerate(rows, 2):
        if any(v is None for v in row.values()):
            problems.append(f"第 {i} 行存在空字段")
    return problems


def analyse_sales(rows):
    """Create a compact sales summary and a separate anomaly list."""
    anomalies = []
    total = 0.0
    stores = {}
    for index, row in enumerate(rows, 2):
        raw = row.get("sales")
        try:
            amount = float(str(raw).replace(",", "")) if raw not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        if amount is None:
            anomalies.append({"行号": index, "问题": "销售额为空或不是数字", "订单号": row.get("order_id", "")})
            continue
        total += amount
        store = row.get("store") or "未填写店铺"
        stores[store] = stores.get(store, 0) + amount
    summary = [{"指标": "订单数", "数值": len(rows)}, {"指标": "销售总额", "数值": round(total, 2)}, {"指标": "平均客单价", "数值": round(total / len(rows), 2) if rows else 0}]
    summary.extend({"指标": f"店铺销售额：{store}", "数值": round(amount, 2)} for store, amount in sorted(stores.items()))
    return summary, anomalies


def write_report(rows, fields, output, summary=None, anomalies=None):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise RuntimeError("写入 Excel 需要 openpyxl，请运行: python -m pip install openpyxl") from exc
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "明细"
        sheet.append(fields)
        for row in rows:
            sheet.append([row.get(field, "") for field in fields])
        summary_sheet = workbook.create_sheet("汇总")
        summary_sheet.append(["指标", "数值"])
        for row in summary or []:
            summary_sheet.append([row.get("指标", ""), row.get("数值", "")])
        issue_sheet = workbook.create_sheet("异常")
        issue_sheet.append(["行号", "问题", "订单号"])
        for row in anomalies or []:
            issue_sheet.append([row.get("行号", ""), row.get("问题", ""), row.get("订单号", "")])
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
    summary_rows, anomalies = analyse_sales(rows)
    problems.extend(f"{len(anomalies)} 行销售额异常" for _ in [0] if anomalies)
    output = DEFAULT_OUTPUT / f"report_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    write_report(rows, fields, output, summary_rows, anomalies)
    summary = {"files": [str(x) for x in files], "rows": len(rows), "output": str(output), "problems": problems, "anomalies": anomalies, "summary": summary_rows}
    (DEFAULT_OUTPUT / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"处理完成：读取 {len(files)} 个文件，输出 {len(rows)} 行。\n报告：{output}\n" + ("异常：" + "；".join(problems) if problems else "校验通过")


if __name__ == "__main__":
    print(run(" ".join(sys.argv[1:]) or "帮助"))
