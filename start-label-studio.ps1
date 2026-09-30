$ErrorActionPreference = "Stop"

$ProjectRoot = "D:\gogelila\label_studio"
$EnvRoot = "D:\Anaconda3\envs\label-studio"
$DataDir = Join-Path $ProjectRoot "label_studio_data"
$LogDir = Join-Path $ProjectRoot "logs"
$TmpDir = Join-Path $ProjectRoot "tmp"
$LocalAppData = Join-Path $ProjectRoot "appdata\Local"
$RoamingAppData = Join-Path $ProjectRoot "appdata\Roaming"
$SecretKeyFile = Join-Path $DataDir "secret_key.txt"
$LocalFilesDocumentRoot = "D:\gogelila\Eating_behavior\outputs\training_frames_4000_20260602_05_06"
$ListenHost = "0.0.0.0"
$PublicHost = "http://192.168.1.200:8090"
$SignupNoticePatchScript = Join-Path $ProjectRoot "scripts\apply_label_studio_signup_disabled_notice.py"
$RuntimePatchScript = Join-Path $ProjectRoot "scripts\apply_label_studio_runtime_patches.py"

New-Item -ItemType Directory -Force -Path $DataDir, $LogDir, $TmpDir, $LocalAppData, $RoamingAppData | Out-Null

if (-not (Test-Path $SecretKeyFile)) {
  $randomBytes = New-Object byte[] 64
  [System.Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
  $secretKey = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  Set-Content -Path $SecretKeyFile -Value $secretKey -Encoding UTF8
}

$env:CONDA_ENVS_PATH = "D:\Anaconda3\envs"
$env:CONDA_PKGS_DIRS = "D:\Anaconda3\pkgs"
$env:PIP_CACHE_DIR = Join-Path $ProjectRoot "pip_cache"
$env:TEMP = $TmpDir
$env:TMP = $TmpDir
$env:LOCALAPPDATA = $LocalAppData
$env:APPDATA = $RoamingAppData
$env:BASE_DATA_DIR = $DataDir
$env:LABEL_STUDIO_BASE_DATA_DIR = $DataDir
$env:LATEST_VERSION_CHECK = "false"
$env:DISABLE_SIGNUP_WITHOUT_LINK = "true"
$env:LOCAL_FILES_SERVING_ENABLED = "true"
$env:LOCAL_FILES_DOCUMENT_ROOT = $LocalFilesDocumentRoot
$env:DATA_UPLOAD_MAX_NUMBER_FILES = "10000"
$env:DATA_UPLOAD_MAX_MEMORY_SIZE = "20000000000"
$env:SECRET_KEY = (Get-Content -Path $SecretKeyFile -Raw).Trim()

if (Test-Path $SignupNoticePatchScript) {
  & (Join-Path $EnvRoot "python.exe") $SignupNoticePatchScript
}

if (Test-Path $RuntimePatchScript) {
  & (Join-Path $EnvRoot "python.exe") $RuntimePatchScript
}

& (Join-Path $EnvRoot "Scripts\label-studio.exe") start `
  --data-dir $DataDir `
  --internal-host $ListenHost `
  --host $PublicHost `
  --port 8090 `
  --no-browser
