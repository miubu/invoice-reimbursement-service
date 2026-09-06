# 电子发票报销填报服务

这是一个可在 Docker 中部署的 FastAPI 服务。用户可以在网页中同时拖入多个电子发票 PDF/ZIP，预览发票上的项目名称、规格型号和价税合计，校验通过后生成 Excel。多页 PDF 会按页拆分为多张发票。

项目名称和规格型号严格保留票面文字；不根据商品常识补写、概括或改写。无法可靠识别时会标记待人工核对并停止导出。

## 功能

- 支持多个 PDF、多个 ZIP 或混合上传；
- 合并 PDF 自动按页处理；
- 报销人姓名、学号和电话仅保存在各自浏览器中；
- 报销类型默认为“材料费”，可根据管理规则自动识别；
- Excel 的报销类型和是否传递发票均有下拉选项；
- 表格使用黑色实线边框，只有非空备注标红；
- 备注中只显示简短说明，说明模板使用可点击超链接；
- 管理后台可编辑关键词、备注、模板链接、首页文案、Logo 和首页展示图；
- SQLite 和上传的站点图片保存在 Docker 命名卷中，重启和更新镜像不会丢失。

## Windows Docker Desktop 部署

克隆项目后，在项目目录新建 `.env`：

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请在部署机上填写管理员密码
```

`.env` 已被 Git 忽略，不会上传到公开仓库。

使用 GitHub Container Registry 中的公开镜像：

```powershell
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d --force-recreate
```

或在本机构建：

```powershell
docker compose up -d --build
```

地址：

- 用户页：`http://localhost:8000/`
- 管理后台：`http://localhost:8000/admin`
- 接口文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/healthz`

普通用户上传和导出不需要密钥。管理员接口使用独立账号密码保护。

## 内置规则

- `* 物流辅助服务 * 物流服务费`：报销类型设为“快递邮寄”，提示补充快递说明；
- `* 交通 *`：报销类型设为“市内交通”，提示补充交通费说明；
- 项目名称含“线”或“缆”：提示补充连接线材说明，不改变报销类型；
- 项目名称含“计算机配件”：标红备注“无法报销”，不改变报销类型。

管理员可在 `/admin` 修改这些规则的关键词、备注、链接和启用状态。

## API 示例

预览：

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/preview" `
  -F "files=@C:\\完整路径\\发票1.pdf" `
  -F "files=@C:\\完整路径\\发票2.pdf" `
  -F "expected_total=423.60" `
  -F "reimbursement_type=材料费"
```

导出：

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/export" `
  -F "files=@C:\\完整路径\\发票1.pdf" `
  -F "expected_total=423.60" `
  -F "claimant=实际报销人姓名" `
  -F "student_id=实际学号" `
  -F "reimbursement_type=材料费" `
  -F "fill_date=2026/9/6" `
  -F "phone=实际联系电话" `
  -F "transmit_invoice=YES" `
  --output "发票报销填报表.xlsx"
```

票面金额合计不等于预期总额，或任一发票需要人工核对时，接口返回 HTTP 422 且不生成 Excel。

## 并发与安全

默认启动 2 个 Uvicorn 进程，每个进程同时处理 10 个任务，可同时服务超过 5 位用户。实际吞吐量取决于 CPU、PDF 页数和文件大小。

容器以非 root 用户运行，根文件系统只读；单次上传、解压总量、PDF 数量和压缩比均有限制。如要通过公网使用，请在服务前增加 HTTPS 反向代理、用户身份验证、日志脱敏和 IP 限流，不要直接暴露 8000 端口。

## 本地命令行模式

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
py main.py --preview
py main.py --export --confirm-reviewed
```
