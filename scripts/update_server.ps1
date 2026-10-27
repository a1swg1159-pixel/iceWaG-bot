param(
    [string]$Target,
    [ValidateRange(0, 65535)][int]$Port,
    [string]$RemotePath = "~/iceWaG-bot",
    [string]$Branch = "main",
    [string]$IdentityFile,
    [switch]$WithWatchdog,
    [switch]$Configure,
    [string]$ConfigDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) ".server-update")
)

$ErrorActionPreference = "Stop"
$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
. (Join-Path $PSScriptRoot "server_update_credentials.ps1")

# POSIX single-quote escaping; values are data inside the encoded payload.
function ConvertTo-ShellLiteral([string]$Value) {
    $quote = [string][char]39
    $escaped = $quote + [char]34 + $quote + [char]34 + $quote
    return $quote + $Value.Replace($quote, $escaped) + $quote
}

try {
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $ConfigDirectory = [IO.Path]::GetFullPath($ConfigDirectory)
    $profile = Get-UpdateProfile $ConfigDirectory
    if ($profile) {
        foreach ($name in @("Target", "Port", "RemotePath", "Branch", "IdentityFile", "WithWatchdog")) {
            if (-not $PSBoundParameters.ContainsKey($name) -and $null -ne $profile.$name) {
                Set-Variable -Name $name -Value $profile.$name
            }
        }
    }
    if ($Configure) {
        $Target = Read-UpdateSetting "SSH server (e.g. a1swg@192.0.2.10)" $Target
        $Port = [int](Read-UpdateSetting "SSH port (0 = SSH config / default 22)" "$Port")
        $RemotePath = Read-UpdateSetting "Server project directory" $RemotePath
        $Branch = Read-UpdateSetting "Git branch" $Branch
    }
    if ([string]::IsNullOrWhiteSpace($Target)) {
        $Target = Read-Host "SSH server (e.g. a1swg@192.0.2.10, or an SSH config alias)"
    }
    $Target = $Target.Trim()
    if ($Target -notmatch '^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_\[][A-Za-z0-9_.:\[\]-]*$') {
        throw "Enter user@hostname, user@IP, or an SSH alias. Set a custom port with -Port."
    }
    if ([string]::IsNullOrWhiteSpace($RemotePath) -or [string]::IsNullOrWhiteSpace($Branch)) {
        throw "RemotePath and Branch must not be empty."
    }
    foreach ($value in @($Target, $RemotePath, $Branch, $IdentityFile)) {
        if ($value -match '[\x00-\x1F\x7F]') { throw "Control characters are not supported." }
    }
    if ($Port -lt 0 -or $Port -gt 65535) { throw "Port must be between 1 and 65535, or 0 to use SSH config." }
    if ($Configure) {
        $password = Read-Host "SSH password to remember (blank = ask SSH each time / remove saved password)" -AsSecureString
        try {
            $settings = [pscustomobject]@{
                Target = $Target; Port = $Port; RemotePath = $RemotePath; Branch = $Branch
                IdentityFile = $IdentityFile; WithWatchdog = [bool]$WithWatchdog.IsPresent
            }
            Save-UpdateProfile $ConfigDirectory $settings $password
        } finally { $password.Dispose() }
        Write-Host "Saved locally. Next time, double-click update_server.bat to update this server." -ForegroundColor Green
        exit 0
    }

    $body = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "update_server.sh"), $utf8)
    $prefix = "UPDATE_PROJECT_DIR=$(ConvertTo-ShellLiteral $RemotePath)`n" +
              "UPDATE_BRANCH=$(ConvertTo-ShellLiteral $Branch)`n" +
              "UPDATE_WITH_WATCHDOG=$([int]$WithWatchdog.IsPresent)`n"
    $payload = [Convert]::ToBase64String($utf8.GetBytes(($prefix + $body).Replace("`r`n", "`n")))
    $sshArgs = @("-t", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3")
    # Omit -p unless supplied, so an existing SSH config keeps its own Port.
    if ($Port -gt 0) { $sshArgs += @("-p", "$Port") }
    if ($IdentityFile) {
        $keyPath = (Resolve-Path -LiteralPath $IdentityFile).Path
        $sshArgs += @("-i", $keyPath)
    }
    # No stdin pipe on Windows: SSH can still prompt for a password/passphrase.
    # Only Base64 crosses the remote shell command boundary (no raw user values).
    $sshArgs += @($Target, "printf %s $payload | base64 -d | bash")
    Write-Host "Connecting to $Target; project: $RemotePath; branch: $Branch" -ForegroundColor Cyan
    $savedEnvironment = @{}
    try {
        if (Test-UpdateSavedPassword $ConfigDirectory $Target $Port) {
            $helper = Initialize-UpdateAskpass $ConfigDirectory
            $variables = @{
                SSH_ASKPASS = $helper
                SSH_ASKPASS_REQUIRE = "force"
                ICEWAG_ASKPASS_SCRIPT = (Join-Path $PSScriptRoot "server_update_askpass.ps1")
                ICEWAG_UPDATE_CREDENTIAL_FILE = (Join-Path $ConfigDirectory "credential.clixml")
            }
            foreach ($key in $variables.Keys) {
                $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
                [Environment]::SetEnvironmentVariable($key, $variables[$key], "Process")
            }
            $sshArgs = @("-o", "StrictHostKeyChecking=ask", "-o", "PreferredAuthentications=publickey,password",
                         "-o", "KbdInteractiveAuthentication=no", "-o", "NumberOfPasswordPrompts=1") + $sshArgs
        }
        & $ssh @sshArgs
        $result = $LASTEXITCODE
    } finally {
        foreach ($key in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], "Process")
        }
    }
    if ($result -ne 0) {
        Write-Host "Update did not complete (exit $result). Check the error above." -ForegroundColor Red
    }
    exit $result
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
