# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    打包源码、创建 GitHub Release，并上传附件（源码 ZIP 与技术说明 PDF）。

.DESCRIPTION
    走 GitHub REST API 完成，因此不依赖 `git push`，在 github.com 无法直接访问的
    网络环境下同样可用（前提是 api.github.com 与 uploads.github.com 可达）。

    打包使用 `git archive`，因此归档内容**只包含已提交的文件**，
    不会把 .venv、日志、配置、token 等本地产物打进去。

    本脚本刻意自带一份 token / curl 引导代码，不与其他脚本共享，
    以便单独拷贝出去执行。

.PARAMETER Owner
    GitHub 用户名，默认 Golden2002。

.PARAMETER Repo
    仓库名，默认 wechat-summary-assistant。

.PARAMETER Tag
    Release 的标签，默认 v1.0.0。

.PARAMETER TokenFile
    存放 token 的文件（支持 `KEY=ghp_xxx` 或直接是 token 本身）。

.PARAMETER Pdf
    要一并上传的 PDF 路径；为空则只上传源码 ZIP。

.PARAMETER Draft
    创建为草稿（不公开）。

.EXAMPLE
    .\tools\release.ps1
    .\tools\release.ps1 -Tag v1.1.0 -Pdf docs/TECHNICAL.pdf
#>
param(
    [string]$Owner = "Golden2002",
    [string]$Repo = "wechat-summary-assistant",
    [string]$Tag = "v1.0.0",
    [string]$TokenFile = "github_access_token.txt",
    [string]$Pdf = "docs/TECHNICAL.pdf",
    [string]$Changelog = "CHANGELOG.md",
    [switch]$Draft
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $repoRoot
$tmpFiles = @()

function Cleanup {
    foreach ($f in $tmpFiles) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    Pop-Location
}
trap { Write-Host "`n[ERROR] $_" -ForegroundColor Red; Cleanup; exit 1 }

# ---- token ---------------------------------------------------------------- #
if (-not (Test-Path $TokenFile)) { throw "找不到 token 文件：$TokenFile" }
$token = ((Get-Content $TokenFile -Raw).Trim() -replace '^\s*KEY\s*=\s*', '').Trim()
if (-not $token) { throw "token 文件内容为空：$TokenFile" }
Write-Host "token: 已读取（长度 $($token.Length)，不显示内容）" -ForegroundColor DarkGray

$cfg = Join-Path $env:TEMP ("ghrel_" + [guid]::NewGuid().ToString('N') + ".cfg")
$tmpFiles += $cfg
@(
    "header = `"Authorization: token $token`"",
    "header = `"Accept: application/vnd.github+json`"",
    "header = `"User-Agent: dsh-release`""
) | Set-Content -Path $cfg -Encoding ASCII

function Invoke-Gh {
    param([string]$Method, [string]$Url, [string]$BodyFile)
    $curlArgs = @('-s', '-S', '-K', $cfg, '-X', $Method)
    if ($BodyFile) { $curlArgs += @('-H', 'Content-Type: application/json', '--data-binary', "@$BodyFile") }
    $curlArgs += $Url
    return (& curl.exe @curlArgs)
}

function New-TempJson {
    param([string]$Json)
    $p = Join-Path $env:TEMP ("ghbody_" + [guid]::NewGuid().ToString('N') + ".json")
    [System.IO.File]::WriteAllText($p, $Json, (New-Object System.Text.UTF8Encoding($false)))
    $script:tmpFiles += $p
    return $p
}

$me = Invoke-Gh -Method GET -Url "https://api.github.com/user" | ConvertFrom-Json
if (-not $me.login) { throw "token 无效或无法访问 GitHub API" }
Write-Host "身份: $($me.login)" -ForegroundColor Cyan

# ---- 1. 打包源码 ----------------------------------------------------------- #
New-Item -ItemType Directory -Force -Path "build/release" | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
$zipName = "wechat-summary-assistant-$Tag-src.zip"
$zipPath = Join-Path $repoRoot "build/release/$zipName"

Write-Host "[1/3] 打包源码（git archive HEAD）" -ForegroundColor Cyan
cmd /c "git archive --format=zip -9 -o `"$zipPath`" HEAD"
if ($LASTEXITCODE -ne 0) { throw "git archive 失败" }

# 校验归档内容：不得包含凭据或本地运行产物
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    $names = $archive.Entries | ForEach-Object { $_.FullName }
} finally {
    $archive.Dispose()
}
$forbidden = $names | Where-Object { $_ -match 'token|\.venv|__pycache__|ai_config\.json|sent_summaries|^logs/|^summary/' }
if ($forbidden) { throw "归档里出现了不该有的文件：`n  $($forbidden -join "`n  ")" }
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1KB, 1)
Write-Host "      已生成 $zipName（$zipSize KB，$($names.Count) 个文件）" -ForegroundColor Green

# ---- 2. 从 CHANGELOG 里取出本版正文 ---------------------------------------- #
$body = ""
if (Test-Path $Changelog) {
    $lines = Get-Content $Changelog -Encoding UTF8
    $collect = $false
    $buffer = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($line -match "^##\s+$([regex]::Escape($Tag))\s*$") { $collect = $true; continue }
        if ($collect -and $line -match '^##\s') { break }
        if ($collect) { $buffer.Add($line) }
    }
    $body = ($buffer -join "`n").Trim()
    # 去掉结尾的分隔线
    $body = $body -replace '(?s)\s*-{3,}\s*$', ''
}
if (-not $body) {
    $body = "本版本的具体变更请参阅仓库中的 CHANGELOG.md。"
    Write-Host "      提示：CHANGELOG 中没有找到 $Tag 小节，改用默认说明" -ForegroundColor Yellow
} else {
    Write-Host "      已从 $Changelog 提取 $Tag 的说明（$($body.Length) 字符）" -ForegroundColor Green
}

# ---- 3. 创建 Release 并上传附件 -------------------------------------------- #
$headSha = (git rev-parse HEAD).Trim()
Write-Host "[2/3] 创建 Release $Tag" -ForegroundColor Cyan
$releaseBody = New-TempJson -Json (@{
    tag_name         = $Tag
    target_commitish = $headSha
    name             = "微信群聊总结助手 $Tag"
    body             = $body
    draft            = [bool]$Draft
    prerelease       = $false
} | ConvertTo-Json -Compress)

$api = "https://api.github.com/repos/$Owner/$Repo"
$rawRelease = Invoke-Gh -Method POST -Url "$api/releases" -BodyFile $releaseBody
$release = $rawRelease | ConvertFrom-Json
if (-not $release.id) { throw "创建 Release 失败`n响应: $rawRelease" }
Write-Host "      $($release.html_url)" -ForegroundColor Green

Write-Host "[3/3] 上传附件" -ForegroundColor Cyan
function Send-Asset {
    param([string]$FilePath, [string]$ContentType)
    $name = Split-Path -Leaf $FilePath
    $url = "https://uploads.github.com/repos/$Owner/$Repo/releases/$($release.id)/assets?name=$name"
    $out = & curl.exe -s -S -K $cfg -X POST -H "Content-Type: $ContentType" --data-binary "@$FilePath" $url
    $asset = $out | ConvertFrom-Json
    if (-not $asset.browser_download_url) { throw "上传 $name 失败`n响应: $out" }
    $size = [math]::Round($asset.size / 1KB, 1)
    Write-Host "      $name（$size KB）" -ForegroundColor Green
}

Send-Asset -FilePath $zipPath -ContentType "application/zip"

if ($Pdf -and (Test-Path $Pdf)) {
    $pdfTarget = Join-Path $repoRoot "build/release/wechat-summary-assistant-$Tag-technical-guide.pdf"
    Copy-Item $Pdf $pdfTarget -Force
    Send-Asset -FilePath $pdfTarget -ContentType "application/pdf"
} elseif ($Pdf) {
    Write-Host "      提示：找不到 PDF（$Pdf），跳过上传" -ForegroundColor Yellow
}

Cleanup
Write-Host "`n完成：$($release.html_url)" -ForegroundColor Green
