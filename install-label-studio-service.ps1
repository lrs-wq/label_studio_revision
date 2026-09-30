$ErrorActionPreference = "Stop"

$ServiceName = "LabelStudio"
$ProjectRoot = "D:\gogelila\label_studio"
$Python = "D:\Anaconda3\envs\label-studio\python.exe"
$ServiceScript = Join-Path $ProjectRoot "scripts\label_studio_windows_service.py"

if (-not (Test-Path $Python)) {
  throw "Python not found: $Python"
}

if (-not (Test-Path $ServiceScript)) {
  throw "Service script not found: $ServiceScript"
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
  if ($service.Status -ne "Stopped") {
    Stop-Service -Name $ServiceName -Force
    $service.WaitForStatus("Stopped", "00:00:30")
  }

  & $Python $ServiceScript remove
}

& $Python $ServiceScript --startup auto install
sc.exe failure $ServiceName reset= 86400 actions= restart/60000/restart/60000/none/0 | Out-Null
Start-Service -Name $ServiceName

Get-Service -Name $ServiceName
