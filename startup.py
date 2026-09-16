"""注册 / 删除登录后自动运行的开机任务。

优先写 Windows 计划任务；若系统拒绝（常见于中文用户名、策略限制），
则回退到当前用户注册表 Run 项，不需要管理员权限。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import winreg
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from config import APP_NAME, app_dir, is_frozen, load_config

log = logging.getLogger(APP_NAME)

TASK_NAME = "AutoConnect Campus Login"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AutoConnect"
CREATE_NO_WINDOW = 0x08000000


def project_root() -> Path:
    return app_dir()


def pythonw_path() -> Path:
    exe = Path(sys.executable).resolve()
    base = getattr(sys, "_base_executable", None)
    if "WindowsApps" in exe.parts and base:
        real = Path(base).resolve()
        if real.exists() and "WindowsApps" not in real.parts:
            exe = real
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return candidate
    return exe


def login_command() -> tuple[str, str]:
    if is_frozen():
        return str(Path(sys.executable).resolve()), "--login"
    command = str(pythonw_path())
    script = str(project_root() / "autoconnect.py")
    arguments = f'"{script}" --login'
    return command, arguments


def _run_command_line() -> str:
    command, arguments = login_command()
    return f'"{command}" {arguments}'


def _run_hidden(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _result_text(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip() or f"退出码 {result.returncode}"


def _is_access_denied(text: str) -> bool:
    lowered = text.lower()
    return "拒绝访问" in text or "access is denied" in lowered or "access denied" in lowered


def _principal_user() -> str:
    result = _run_hidden(["whoami", "/user", "/fo", "csv", "/nh"])
    if result.returncode == 0:
        parts = [item.strip().strip('"') for item in result.stdout.strip().split(",")]
        if len(parts) >= 2 and parts[-1].startswith("S-1-"):
            return parts[-1]
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    user = (os.environ.get("USERNAME") or "").strip()
    if domain and user:
        return f"{domain}\\{user}"
    return user


def _ascii_xml_path() -> Path:
    public = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
    folder = public / "AutoConnect"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "autoconnect-task.xml"


def _task_exists() -> bool:
    return _run_hidden(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0


def _run_key_exists() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def is_registered() -> bool:
    return _task_exists() or _run_key_exists()


def _task_xml(delay_sec: int) -> str:
    command, arguments = login_command()
    workdir = str(project_root())
    user_id = xml_escape(_principal_user())
    delay = max(0, int(delay_sec))
    delay_xml = f"\n      <Delay>PT{delay}S</Delay>" if delay > 0 else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>开机登录后自动登录校园网（AutoConnect）</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user_id}</UserId>{delay_xml}
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_id}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(command)}</Command>
      <Arguments>{xml_escape(arguments)}</Arguments>
      <WorkingDirectory>{xml_escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _register_task_xml(delay_sec: int) -> None:
    xml_path = _ascii_xml_path()
    xml_path.write_bytes(_task_xml(delay_sec).encode("utf-16"))
    try:
        result = _run_hidden(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]
        )
        if result.returncode != 0:
            raise RuntimeError(_result_text(result))
    finally:
        try:
            xml_path.unlink(missing_ok=True)
        except OSError:
            pass


def _register_task_onlogon() -> None:
    result = _run_hidden(
        [
            "schtasks",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            _run_command_line(),
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/IT",
            "/F",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(_result_text(result))


def _register_run_key() -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _run_command_line())


def _unregister_run_key() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        # 值为空或不存在时 Windows 可能返回 2
        if getattr(exc, "winerror", None) not in {2}:
            raise


def register_startup(delay_sec: int | None = None) -> None:
    if delay_sec is None:
        delay_sec = load_config().startup_delay_sec

    errors: list[str] = []
    try:
        _register_task_xml(delay_sec)
        _unregister_run_key()
        log.info("已注册开机任务: %s", TASK_NAME)
        return
    except Exception as exc:
        errors.append(str(exc))
        log.warning("计划任务 XML 注册失败: %s", exc)
        if _is_access_denied(str(exc)):
            log.warning("计划任务被拒绝访问，改用注册表开机启动")
            _register_run_key()
            log.info("已写入注册表开机启动: %s", RUN_VALUE)
            return

    try:
        _register_task_onlogon()
        _unregister_run_key()
        log.info("已用 ONLOGON 注册开机任务: %s", TASK_NAME)
        return
    except Exception as exc:
        errors.append(str(exc))
        log.warning("计划任务 ONLOGON 注册失败: %s", exc)

    try:
        _register_run_key()
        log.info("计划任务不可用，已改用注册表开机启动")
        return
    except Exception as exc:
        errors.append(str(exc))
        raise RuntimeError("无法注册开机自启：" + "；".join(item for item in errors if item)) from exc


def unregister_startup() -> None:
    existed = is_registered()
    errors: list[str] = []
    if _task_exists():
        result = _run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        if result.returncode != 0:
            errors.append(_result_text(result))
    try:
        _unregister_run_key()
    except OSError as exc:
        errors.append(str(exc))
    if errors:
        raise RuntimeError("；".join(errors))
    if existed:
        log.info("已删除开机任务: %s", TASK_NAME)
    else:
        log.info("开机任务不存在，无需删除")
