<#
.SYNOPSIS
  OntoBot 오프라인 설치 번들을 만든다(인터넷이 되는 빌드 장비에서, ADR-0010).

.DESCRIPTION
  번들 = 앱(저장소와 같은 배치) + 프론트 빌드 + CPython 3.12 + uv + wheelhouse
       + 제3자 설치본(운영자가 받아 둔 것) + Ollama 모델 + 설치 스크립트 + manifest.json(SHA-256).

  제3자 설치본은 받지 않는다 — 운영자가 공식 배포처에서 받아 -PrereqDir 에 둔다(INSTALL.md 1절).
  파이썬 패키지는 uv.lock 의 해시 고정 목록으로 PyPI 에서 받는다(pip download --require-hashes).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\offline\build_bundle.ps1 -PrereqDir D:\ontobot-prereqs

.EXAMPLE
  # 앱 부분만(설치본·모델 없이) — 개발 PC 에서 설치 스크립트를 -SkipPrerequisites 로 시험할 때
  powershell -ExecutionPolicy Bypass -File deploy\offline\build_bundle.ps1 -AppOnly
#>
[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$PrereqDir,
    [string]$OllamaModelsDir = (Join-Path $env:USERPROFILE ".ollama\models"),
    [switch]$AppOnly,
    [switch]$SkipFrontendInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $OutDir) { $OutDir = Join-Path $Repo "dist\bundle" }

function Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Invoke-Native {
    # 네이티브 명령의 종료 코드를 확인한다(PowerShell 5.1 은 실패해도 계속 간다).
    param([string]$Exe, [string[]]$Arguments, [string]$WorkDir = $Repo)
    Push-Location $WorkDir
    try {
        & $Exe @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Exe 실패(종료 코드 $LASTEXITCODE): $($Arguments -join ' ')" }
    } finally { Pop-Location }
}

function Copy-Tree([string]$From, [string]$To, [string[]]$ExcludeDirs = @()) {
    # robocopy 는 0~7 이 성공이다(8 이상이 실패).
    $xd = @("__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache") + $ExcludeDirs
    & robocopy $From $To /E /NFL /NDL /NJH /NJS /NP /XD @xd /XF "*.pyc" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy 실패($LASTEXITCODE): $From -> $To" }
}

# ---------------------------------------------------------------- 버전
$pyproject = Get-Content (Join-Path $Repo "backend\pyproject.toml") -Raw -Encoding UTF8
if ($pyproject -notmatch '(?m)^version\s*=\s*"([^"]+)"') { throw "pyproject.toml 에서 version 을 찾지 못했습니다" }
$appVersion = $Matches[1]
$commit = (& git -C $Repo rev-parse --short=10 HEAD).Trim()
$dirty = [bool](& git -C $Repo status --porcelain)
$version = "$appVersion+$commit"
if ($dirty) {
    Write-Warning "커밋하지 않은 변경이 있습니다 — 번들 버전에 '.dirty' 를 붙입니다(운영 배포에 쓰지 말 것)"
    $version = "$version.dirty"
}
$folderName = "ontobot-" + ($version -replace '[+]', '-')
$Bundle = Join-Path $OutDir $folderName
if (Test-Path $Bundle) { Remove-Item $Bundle -Recurse -Force }
New-Item -ItemType Directory -Force $Bundle | Out-Null
Step "번들: $Bundle (버전 $version)"

# ---------------------------------------------------------------- 프론트 빌드
Step "프론트 빌드(CDN 없음 — 모든 자산이 dist 에 들어간다)"
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
if (-not $SkipFrontendInstall) { Invoke-Native $npm @("ci") (Join-Path $Repo "frontend") }
Invoke-Native $npm @("run", "build") (Join-Path $Repo "frontend")

# ---------------------------------------------------------------- 앱
Step "앱 복사(저장소와 같은 배치)"
$App = Join-Path $Bundle "app"
foreach ($f in @("pyproject.toml", "uv.lock", "alembic.ini")) {
    New-Item -ItemType Directory -Force (Join-Path $App "backend") | Out-Null
    Copy-Item (Join-Path $Repo "backend\$f") (Join-Path $App "backend\$f")
}
Copy-Tree (Join-Path $Repo "backend\alembic") (Join-Path $App "backend\alembic")
Copy-Tree (Join-Path $Repo "backend\src") (Join-Path $App "backend\src")
Copy-Tree (Join-Path $Repo "config") (Join-Path $App "config")
Copy-Tree (Join-Path $Repo "domain-packs") (Join-Path $App "domain-packs")
Copy-Tree (Join-Path $Repo "frontend\dist") (Join-Path $App "frontend\dist")
New-Item -ItemType Directory -Force (Join-Path $App "scripts"), (Join-Path $App "deploy") | Out-Null
foreach ($s in @("seed_domain_pack.py", "graph_rebuild.py", "run_eval.py", "load_test.py")) {
    Copy-Item (Join-Path $Repo "scripts\$s") (Join-Path $App "scripts\$s")
}
Copy-Item (Join-Path $Repo "deploy\env.example") (Join-Path $App "deploy\env.example")
Set-Content -Path (Join-Path $App "VERSION") -Value $version -Encoding ASCII

# ---------------------------------------------------------------- 파이썬·uv
Step "CPython 3.12 · uv"
$uv = (Get-Command uv.exe -ErrorAction Stop).Source
$pyExe = (& $uv python find "3.12").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pyExe)) { throw "uv 가 관리하는 CPython 3.12 가 없습니다: uv python install 3.12" }
$pyHome = Split-Path $pyExe -Parent
if (-not (Test-Path (Join-Path $pyHome "Lib\venv"))) { throw "독립 실행 CPython 이 아닙니다(가상환경일 수 있음): $pyHome" }
Copy-Tree $pyHome (Join-Path $Bundle "python")
New-Item -ItemType Directory -Force (Join-Path $Bundle "tools") | Out-Null
Copy-Item $uv (Join-Path $Bundle "tools\uv.exe")
$pyVersion = (& $pyExe -c "import platform; print(platform.python_version())").Trim()

# ---------------------------------------------------------------- wheelhouse
Step "wheelhouse(uv.lock 의 해시 고정 목록, Windows x64 · CPython 3.12 wheel 만)"
$Wheels = Join-Path $Bundle "wheelhouse"
New-Item -ItemType Directory -Force $Wheels | Out-Null
$req = Join-Path $Wheels "requirements.txt"
Invoke-Native $uv @("export", "--project", "backend", "--frozen", "--no-dev", "--no-emit-project",
    "--format", "requirements-txt", "-o", $req)
$pipDownload = @("tool", "run", "--from", "pip==25.2", "pip", "download", "--disable-pip-version-check",
    "--dest", $Wheels, "--only-binary=:all:", "--platform", "win_amd64",
    "--python-version", "3.12", "--implementation", "cp")
Invoke-Native $uv ($pipDownload + @("--require-hashes", "-r", $req))
# 편집 가능 설치(ADR-0010)의 빌드 백엔드. 버전은 pyproject 의 build-system.requires 와 같다.
if ($pyproject -notmatch '(?m)^requires\s*=\s*\["(hatchling==[^"]+)"\]') { throw "build-system.requires 에서 hatchling 고정 버전을 찾지 못했습니다" }
# hatchling 의 편집 가능 빌드는 editables 를 더 요구한다(build-system.requires 에 없다 — 오프라인 설치 시험에서 발견).
$buildReq = Join-Path $Wheels "build-requirements.txt"
Set-Content -Path $buildReq -Value @($Matches[1], "editables==0.5") -Encoding ASCII
Invoke-Native $uv ($pipDownload + @("-r", $buildReq))

# ---------------------------------------------------------------- 설치 스크립트·문서
Step "설치 스크립트·서비스 템플릿·INSTALL.md"
Copy-Item (Join-Path $Repo "deploy\windows\install.ps1") (Join-Path $Bundle "install.ps1")
Copy-Tree (Join-Path $Repo "deploy\windows\services") (Join-Path $Bundle "services")
Copy-Item (Join-Path $Repo "deploy\INSTALL.md") (Join-Path $Bundle "INSTALL.md")

# ---------------------------------------------------------------- 제3자 설치본
$prereqs = [ordered]@{}
if (-not $AppOnly) {
    Step "제3자 설치본(-PrereqDir)"
    if (-not $PrereqDir -or -not (Test-Path $PrereqDir)) { throw "-PrereqDir 가 필요합니다(INSTALL.md 1절의 파일들). 앱만 만들려면 -AppOnly" }
    $Inst = Join-Path $Bundle "installers"
    New-Item -ItemType Directory -Force $Inst | Out-Null
    # 이름 → 파일 패턴. 같은 패턴에 둘 이상 맞으면 버전이 모호하므로 멈춘다.
    $patterns = [ordered]@{
        postgresql = "postgresql-18*-windows-x64.exe"
        neo4j      = "neo4j-community-5.26*-windows.zip"
        jre        = "*j*21*windows*x64*.zip"
        ollama     = "ollama-windows-amd64.zip"
        winsw      = "WinSW-x64.exe"
    }
    foreach ($name in $patterns.Keys) {
        $found = @(Get-ChildItem -Path $PrereqDir -Filter $patterns[$name] -File)
        if ($found.Count -ne 1) { throw "$name 설치본이 하나여야 합니다(패턴 $($patterns[$name])): $($found.Count)개" }
        Copy-Item $found[0].FullName (Join-Path $Inst $found[0].Name)
        $prereqs[$name] = $found[0].Name
    }
    # pgvector(선택 — 사용은 P1): lib\vector.dll, share\extension\vector*.
    $pgv = Join-Path $PrereqDir "pgvector"
    if (Test-Path (Join-Path $pgv "lib\vector.dll")) {
        Copy-Tree $pgv (Join-Path $Inst "pgvector")
        $prereqs["pgvector"] = "pgvector"
    } else {
        Write-Warning "pgvector 빌드본이 없습니다($pgv\lib\vector.dll) — 확장 없이 설치합니다(사용은 P1)"
    }

    # ------------------------------------------------------------ Ollama 모델
    Step "Ollama 모델(config\llm.yaml 의 ollama 공급자 역할)"
    $listModels = @"
import yaml, pathlib
cfg = yaml.safe_load(pathlib.Path(r'$Repo\config\llm.yaml').read_text(encoding='utf-8'))
local = {n for n, p in cfg['providers'].items() if p['adapter'] == 'ollama'}
print('\n'.join(sorted({r['model'] for r in cfg['roles'].values() if r['provider'] in local})))
"@
    $models = @(& $uv run --project (Join-Path $Repo "backend") python -c $listModels | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0) { throw "llm.yaml 을 읽지 못했습니다" }
    $Mdl = Join-Path $Bundle "ollama-models"
    foreach ($m in $models) {
        $name, $tag = $m.Split(":", 2)
        if (-not $tag) { $tag = "latest" }
        $rel = "manifests\registry.ollama.ai\library\$name\$tag"
        $manifestPath = Join-Path $OllamaModelsDir $rel
        if (-not (Test-Path $manifestPath)) { throw "Ollama 모델이 없습니다: $m ($manifestPath) — ollama pull $m" }
        New-Item -ItemType Directory -Force (Split-Path (Join-Path $Mdl $rel) -Parent) | Out-Null
        Copy-Item $manifestPath (Join-Path $Mdl $rel)
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $digests = @($manifest.config.digest) + @($manifest.layers | ForEach-Object { $_.digest })
        New-Item -ItemType Directory -Force (Join-Path $Mdl "blobs") | Out-Null
        foreach ($d in $digests) {
            $blob = $d -replace ':', '-'
            Copy-Item (Join-Path $OllamaModelsDir "blobs\$blob") (Join-Path $Mdl "blobs\$blob")
        }
        Write-Host "  $m : 조각 $($digests.Count)개"
    }
}

# ---------------------------------------------------------------- manifest
Step "manifest.json(모든 파일의 SHA-256)"
$files = [ordered]@{}
Get-ChildItem -Path $Bundle -Recurse -File | Sort-Object FullName | ForEach-Object {
    $rel = $_.FullName.Substring($Bundle.Length + 1)
    $files[$rel] = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant()
}
$manifestOut = [ordered]@{
    name       = "ontobot"
    version    = $version
    commit     = $commit
    dirty      = $dirty
    built_at   = (Get-Date).ToString("s")
    python     = $pyVersion
    app_only   = [bool]$AppOnly
    prereqs    = $prereqs
    files      = $files
}
$json = $manifestOut | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $Bundle "manifest.json"), $json, (New-Object Text.UTF8Encoding $false))

$size = (Get-ChildItem $Bundle -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Step ("완료: {0} ({1:N0} MB, 파일 {2}개)" -f $Bundle, $size, $files.Count)
