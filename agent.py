"""A small office automation agent with Yingdao handoff and Excel tools.

Usage: python agent.py "处理 data 文件夹里的销售报表"
"""
import csv
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parent
DEFAULT_INPUT = ROOT / "data"
YINGDAO_INBOX = ROOT / "inbox"
MAIL_INBOX = ROOT / "mail_inbox"
ARCHIVE_ROOT = ROOT / "archive"
DEFAULT_OUTPUT = ROOT / "output"

ALLOWED_TOOLS = {
    "list_files": "扫描输入目录中的 CSV、TSV、XLSX 文件",
    "process_report": "合并、去重、汇总并生成 Excel 报告",
    "list_reports": "查看最近生成的报告",
    "extract_pdf": "提取 PDF 文本并保存结构化结果",
    "collect_web": "读取公开网页的标题和链接",
    "list_mail_attachments": "扫描邮件附件交接目录",
    "archive_files": "将指定输入文件归档到日期目录",
    "notify": "生成待发送通知草稿，不直接发送",
}

FIELD_ALIASES = {
    "order_id": {"订单号", "订单编号", "order_id", "orderid", "id"},
    "date": {"日期", "下单日期", "date", "order_date"},
    "store": {"店铺", "店铺名称", "store", "shop"},
    "sales": {"销售额", "销售金额", "成交金额", "gmv", "sales", "amount"},
}


def find_files(folder):
    return sorted(p for p in Path(folder).glob("*") if p.is_file() and not p.is_symlink()
                  and p.suffix.lower() in {".csv", ".tsv", ".xlsx"})


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.links = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()


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


def extract_pdf(path):
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("PDF 提取需要 pdfplumber，请运行: python -m pip install pdfplumber") from exc
    pages = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            pages.append({"page": number, "text": (page.extract_text() or "").strip()})
    result_path = DEFAULT_OUTPUT / f"{path.stem}_extracted.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({"source": str(path), "pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"source": str(path), "pages": len(pages), "output": str(result_path), "preview": " ".join(p["text"] for p in pages)[:240]}


def collect_web(url):
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("网页采集只接受 http/https 地址")
    request = urllib.request.Request(url, headers={"User-Agent": "OfficeAgent/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(2_000_000).decode("utf-8", errors="replace")
    parser = LinkParser()
    parser.feed(body)
    result = {"url": url, "title": parser.title, "links": parser.links[:50], "link_count": len(parser.links)}
    output = DEFAULT_OUTPUT / "web_capture.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output"] = str(output)
    return result


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


def plan_task(text):
    """Create a local fallback plan using only registered tools."""
    lowered = text.lower()
    if any(word in text for word in ["归档", "整理文件"]):
        steps = []
        if any(word in lowered for word in ["报表", "汇总", "合并", "excel", "csv"]):
            steps = [{"tool": "list_files", "reason": "确认输入文件"},
                     {"tool": "process_report", "reason": "先生成报表"}]
        steps.append({"tool": "archive_files", "reason": "确认清单后归档输入文件"})
        return {"steps": steps}
    if any(word in lowered for word in ["pdf", "提取文字", "解析文档"]):
        return {"steps": [{"tool": "extract_pdf", "reason": "提取 PDF 文本"}]}
    if any(word in text for word in ["网页", "网址", "采集网页", "抓取"]) and "报表" not in text:
        return {"steps": [{"tool": "collect_web", "reason": "读取公开网页信息"}]}
    if any(word in text for word in ["邮件附件", "邮箱附件", "mail"]):
        return {"steps": [{"tool": "list_mail_attachments", "reason": "扫描邮件附件交接目录"}]}
    if any(word in text for word in ["归档", "整理文件"]):
        return {"steps": [{"tool": "archive_files", "reason": "将输入文件归档"}]}
    if any(word in text for word in ["通知草稿", "消息草稿"]):
        return {"steps": [{"tool": "notify", "reason": "生成通知草稿"}]}
    if any(word in text for word in ["最近报告", "历史报告", "生成过的报告"]) or "report" in lowered:
        return {"steps": [{"tool": "list_reports", "reason": "查看最近生成的报告"}]}
    if any(word in text for word in ["扫描", "查找文件", "有哪些文件", "文件列表"]):
        return {"steps": [{"tool": "list_files", "reason": "扫描输入目录"}]}
    return {"steps": [
        {"tool": "list_files", "reason": "确认输入文件"},
        {"tool": "process_report", "reason": "生成整理后的报表"},
    ]}


def execute_tool(tool, task, folder):
    """Run one registered tool and return structured output."""
    folder = Path(folder)
    if tool == "list_files":
        files = find_files(folder)
        return {"tool": tool, "files": [str(path) for path in files], "count": len(files)}
    if tool == "list_reports":
        files = sorted(DEFAULT_OUTPUT.glob("report_*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)
        return {"tool": tool, "reports": [str(path) for path in files[:10]], "count": min(len(files), 10)}
    if tool == "extract_pdf":
        pdfs = sorted(folder.glob("*.pdf"))
        if not pdfs:
            return {"tool": tool, "error": f"目录中没有 PDF：{folder}"}
        return {"tool": tool, "files": [extract_pdf(path) for path in pdfs]}
    if tool == "collect_web":
        url_match = re.search(r"https?://[^\s，。]+", task, re.I)
        if not url_match:
            return {"tool": tool, "error": "请在任务中提供公开网页 URL"}
        return {"tool": tool, **collect_web(url_match.group(0))}
    if tool == "list_mail_attachments":
        files = sorted(path for path in MAIL_INBOX.glob("*") if path.is_file() and path.name != ".gitkeep")
        return {"tool": tool, "handoff_dir": str(MAIL_INBOX), "files": [str(path) for path in files], "count": len(files)}
    if tool == "archive_files":
        raise PermissionError("归档必须通过任务确认接口执行")
    if tool == "notify":
        DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
        draft = {"title": "Office Agent 任务通知", "body": f"任务：{task}\n请查看最新执行结果。", "sent": False, "reason": "通知工具当前为草稿模式，需配置渠道并人工确认"}
        output = DEFAULT_OUTPUT / "notification_draft.json"
        output.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"tool": tool, **draft, "output": str(output)}
    if tool == "process_report":
        return {"tool": tool, **process_report(folder)}
    raise ValueError(f"不允许调用工具：{tool}")


def process_report(folder):
    files = find_files(folder)
    if not files:
        return {"error": f"未找到 CSV/TSV/XLSX 文件：{folder}"}
    rows, fields = merge_reports(files)
    if not rows:
        return {"error": "输入报表没有数据行"}
    problems = validate(rows, fields)
    summary_rows, anomalies = analyse_sales(rows)
    problems.extend(f"{len(anomalies)} 行销售额异常" for _ in [0] if anomalies)
    output = DEFAULT_OUTPUT / f"report_{datetime.now():%Y%m%d_%H%M%S_%f}.xlsx"
    write_report(rows, fields, output, summary_rows, anomalies)
    summary = {"files": [str(x) for x in files], "rows": len(rows), "output": str(output), "problems": problems, "anomalies": anomalies, "summary": summary_rows}
    (DEFAULT_OUTPUT / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["message"] = f"处理完成：读取 {len(files)} 个文件，输出 {len(rows)} 行。\n报告：{output}\n" + ("异常：" + "；".join(problems) if problems else "校验通过")
    return summary


def run(task, folder_override=None):
    intent = understand(task)
    if intent["action"] == "help":
        return "我目前支持：处理/合并销售报表、去重、检查空字段、生成汇总文件。"
    folder = Path(folder_override) if folder_override else intent["folder"]
    if folder_override is None and folder == DEFAULT_INPUT and find_files(YINGDAO_INBOX):
        folder = YINGDAO_INBOX
    result = process_report(folder)
    return result.get("error") or result["message"]


if __name__ == "__main__":
    print(run(" ".join(sys.argv[1:]) or "帮助"))
