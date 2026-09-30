$ErrorActionPreference = "Stop"

$service = Get-Service -Name "LabelStudio" -ErrorAction SilentlyContinue
if ($service -and $service.Status -ne "Stopped") {
  Stop-Service -Name "LabelStudio" -Force
  $service.WaitForStatus("Stopped", "00:00:30")
  Write-Host "Stopped LabelStudio service"
}

$processIds = @()
$processIds += Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess
$processIds += Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -like "*start-label-studio.ps1*" -or
    $_.CommandLine -like "*label_studio_windows_service.py*" -or
    $_.ExecutablePath -like "D:\Anaconda3\envs\label-studio\*"
  } |
  Select-Object -ExpandProperty ProcessId

$processIds = $processIds | Where-Object { $_ } | Sort-Object -Unique

foreach ($processId in $processIds) {
  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if ($process) {
    Stop-Process -Id $processId -Force
    Write-Host "Stopped $($process.ProcessName) PID $processId"
  }
}

if (-not $processIds) {
  Write-Host "Label Studio is not running on port 8090."
}
