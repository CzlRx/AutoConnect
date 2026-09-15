"""Windows 气泡/Toast 通知（登录失败时提醒）。"""

from __future__ import annotations

import logging
import subprocess
import sys

from config import APP_NAME

log = logging.getLogger(APP_NAME)

CREATE_NO_WINDOW = 0x08000000


def _ps_escape(text: str) -> str:
    return (text or "").replace("'", "''")


def notify(title: str, message: str) -> None:
    if sys.platform != "win32":
        log.info("通知: %s - %s", title, message)
        return
    title_ps = _ps_escape(title)
    message_ps = _ps_escape(message)
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(3000, '{title_ps}', '{message_ps}', [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Milliseconds 800
$n.Dispose()
"""
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        log.warning("弹出通知失败: %s", exc)
