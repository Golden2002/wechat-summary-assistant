# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    通过 GitHub REST API 把当前 git 仓库的 HEAD 发布到 GitHub。

.DESCRIPTION
    为什么需要这个脚本：某些网络环境下 `github.com:443` 的 TLS 握手会被重置，
    但 `api.github.com` 可以正常访问 —— 此时 `git push` 必然失败，而
    GitHub 的 Git Data API 完全走 api.github.com，不受影响。

    做法是把本地 HEAD 的内容（blob → tree → commit）原样重建到远端：
    因为 blob 内容一致，重建出来的 tree / commit SHA 与本地**完全相同**，
    相当于一次正常的 push，而不是"另建一个 commit"。

.PARAMETER Owner
    GitHub 用户名，默认 Golden2002。

.PARAMETER Repo
    仓库名，默认 wechat-summary-assistant。

.PARAMETER TokenFile
    存放 token 的文件（支持 `KEY=ghp_xxx` 或直接是 token 本身）。

.PARAMETER Branch
    目标分支，默认 main。

.PARAMETER CreateRepo
    仓库不存在时自动创建（公开仓库）。

.EXAMPLE
    .\tools\publish_to_github.ps1
    .\tools\publish_to_github.ps1 -Owner someone -Repo my-repo -CreateRepo
#>
param(
    [string]$Owner = "Golden2002",
    [string]$Repo = "wechat-summary-assistant",
    [string]$TokenFile = "github_access_token.txt",
    [string]$Branch = "main",
    [switch]$CreateRepo
)

$ErrorActionPreference = "Stop"

# ---- 定位仓库根（脚本在 tools/ 下） ----------------------------------------
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $repoRoot
$tmpFiles = @()

function Cleanup {
    foreach ($f in $tmpFiles) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    Pop-Location
}
trap { Write-Host "`n[ERROR] $_" -ForegroundColor Red; Cleanup; exit 1 }

# ---- 读取 token（绝不打印） -------------------------------------------------
if (-not (Test-Path $TokenFile)) { throw "找不到 token 文件：$TokenFile" }
$token = ((Get-Content $TokenFile -Raw).Trim() -replace '^\s*KEY\s*=\s*', '').Trim()
if (-not $token) { throw "token 文件内容为空：$TokenFile" }
Write-Host "token: 已读取（长度 $($token.Length)，不显示内容）" -ForegroundColor DarkGray

# ---- curl 配置（把鉴权头放进临时文件，避免出现在命令行里） ------------------
$cfg = Join-Path $env:TEMP ("gh_" + [guid]::NewGuid().ToString('N') + ".cfg")
$tmpFiles += $cfg
@(
    "header = `"Authorization: token $token`"",
    "header = `"Accept: application/vnd.github+json`"",
    "header = `"User-Agent: dsh-publish`""
) | Set-Content -Path $cfg -Encoding ASCII

function Invoke-Gh {
    param([string]$Method, [string]$Url, [string]$BodyFile)
    # 注意：不要把变量命名为 $args —— 那是 PowerShell 的自动变量，赋值会被忽略
    $curlArgs = @('-s', '-K', $cfg, '-X', $Method)
    if ($BodyFile) { $curlArgs += @('-H', 'Content-Type: application/json', '--data-binary', "@$BodyFile") }
    $curlArgs += $Url
    return (& curl.exe @curlArgs)
}

function New-TempJson {
    param([string]$Json)
    $p = Join-Path $env:TEMP ("ghbody_" + [guid]::NewGuid().ToString('N') + ".json")
    # 必须不带 BOM，否则 API 解析失败
    [System.IO.File]::WriteAllText($p, $Json, (New-Object System.Text.UTF8Encoding($false)))
    $script:tmpFiles += $p
    return $p
}

# ---- 1. 确认身份 ------------------------------------------------------------
$me = Invoke-Gh -Method GET -Url "https://api.github.com/user" | ConvertFrom-Json
if (-not $me.login) { throw "token 无效或无法访问 GitHub API" }
Write-Host "身份: $($me.login)" -ForegroundColor Cyan
if ($me.login -ne $Owner) {
    Write-Host "提示: token 属于 $($me.login)，与 -Owner $Owner 不一致" -ForegroundColor Yellow
}

# ---- 2. 仓库是否存在 --------------------------------------------------------
$code = & curl.exe -s -o NUL -w "%{http_code}" -K $cfg "https://api.github.com/repos/$Owner/$Repo"
if ($code -eq "404") {
    if (-not $CreateRepo) { throw "仓库 $Owner/$Repo 不存在。加 -CreateRepo 自动创建。" }
    Write-Host "仓库不存在，正在创建…" -ForegroundColor Cyan
    $body = New-TempJson -Json (@{
        name = $Repo; private = $false; has_issues = $true
        has_projects = $false; has_wiki = $false; auto_init = $false
    } | ConvertTo-Json -Compress)
    $created = Invoke-Gh -Method POST -Url "https://api.github.com/user/repos" -BodyFile $body | ConvertFrom-Json
    if (-not $created.full_name) { throw "创建仓库失败" }
    Write-Host "已创建: $($created.html_url)" -ForegroundColor Green
} elseif ($code -ne "200") {
    throw "查询仓库返回 HTTP $code"
} else {
    Write-Host "仓库已存在: https://github.com/$Owner/$Repo" -ForegroundColor Cyan
}

# ---- 2.5 空仓库要先初始化 ---------------------------------------------------
# GitHub 不允许在**完全没有 commit** 的仓库上使用 Git Data API
# （会返回 409 "Git Repository is empty"），所以先用 Contents API 放一个占位文件。
# 后面会用 parents=[] 的新 commit 强制覆盖分支，这个占位提交随即变成不可达对象。
$refProbe = "https://api.github.com/repos/$Owner/$Repo/git/refs/heads/$Branch"
$refProbeCode = & curl.exe -s -o NUL -w "%{http_code}" -K $cfg $refProbe
if ($refProbeCode -ne "200") {
    Write-Host "仓库为空，先用 Contents API 初始化…" -ForegroundColor Cyan
    $placeholder = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes("# placeholder`n"))
    $initBody = New-TempJson -Json (@{
        message = "chore: initialize repository"
        content = $placeholder
    } | ConvertTo-Json -Compress)
    $init = Invoke-Gh -Method PUT -Url "https://api.github.com/repos/$Owner/$Repo/contents/.gitattributes" -BodyFile $initBody | ConvertFrom-Json
    if (-not $init.commit) { throw "初始化空仓库失败" }
    Write-Host "已初始化（占位提交）" -ForegroundColor Green
}

# ---- 3. 收集本地 HEAD 的内容 ------------------------------------------------
$headSha = (git rev-parse HEAD).Trim()
$rawCommit = git cat-file commit HEAD
$authorLine = ($rawCommit | Select-String -Pattern '^author ').Line
$committerLine = ($rawCommit | Select-String -Pattern '^committer ').Line
if (-not $authorLine) { throw "无法读取本地 commit 的 author 信息" }

function Parse-Ident {
    param([string]$Line)
    if ($Line -match '^(?:author|committer)\s+(.*?)\s+<([^>]*)>\s+(\d+)\s+([+-]\d{4})$') {
        $epoch = [int64]$Matches[3]
        $dt = [DateTimeOffset]::FromUnixTimeSeconds($epoch).ToOffset(
            [TimeSpan]::FromHours([int]$Matches[4].Substring(1, 2)) )
        return @{ name = $Matches[1]; email = $Matches[2]; date = $dt.ToString("yyyy-MM-ddTHH:mm:sszzz") }
    }
    throw "无法解析身份行: $Line"
}
$author = Parse-Ident $authorLine
$committer = Parse-Ident $committerLine
# git log 输出是多行，PowerShell 会拆成字符串数组 —— 必须 join 回单个字符串，
# 否则 API 会返回 422 "For 'properties/message', [...] is not a string"。
$message = ((git log -1 --pretty=%B) -join "`n")
Write-Host "本地 HEAD: $headSha" -ForegroundColor DarkGray

$files = @(git ls-files)
Write-Host "待上传文件: $($files.Count) 个" -ForegroundColor Cyan

# ---- 4. 逐个创建 blob -------------------------------------------------------
# 关键：内容必须取自 **git 对象库**（`git cat-file blob <sha>`），而不是工作区文件。
# 工作区文件可能被 autocrlf / .gitattributes 影响而带 CRLF，直接读字节会算出不同的
# blob SHA，进而导致 tree / commit SHA 与本地不一致（不再是"同一棵提交树"）。
# 这里用 cmd 的重定向把 git 输出原样落盘 —— PowerShell 的 > 会破坏二进制。
$treeEntries = @()
$i = 0
foreach ($f in $files) {
    $i++
    $oldSha = (git rev-parse "HEAD:$f").Trim()
    $tmpBlob = Join-Path $env:TEMP ("ghblob_" + [guid]::NewGuid().ToString('N') + ".bin")
    $tmpFiles += $tmpBlob
    cmd /c "git cat-file blob $oldSha > `"$tmpBlob`""
    if (-not (Test-Path $tmpBlob)) { throw "无法从 git 取出 $f" }
    $b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($tmpBlob))
    Remove-Item $tmpBlob -Force -ErrorAction SilentlyContinue

    $body = New-TempJson -Json (@{ content = $b64; encoding = "base64" } | ConvertTo-Json -Compress)
    $raw = Invoke-Gh -Method POST -Url "https://api.github.com/repos/$Owner/$Repo/git/blobs" -BodyFile $body
    $blob = $raw | ConvertFrom-Json
    if (-not $blob.sha) { throw "创建 blob 失败: $f`n响应: $raw" }
    if ($oldSha -ne $blob.sha) {
        Write-Host "  注意: $f 的 blob 与本地不一致（本地 $oldSha / 远端 $($blob.sha)）" -ForegroundColor Yellow
    }
    $treeEntries += @{ path = $f; mode = "100644"; type = "blob"; sha = $blob.sha }
    if ($i % 5 -eq 0 -or $i -eq $files.Count) { Write-Host "  blobs: $i/$($files.Count)" -ForegroundColor DarkGray }
}

# ---- 5. tree → commit → ref ------------------------------------------------
$treeBody = New-TempJson -Json (@{ tree = $treeEntries } | ConvertTo-Json -Depth 6 -Compress)
$rawTree = Invoke-Gh -Method POST -Url "https://api.github.com/repos/$Owner/$Repo/git/trees" -BodyFile $treeBody
$tree = $rawTree | ConvertFrom-Json
if (-not $tree.sha) { throw "创建 tree 失败`n响应: $rawTree" }
Write-Host "tree: $($tree.sha)" -ForegroundColor DarkGray

$localTree = (git rev-parse 'HEAD^{tree}').Trim()
if ($tree.sha -ne $localTree) {
    Write-Host "  注意: tree SHA 与本地不同（本地 $localTree）—— 内容一致但元数据有差异" -ForegroundColor Yellow
}

$commitBody = New-TempJson -Json (@{
    message   = $message
    tree      = $tree.sha
    parents   = @()
    author    = $author
    committer = $committer
} | ConvertTo-Json -Depth 6 -Compress)
$rawCommit = Invoke-Gh -Method POST -Url "https://api.github.com/repos/$Owner/$Repo/git/commits" -BodyFile $commitBody
$commit = $rawCommit | ConvertFrom-Json
if (-not $commit.sha) { throw "创建 commit 失败`n响应: $rawCommit" }
Write-Host "commit: $($commit.sha)" -ForegroundColor Green

if ($commit.sha -eq $headSha) {
    Write-Host "与本地 HEAD 完全一致（相当于一次正常 push）" -ForegroundColor Green
} else {
    Write-Host "注意: 远端 commit SHA 与本地不同（本地 $headSha）" -ForegroundColor Yellow
}

# 分支引用：存在就强制更新，不存在就创建
$refUrl = "https://api.github.com/repos/$Owner/$Repo/git/refs/heads/$Branch"
$refCode = & curl.exe -s -o NUL -w "%{http_code}" -K $cfg $refUrl
if ($refCode -eq "200") {
    $refBody = New-TempJson -Json (@{ sha = $commit.sha; force = $true } | ConvertTo-Json -Compress)
    $res = Invoke-Gh -Method PATCH -Url $refUrl -BodyFile $refBody | ConvertFrom-Json
} else {
    $refBody = New-TempJson -Json (@{ ref = "refs/heads/$Branch"; sha = $commit.sha } | ConvertTo-Json -Compress)
    $res = Invoke-Gh -Method POST -Url "https://api.github.com/repos/$Owner/$Repo/git/refs" -BodyFile $refBody | ConvertFrom-Json
}
if (-not $res.ref) { throw "更新分支引用失败" }
Write-Host "分支: $($res.ref) -> $($res.object.sha)" -ForegroundColor Green

# 顺便把默认分支设成目标分支
$repoBody = New-TempJson -Json (@{ default_branch = $Branch } | ConvertTo-Json -Compress)
Invoke-Gh -Method PATCH -Url "https://api.github.com/repos/$Owner/$Repo" -BodyFile $repoBody | Out-Null

# ---- 6. 让本地仓库"看起来"是同步的 -----------------------------------------
# 注意：不能直接 `git remote remove origin` —— 没有 origin 时它会写 stderr，
# 而 $ErrorActionPreference = "Stop" 会把它当成致命错误。
if (@(git remote) -contains "origin") { git remote remove origin }
git remote add origin "https://github.com/$Owner/$Repo.git"
git update-ref "refs/remotes/origin/$Branch" $commit.sha
Write-Host "已设置 origin 与远端跟踪分支（本机无法 git push，请用本脚本再次发布）" -ForegroundColor DarkGray

Cleanup
Write-Host "`n完成: https://github.com/$Owner/$Repo" -ForegroundColor Green
