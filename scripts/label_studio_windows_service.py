import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil


SERVICE_NAME = "LabelStudio"
PROJECT_ROOT = Path(r"D:\gogelila\label_studio")
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
LOG_DIR = PROJECT_ROOT / "logs"
TMP_DIR = PROJECT_ROOT / "tmp"
LOCAL_APPDATA = PROJECT_ROOT / "appdata" / "Local"
ROAMING_APPDATA = PROJECT_ROOT / "appdata" / "Roaming"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
SIGNUP_NOTICE_PATCH_SCRIPT = PROJECT_ROOT / "scripts" / "apply_label_studio_signup_disabled_notice.py"
RUNTIME_PATCH_SCRIPT = PROJECT_ROOT / "scripts" / "apply_label_studio_runtime_patches.py"
LOCAL_FILES_DOCUMENT_ROOT = Path(
    r"D:\gogelila\Eating_behavior\outputs\training_frames_4000_20260602_05_06"
)
PORT = 8090
LISTEN_HOST = "0.0.0.0"
PUBLIC_HOST = "http://192.168.1.200:8090"


class LabelStudioService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "Label Studio"
    _svc_description_ = f"Runs Label Studio on {PUBLIC_HOST}."

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None
        self.stdout_handle = None
        self.stderr_handle = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} service is starting.")
        try:
            self.process = self._start_label_studio()
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            self._wait_for_stop_or_exit()
        except Exception as exc:
            servicemanager.LogErrorMsg(f"{SERVICE_NAME} failed: {exc}")
            raise
        finally:
            self._stop_label_studio()
            servicemanager.LogInfoMsg(f"{SERVICE_NAME} service stopped.")

    def _wait_for_stop_or_exit(self):
        while True:
            result = win32event.WaitForSingleObject(self.stop_event, 1000)
            if result == win32event.WAIT_OBJECT_0:
                return

            if self.process and self.process.poll() is not None:
                code = self.process.returncode
                servicemanager.LogErrorMsg(
                    f"Label Studio process exited with code {code}."
                )
                return

    def _start_label_studio(self):
        self._ensure_directories()
        env = self._build_environment()
        self._apply_startup_patches(env)

        command = [
            str(ENV_ROOT / "Scripts" / "label-studio.exe"),
            "start",
            "--data-dir",
            str(DATA_DIR),
            "--internal-host",
            LISTEN_HOST,
            "--host",
            PUBLIC_HOST,
            "--port",
            str(PORT),
            "--no-browser",
        ]

        self.stdout_handle = open(LOG_DIR / "label-studio-service.out.log", "ab", 0)
        self.stderr_handle = open(LOG_DIR / "label-studio-service.err.log", "ab", 0)

        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self.stdout_handle,
            stderr=self.stderr_handle,
            creationflags=creation_flags,
        )

    def _stop_label_studio(self):
        if self.process and self.process.poll() is None:
            self._taskkill(self.process.pid)
            deadline = time.time() + 20
            while time.time() < deadline and self.process.poll() is None:
                time.sleep(0.5)

        self._close_log_handles()

    def _taskkill(self, pid):
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def _close_log_handles(self):
        for handle_name in ("stdout_handle", "stderr_handle"):
            handle = getattr(self, handle_name)
            if handle:
                handle.close()
                setattr(self, handle_name, None)

    def _ensure_directories(self):
        for path in (DATA_DIR, LOG_DIR, TMP_DIR, LOCAL_APPDATA, ROAMING_APPDATA):
            path.mkdir(parents=True, exist_ok=True)

    def _build_environment(self):
        secret_key = self._read_or_create_secret_key()
        env = os.environ.copy()
        env.update(
            {
                "CONDA_ENVS_PATH": r"D:\Anaconda3\envs",
                "CONDA_PKGS_DIRS": r"D:\Anaconda3\pkgs",
                "PIP_CACHE_DIR": str(PROJECT_ROOT / "pip_cache"),
                "TEMP": str(TMP_DIR),
                "TMP": str(TMP_DIR),
                "LOCALAPPDATA": str(LOCAL_APPDATA),
                "APPDATA": str(ROAMING_APPDATA),
                "BASE_DATA_DIR": str(DATA_DIR),
                "LABEL_STUDIO_BASE_DATA_DIR": str(DATA_DIR),
                "LATEST_VERSION_CHECK": "false",
                "DISABLE_SIGNUP_WITHOUT_LINK": "true",
                "LOCAL_FILES_SERVING_ENABLED": "true",
                "LOCAL_FILES_DOCUMENT_ROOT": str(LOCAL_FILES_DOCUMENT_ROOT),
                "DATA_UPLOAD_MAX_NUMBER_FILES": "10000",
                "DATA_UPLOAD_MAX_MEMORY_SIZE": "20000000000",
                "SECRET_KEY": secret_key,
                "PYTHONUNBUFFERED": "1",
            }
        )

        path_parts = [
            str(ENV_ROOT),
            str(ENV_ROOT / "Scripts"),
            str(ENV_ROOT / "Library" / "bin"),
            env.get("PATH", ""),
        ]
        env["PATH"] = os.pathsep.join(path_parts)
        return env

    def _apply_startup_patches(self, env):
        for script in (SIGNUP_NOTICE_PATCH_SCRIPT, RUNTIME_PATCH_SCRIPT):
            self._run_patch_script(script, env)

    def _run_patch_script(self, script, env):
        if not script.exists():
            return
        subprocess.run(
            [str(ENV_ROOT / "python.exe"), str(script)],
            cwd=str(PROJECT_ROOT),
            env=env,
            check=True,
        )

    def _read_or_create_secret_key(self):
        if SECRET_KEY_FILE.exists():
            return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()

        secret = secrets.token_urlsafe(64)
        SECRET_KEY_FILE.write_text(secret, encoding="utf-8")
        return secret


if __name__ == "__main__":
    if not (ENV_ROOT / "Scripts" / "label-studio.exe").exists():
        raise FileNotFoundError(ENV_ROOT / "Scripts" / "label-studio.exe")

    win32serviceutil.HandleCommandLine(LabelStudioService)
