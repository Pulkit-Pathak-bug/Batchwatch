# Stops this project's bot and dashboard (and the conda/shell wrappers that launched them).
# Matches only BatchWatch command lines, never itself. Run: powershell -NoProfile -File scripts/stop_demo.ps1
$pattern = 'batchwatch\.bot|streamlit run batchwatch/dashboard\.py'
$found = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match $pattern }
foreach ($p in $found) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    "stopped $($p.Name) (PID $($p.ProcessId))"
}
if (-not $found) { "nothing to stop" }
