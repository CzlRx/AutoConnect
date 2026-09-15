"""注册 / 删除 Windows 登录后自动运行的计划任务。"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from config import APP_NAME, load_config

log = logging.getLogger(APP_NAME)

TASK_NAME = "AutoConnect Campus Login"
CREATE_NO_WINDOW = 0x08000000


def project_root() -> Path:
    return Path(__file__).resolve().parent


def pythonw_path() -> Path:
    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return candidate
    return exe


def login_command() -> tuple[str, str]:
    command = str(pythonw_path())
    script = str(project_root() / "autoconnect.py")
    arguments = f'"{script}" --login'
    return command, arguments


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def is_registered() -> bool:
    result = _run_schtasks(["schtasks", "/Query", "/TN", TASK_NAME])
    return result.returncode == 0


def _task_xml(delay_sec: int) -> str:
    command, arguments = login_command()
    workdir = str(project_root())
    delay = max(0, int(delay_sec))
    delay_xml = f"\n      <Delay>PT{delay}S</Delay>" if delay > 0 else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>开机登录后自动登录校园网（AutoConnect）</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>{delay_xml}
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
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


def register_startup(delay_sec: int | None = None) -> None:
    if delay_sec is None:
        delay_sec = load_config().startup_delay_sec
    xml = _task_xml(delay_sec)
    with tempfile.NamedTemporaryFile(
        suffix=".xml",
        prefix="autoconnect-task-",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
    try:
        tmp_path.write_bytes(xml.encode("utf-16"))
        result = _run_schtasks(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(tmp_path), "/F"]
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(detail or f"schtasks 退出码 {result.returncode}")
        log.info("已注册开机任务: %s", TASK_NAME)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def unregister_startup() -> None:
    if not is_registered():
        log.info("开机任务不存在，无需删除")
        return
    result = _run_schtasks(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"schtasks 退出码 {result.returncode}")
    log.info("已删除开机任务: %s", TASK_NAME)
