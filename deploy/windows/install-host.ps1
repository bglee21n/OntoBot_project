<#
.SYNOPSIS
  OntoBot 호스트 준비(Windows Server 2022 이상, WSL2 + Docker Engine) — 최초 1회(ADR-0013).

.DESCRIPTION
  관리자 PowerShell 에서 번들의 host 폴더로 실행한다. 인터넷을 쓰지 않는다. 하는 일:

    1. OS·관리자 확인, WSL·가상 머신 플랫폼 기능 켜기(필요하면 재부팅 뒤 다시 실행)
    2. WSL 패키지(msi) 설치, Ubuntu rootfs 를 배포판 'ontobot' 으로 가져오기(wsl --import)
    3. 배포판 설정(systemd·root 기본 사용자), .wslconfig(메모리·CPU 상한)
    4. Docker Engine·compose·NVIDIA Container Toolkit .deb 오프라인 설치, GPU 확인
    5. 번들·신뢰 공개키를 배포판의 /opt/ontobot 으로 복사하고 ontobotctl install 실행
    6. 부팅 시 자동 기동 예약 작업(배포판 유지 + 포트 전달 갱신), 방화벽 규칙, 반입 폴더

  WSL 배포판은 사용자별이다 — 이 스크립트를 실행한 계정(-TaskUser)이 배포판의 주인이 되고, 부팅 예약 작업도
  그 계정으로 돈다(계정 비밀번호를 한 번 묻는다). 서비스 전용 로컬 계정을 만들어 그 계정으로 실행하길 권한다.

  주의: 이 스크립트는 개발 PC(Windows 11)에서 실행하지 않았다 — 대상 OS 에서 0단계 점검(ADR-0013)을 먼저 한다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\host\install-host.ps1 -HostAssets E:\ontobot-host-assets `
      -TrustedKey E:\keys\ontobot-release.pub -AdminUsername admin -SeedSample -AllowCidrs 10.10.0.0/24
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HostAssets,
    [Parameter(Mandatory = $true)][string[]]$TrustedKey,
    [string]$Bundle = (Split-Path $PSScriptRoot -Parent),
    [string]$Distro = "ontobot",
    [string]$Root = "C:\OntoBot",
    [string]$DropDir = "C:\OntoBot-bundles",
    [int]$MemoryGB = 12,
    [int]$Processors = 0,
    [int]$ApiPort = 8000,
    [int]$DeployerPort = 8100,
    [string]$AllowCidrs = "",
    [string]$AdminUsername,
    [switch]$SeedSample,
    [string]$TaskUser = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

function Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Note([string]$msg) { Write-Host "    $msg" }

function Invoke-Wsl {
    # 배포판 안에서 root 로 bash 명령. 실패하면 멈춘다(5.1 은 네이티브 실패에도 계속 가므로 종료 코드를 본다).
    param([string]$Command)
    & wsl.exe -d $Distro -u root -- bash -lc $Command | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "WSL 명령 실패($LASTEXITCODE): $Command" }
}

function To-WslPath([string]$WinPath) {
    $p = (Resolve-Path $WinPath).Path
    $drive = $p.Substring(0, 1).ToLowerInvariant()
    return "/mnt/$drive" + ($p.Substring(2) -replace '\\', '/')
}

function Find-One([string]$Pattern) {
    $found = @(Get-ChildItem -Path $HostAssets -Filter $Pattern -File)
    if ($found.Count -ne 1) { throw "호스트 자산 '$Pattern' 이 하나여야 합니다($($found.Count)개): $HostAssets" }
    return $found[0].FullName
}

# ---------------------------------------------------------------- 1. 확인·기능
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "관리자 PowerShell 에서 실행하세요" }
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 20348) { throw "Windows Server 2022(빌드 20348) 이상이 필요합니다 — 이 서버는 빌드 ${build}입니다(WSL2 미지원)" }
if (-not (Test-Path (Join-Path $Bundle "bundle.env"))) { throw "번들 폴더가 아닙니다: $Bundle" }

Step "WSL·가상 머신 플랫폼 기능"
$restart = $false
foreach ($f in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    if ($state -ne "Enabled") {
        $r = Enable-WindowsOptionalFeature -Online -FeatureName $f -NoRestart -All
        if ($r.RestartNeeded) { $restart = $true }
        Note "$f 켬"
    }
}
if ($restart) {
    Write-Warning "재부팅이 필요합니다. 재부팅한 뒤 같은 명령으로 다시 실행하세요."
    exit 3
}

# ---------------------------------------------------------------- 2. WSL·배포판
Step "WSL 패키지"
& wsl.exe --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    $msi = Find-One "wsl*.msi"
    $p = Start-Process msiexec.exe -ArgumentList @("/i", "`"$msi`"", "/qn", "/norestart") -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "WSL 설치 실패($($p.ExitCode))" }
}
& wsl.exe --set-default-version 2 | Out-Null

Step ".wslconfig(메모리 ${MemoryGB}GB)"
$cfg = "[wsl2]`r`nmemory=${MemoryGB}GB`r`n"
if ($Processors -gt 0) { $cfg += "processors=$Processors`r`n" }
# 도커 볼륨이 커져도 Windows 쪽 가상 디스크가 자동으로 줄지 않는다 — 스파스 모드는 쓰지 않는다(안정성 우선).
$cfg += "vmIdleTimeout=-1`r`n"
[IO.File]::WriteAllText((Join-Path $env:USERPROFILE ".wslconfig"), $cfg, (New-Object Text.UTF8Encoding $false))

$distros = @(& wsl.exe -l -q | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
if ($distros -notcontains $Distro) {
    Step "배포판 $Distro 가져오기"
    $rootfs = Find-One "*ubuntu*.tar*"
    New-Item -ItemType Directory -Force (Join-Path $Root "wsl") | Out-Null
    & wsl.exe --import $Distro (Join-Path $Root "wsl") $rootfs --version 2 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "wsl --import 실패" }
}

Step "배포판 설정(systemd)"
Invoke-Wsl "printf '[boot]\nsystemd=true\n[user]\ndefault=root\n[interop]\nappendWindowsPath=false\n' > /etc/wsl.conf"
& wsl.exe --terminate $Distro | Out-Null
Invoke-Wsl "systemctl is-system-running --wait || true"

# ---------------------------------------------------------------- 3. Docker·GPU
Step "Docker Engine·compose·NVIDIA Container Toolkit(오프라인 .deb)"
$debs = To-WslPath (Join-Path $HostAssets "debs")
Invoke-Wsl "command -v docker >/dev/null || (dpkg -i $debs/*.deb || (echo '의존 패키지 부족 — debs 폴더를 확인하세요' >&2; exit 1))"
Invoke-Wsl "systemctl enable --now docker containerd"
& wsl.exe -d $Distro -u root -- bash -lc "test -x /usr/lib/wsl/lib/nvidia-smi && /usr/lib/wsl/lib/nvidia-smi -L" | Out-Host
if ($LASTEXITCODE -eq 0) {
    Invoke-Wsl "command -v nvidia-ctk && nvidia-ctk runtime configure --runtime=docker && systemctl restart docker"
    Note "GPU 사용"
} else {
    Write-Warning "GPU 를 찾지 못했습니다(NVIDIA 드라이버·WSL GPU 지원 확인) — 모델이 CPU 로 돌아 느립니다"
}

# ---------------------------------------------------------------- 4. 번들·설치
Step "번들·신뢰 공개키 → /opt/ontobot"
$name = Split-Path $Bundle -Leaf
Invoke-Wsl "mkdir -p /opt/ontobot/bundles /opt/ontobot/trusted-keys && chmod 700 /opt/ontobot"
Invoke-Wsl "rm -rf /opt/ontobot/bundles/$name && cp -a '$(To-WslPath $Bundle)' /opt/ontobot/bundles/$name"
foreach ($k in $TrustedKey) {
    Invoke-Wsl "cp '$(To-WslPath $k)' /opt/ontobot/trusted-keys/ && chmod 644 /opt/ontobot/trusted-keys/*.pub"
}
Invoke-Wsl "install -m 755 /opt/ontobot/bundles/$name/bin/ontobotctl /usr/local/bin/ontobotctl"

New-Item -ItemType Directory -Force $DropDir, (Join-Path $Root "backups") | Out-Null
$installArgs = @("install", "/opt/ontobot/bundles/$name")
if ($SeedSample) { $installArgs += "--seed-sample" }
# 설치 설정은 WSLENV 로 넘긴다(명령줄에 비밀번호가 남지 않게).
$env:ONTOBOT_API_PORT = "$ApiPort"
$env:ONTOBOT_DEPLOYER_PORT = "$DeployerPort"
$env:ONTOBOT_DEPLOYER_ALLOW_CIDRS = $AllowCidrs
$env:ONTOBOT_DEPLOYER_DROP_HOST_DIR = (To-WslPath $DropDir)
$env:ONTOBOT_BACKUP_COPY_DIR = (To-WslPath (Join-Path $Root "backups"))
$pass = @("ONTOBOT_API_PORT", "ONTOBOT_DEPLOYER_PORT", "ONTOBOT_DEPLOYER_ALLOW_CIDRS", "ONTOBOT_DEPLOYER_DROP_HOST_DIR", "ONTOBOT_BACKUP_COPY_DIR")
if ($AdminUsername) {
    $installArgs += @("--admin", $AdminUsername)
    $sec = Read-Host "OntoBot 관리자 '$AdminUsername' 비밀번호(12자 이상)" -AsSecureString
    $env:ONTOBOT_NEW_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    $pass += "ONTOBOT_NEW_PASSWORD"
}
$env:WSLENV = (($pass | ForEach-Object { "$_/u" }) -join ":")
try {
    Step "ontobotctl install"
    & wsl.exe -d $Distro -u root -- ontobotctl --non-interactive @installArgs | Out-Host
    $code = $LASTEXITCODE
} finally {
    Remove-Item Env:ONTOBOT_NEW_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:WSLENV -ErrorAction SilentlyContinue
}
if ($code -eq 1) { throw "ontobotctl install 실패 — 위 출력을 보세요" }

# ---------------------------------------------------------------- 5. 부팅 자동 기동·포트·방화벽
Step "부팅 시 자동 기동(예약 작업 OntoBot-Host)"
$hostDir = Join-Path $Root "host"
New-Item -ItemType Directory -Force $hostDir | Out-Null
$startScript = @"
# OntoBot 부팅 스크립트 — 배포판을 깨워 두고, 바뀐 WSL 주소로 포트 전달을 다시 건다(install-host.ps1 이 만든다).
`$ErrorActionPreference = 'Continue'
Start-Process wsl.exe -ArgumentList @('-d','$Distro','-u','root','--','sleep','infinity') -WindowStyle Hidden
for (`$i = 0; `$i -lt 60; `$i++) {
    `$ip = (& wsl.exe -d $Distro -u root -- hostname -I).Trim().Split(' ')[0]
    if (`$ip) { break }
    Start-Sleep -Seconds 2
}
foreach (`$port in @($ApiPort, $DeployerPort)) {
    netsh interface portproxy delete v4tov4 listenport=`$port listenaddress=0.0.0.0 | Out-Null
    netsh interface portproxy add v4tov4 listenport=`$port listenaddress=0.0.0.0 connectport=`$port connectaddress=`$ip | Out-Null
}
"@
$startPath = Join-Path $hostDir "start-ontobot.ps1"
[IO.File]::WriteAllText($startPath, $startScript, (New-Object Text.UTF8Encoding $true))
$cred = Get-Credential -UserName $TaskUser -Message "부팅 예약 작업을 실행할 계정($TaskUser — 배포판 주인)의 비밀번호"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$startPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "OntoBot-Host" -Action $action -Trigger $trigger -Settings $settings `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName "OntoBot-Host"

Step "방화벽(api $ApiPort · 배포 콘솔 $DeployerPort)"
Get-NetFirewallRule -DisplayName "OntoBot*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "OntoBot API" -Direction Inbound -Protocol TCP -LocalPort $ApiPort -Action Allow | Out-Null
$remote = if ($AllowCidrs) { $AllowCidrs.Split(",") | ForEach-Object { $_.Trim() } } else { @("LocalSubnet") }
New-NetFirewallRule -DisplayName "OntoBot 배포 콘솔" -Direction Inbound -Protocol TCP -LocalPort $DeployerPort `
    -RemoteAddress $remote -Action Allow | Out-Null

Step "완료"
Note "OntoBot:      http://$([Net.Dns]::GetHostName()):$ApiPort"
Note "배포 콘솔:    http://$([Net.Dns]::GetHostName()):$DeployerPort  (관리자 대역에서만)"
Note "반입 폴더:    $DropDir   백업 복사본: $(Join-Path $Root 'backups')"
if ($code -eq 2) { Write-Warning "설치는 끝났지만 준비 확인을 통과하지 못했습니다 — 배포 콘솔의 현황을 보세요" }
