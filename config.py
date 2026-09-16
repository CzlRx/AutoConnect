"""读写 AutoConnect 本地配置（不含密码）。"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

APP_NAME = "AutoConnect"
log = logging.getLogger(APP_NAME)

SCHOOL_NAME = "常州大学"
PORTAL_HOST = "211.103.11.101"
PORTAL_PAGE_PORT = 1028
DEFAULT_WLAN_AC_NAME = "0011.0519.250.00"
DEFAULT_SSID = "CCZU-CMCC"

DEFAULT_RETRY_COUNT = 20
DEFAULT_RETRY_INTERVAL_SEC = 1
DEFAULT_STARTUP_DELAY_SEC = 0


def default_portal_url() -> str:
    return (
        f"http://{PORTAL_HOST}:{PORTAL_PAGE_PORT}/a79.htm"
        f"?wlanacname={DEFAULT_WLAN_AC_NAME}&ssid={DEFAULT_SSID}"
    )


_SECRET_QS = re.compile(
    r"(user_password|password|passwd|pwd|upass)=([^&\s)'\"]+)",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    return _SECRET_QS.sub(r"\1=***", text or "")


# 这些是你电脑当时的地址，每次拨号都会变，不能写死在配置里
_CLIENT_QUERY_KEYS = {
    "wlanuserip",
    "ip",
    "userip",
    "user-ip",
    "userip",
    "mac",
    "wlanusermac",
    "usermac",
    "vlan",
    "vlanid",
}


def sanitize_portal_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _CLIENT_QUERY_KEYS
    ]
    return urlunparse(parsed._replace(query=urlencode(kept)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "autoconnect.log"


@dataclass
class AppConfig:
    portal_url: str = ""
    username: str = ""
    account_suffix: str = ""
    retry_count: int = DEFAULT_RETRY_COUNT
    retry_interval_sec: int = DEFAULT_RETRY_INTERVAL_SEC
    startup_delay_sec: int = DEFAULT_STARTUP_DELAY_SEC

    def normalized_portal_url(self) -> str:
        return sanitize_portal_url(self.portal_url) or default_portal_url()


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("读取配置失败，将使用默认值: %s", exc)
        return AppConfig()
    return AppConfig(
        portal_url=str(raw.get("portal_url") or ""),
        username=str(raw.get("username") or ""),
        account_suffix=str(raw.get("account_suffix") or ""),
        retry_count=int(raw.get("retry_count") or DEFAULT_RETRY_COUNT),
        retry_interval_sec=int(raw.get("retry_interval_sec") or DEFAULT_RETRY_INTERVAL_SEC),
        startup_delay_sec=int(raw.get("startup_delay_sec") or DEFAULT_STARTUP_DELAY_SEC),
    )


def save_config(cfg: AppConfig) -> None:
    cfg.portal_url = sanitize_portal_url(cfg.portal_url) or default_portal_url()
    path = config_path()
    path.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("已保存配置: %s", path)


def config_exists() -> bool:
    cfg = load_config()
    return bool(cfg.username.strip())
