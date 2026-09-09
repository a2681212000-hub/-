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
