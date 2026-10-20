<#
.SYNOPSIS
  OntoBot 설치·업그레이드·롤백(Windows Server, 오프라인 번들 — ADR-0007, ADR-0010).

.DESCRIPTION
  번들 루트(이 파일이 있는 폴더)에서 관리자 PowerShell 로 실행한다. 인터넷을 쓰지 않는다.

    -Mode Install    선행 구성 요소(PostgreSQL·pgvector·JRE·Neo4j·Ollama) → 앱 릴리스 → 마이그레이션
                     → 서비스 등록(WinSW) → 시작 → /readyz 확인
    -Mode Upgrade    새 릴리스 준비 → DB 백업(pg_dump) → 중지 → 마이그레이션 → current 전환 → 시작 → 확인
    -Mode Rollback   중지 → current 를 이전 릴리스로 → (-RestoreDatabase) 업그레이드 직전 백업 복원 → 시작
    -Mode Status     설치 상태·서비스·/readyz

  이미 있는 구성 요소(서비스 이름으로 판단)는 건너뛴다. -SkipPrerequisites 는 선행 구성 요소를 아예 보지 않고,
  -NoServices 는 서비스를 등록·시작하지 않는다(둘을 함께 주면 관리자 권한 없이 앱만 설치 — 개발 PC 검증용).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Mode Install -AdminUsername admin -SeedSample
#>
[CmdletBinding()]
param(
    [ValidateSet("Install", "Upgrade", "Rollback", "Status")]
    [string]$Mode = "Status",
    [string]$Root = "C:\OntoBot",
    [string]$ApiHost = "0.0.0.0",
    [int]$ApiPort = 8000,
    [string]$PgService = "postgresql-x64-18",
    [int]$PgPort = 5432,
    [string]$Neo4jService = "neo4j-ontobot",
    [string]$Neo4jHeap = "1g",
    [string]$Neo4jPageCache = "1g",
    [string]$AdminUsername,
    [switch]$SeedSample,
    [switch]$SkipPrerequisites,
    [switch]$NoServices,
    [switch]$RestoreDatabase,
    [switch]$SkipManifestCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

$Bundle = $PSScriptRoot
$StateFile = Join-Path $Root "state.json"
$Shared = Join-Path $Root "shared"
$EnvFile = Join-Path $Shared ".env"
$Current = Join-Path $Root "current"
$PythonHome = Join-Path $Root "python"
$ServiceDir = Join-Path $Root "services"
$AppServices = @("ontobot-api", "ontobot-worker")
$script:PgSuperPassword = $null

function Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Note([string]$msg) { Write-Host "    $msg" }

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [string]$WorkDir = $Root, [hashtable]$Env = @{})
    $saved = @{}
    foreach ($k in $Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $Env[$k]) }
    Push-Location $WorkDir
    try {
        # Out-Host: 출력이 함수의 반환값에 섞이지 않게 한다(PowerShell 은 출력을 모두 반환한다).
        & $Exe @Arguments | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "$(Split-Path $Exe -Leaf) 실패(종료 코드 $LASTEXITCODE)" }
    } finally {
        Pop-Location
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

function Invoke-PsqlScalar([string]$Psql, [string[]]$Arguments, [hashtable]$Env) {
    $saved = @{}
    foreach ($k in $Env.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $Env[$k]) }
    try {
        $out = & $Psql @Arguments
        if ($LASTEXITCODE -ne 0) { throw "psql 실패(종료 코드 $LASTEXITCODE)" }
        return "$out".Trim()
    } finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function New-Secret {
    # 영숫자만(hex) — .env·SQL 리터럴·명령줄 어디에 넣어도 따옴표 문제가 없다.
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Write-Utf8([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

function Read-State {
    if (Test-Path $StateFile) { return Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json }
    return $null
}

function Save-State($state) { Write-Utf8 $StateFile ($state | ConvertTo-Json -Depth 4) }

function Get-BundleVersion {
    return (Get-Content (Join-Path $Bundle "app\VERSION") -Raw).Trim()
}

function Get-Manifest { return Get-Content (Join-Path $Bundle "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json }

# ---------------------------------------------------------------- 무결성
function Test-Manifest {
    if ($SkipManifestCheck) { Write-Warning "manifest 대조를 건너뜁니다"; return }
    Step "번들 무결성(manifest.json SHA-256)"
    $manifest = Get-Manifest
    $bad = @()
    $n = 0
    foreach ($p in $manifest.files.PSObject.Properties) {
        $n++
        $path = Join-Path $Bundle $p.Name
        if (-not (Test-Path $path)) { $bad += "없음: $($p.Name)"; continue }
        if ((Get-FileHash -Algorithm SHA256 $path).Hash.ToLowerInvariant() -ne $p.Value) { $bad += "다름: $($p.Name)" }
    }
    if ($bad.Count -gt 0) { throw "번들이 손상되었습니다:`n  " + ($bad[0..([Math]::Min(19, $bad.Count - 1))] -join "`n  ") }
    Note "파일 ${n}개 일치 (버전 $($manifest.version))"
}

# ---------------------------------------------------------------- .env
function Set-EnvLine([string[]]$Lines, [string]$Key, [string]$Value) {
    $out = New-Object Collections.Generic.List[string]
    $done = $false
    foreach ($l in $Lines) {
        if ($l -match "^\s*$([regex]::Escape($Key))\s*=") {
            if (-not $done) { $out.Add("$Key=$Value"); $done = $true }
        } else { $out.Add($l) }
    }
    if (-not $done) { $out.Add("$Key=$Value") }
    return , $out.ToArray()
}

function Get-EnvValue([string]$Key) {
    foreach ($l in (Get-Content $EnvFile -Encoding UTF8)) {
        if ($l -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

function Initialize-EnvFile {
    if (Test-Path $EnvFile) { Note "기존 $EnvFile 을 그대로 씁니다"; return }
    Step "설정 파일 $EnvFile 생성(비밀번호는 무작위)"
    New-Item -ItemType Directory -Force $Shared | Out-Null
    $lines = Get-Content (Join-Path $Bundle "app\deploy\env.example") -Encoding UTF8
    $hostName = [Net.Dns]::GetHostName().ToLowerInvariant()
    $values = [ordered]@{
        ONTOBOT_ENV                   = "prod"
        ONTOBOT_DB_HOST               = "localhost"
        ONTOBOT_DB_PORT               = "$PgPort"
        ONTOBOT_DB_PASSWORD           = (New-Secret)
        ONTOBOT_NEO4J_URI             = "bolt://localhost:7687"
        ONTOBOT_NEO4J_PASSWORD        = (New-Secret)
        # 테스트 인스턴스는 운영에 두지 않는다.
        ONTOBOT_TEST_NEO4J_PASSWORD   = ""
        ONTOBOT_ALLOWED_ORIGINS       = "[`"http://${hostName}:$ApiPort`",`"http://localhost:$ApiPort`"]"
        ONTOBOT_OIDC_REDIRECT_URI     = "http://${hostName}:$ApiPort/api/v1/auth/callback"
        # 운영은 API 가 프론트를 같은 오리진에서 서빙한다(비워 둔다).
        ONTOBOT_FRONTEND_BASE_URL     = ""
        ONTOBOT_FRONTEND_DIST         = (Join-Path $Current "frontend\dist")
        ONTOBOT_LOG_DIR               = (Join-Path $Root "logs")
        # 메일·SMS 게이트웨이 설정은 사이트마다 다르므로 릴리스 밖(shared)에 둔다(TRD 17.1-4).
        ONTOBOT_NOTIFY_CONFIG_PATH    = (Join-Path $Shared "notify.yaml")
    }
    foreach ($k in $values.Keys) { $lines = Set-EnvLine $lines $k $values[$k] }
    Write-Utf8 $EnvFile (($lines -join "`r`n") + "`r`n")
    # 비밀값 파일 — 관리자·SYSTEM 만 읽는다.
    & icacls $EnvFile /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null
    Write-Warning "HTTPS 앞단(역방향 프록시)을 두면 ONTOBOT_COOKIE_SECURE=true 와 ALLOWED_ORIGINS 를 고치세요(INSTALL.md 5절)"
}

function Initialize-NotifyConfig {
    # 처음 설치할 때만 견본을 복사한다(모든 채널이 꺼진 상태). 운영자가 사내 게이트웨이를 채운다.
    $target = Join-Path $Shared "notify.yaml"
    if (Test-Path $target) { return }
    Copy-Item (Join-Path $Bundle "app\config\notify.yaml") $target
    Note "메일·SMS 발송 설정 견본: $target (채널이 꺼져 있습니다 — INSTALL.md 5절)"
}

# ---------------------------------------------------------------- 선행 구성 요소
function Get-Installer([string]$Name) {
    $manifest = Get-Manifest
    if ($manifest.app_only) { throw "앱만 담은 번들(-AppOnly)입니다 — -SkipPrerequisites 로 실행하세요" }
    $file = $manifest.prereqs.$Name
    if (-not $file) { return $null }
    return Join-Path $Bundle "installers\$file"
}

function Get-PgBin {
    $svc = Get-CimInstance Win32_Service -Filter "Name='$PgService'" -ErrorAction SilentlyContinue
    if (-not $svc) { throw "PostgreSQL 서비스 $PgService 가 없습니다" }
    if ($svc.PathName -notmatch '"?([^"]+\\bin)\\pg_ctl\.exe') { throw "PostgreSQL bin 경로를 찾지 못했습니다: $($svc.PathName)" }
    return $Matches[1]
}

function Install-Postgres {
    if (Get-Service $PgService -ErrorAction SilentlyContinue) { Note "PostgreSQL($PgService) 있음 — 건너뜀"; return }
    Step "PostgreSQL 설치(무인)"
    $super = Read-Host "PostgreSQL 슈퍼유저(postgres) 비밀번호를 정하세요" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($super))
    Invoke-Native (Get-Installer "postgresql") @(
        "--mode", "unattended", "--unattendedmodeui", "none",
        "--superpassword", $plain, "--serverport", "$PgPort", "--servicename", $PgService,
        "--datadir", (Join-Path $Root "pgdata"), "--disable-components", "stackbuilder")
    $script:PgSuperPassword = $plain
}

function Initialize-Database {
    Step "앱 DB 역할·데이터베이스"
    $bin = Get-PgBin
    $psql = Join-Path $bin "psql.exe"
    if (-not $script:PgSuperPassword) {
        $super = Read-Host "PostgreSQL 슈퍼유저(postgres) 비밀번호" -AsSecureString
        $script:PgSuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($super))
    }
    $user = Get-EnvValue "ONTOBOT_DB_USER"
    $db = Get-EnvValue "ONTOBOT_DB_NAME"
    $pw = Get-EnvValue "ONTOBOT_DB_PASSWORD"
    if ($user -notmatch '^[a-z_][a-z0-9_]*$' -or $db -notmatch '^[a-z_][a-z0-9_]*$') { throw "DB 사용자·이름은 소문자 식별자여야 합니다" }
    if ($pw -notmatch '^[A-Za-z0-9]+$') { throw "ONTOBOT_DB_PASSWORD 는 영숫자여야 합니다(설치가 SQL 에 넣는다)" }
    $envs = @{ PGPASSWORD = $script:PgSuperPassword; PGCLIENTENCODING = "UTF8" }
    $base = @("-h", "localhost", "-p", "$PgPort", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-tA")
    $hasRole = Invoke-PsqlScalar $psql ($base + @("-d", "postgres", "-c", "SELECT 1 FROM pg_roles WHERE rolname = '$user'")) $envs
    if ($hasRole -ne "1") {
        Invoke-Native $psql ($base + @("-d", "postgres", "-c", "CREATE ROLE $user LOGIN PASSWORD '$pw'")) $Root $envs
    }
    $hasDb = Invoke-PsqlScalar $psql ($base + @("-d", "postgres", "-c", "SELECT 1 FROM pg_database WHERE datname = '$db'")) $envs
    if ($hasDb -ne "1") {
        Invoke-Native $psql ($base + @("-d", "postgres", "-c", "CREATE DATABASE $db OWNER $user ENCODING 'UTF8' TEMPLATE template0")) $Root $envs
    }
    $pgv = Get-Installer "pgvector"
    if ($pgv) {
        $pgRoot = Split-Path $bin -Parent
        Copy-Item (Join-Path $pgv "lib\*") (Join-Path $pgRoot "lib") -Force
        Copy-Item (Join-Path $pgv "share\extension\*") (Join-Path $pgRoot "share\extension") -Force
        Invoke-Native $psql ($base + @("-d", $db, "-c", "CREATE EXTENSION IF NOT EXISTS vector")) $Root $envs
        Note "pgvector 확장 설치"
    }
}

function Expand-Single([string]$Zip, [string]$Dest, [string]$Marker) {
    # zip 안의 최상위 폴더 하나를 $Dest 로 옮긴다. $Marker 는 그 폴더에 있어야 할 파일.
    $tmp = Join-Path $Root ("_unzip_" + [Guid]::NewGuid().ToString("n"))
    Expand-Archive -Path $Zip -DestinationPath $tmp
    $top = Get-ChildItem $tmp -Recurse -Filter (Split-Path $Marker -Leaf) -File |
        Where-Object { $_.FullName.EndsWith($Marker) } | Select-Object -First 1
    if (-not $top) { throw "$Zip 에서 $Marker 를 찾지 못했습니다" }
    $dir = $top.FullName.Substring(0, $top.FullName.Length - $Marker.Length).TrimEnd("\")
    Move-Item $dir $Dest
    Remove-Item $tmp -Recurse -Force
}

function Set-ConfLines([string]$Path, [hashtable]$Values) {
    # 같은 키를 두 번 적으면 Neo4j 가 기동하지 않는다 — 기존 줄을 주석으로 돌리고 끝에 모아 적는다.
    $lines = Get-Content $Path -Encoding UTF8
    $out = foreach ($l in $lines) {
        $hit = $false
        foreach ($k in $Values.Keys) { if ($l -match "^\s*$([regex]::Escape($k))\s*=") { $hit = $true } }
        if ($hit) { "#(ontobot) $l" } else { $l }
    }
    $out += ""
    $out += "# --- OntoBot (install.ps1)"
    foreach ($k in ($Values.Keys | Sort-Object)) { $out += "$k=$($Values[$k])" }
    Write-Utf8 $Path (($out -join "`r`n") + "`r`n")
}

function Install-Neo4j {
    if (Get-Service $Neo4jService -ErrorAction SilentlyContinue) { Note "Neo4j($Neo4jService) 있음 — 건너뜀"; return }
    Step "JRE · Neo4j Community"
    $jre = Join-Path $Root "jre"
    if (-not (Test-Path (Join-Path $jre "bin\java.exe"))) { Expand-Single (Get-Installer "jre") $jre "bin\java.exe" }
    $neo = Join-Path $Root "neo4j"
    if (-not (Test-Path (Join-Path $neo "bin\neo4j.bat"))) { Expand-Single (Get-Installer "neo4j") $neo "bin\neo4j.bat" }
    Set-ConfLines (Join-Path $neo "conf\neo4j.conf") @{
        "server.default_listen_address"  = "127.0.0.1"
        "server.bolt.listen_address"     = ":7687"
        "server.http.listen_address"     = ":7474"
        "server.https.enabled"           = "false"
        "dbms.usage_report.enabled"      = "false"
        "server.memory.heap.initial_size" = $Neo4jHeap
        "server.memory.heap.max_size"    = $Neo4jHeap
        "server.memory.pagecache.size"   = $Neo4jPageCache
        "server.windows_service_name"    = $Neo4jService
    }
    $envs = @{ JAVA_HOME = $jre; NEO4J_HOME = $neo }
    # 첫 기동 전에만 효과가 있다(deploy/windows/README.md).
    Invoke-Native (Join-Path $neo "bin\neo4j-admin.bat") @("dbms", "set-initial-password", (Get-EnvValue "ONTOBOT_NEO4J_PASSWORD")) $neo $envs
    Invoke-Native (Join-Path $neo "bin\neo4j.bat") @("windows-service", "install") $neo $envs
    Set-Service $Neo4jService -StartupType Automatic
    Start-Service $Neo4jService
}

function Install-WinswService([string]$Id) {
    $winsw = Get-Installer "winsw"
    if (-not $winsw) { throw "번들에 WinSW 가 없습니다" }
    New-Item -ItemType Directory -Force $ServiceDir, (Join-Path $Root "logs\service") | Out-Null
    $exe = Join-Path $ServiceDir "$Id.exe"
    $xml = Join-Path $ServiceDir "$Id.xml"
    Copy-Item $winsw $exe -Force
    $text = Get-Content (Join-Path $Bundle "services\$Id.xml") -Raw -Encoding UTF8
    $text = $text.Replace("{{ROOT}}", $Root).Replace("{{API_HOST}}", $ApiHost).Replace("{{API_PORT}}", "$ApiPort").Replace("{{PG_SERVICE}}", $PgService)
    Write-Utf8 $xml $text
    if (-not (Get-Service $Id -ErrorAction SilentlyContinue)) { Invoke-Native $exe @("install") $ServiceDir }
    else { Note "$Id 서비스 있음 — 설정(XML)만 갱신" }
}

function Install-Ollama {
    if (Get-Service "ontobot-ollama" -ErrorAction SilentlyContinue) { Note "Ollama 서비스 있음 — 건너뜀"; return }
    Step "Ollama(독립 실행본) · 모델"
    $dir = Join-Path $Root "ollama"
    if (-not (Test-Path (Join-Path $dir "ollama.exe"))) {
        New-Item -ItemType Directory -Force $dir | Out-Null
        Expand-Archive -Path (Get-Installer "ollama") -DestinationPath $dir
    }
    & robocopy (Join-Path $Bundle "ollama-models") (Join-Path $Root "ollama-models") /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "모델 복사 실패($LASTEXITCODE)" }
    Install-WinswService "ontobot-ollama"
    Start-Service "ontobot-ollama"
}

# ---------------------------------------------------------------- 앱 릴리스
function Install-Release([string]$Version) {
    $rel = Join-Path $Root "releases\$Version"
    # 끝까지 만든 릴리스에만 표시 파일이 있다 — 중간에 실패한 폴더는 지우고 다시 만든다.
    $done = Join-Path $rel ".installed"
    if (Test-Path $done) { Note "릴리스 $Version 있음 — 다시 만들지 않습니다"; return $rel }
    if (Test-Path $rel) { Remove-Item $rel -Recurse -Force }
    Step "릴리스 $Version → $rel"
    & robocopy (Join-Path $Bundle "app") $rel /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "앱 복사 실패($LASTEXITCODE)" }

    # .env 는 한 곳(shared)에만 — 관리자면 심볼릭 링크, 아니면 복사(-NoServices 검증용).
    $relEnv = Join-Path $rel ".env"
    if (Test-Admin) { New-Item -ItemType SymbolicLink -Path $relEnv -Target $EnvFile | Out-Null }
    else { Copy-Item $EnvFile $relEnv; Write-Warning "관리자가 아니라 .env 를 복사했습니다(링크 아님): $relEnv" }

    if (-not (Test-Path (Join-Path $PythonHome "python.exe"))) {
        Step "CPython $((Get-Manifest).python) → $PythonHome"
        & robocopy (Join-Path $Bundle "python") $PythonHome /E /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "파이썬 복사 실패($LASTEXITCODE)" }
    }
    Step "가상환경(오프라인 — wheelhouse 만)"
    $uv = Join-Path $Bundle "tools\uv.exe"
    $venv = Join-Path $rel "backend\.venv"
    $wheels = Join-Path $Bundle "wheelhouse"
    # uv 캐시를 설치 폴더 안에 둔다(서비스 계정 프로필에 흩어지지 않게).
    $uvEnv = @{ UV_CACHE_DIR = (Join-Path $Root "uv-cache"); UV_PYTHON_DOWNLOADS = "never"; UV_NO_CONFIG = "1" }
    Invoke-Native $uv @("venv", "--python", (Join-Path $PythonHome "python.exe"), $venv) $rel $uvEnv
    $py = Join-Path $venv "Scripts\python.exe"
    $offline = @("--python", $py, "--offline", "--no-index", "--find-links", $wheels)
    Invoke-Native $uv (@("pip", "install") + $offline + @("--require-hashes", "-r", (Join-Path $wheels "requirements.txt"))) $rel $uvEnv
    # 편집 가능 설치 — .env·config 를 릴리스 루트에서 찾는다(ADR-0010).
    Invoke-Native $uv (@("pip", "install") + $offline + @("--no-deps", "-e", (Join-Path $rel "backend"))) $rel $uvEnv
    Invoke-Native $py @("-c", "import ontobot.config as c; print('설정 루트', c._REPO_ROOT)") $rel @{ PYTHONUTF8 = "1" }
    Set-Content -Path $done -Value (Get-Date).ToString("s") -Encoding ASCII
    return $rel
}

function Invoke-Migrate([string]$Rel) {
    Step "마이그레이션(alembic upgrade head)"
    $py = Join-Path $Rel "backend\.venv\Scripts\python.exe"
    Invoke-Native $py @("-m", "alembic", "upgrade", "head") (Join-Path $Rel "backend") @{ PYTHONUTF8 = "1" }
}

function Set-Current([string]$Rel) {
    if (Test-Path $Current) { (Get-Item $Current).Delete() }  # 정션만 지운다(대상은 그대로)
    New-Item -ItemType Junction -Path $Current -Target $Rel | Out-Null
    Note "current → $Rel"
}

function Start-App {
    if ($NoServices) { Note "-NoServices: 서비스를 시작하지 않습니다"; return }
    foreach ($s in $AppServices) { Start-Service $s }
}

function Stop-App {
    if ($NoServices) { return }
    foreach ($s in $AppServices) { if (Get-Service $s -ErrorAction SilentlyContinue) { Stop-Service $s -ErrorAction Continue } }
}

function Get-Readiness {
    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds(10)
    try {
        $res = $client.GetAsync("http://127.0.0.1:$ApiPort/readyz").Result
        return [pscustomobject]@{ Code = [int]$res.StatusCode; Body = $res.Content.ReadAsStringAsync().Result }
    } catch { return $null } finally { $client.Dispose() }
}

function Wait-Ready([int]$Seconds = 120) {
    if ($NoServices) { return $true }
    Step "/readyz 확인(최대 ${Seconds}초)"
    $deadline = (Get-Date).AddSeconds($Seconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Get-Readiness
        if ($last -and $last.Code -eq 200) {
            $body = $last.Body | ConvertFrom-Json
            if ($body.status -eq "ok") { Note "준비됨: $($last.Body)"; return $true }
        }
        Start-Sleep -Seconds 3
    }
    if ($last) { Write-Warning "준비되지 않음($($last.Code)): $($last.Body)" } else { Write-Warning "API 에 닿지 못했습니다" }
    return $false
}

function Invoke-Seed([string]$Rel) {
    Step "샘플 도메인 팩 시드(합성 데이터) · 그래프 재구축"
    $py = Join-Path $Rel "backend\.venv\Scripts\python.exe"
    Invoke-Native $py @((Join-Path $Rel "scripts\seed_domain_pack.py"), "--pack", "sample", "--grant-read", "user", "--grant-read", "admin") $Rel @{ PYTHONUTF8 = "1" }
    Invoke-Native $py @((Join-Path $Rel "scripts\graph_rebuild.py")) $Rel @{ PYTHONUTF8 = "1" }
}

function New-AdminUser([string]$Rel) {
    Step "관리자 계정 ${AdminUsername}(비밀번호는 콘솔에서 입력)"
    $py = Join-Path $Rel "backend\.venv\Scripts\python.exe"
    Invoke-Native $py @("-m", "ontobot.security.cli", "create-user", "--username", $AdminUsername,
        "--display-name", "관리자", "--role", "admin", "--role", "modeler") (Join-Path $Rel "backend") @{ PYTHONUTF8 = "1" }
}

function New-Backup([string]$Label) {
    $bin = Get-PgBin
    New-Item -ItemType Directory -Force (Join-Path $Root "backups") | Out-Null
    $file = Join-Path $Root ("backups\{0:yyyyMMdd-HHmmss}-{1}.dump" -f (Get-Date), ($Label -replace '[^A-Za-z0-9._-]', '_'))
    Step "DB 백업 → $file"
    Invoke-Native (Join-Path $bin "pg_dump.exe") @("-h", (Get-EnvValue "ONTOBOT_DB_HOST"), "-p", (Get-EnvValue "ONTOBOT_DB_PORT"),
        "-U", (Get-EnvValue "ONTOBOT_DB_USER"), "-Fc", "-f", $file, (Get-EnvValue "ONTOBOT_DB_NAME")) $Root @{ PGPASSWORD = (Get-EnvValue "ONTOBOT_DB_PASSWORD") }
    return $file
}

# ---------------------------------------------------------------- 모드
function Invoke-Install {
    if (Read-State) { throw "이미 설치되어 있습니다($StateFile) — -Mode Upgrade 를 쓰세요" }
    $needAdmin = (-not $SkipPrerequisites) -or (-not $NoServices)
    if ($needAdmin -and -not (Test-Admin)) { throw "관리자 PowerShell 에서 실행하세요(서비스 등록·설치본 실행)" }
    Test-Manifest
    New-Item -ItemType Directory -Force $Root, (Join-Path $Root "logs") | Out-Null
    Initialize-EnvFile
    Initialize-NotifyConfig
    if (-not $SkipPrerequisites) {
        Install-Postgres
        Initialize-Database
        Install-Neo4j
        Install-Ollama
    }
    $version = Get-BundleVersion
    $rel = Install-Release $version
    Invoke-Migrate $rel
    Set-Current $rel
    if ($SeedSample) { Invoke-Seed $rel }
    if ($AdminUsername) { New-AdminUser $rel }
    if (-not $NoServices) { foreach ($s in $AppServices) { Install-WinswService $s } }
    Save-State ([ordered]@{ current = $version; previous = $null; last_backup = $null; installed_at = (Get-Date).ToString("s") })
    Start-App
    if (-not (Wait-Ready)) { throw "설치는 끝났지만 준비 확인에 실패했습니다 — $Root\logs 를 보세요" }
    Step "설치 완료: http://$([Net.Dns]::GetHostName()):$ApiPort"
}

function Invoke-Upgrade {
    $state = Read-State
    if (-not $state) { throw "설치 기록이 없습니다 — -Mode Install" }
    if (-not $NoServices -and -not (Test-Admin)) { throw "관리자 PowerShell 에서 실행하세요" }
    Test-Manifest
    $version = Get-BundleVersion
    if ($state.current -eq $version) { throw "이미 $version 입니다" }
    $rel = Install-Release $version            # 서비스가 도는 동안 준비한다(중단 시간 단축)
    $backup = New-Backup "before-$version"
    Stop-App
    try {
        Invoke-Migrate $rel
    } catch {
        Write-Warning "마이그레이션 실패 — 이전 릴리스로 다시 시작합니다. DB 가 일부 바뀌었으면: -Mode Rollback -RestoreDatabase"
        Start-App
        throw
    }
    Set-Current $rel
    Save-State ([ordered]@{ current = $version; previous = $state.current; last_backup = $backup; upgraded_at = (Get-Date).ToString("s") })
    if ($NoServices) { Note "-NoServices: 서비스 설정을 갱신하지 않습니다" } else { foreach ($s in $AppServices) { Install-WinswService $s } }
    Start-App
    if (-not (Wait-Ready)) { Write-Warning "준비 확인 실패 — 되돌리려면: .\install.ps1 -Mode Rollback [-RestoreDatabase]"; exit 1 }
    Step "업그레이드 완료: $($state.current) → $version"
}

function Invoke-Rollback {
    $state = Read-State
    if (-not $state -or -not $state.previous) { throw "되돌릴 이전 릴리스가 없습니다" }
    if (-not $NoServices -and -not (Test-Admin)) { throw "관리자 PowerShell 에서 실행하세요" }
    $prev = Join-Path $Root "releases\$($state.previous)"
    if (-not (Test-Path $prev)) { throw "이전 릴리스 폴더가 없습니다: $prev" }
    if ($RestoreDatabase) {
        if (-not $state.last_backup -or -not (Test-Path $state.last_backup)) { throw "업그레이드 직전 백업이 없습니다" }
        Write-Warning "DB 를 $($state.last_backup) 시점으로 되돌립니다 — 업그레이드 뒤에 쓰인 데이터(대화·감사 로그 포함)는 사라집니다"
        if ((Read-Host "계속하려면 RESTORE 를 입력") -ne "RESTORE") { throw "취소했습니다" }
    }
    Stop-App
    if ($RestoreDatabase) {
        Step "DB 복원"
        $bin = Get-PgBin
        if (-not $script:PgSuperPassword) {
            $super = Read-Host "PostgreSQL 슈퍼유저(postgres) 비밀번호" -AsSecureString
            $script:PgSuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($super))
        }
        $db = Get-EnvValue "ONTOBOT_DB_NAME"
        $owner = Get-EnvValue "ONTOBOT_DB_USER"
        $psql = Join-Path $bin "psql.exe"
        $envs = @{ PGPASSWORD = $script:PgSuperPassword }
        # 감사 로그는 트리거로 지울 수 없으므로 덮어쓰기(--clean) 대신 DB 를 새로 만든다.
        $base = @("-h", "localhost", "-p", "$PgPort", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-d", "postgres")
        Invoke-Native $psql ($base + @("-c", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid()")) $Root $envs
        Invoke-Native $psql ($base + @("-c", "DROP DATABASE $db")) $Root $envs
        Invoke-Native $psql ($base + @("-c", "CREATE DATABASE $db OWNER $owner ENCODING 'UTF8' TEMPLATE template0")) $Root $envs
        Invoke-Native (Join-Path $bin "pg_restore.exe") @("-h", "localhost", "-p", "$PgPort", "-U", "postgres", "--no-owner", "--role", $owner, "-d", $db, $state.last_backup) $Root $envs
    }
    Set-Current $prev
    Save-State ([ordered]@{ current = $state.previous; previous = $null; last_backup = $state.last_backup; rolled_back_from = $state.current; rolled_back_at = (Get-Date).ToString("s") })
    Start-App
    [void](Wait-Ready)
    Step "롤백 완료: $($state.current) → $($state.previous)"
}

function Show-Status {
    $state = Read-State
    if (-not $state) { Note "설치 기록 없음($StateFile)"; return }
    Step "설치 상태"
    $state | Format-List | Out-String | Write-Host
    $names = @($PgService, $Neo4jService, "ontobot-ollama") + $AppServices
    Get-Service $names -ErrorAction SilentlyContinue | Format-Table Status, Name, StartType -AutoSize | Out-String | Write-Host
    $r = Get-Readiness
    if ($r) { Note "/readyz $($r.Code): $($r.Body)" } else { Note "/readyz: API 에 닿지 못함" }
}

switch ($Mode) {
    "Install" { Invoke-Install }
    "Upgrade" { Invoke-Upgrade }
    "Rollback" { Invoke-Rollback }
    "Status" { Show-Status }
}
