// Small native entry point for OpenSSH's SSH_ASKPASS on Windows.
// Pass the prompt as environment data, never through cmd.exe or shell quoting.
using System;
using System.Diagnostics;
using System.IO;

class IcewagAskpass {
    static int Main(string[] args) {
        try {
            if (args.Length != 1) return 1;
            string script = Environment.GetEnvironmentVariable("ICEWAG_ASKPASS_SCRIPT");
            if (String.IsNullOrEmpty(script) || !File.Exists(script)) return 1;
            var start = new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                    @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            start.EnvironmentVariables["ICEWAG_ASKPASS_PROMPT"] = args[0];
            using (var process = Process.Start(start)) {
                var output = process.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
                var error = process.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
                process.WaitForExit();
                System.Threading.Tasks.Task.WaitAll(output, error);
                return process.ExitCode;
            }
        } catch {
            Console.Error.WriteLine("Unable to read the saved SSH credential. Run setup_server_update.bat again.");
            return 1;
        }
    }
}
