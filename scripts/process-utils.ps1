function Test-QingheProcess([int]$ProcessId, [string]$Signature, [string]$Root) {
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $details -or -not $details.CommandLine -or -not $details.CommandLine.Contains($Signature)) { return $false }
    $rootPrefix = [IO.Path]::GetFullPath($Root).TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
    $candidates = @($details)
    # Windows venv launchers can delegate to the base interpreter. Check its direct parent as well.
    if ($details.ParentProcessId) {
        $parentDetails = Get-CimInstance Win32_Process -Filter "ProcessId = $($details.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parentDetails) { $candidates += $parentDetails }
    }
    foreach ($candidate in $candidates) {
        if (-not $candidate.CommandLine -or -not $candidate.CommandLine.Contains($Signature)) { continue }
        $executableInRoot = $candidate.ExecutablePath -and $candidate.ExecutablePath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)
        $commandInRoot = $candidate.CommandLine.IndexOf($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -ge 0
        if ($executableInRoot -or $commandInRoot) { return $true }
    }
    return $false
}
