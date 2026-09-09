# 办公报表 Agent 原型

这是一个可运行的本地 Agent：通过一句话判断任务，查找 `data` 或影刀交接目录中的 CSV/TSV/XLSX，合并报表、按订单号去重、检查空字段，并把结果写入 `output`。

## 运行

```powershell
cd D:\ai\office-agent
python agent.py "处理销售报表"
```

结果文件位于 `output`，默认生成 Excel 报告；每次运行也会生成 `last_run.json` 执行记录。

首次使用 Excel 文件时安装依赖：

```powershell
python -m pip install openpyxl
```

## 接入影刀 RAP

影刀负责从网页或桌面系统采集数据，并将导出的 CSV 或 XLSX 放入 `inbox`；然后用 Python 节点执行：

```powershell
python D:\ai\office-agent\agent.py "处理销售报表"
```

影刀流程建议为：打开网页并登录 → 搜索或筛选数据 → 循环读取表格/详情 → 写入 `D:\ai\office-agent\inbox\web_export.xlsx` → 调用上面的 Python 节点。Agent 会自动读取 `inbox` 中的文件并生成标准报告。

如果要处理指定目录，可使用：

```powershell
python agent.py "处理报表 文件夹:D:\\my-reports"
```

注意：网页采集由影刀执行，Agent 不绕过验证码或登录保护；影刀只需要把合法取得的数据导出到 `inbox`。

## 启动 API 和网页

```powershell
cd D:\ai\office-agent
python server.py
```

打开 `http://127.0.0.1:8787`。网页会调用 `POST /api/task`：

```json
{"task":"处理网页采集报表","folder":"D:\\ai\\office-agent\\inbox"}
```

如需让 AI 参与任务规划，配置 OpenAI 兼容接口：

```powershell
$env:AI_API_KEY="你的密钥"
$env:AI_MODEL="gpt-4o-mini"
python server.py
```

也可以设置 `AI_BASE_URL` 接入其他 OpenAI 兼容模型服务。模型只负责选择受限工具，实际文件处理仍由本地 Agent 执行。

## 第一层：模板化报表处理

当前报表输出的 Excel 会包含三个工作表：

- `明细`：合并并按订单号去重后的原始记录
- `汇总`：订单数、销售总额、平均客单价和店铺销售额
- `异常`：销售额为空、格式错误等需要人工检查的行

Agent 会自动识别常见字段别名，例如 `订单号`、`Order ID`、`GMV`、`销售额`，并映射为统一字段。后续可以继续增加库存、客户、采购等报表模板。

## 第二层：多工具规划

Agent 现在会先生成执行计划，再按白名单调用工具。当前工具包括：

- `list_files`：扫描输入目录
- `process_report`：处理并生成报告
- `list_reports`：查看最近报告

网页结果区域会显示本次计划。配置 AI 后，模型可以参与步骤选择；模型不可用时使用本地规划器。所有模型返回的工具名都会经过白名单校验，未知工具不会执行。
