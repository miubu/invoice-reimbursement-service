# 电子发票报销填报服务

这是一个 FastAPI 服务。用户通过网络上传包含电子发票 PDF 的 ZIP，服务按发票明细表的实际列位置提取“项目名称”“规格型号”和“价税合计（小写）”，并返回识别预览或 Excel 文件。

项目名称保留票面中的税收分类前缀和商品文字。规格型号为空时保持为空。服务不会根据商品常识补充、概括或改写字段，也不需要视觉模型或 API 密钥。

## 启动 Docker 服务

```powershell
docker compose up -d --build
```

服务地址：`http://服务器地址:8000`  
交互式接口文档：`http://服务器地址:8000/docs`

如果部署机器无法访问 Docker Hub，可以直接使用 GitHub Actions 已构建的公开镜像，避免在部署机本地构建：

```powershell
docker pull ghcr.io/miubu/invoice-reimbursement-service:latest
```

检查运行状态：

```powershell
curl.exe http://localhost:8000/healthz
```

## 预览识别结果

预览接口只返回 JSON，不生成 Excel：

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/preview" `
  -F "file=@C:\完整路径\发票.zip" `
  -F "expected_total=423.60"
```

如果 ZIP 文件名已经含有“总金额423.60元”，可以省略 `expected_total`。响应会列出每个 PDF 的项目名称、规格型号、票面金额、状态和待核对原因。

## 下载 Excel

```powershell
curl.exe -X POST "http://localhost:8000/v1/invoices/export" `
  -F "file=@C:\完整路径\发票.zip" `
  -F "expected_total=423.60" `
  -F "claimant=李府鸿" `
  -F "student_id=12604040" `
  -F "reimbursement_type=材 料 费" `
  -F "fill_date=2026/9/6" `
  -F "phone=13516389370" `
  -F "transmit_invoice=YES" `
  --output "发票报销填报表.xlsx"
```

当任一 PDF 需要人工核对、PDF 数量与文件名不一致、缺少金额，或票面金额合计不等于预期金额时，接口返回 HTTP 422，不会返回伪造或不完整的 Excel。

## 多人并发

默认配置为：

- 每个 Uvicorn 进程同时处理 10 个任务；
- Docker 默认启动 2 个进程；
- 每个进程最多排队 50 个请求；
- 排队超过 30 秒返回 HTTP 503；
- 队列已满返回 HTTP 429。

因此默认配置可以同时服务超过 5 位用户。实际吞吐量取决于 CPU、PDF 页数和文件大小。可在 `docker-compose.yml` 中修改：

- `WEB_CONCURRENCY`：进程数量；
- `MAX_CONCURRENT_JOBS`：每个进程的并发任务数，代码保证不低于 5；
- `MAX_QUEUED_JOBS`：每个进程的等待队列长度；
- `QUEUE_TIMEOUT_SECONDS`：最长排队时间。

## 上传安全限制

默认限制：

- ZIP 上传最大 50 MB；
- 解压后全部文件最大 200 MB；
- 最多 100 张 PDF；
- 单文件压缩比最大 200；
- 拒绝 ZIP 路径穿越、同名 PDF 和伪造 ZIP 内容；
- 每个请求使用独立临时目录，响应结束后自动清理；
- 容器以非 root 用户运行、根文件系统只读，仅 `/tmp` 可写。

公网部署时，请在服务前增加 HTTPS 反向代理、身份认证、请求日志脱敏和按用户/IP 的限流。不要直接把 8000 端口裸露到互联网。

服务内置可选的 Bearer Token 校验。启动前设置独立的服务令牌：

```powershell
$env:SERVICE_API_KEY = "请使用单独生成的随机长字符串"
docker compose up -d --build
```

设置后，请求需要增加：

```text
Authorization: Bearer <服务令牌>
```

该令牌仅用于访问本服务，不要使用 OpenAI API 密钥代替。

## 本地命令行模式

原命令行模式仍可使用：

```powershell
py main.py --preview
py main.py --export --confirm-reviewed
```

安装本地依赖：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
