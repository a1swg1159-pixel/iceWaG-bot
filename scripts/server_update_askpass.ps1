$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $promptText = $env:ICEWAG_ASKPASS_PROMPT
    if ($promptText -match '^Are you sure you want to continue connecting') {
        # Keep OpenSSH's first-use host verification; never auto-accept a key.
        Add-Type -AssemblyName System.Windows.Forms
        $answer = [Windows.Forms.MessageBox]::Show(
            "Check the host fingerprint shown in the SSH window before choosing Yes.`r`n`r`n$promptText",
            "SSH host verification", [Windows.Forms.MessageBoxButtons]::YesNo,
            [Windows.Forms.MessageBoxIcon]::Warning, [Windows.Forms.MessageBoxDefaultButton]::Button2)
        if ($answer -eq [Windows.Forms.DialogResult]::Yes) { [Console]::WriteLine("yes") }
        else { [Console]::WriteLine("no") }
        exit 0
    }
    # Do not send the server password as a private-key passphrase, OTP, or a new password.
    if ($env:SSH_ASKPASS_PROMPT -eq "none" -or $promptText -notmatch "^[^`r`n]+@[^`r`n]+'s password:\s*$") {
        exit 1
    }
    $record = Import-Clixml -LiteralPath $env:ICEWAG_UPDATE_CREDENTIAL_FILE
    if ($record.Credential -isnot [Management.Automation.PSCredential]) { exit 1 }
    # Stdout is OpenSSH's private pipe, not the update log or a temporary plaintext file.
    [Console]::WriteLine($record.Credential.GetNetworkCredential().Password)
    exit 0
} catch {
    [Console]::Error.WriteLine("Saved password could not be decrypted. Run setup_server_update.bat again on this Windows account.")
    exit 1
}
