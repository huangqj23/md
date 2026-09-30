# 注册 Windows 计划任务：每天 07:00 运行 ai-daily run；错过（关机）就在开机后补跑。
# 先在 md 扩展的“AI 早报 → 模型设置”里配好模型和 key（或在 .env 里填 LLM_*），再在 PowerShell 里执行：
#   powershell -ExecutionPolicy Bypass -File apps\ai-daily\scripts\install_task.ps1   （在 md 仓库根目录下）
# 取消：Unregister-ScheduledTask -TaskName hollis23-ai-daily -Confirm:$false
# 本文件要存成带 BOM 的 UTF-8：Windows PowerShell 5.1 按系统代码页（GBK）读不带 BOM 的脚本，中文会让解析出错。
# 字符串里不要用中文弯引号：PowerShell 把它们当成英文引号，会提前结束字符串。
param(
    [string]$At = "07:00",
    [string]$TaskName = "hollis23-ai-daily"
)

$Root = Split-Path -Parent $PSScriptRoot
$Exe = Join-Path $Root ".venv\Scripts\ai-daily.exe"
if (-not (Test-Path $Exe)) { throw "找不到 $Exe，先在 $Root 建 venv 并 pip install -e ." }
# 模型配置以面板保存的 data\llm.json 为准（key 加密），没有时用 .env 的 LLM_*；没配好时 llm-status 返回 1
& $Exe llm-status
if ($LASTEXITCODE -ne 0) {
    throw '模型还没配好：在 md 扩展的「AI 早报 → 模型设置」里配置（或在 .env 里填 LLM_*），再运行本脚本'
}

$Action = New-ScheduledTaskAction -Execute $Exe -Argument "run" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "hollis23 AI 早报：采集、选题、写初稿到 Obsidian" -Force | Out-Null
Write-Host "已注册 $TaskName：每天 $At 运行；日志在 $Root\data\logs\"
