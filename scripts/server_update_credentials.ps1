# Windows-only helpers. Passwords are serialized as DPAPI-protected SecureStrings.
function Get-UpdateProfile([string]$Directory) {
    $path = Join-Path $Directory "connection.json"
    if (Test-Path -LiteralPath $path) {
        return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    return $null
}

function Initialize-UpdateAskpass([string]$Directory) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Saved passwords are supported only on Windows."
    }
    New-Item -ItemType Directory -Path $Directory -Force > $null
    $source = Join-Path $PSScriptRoot "server_update_askpass.cs"
    $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $exe = Join-Path $Directory "askpass-$hash.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        # The .bat entry points use Windows PowerShell 5.1 / .NET Framework.
        if ($PSVersionTable.PSEdition -ne "Desktop") {
            throw "Run setup_server_update.bat with Windows PowerShell to initialize saved passwords."
        }
        Add-Type -Path $source -OutputAssembly $exe -OutputType ConsoleApplication -IgnoreWarnings -WarningAction SilentlyContinue
    }
    return $exe
}

function Save-UpdateProfile([string]$Directory, $Profile, [Security.SecureString]$Password) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Saved passwords are supported only on Windows."
    }
    New-Item -ItemType Directory -Path $Directory -Force > $null
    $credentialPath = Join-Path $Directory "credential.clixml"
    if ($Password.Length -gt 0) {
        $null = Initialize-UpdateAskpass $Directory
        $record = [pscustomobject]@{
            Target = $Profile.Target
            Port = [int]$Profile.Port
            Credential = [Management.Automation.PSCredential]::new($Profile.Target, $Password)
        }
        $record | Export-Clixml -LiteralPath "$credentialPath.tmp" -Depth 5
        Move-Item -LiteralPath "$credentialPath.tmp" -Destination $credentialPath -Force
    } elseif (Test-Path -LiteralPath $credentialPath) {
        Remove-Item -LiteralPath $credentialPath
    }
    $path = Join-Path $Directory "connection.json"
    [IO.File]::WriteAllText("$path.tmp", ($Profile | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath "$path.tmp" -Destination $path -Force
}

function Test-UpdateSavedPassword([string]$Directory, [string]$Target, [int]$Port) {
    $path = Join-Path $Directory "credential.clixml"
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $record = Import-Clixml -LiteralPath $path
    # Never reuse a saved password with a different command-line host or port.
    return ($record.Target -ceq $Target -and [int]$record.Port -eq $Port -and
            $record.Credential -is [Management.Automation.PSCredential])
}

function Read-UpdateSetting([string]$Label, [string]$Default) {
    $answer = Read-Host "$Label [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}
