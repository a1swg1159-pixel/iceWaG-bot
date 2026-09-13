param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host "[SETUP] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
    return $values
}

function Set-DotEnvValue([string]$Path, [string]$Name, [string]$Value) {
    $lines = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            $lines.Add($line)
        }
    }
    $prefix = "$Name="
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index].StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            $lines[$index] = "$Name=$Value"
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        $lines.Add("$Name=$Value")
    }
    [IO.File]::WriteAllLines($Path, $lines, [Text.UTF8Encoding]::new($false))
}

function Find-Conda {
    $candidates = @()
    if ($env:CONDA_EXE) {
        $candidates += $env:CONDA_EXE
    }
    $command = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($command) {
        $candidates += $command.Source
    }
    $candidates += @(
        (Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"),
        (Join-Path $env:LOCALAPPDATA "anaconda3\Scripts\conda.exe"),
        (Join-Path $env:LOCALAPPDATA "miniconda3\Scripts\conda.exe"),
        "C:\ProgramData\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "D:\anaconda3\Scripts\conda.exe",
        "D:\miniconda3\Scripts\conda.exe"
    )
    return $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

function Find-NapCatBoot([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root)) {
        return $null
    }
    foreach ($boot in Get-ChildItem -LiteralPath $Root -Filter "NapCatWinBootMain.exe" -Recurse -File -ErrorAction SilentlyContinue) {
        $versionsDir = Join-Path $boot.Directory.FullName "versions"
        $versionConfigPath = Join-Path $versionsDir "config.json"
        if (-not (Test-Path -LiteralPath $versionConfigPath)) {
            continue
        }
        try {
            $versionConfig = Get-Content -LiteralPath $versionConfigPath -Raw | ConvertFrom-Json
            $appDir = Join-Path (Join-Path $versionsDir $versionConfig.curVersion) "resources\app"
            $packagePath = Join-Path $appDir "package.json"
            $napCatMain = Join-Path $appDir "napcat\napcat.mjs"
            if ((Test-Path -LiteralPath $packagePath) -and (Test-Path -LiteralPath $napCatMain)) {
                $packageConfig = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
                if ($packageConfig.main -eq "./napcat/napcat.mjs") {
                    return $boot
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Get-NapCatRuntimeInfo([IO.FileInfo]$Boot) {
    $versionConfigPath = Join-Path $Boot.Directory.FullName "versions\config.json"
    if (-not (Test-Path -LiteralPath $versionConfigPath)) {
        return $null
    }

    try {
        $versionConfig = Get-Content -LiteralPath $versionConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $versionConfig.curVersion) {
            return $null
        }
        $napCatAppDir = Join-Path $Boot.Directory.FullName "versions\$($versionConfig.curVersion)\resources\app\napcat"
        return [pscustomobject]@{
            ConfigDir = Join-Path $napCatAppDir "config"
            QrCodePath = Join-Path $napCatAppDir "cache\qrcode.png"
            WebUiConfigPath = Join-Path $napCatAppDir "config\webui.json"
        }
    } catch {
        return $null
    }
}

function Get-NapCatWebUiUrl($RuntimeInfo) {
    $url = "http://127.0.0.1:6099/webui"
    if (-not $RuntimeInfo -or -not (Test-Path -LiteralPath $RuntimeInfo.WebUiConfigPath)) {
        return $url
    }

    try {
        $config = Get-Content -LiteralPath $RuntimeInfo.WebUiConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($config.port) {
            $url = "http://127.0.0.1:$($config.port)/webui"
        }
        if ($config.token) {
            $encodedToken = [Uri]::EscapeDataString([string]$config.token)
            $url = "$url`?token=$encodedToken"
        }
    } catch {
        # The default URL remains usable if NapCat has not created its config yet.
    }
    return $url
}

function Show-NapCatLogin($RuntimeInfo, [Nullable[DateTime]]$PreviousQrWriteTime) {
    $webUiUrl = Get-NapCatWebUiUrl $RuntimeInfo
    $webUiDisplayUrl = $webUiUrl.Split("?", 2)[0]
    $webUiDeadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $webUiDeadline) {
        try {
            Invoke-WebRequest -Uri $webUiUrl -UseBasicParsing -TimeoutSec 1 | Out-Null
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    Write-Host "WebUI: $webUiDisplayUrl (local token applied automatically)" -ForegroundColor Yellow
    Start-Process $webUiUrl

    if (-not $RuntimeInfo) {
        return
    }

    # A QR code is normally written within a few seconds. Open the PNG itself so
    # login never depends on terminal fonts or Unicode block-character rendering.
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $RuntimeInfo.QrCodePath) {
            $qrFile = Get-Item -LiteralPath $RuntimeInfo.QrCodePath
            $isFresh = (-not $PreviousQrWriteTime.HasValue) -or
                ($qrFile.LastWriteTimeUtc -gt $PreviousQrWriteTime.Value) -or
                ($qrFile.LastWriteTimeUtc -gt (Get-Date).ToUniversalTime().AddMinutes(-2))
            if ($isFresh) {
                Write-Ok "Opening the QQ login QR code as an image."
                Start-Process -FilePath $qrFile.FullName
                return
            }
        }
        Start-Sleep -Milliseconds 500
    }

    Write-Ok "No new QR code was created; QQ may already be logged in."
}

function Complete-NapCatInjection([string]$InstallDir, [string]$ShellPackage) {
    $versionConfigPath = Join-Path $InstallDir "versions\config.json"
    if (-not (Test-Path -LiteralPath $versionConfigPath)) {
        throw "QQ version config was not created at $versionConfigPath."
    }
    $versionConfig = Get-Content -LiteralPath $versionConfigPath -Raw | ConvertFrom-Json
    $currentVersion = $versionConfig.curVersion
    if (-not $currentVersion) {
        throw "QQ version config does not contain curVersion."
    }

    $appDir = Join-Path (Join-Path $InstallDir "versions\$currentVersion") "resources\app"
    $packagePath = Join-Path $appDir "package.json"
    if (-not (Test-Path -LiteralPath $packagePath)) {
        throw "Current QQ package.json was not found at $packagePath."
    }

    $napCatDir = Join-Path $appDir "napcat"
    New-Item -ItemType Directory -Path $napCatDir -Force | Out-Null
    Expand-Archive -LiteralPath $ShellPackage -DestinationPath $napCatDir -Force

    $packageConfig = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
    $packageConfig.main = "./napcat/napcat.mjs"
    $packageJson = $packageConfig | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($packagePath, $packageJson, [Text.UTF8Encoding]::new($false))
    Write-Ok "NapCat injected into QQ $currentVersion."
}

function Install-NapCat([string]$Root) {
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    $installer = Get-ChildItem -LiteralPath $Root -Filter "NapCatInstaller.exe" -Recurse -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $installer) {
        Write-Step "Downloading the latest official NapCat Windows OneKey package..."
        $oneKeyZip = Join-Path ([IO.Path]::GetTempPath()) "NapCat.Shell.Windows.OneKey.zip"
        $oneKeyUrl = "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.Windows.OneKey.zip"
        Invoke-WebRequest -Uri $oneKeyUrl -OutFile $oneKeyZip -UseBasicParsing
        Expand-Archive -LiteralPath $oneKeyZip -DestinationPath $Root -Force
        Remove-Item -LiteralPath $oneKeyZip -Force
        $installer = Get-ChildItem -LiteralPath $Root -Filter "NapCatInstaller.exe" -Recurse -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if (-not $installer) {
        throw "NapCatInstaller.exe was not found after extracting the OneKey package."
    }

    # The OneKey installer still contains a removed QQ 9.9.26 URL. Supplying
    # both input files locally makes it skip that stale download path.
    $installerDir = $installer.Directory.FullName
    $qqPackage = Join-Path $installerDir "QQ.exe"
    $shellPackage = Join-Path $installerDir "NapCat.Shell.zip"
    $qqDownloadUrl = "https://qqdl.gtimg.cn/qqfile/QQNT/9.9.33/release/497e2f1f/QQ_9.9.33_260813_x64_01.exe"
    $shellDownloadUrl = "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip"

    if (-not (Test-Path -LiteralPath $qqPackage) -or (Get-Item -LiteralPath $qqPackage).Length -lt 50MB) {
        Write-Step "Downloading the NapCat-compatible QQ package from Tencent..."
        Invoke-WebRequest -Uri $qqDownloadUrl -OutFile $qqPackage -UseBasicParsing
    } else {
        Write-Ok "Compatible QQ package is already cached."
    }
    if (-not (Test-Path -LiteralPath $shellPackage) -or (Get-Item -LiteralPath $shellPackage).Length -lt 1MB) {
        Write-Step "Downloading the latest official NapCat Shell package..."
        Invoke-WebRequest -Uri $shellDownloadUrl -OutFile $shellPackage -UseBasicParsing
    } else {
        Write-Ok "NapCat Shell package is already cached."
    }

    Write-Step "Running the official NapCat installer with local packages..."
    $process = Start-Process -FilePath $installer.FullName -WorkingDirectory $installerDir -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "NapCatInstaller exited with code $($process.ExitCode)."
    }

    $installDir = Get-ChildItem -LiteralPath $installerDir -Directory -Filter "NapCat.*.Shell" -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "versions\config.json") } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $installDir) {
        throw "The OneKey installer did not create a NapCat.*.Shell directory."
    }
    Complete-NapCatInjection $installDir.FullName $shellPackage

    $boot = Find-NapCatBoot $Root
    if (-not $boot) {
        throw "NapCat installation finished, but NapCatWinBootMain.exe was not found under $Root."
    }
    return $boot
}

function Stop-StaleNoneBot([int]$Port) {
    # Free the OneBot reverse-WS port and kill leftover bot processes from a
    # previous launch. A stale `python bot.py` holding the port makes the new
    # NoneBot fail with "[Errno 10048] ... 只允许使用一次".
    $killed = $false

    # 1) Kill whatever is listening on the OneBot port (a stale uvicorn/NoneBot).
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { $_.OwningProcess } |
        Sort-Object -Unique |
        ForEach-Object {
            $proc = Get-Process -Id $_ -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -match "python|conda") {
                Write-Step "Stopping leftover bot process (PID $_) holding port $Port..."
                Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
                $killed = $true
            }
        }

    # 2) Kill leftover bot python that may linger even without the port.
    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path -like "*envs\bot\python*" } |
        ForEach-Object {
            Write-Step "Stopping leftover bot python (PID $($_.Id))..."
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            $killed = $true
        }

    if ($killed) {
        Start-Sleep -Milliseconds 800
    }
}

try {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host "  QQ Bot full local launcher (NapCat + NoneBot)" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host ""

    $conda = Find-Conda
    if (-not $conda) {
        throw "Conda was not found. Install Anaconda/Miniconda or run from an Anaconda Prompt."
    }
    Write-Ok "Conda: $conda"

    & $conda run -n bot python --version *> $null
    if ($LASTEXITCODE -ne 0) {
        if ($Check) {
            throw 'Conda environment "bot" is missing.'
        }
        Write-Step 'Creating Conda environment "bot" with Python 3.11...'
        & $conda create -n bot python=3.11 -y
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create the bot Conda environment."
        }
    }
    Write-Ok 'Conda environment "bot" is ready.'

    & $conda run -n bot python -c "import nonebot, requests, PIL, httpx, apscheduler" *> $null
    if ($LASTEXITCODE -ne 0) {
        if ($Check) {
            throw "Python dependencies are incomplete."
        }
        Write-Step "Installing Python dependencies..."
        & $conda run -n bot python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install Python dependencies."
        }
    }
    Write-Ok "Python dependencies are ready."

    $envFile = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        if ($Check) {
            throw ".env is missing."
        }
        $exampleFile = Join-Path $ProjectRoot ".env.example"
        if (-not (Test-Path -LiteralPath $exampleFile)) {
            throw "Both .env and .env.example are missing."
        }
        Copy-Item -LiteralPath $exampleFile -Destination $envFile
        Start-Process notepad.exe -ArgumentList $envFile
        throw "Created .env. Fill in the configuration, save it, and launch again."
    }

    $settings = Read-DotEnv $envFile
    $accessToken = $settings["ONEBOT_V11_ACCESS_TOKEN"]
    if (-not $accessToken -or $accessToken -eq "CHANGE_ME") {
        if ($Check) {
            throw "ONEBOT_V11_ACCESS_TOKEN is missing from .env."
        }
        $randomBytes = New-Object byte[] 24
        $randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
        $randomGenerator.GetBytes($randomBytes)
        $randomGenerator.Dispose()
        $accessToken = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "A").Replace("/", "B")
        Set-DotEnvValue $envFile "ONEBOT_V11_ACCESS_TOKEN" $accessToken
        $settings["ONEBOT_V11_ACCESS_TOKEN"] = $accessToken
        Write-Ok "Generated and saved a OneBot access token."
    }

    $botQQ = $settings["BOT_QQ"]
    if (-not $botQQ -or $botQQ -notmatch "^\d{5,12}$") {
        if ($Check) {
            throw "BOT_QQ is missing from .env."
        }
        do {
            $botQQ = (Read-Host "Enter the QQ number used by the bot").Trim()
        } until ($botQQ -match "^\d{5,12}$")
        Set-DotEnvValue $envFile "BOT_QQ" $botQQ
        $settings["BOT_QQ"] = $botQQ
        Write-Ok "Saved BOT_QQ to .env."
    }

    $port = $settings["PORT"]
    if (-not $port) {
        $port = "8081"
    }
    $reverseWsUrl = "ws://127.0.0.1:$port/onebot/v11/ws"

    $napCatRoot = Join-Path $ProjectRoot "NapCat"
    $napCatBoot = Find-NapCatBoot $napCatRoot
    if (-not $napCatBoot) {
        if ($Check) {
            throw "NapCat is not installed under $napCatRoot."
        }
        $napCatBoot = Install-NapCat $napCatRoot
    }
    Write-Ok "NapCat: $($napCatBoot.FullName)"

    $napCatRuntime = Get-NapCatRuntimeInfo $napCatBoot
    if (-not $napCatRuntime) {
        throw "NapCat runtime directory could not be resolved."
    }
    $napCatConfigDir = $napCatRuntime.ConfigDir
    New-Item -ItemType Directory -Path $napCatConfigDir -Force | Out-Null
    $oneBotConfigPath = Join-Path $napCatConfigDir "onebot11_$botQQ.json"
    $oneBotConfig = [ordered]@{
        network = [ordered]@{
            httpServers = @()
            httpSseServers = @()
            httpClients = @()
            websocketServers = @()
            websocketClients = @(
                [ordered]@{
                    enable = $true
                    name = "local-nonebot"
                    url = $reverseWsUrl
                    reportSelfMessage = $false
                    messagePostFormat = "array"
                    token = $accessToken
                    debug = $false
                    heartInterval = 30000
                    reconnectInterval = 5000
                }
            )
            plugins = @()
        }
        musicSignUrl = ""
        enableLocalFile2Url = $false
        parseMultMsg = $false
        imageDownloadProxy = ""
        timeout = [ordered]@{
            baseTimeout = 10000
            uploadSpeedKBps = 256
            downloadSpeedKBps = 256
            maxTimeout = 1800000
        }
    }
    $json = $oneBotConfig | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($oneBotConfigPath, $json, [Text.UTF8Encoding]::new($false))
    Write-Ok "OneBot reverse WebSocket configured: $reverseWsUrl"

    if ($Check) {
        Write-Ok "Full local environment is ready."
        exit 0
    }

    [Nullable[DateTime]]$previousQrWriteTime = $null
    if ($napCatRuntime -and (Test-Path -LiteralPath $napCatRuntime.QrCodePath)) {
        $previousQrWriteTime = (Get-Item -LiteralPath $napCatRuntime.QrCodePath).LastWriteTimeUtc
    }

    Stop-StaleNoneBot ([int]$port)

    $existingNapCat = Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue
    if (-not $existingNapCat) {
        Write-Step "Starting NapCat for QQ $botQQ..."
        $napCatLauncher = Join-Path $PSScriptRoot "start_napcat_utf8.cmd"
        Start-Process -FilePath $napCatLauncher -ArgumentList @("`"$($napCatBoot.FullName)`"", "`"$botQQ`"") -WorkingDirectory $napCatBoot.Directory.FullName
    } else {
        Write-Ok "NapCat is already running."
    }

    Write-Host ""
    Write-Host "First login: scan the QR image that opens automatically with mobile QQ." -ForegroundColor Yellow
    Write-Host ""
    Show-NapCatLogin $napCatRuntime $previousQrWriteTime

    Write-Step "Starting NoneBot. Press Ctrl+C to stop it."
    $env:PYTHONUTF8 = "1"
    $env:PYTHONUNBUFFERED = "1"
    & $conda run --no-capture-output -n bot python bot.py
    exit $LASTEXITCODE
}
catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
