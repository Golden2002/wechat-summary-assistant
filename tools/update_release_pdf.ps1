# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    就地替换某个已发布 Release 里的技术说明 PDF，不新建版本。

.DESCRIPTION
    适用于"文档改好了，但功能版本号不变"的情形：Release 已经发布，用户也不会因为
    一份文档的排版修订而升级版本。此时正确的做法是把附件换成新的，
    而不是再发一个 v1.0.1 —— 后者会在版本历史上留下一串没有功能差异的噪声。

    走 GitHub REST API，因此不依赖 `git push`，在 github.com 无法直连的
    网络环境下同样可用（前提是 api.github.com 与 uploads.github.com 可达）。

    流程：取 tag 对应的 Release → 删掉同名旧附件 → 上传新文件 → 回读校验大小。
    旧的 .zip 源码包不受影响。

.PARAMETER Owner
    GitHub 用户名，默认 Golden2002。

.PARAMETER Repo
    仓库名，默认 wechat-summary-assistant。

.PARAMETER Tag
    Release 的标签，默认 v1.0.0。

.PARAMETER Pdf
    本地 PDF 路径，默认 docs/TECHNICAL.pdf。

.PARAMETER AssetName
    附件名；默认沿用 releases 里已有的技术说明附件名，避免下游链接失效。

.PARAMETER TokenFile
    存放 token 的文件（支持 `KEY=ghp_xxx` 或直接是 token 本身）。

.PARAMETER DryRun
    只打印将要执行的操作，不真正删除或上传。

.EXAMPLE
    .\tools\update_release_pdf.ps1 -DryRun
    .\tools\update_release_pdf.ps1
#>
param(
    [string]$Owner = "Golden2002",
    [string]$Repo = "wechat-summary-assistant",
    [string]$Tag = "v1.0.0",
    [string]$Pdf = "docs/TECHNICAL.pdf",
    [string]$AssetName = "",
    [string]$TokenFile = "github_access_token.txt",
    [switch]$DryRun
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

$cfg = Join-Path $env:TEMP ("ghpdf_" + [guid]::NewGuid().ToString('N') + ".cfg")
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

if (-not (Test-Path $Pdf)) { throw "找不到 PDF：$Pdf" }
$pdfSize = [math]::Round((Get-Item $Pdf).Length / 1KB, 1)

$me = Invoke-Gh -Method GET -Url "https://api.github.com/user" | ConvertFrom-Json
if (-not $me.login) { throw "token 无效或无法访问 GitHub API" }
Write-Host "身份: $($me.login)" -ForegroundColor Cyan

$api = "https://api.github.com/repos/$Owner/$Repo"

# ---- 1. 找到 Release 与目标附件 -------------------------------------------- #
$release = Invoke-Gh -Method GET -Url "$api/releases/tags/$Tag" | ConvertFrom-Json
if (-not $release.id) { throw "找不到 Release $Tag" }
Write-Host "Release: $($release.name)（id=$($release.id)）" -ForegroundColor Cyan

$assets = Invoke-Gh -Method GET -Url "$api/releases/$($release.id)/assets" | ConvertFrom-Json
if (-not $AssetName) {
    # 默认沿用已有的技术说明附件名，保证 README 里的下载链接不变
    $existing = $assets | Where-Object { $_.name -match 'technical-guide\.pdf$' } | Select-Object -First 1
    if (-not $existing) { throw "Release $Tag 里没有技术说明附件，请用 -AssetName 指定名称" }
    $AssetName = $existing.name
}
Write-Host "目标附件: $AssetName" -ForegroundColor Cyan

# ---- 2. 删除同名旧附件 ------------------------------------------------------ #
$old = $assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
if ($old) {
    $oldSize = [math]::Round($old.size / 1KB, 1)
    Write-Host "[1/2] 删除旧附件（$oldSize KB，上传于 $($old.created_at)）" -ForegroundColor Cyan
    if (-not $DryRun) {
        Invoke-Gh -Method DELETE -Url "$api/releases/assets/$($old.id)" | Out-Null
    }
} else {
    Write-Host "[1/2] 没有同名旧附件，直接上传" -ForegroundColor Cyan
}

# ---- 3. 上传新附件 ---------------------------------------------------------- #
Write-Host "[2/2] 上传 $Pdf（$pdfSize KB）" -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "`n（dry-run：以上操作均未执行）" -ForegroundColor Yellow
    Cleanup
    exit 0
}

$uploadUrl = "https://uploads.github.com/repos/$Owner/$Repo/releases/$($release.id)/assets?name=$AssetName"
$out = & curl.exe -s -S -K $cfg -X POST -H "Content-Type: application/pdf" --data-binary "@$Pdf" $uploadUrl
$asset = $out | ConvertFrom-Json
if (-not $asset.browser_download_url) { throw "上传失败`n响应: $out" }

# 回读校验：附件必须可下载，且大小与本地文件一致
$check = Invoke-Gh -Method GET -Url "$api/releases/assets/$($asset.id)" | ConvertFrom-Json
$remoteSize = [math]::Round($check.size / 1KB, 1)
if ($check.size -ne (Get-Item $Pdf).Length) {
    throw "附件大小不一致：本地 $pdfSize KB，远端 $remoteSize KB"
}

Write-Host "`n完成：$($check.name)（$remoteSize KB）" -ForegroundColor Green
Write-Host "下载地址：$($check.browser_download_url)" -ForegroundColor Green

Cleanup
