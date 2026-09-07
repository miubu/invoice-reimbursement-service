# 电子发票报销填报服务

这是一个可在 Docker 中部署的 FastAPI 服务。用户可以在网页中同时拖入多个电子发票 PDF/ZIP，预览发票上的项目名称、规格型号和价税合计，确认票面字段后生成 Excel。多页 PDF 会按页拆分为多张发票。

项目名称和规格型号严格保留票面文字；不根据商品常识补写、概括或改写。无法可靠识别时会标记待人工核对并停止导出。

## 功能

- 支持多个 PDF、多个 ZIP 或混合上传；
- 合并 PDF 自动按页处理；
- 发票含多个非折扣项目时，可逐张对照票面后勾选“已人工核对”再导出；
- 报销人姓名、学号和电话仅保存在各自浏览器中；
- 报销类型默认为“材料费”，可根据管理规则自动识别；
- Excel 的报销类型和是否传递发票均有下拉选项；
- 表格使用黑色实线边框，只有非空备注标红；
- 备注中只显示简短说明，说明模板使用可点击超链接；
- 快递、交通和连接线材说明模板内置在 Docker 镜像中，可通过服务自身地址直接下载；
- 管理员可为任意规则上传 DOCX/PDF 说明附件，上传文件持久化保存在 Docker 命名卷中；
- 可一键把全部发票页与命中规则对应的说明文件合并为 PDF，并直接打开浏览器打印窗口；
- 管理后台可新建、编辑、删除自定义关键词规则，并可编辑内置规则、首页文案、Logo 和首页展示图；
- 规则只按“发票项目名称是否包含任一关键词”匹配；不使用通配符，不要求完整名称；
- 不再按压缩包文件名或人工输入的总金额核销；票面金额只展示并写入 Excel；
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

## 内置规则与自定义规则

- 项目名称包含`物流服务费`：报销类型设为“快递邮寄”，提示补充快递说明；
- 项目名称包含`交通`：报销类型设为“市内交通”，提示补充交通费说明；
- 项目名称含“线”或“缆”：提示补充连接线材说明，不改变报销类型；
- 项目名称含“计算机配件”：标红备注“无法报销”，不改变报销类型。

管理员可在 `/admin` 点击“新建规则”，填写关键词、报销类型、备注和模板链接。每行一个关键词，只要项目名称包含其中任一关键词即命中。自定义规则可以删除；内置规则可关闭但不能删除。

说明文件既可以填写外部链接，也可以直接上传 DOCX/PDF。上传后系统会自动写入 `/rule-files/...` 站内链接；预览页和生成的 Excel 会使用当前服务的完整地址。默认内置文件为：

- `/rule-files/courier.docx`
- `/rule-files/transport.docx`
- `/rule-files/cable.docx`

“下载 Excel”按钮位于识别结果表格底部，便于完成逐行核对后直接下载。

“一键打印全部发票及说明”按钮位于“下载 Excel”左边。系统先打印全部发票页，再把本批发票命中规则对应的说明文件各附一份。三个内置 DOCX 已带有预转换的 PDF 打印版本；管理员自定义附件如需参与一键打印，请上传 PDF。仅填写外部网址或上传 DOCX 时仍可点击查看，但系统不会联网抓取或在 Docker 中临时转换，打印时会明确提示改传 PDF。

## API 示例

预览：

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/preview" `
  -F "files=@C:\\完整路径\\发票1.pdf" `
  -F "files=@C:\\完整路径\\发票2.pdf" `
  -F "reimbursement_type=材料费"
```

导出：

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/export" `
  -F "files=@C:\\完整路径\\发票1.pdf" `
  -F "claimant=实际报销人姓名" `
  -F "student_id=实际学号" `
  -F "reimbursement_type=材料费" `
  -F "fill_date=2026/9/6" `
  -F "phone=实际联系电话" `
  -F "transmit_invoice=YES" `
  --output "发票报销填报表.xlsx"
```

合并并打印（接口返回 PDF，网页按钮会自动打开打印窗口）：

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/print" `
  -F "files=@C:\完整路径\发票1.pdf" `
  -F "files=@C:\完整路径\发票2.pdf" `
  -F "reimbursement_type=材料费" `
  --output "发票及报销说明.pdf"
```

系统不再执行票面金额合计核销。若项目名称、规格型号等票面字段无法可靠识别，仍会要求人工核对，避免将猜测内容写入 Excel。

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
