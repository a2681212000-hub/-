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

网页中的“选择”按钮会调用 `POST /api/open-path` 打开数据来源文件夹；报告位置右侧的箭头会打开报告所在文件夹。该接口只接受本机同源请求，并通过 Windows 原生文件关联启动资源管理器。

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

## 第三层：办公工具接入

当前已加入以下工具接口：

- `extract_pdf`：读取输入目录中的 PDF，提取每页文字到 JSON
- `collect_web`：读取任务中的公开 `http/https` URL，保存标题和链接
- `list_mail_attachments`：扫描 `mail_inbox` 交接目录，供邮件/RPA 流程落盘附件
- `archive_files`：按日期将输入文件移动到 `archive` 目录
- `notify`：生成通知草稿，不直接发送消息

邮件下载和飞书/企业微信发送采用交接目录、草稿和人工确认方式，接入账号凭证后再扩展为正式连接器。网页采集只处理公开 URL；需要登录的后台仍由影刀执行并将结果保存到 `inbox`。

## 第四层：人工确认与执行记录

需要移动文件的 `archive_files` 工具会先生成待确认清单，列出源文件、目标目录、文件数量和过期时间。只有调用 `POST /api/task/{task_id}/decision` 并提交对应的确认编号和 `approve` 后才会执行；`cancel`、过期、服务重启中断都会写入任务记录。

归档执行使用确认时的固定文件清单，并在移动前复核文件大小、修改时间和 SHA-256。文件发生变化时会停止，不会重新扫描并扩大操作范围。`notify` 仍然只生成草稿，发送动作需要后续渠道授权和确认。

## 第五层：自动任务

网页底部的“自动任务”可以保存一个任务描述、输入文件夹和运行间隔。调度器会在本地后台按计划提交任务，支持停用、立即运行和删除；计划记录保存在 `runtime/schedules`。自动触发归档时仍然会进入第四层的人工确认，不会自动移动文件。

## 第六层：工作流模板与通知草稿

网页底部的“工作流模板”会保存经过工具白名单校验的执行步骤，运行模板时复用任务记录和人工确认。`notify` 产生的通知会进入“通知草稿”中心，支持标记已读；当前不会直接发送到飞书、企业微信或邮箱。
