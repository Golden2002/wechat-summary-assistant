# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    把 docs/TECHNICAL.md 编译成排版精良的 PDF。

.DESCRIPTION
    两步流水线：

      1. tools/md_to_html.py   把 Markdown 套进 tools/pdf/template.html
      2. tools/render_pdf.js   用无头 Chrome 经 DevTools Protocol 打印成 PDF

    第二步之所以不用 `chrome --headless --print-to-pdf`，是因为命令行方式无法同时
    得到「自定义页脚」与「页码」—— CSS 的 @page 页边距框（@bottom-center）
    在 Chromium 里并不支持，只能通过 Page.printToPDF 的 footerTemplate 实现。

.PARAMETER Version
    写入封面与页脚的版本号，默认 v1.0.0。

.PARAMETER Source
    输入的 Markdown 文件，默认 docs/TECHNICAL.md。
    注意：这个参数不能命名为 $Input —— 那是 PowerShell 的自动变量，赋值会被忽略。

.PARAMETER Output
    输出的 PDF 路径，默认 docs/TECHNICAL.pdf。

.EXAMPLE
    .\tools\build_docs.ps1
    .\tools\build_docs.ps1 -Version v1.1.0 -Source docs/USAGE.md -Output docs/USAGE.pdf
#>
param(
    [string]$Version = "v1.0.0",
    [string]$Source = "docs/TECHNICAL.md",
    [string]$Output = "docs/TECHNICAL.pdf",
    [string]$Title = "技术说明",
    [string]$Subtitle = "结构、机制与实测结论"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $repoRoot

try {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { throw "找不到虚拟环境中的 Python：$python（请先运行 install.bat）" }

    $date = Get-Date -Format "yyyy-MM-dd"
    $html = Join-Path $repoRoot "build\docs\technical.html"

    Write-Host "[1/2] Markdown -> HTML" -ForegroundColor Cyan
    & $python "tools\md_to_html.py" `
        --input $Source `
        --output "build/docs/technical.html" `
        --template "tools/pdf/template.html" `
        --css "tools/pdf/style.css" `
        --title $Title `
        --subtitle $Subtitle `
        --project "wechat-summary-assistant" `
        --version $Version `
        --date $date `
        --running-title "微信总结助手 · $Title"
    if ($LASTEXITCODE -ne 0) { throw "Markdown 转换失败" }

    # 页眉页脚交给样式表里的 @page 页边距框绘制（封面用 @page :first 抑制），
    # 因此这里**不加** --header-footer，否则会出现重复的页眉与页码。
    Write-Host "[2/2] HTML -> PDF" -ForegroundColor Cyan
    & node "tools\render_pdf.js" --html $html --out $Output
    if ($LASTEXITCODE -ne 0) { throw "PDF 渲染失败" }

    $size = [math]::Round((Get-Item $Output).Length / 1KB, 1)
    Write-Host "`n完成：$Output（$size KB）" -ForegroundColor Green
}
finally {
    Pop-Location
}
