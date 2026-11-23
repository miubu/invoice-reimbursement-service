[CmdletBinding()]
param(
    [string]$ContainerName,
    [string]$BackupDirectory
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 在绑定脚本参数后才能可靠读取 $PSScriptRoot。
if ([string]::IsNullOrEmpty($BackupDirectory)) {
    $BackupDirectory = Join-Path $PSScriptRoot 'backups'
}
$service = 'invoice-service'
$image = 'ghcr.io/miubu/invoice-reimbursement-service:latest'

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker 操作失败（退出码 $LASTEXITCODE）。" }
}

function Read-Container([string]$Id) {
    (Invoke-Docker @('container', 'inspect', '--format', '{{json .}}', $Id)) | ConvertFrom-Json
}

function Read-Environment($Items) {
    $result = @{}
    foreach ($item in $Items) {
        $key, $value = $item -split '=', 2
        $result[$key] = $value
    }
    $result
}

function Resolve-DeploymentPath([string]$Path) {
    if ($Path -match '^/mnt/([a-zA-Z])/(.*)$') { $Path = $Matches[1] + ':/' + $Matches[2] }
    (Resolve-Path -LiteralPath $Path).ProviderPath
}

$previousVolume = $env:INVOICE_DATA_VOLUME
$previousImage = $env:INVOICE_SERVICE_IMAGE
$previousEncoding = [Console]::OutputEncoding
$stopped = $false
Push-Location -LiteralPath $PSScriptRoot
try {
    # Docker 返回 UTF-8 JSON，Windows PowerShell 也需正确读取中文部署目录。
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    if (-not $ContainerName) {
        $ids = @(Invoke-Docker @('compose', '-f', 'docker-compose.ghcr.yml', 'ps', '--all', '-q', $service))
        if ($ids.Count -ne 1) { throw '未找到唯一的已有部署。可用 docker ps -a 查看，再用 -ContainerName 指定原容器名。' }
        $ContainerName = $ids[0]
    }
    $old = Read-Container $ContainerName
    $labels = $old.Config.Labels
    $project = $labels.'com.docker.compose.project'
    if (-not $project -or $labels.'com.docker.compose.service' -cne $service -or -not $old.State.Running) {
        throw '请选择当前正在运行的 Compose 报销服务容器。'
    }
    $mounts = @($old.Mounts | Where-Object { $_.Type -ne 'tmpfs' })
    if ($mounts.Count -ne 1 -or $mounts[0].Type -ne 'volume' -or $mounts[0].Destination -cne '/data') {
        throw '此部署使用自定义挂载。请在原部署配置中保留这些挂载，单独备份后更新。'
    }
    $volume = $mounts[0].Name
    $users = @(Invoke-Docker @('ps', '--no-trunc', '-q', '--filter', "volume=$volume"))
    if ($users.Count -ne 1 -or $users[0] -ne $old.Id) { throw '此数据卷还被其他运行中的容器使用，请先安排一致的备份。' }

    # 从容器标签读取原项目及配置，避免因目录改名或遗漏 override 而接上空卷。
    $deployment = Resolve-DeploymentPath $labels.'com.docker.compose.project.working_dir'
    $compose = @('compose', '--project-directory', $deployment, '-p', $project)
    $dotenv = Join-Path $deployment '.env'
    $envFile = $labels.'com.docker.compose.project.environment_file'
    if ($envFile -and (Resolve-DeploymentPath $envFile) -ne $dotenv) { throw '此部署指定了自定义 env 文件，请沿用原启动命令和环境文件更新。' }
    if (Test-Path -LiteralPath $dotenv -PathType Leaf) { $compose += @('--env-file', $dotenv) }
    foreach ($file in ($labels.'com.docker.compose.project.config_files' -split ',')) {
        $compose += @('-f', (Resolve-DeploymentPath $file))
    }
    $env:INVOICE_SERVICE_IMAGE = $image
    $env:INVOICE_DATA_VOLUME = $volume
    $plan = (Invoke-Docker ($compose + @('config', '--format', 'json'))) | ConvertFrom-Json
    $target = $plan.services.$service
    $targets = @($target.volumes | Where-Object { $_.type -ne 'tmpfs' })
    if ($target.image -ne $image -or $targets.Count -ne 1 -or $targets[0].type -ne 'volume' -or
        $targets[0].target -cne '/data' -or $plan.volumes.($targets[0].source).name -cne $volume) {
        throw '原 Compose 配置解析出的镜像或挂载与升级要求不一致。请检查项目文件和原数据卷。'
    }
    $actualPorts = @(foreach ($port in $old.HostConfig.PortBindings.PSObject.Properties) {
        foreach ($binding in $port.Value) { "$($port.Name)|$($binding.HostIp)|$($binding.HostPort)" }
    })
    $newPorts = @(foreach ($port in $target.ports) { "$($port.target)/$($port.protocol)|$($port.host_ip)|$($port.published)" })
    if ((($actualPorts | Sort-Object) -join ',') -cne (($newPorts | Sort-Object) -join ',')) {
        throw '配置文件中的端口与正在运行的服务不同，请先恢复原端口配置。'
    }
    $actualEnv = Read-Environment $old.Config.Env
    $imageEnv = Read-Environment ((Invoke-Docker @('image', 'inspect', '--format', '{{json .Config.Env}}', $old.Image)) | ConvertFrom-Json)
    $newEnv = $target.environment
    foreach ($key in $actualEnv.Keys) {
        if ($newEnv.PSObject.Properties[$key] -or $actualEnv[$key] -cne $imageEnv[$key]) {
            if ($actualEnv[$key] -cne $newEnv.$key) { throw "环境变量 $key 与原部署不同，请检查原 .env 和启动环境。" }
        }
    }
    if ($actualEnv.RULES_DB_PATH -cne '/data/invoice-service.db' -or
        $newEnv.SITE_ASSET_DIR -cne '/data/site-assets' -or $newEnv.RULE_FILE_DIR -cne '/data/rule-files') {
        throw '检测到自定义数据路径，请按原数据路径单独备份和更新。'
    }

    Write-Host "已找到项目 $project 和数据卷 $volume。正在预拉取镜像……"
    Invoke-Docker @('pull', $image)
    $backup = Join-Path $BackupDirectory (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')
    $null = New-Item -ItemType Directory -Path $backup
    $dataCopy = (New-Item -ItemType Directory -Path (Join-Path $backup 'data')).FullName
    if (Test-Path -LiteralPath $dotenv -PathType Leaf) { Copy-Item -LiteralPath $dotenv -Destination $backup }

    # 服务停止后再复制整个卷，SQLite、WAL、模板及图片来自同一时刻。
    Invoke-Docker @('stop', $old.Id)
    $stopped = $true
    Invoke-Docker @('cp', "$($old.Id):/data/.", $dataCopy)
    $stream = [IO.File]::OpenRead((Join-Path $dataCopy 'invoice-service.db'))
    try {
        $header = New-Object byte[] 16
        if ($stream.Read($header, 0, 16) -ne 16 -or
            [Text.Encoding]::ASCII.GetString($header) -cne ("SQLite format 3" + [char]0)) { throw '备份数据库的 SQLite 文件头无效。' }
    } finally { $stream.Dispose() }
    Write-Host "数据备份已完成：$backup"
    Invoke-Docker ($compose + @('up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120', $service))
    $new = Read-Container (Invoke-Docker ($compose + @('ps', '-q', $service)))
    if (($new.Mounts | Where-Object Destination -CEQ '/data').Name -cne $volume) { throw '新服务的数据卷检查失败，请核查原卷及备份。' }
    $stopped = $false
    Write-Host "升级完成。原数据卷：$volume ；备份位置：$backup"
} catch {
    $failure = $_
    if ($stopped) {
        $remaining = & docker ps -aq --no-trunc --filter "id=$($old.Id)"
        if ($LASTEXITCODE -eq 0 -and $remaining -eq $old.Id) {
            & docker start $old.Id | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Warning '已重新启动原容器。' }
            else { Write-Warning "原容器未能启动，请用 docker ps -a 查看。原卷为 $volume。" }
        } else { Write-Warning "请核查 Compose 服务状态。原数据卷为 $volume，备份位于 $backup。" }
    }
    throw $failure
} finally {
    [Console]::OutputEncoding = $previousEncoding
    $env:INVOICE_DATA_VOLUME = $previousVolume
    $env:INVOICE_SERVICE_IMAGE = $previousImage
    Pop-Location
}
