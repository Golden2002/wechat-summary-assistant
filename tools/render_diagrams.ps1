# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    把 tools/pdf/diagrams/*.mmd 渲染成 SVG。

.DESCRIPTION
    使用 Mermaid CLI（@mermaid-js/mermaid-cli）。它通过 puppeteer 启动一个浏览器来
    排版，默认会去下载一份 chrome-headless-shell；本脚本改为**直接复用本机已安装的
    Chrome**，因此不需要联网下载任何东西。

    样式由 tools/pdf/mermaid-config.json 提供：配色与字号刻意和 style.css 里的
    设计变量保持一致，这样插图与正文看起来是同一套设计系统。

.PARAMETER OutputDir
    SVG 的输出目录，默认 docs/diagrams。

    刻意放在 docs/ 下并纳入版本管理：这样同一份 Markdown 在 GitHub 上能直接显示
    插图，而 PDF 流水线会把这些 SVG 内联成矢量图。真正的源文件（.mmd）在
    tools/pdf/diagrams/，SVG 只是它的渲染产物。

.PARAMETER Only
    只渲染指定的图（不含扩展名），便于调试。

.EXAMPLE
    .\tools\render_diagrams.ps1
    .\tools\render_diagrams.ps1 -Only architecture
#>
param(
    [string]$OutputDir = "docs/diagrams",
    [string]$MermaidConfig = "tools/pdf/mermaid-config.json",
    [string]$Only = ""
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

# ---- 找到 mmdc ------------------------------------------------------------- #
$mmdc = (Get-Command mmdc -ErrorAction SilentlyContinue).Source
if (-not $mmdc) {
    throw "没有找到 mmdc（Mermaid CLI）。安装方式：npm install -g @mermaid-js/mermaid-cli"
}
Write-Host "mmdc: $mmdc" -ForegroundColor DarkGray

# ---- 让 puppeteer 复用本机 Chrome ------------------------------------------ #
$chrome = $null
foreach ($candidate in @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)) {
    if (Test-Path $candidate) { $chrome = $candidate; break }
}
if (-not $chrome) { throw "找不到 Chrome 或 Edge，无法渲染 Mermaid 图。" }
Write-Host "浏览器: $chrome" -ForegroundColor DarkGray

$puppeteer = Join-Path $env:TEMP ("mmdc_puppeteer_" + [guid]::NewGuid().ToString('N') + ".json")
$tmpFiles += $puppeteer
$json = @{
    executablePath = $chrome
    headless       = "new"
    args           = @("--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--font-render-hinting=none")
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($puppeteer, $json, (New-Object System.Text.UTF8Encoding($false)))

# ---- 逐张渲染 -------------------------------------------------------------- #
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$sources = Get-ChildItem "tools/pdf/diagrams" -Filter "*.mmd" | Sort-Object Name
if ($Only) { $sources = $sources | Where-Object { $_.BaseName -eq $Only } }
if (-not $sources) { throw "tools/pdf/diagrams 下没有找到 .mmd 源文件" }

Write-Host "待渲染 $($sources.Count) 张图" -ForegroundColor Cyan
$failed = @()
foreach ($src in $sources) {
    $target = Join-Path $OutputDir ($src.BaseName + ".svg")
    # mermaid-cli 会往 stderr 写进度信息；这里临时放宽错误策略，避免被当成致命错误
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # -b transparent：底色交给 style.css 控制，避免与页面背景打架
        # -c 指定配色配置，使插图与正文共用同一套设计变量
        & mmdc -i $src.FullName -o $target -b transparent -c $MermaidConfig -p $puppeteer 2>&1 | Out-Null
    } finally {
        $ErrorActionPreference = $previous
    }
    if (Test-Path $target) {
        $size = [math]::Round((Get-Item $target).Length / 1KB, 1)
        Write-Host ("  {0,-22} -> {1}（{2} KB）" -f $src.Name, (Split-Path -Leaf $target), $size) -ForegroundColor Green
    } else {
        $failed += $src.Name
        Write-Host ("  {0,-22} -> 失败" -f $src.Name) -ForegroundColor Red
    }
}

if ($failed.Count) { throw "以下图渲染失败：$($failed -join ', ')" }

Cleanup
Write-Host "`n完成：$OutputDir" -ForegroundColor Green
