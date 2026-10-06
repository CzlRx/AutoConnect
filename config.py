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

# 学校档案：key 会写进 config.json 的 school 字段
SCHOOL_CJT = "常州工学院"
SCHOOL_CZU = "常州大学"
DEFAULT_SCHOOL = "cjt"

# 常州大学（城市热点 eportal）
PORTAL_HOST = "211.103.11.101"
PORTAL_PAGE_PORT = 1028
DEFAULT_WLAN_AC_NAME = "0011.0519.250.00"
DEFAULT_SSID = "CCZU-CMCC"

# 常州工学院（局域网网关页面登录）
CJT_PORTAL_URL = "http://172.19.0.1/"

DEFAULT_RETRY_COUNT = 20
DEFAULT_RETRY_INTERVAL_SEC = 2
DEFAULT_STARTUP_DELAY_SEC = 5
# 开机后网络（网卡/DHCP）要过一会儿才就绪，先等它，别白烧重试次数
DEFAULT_PORTAL_WAIT_SEC = 60


def default_portal_url() -> str:
    """常州大学的城市热点认证入口。"""
    return (
        f"http://{PORTAL_HOST}:{PORTAL_PAGE_PORT}/a79.htm"
        f"?wlanacname={DEFAULT_WLAN_AC_NAME}&ssid={DEFAULT_SSID}"
    )


@dataclass(frozen=True)
class CarrierOption:
    """城市热点门户的“服务类型 / 运营商”选项，suffix 会拼到账号后面。"""

    id: str
    name: str
    suffix: str


@dataclass(frozen=True)
class SchoolProfile:
    key: str
    name: str
    portal_url: str
    account_hint: str
    carriers: tuple[CarrierOption, ...] = ()
    portal_api_first: bool = False


# 常州工学院的运营商选项（取自该校认证页模板里的 ISP_select 下拉框）
CJT_CARRIERS: tuple[CarrierOption, ...] = (
    CarrierOption(id="1", name="校园网", suffix=""),
    CarrierOption(id="2", name="中国移动", suffix="@cmcc"),
    CarrierOption(id="3", name="中国联通", suffix="@unicom"),
    CarrierOption(id="4", name="中国电信", suffix="@telecom"),
)


SCHOOL_PROFILES: dict[str, SchoolProfile] = {
    "cjt": SchoolProfile(
        key="cjt",
        name=SCHOOL_CJT,
        portal_url=CJT_PORTAL_URL,
        account_hint="账号密码就是认证页面上填的那一组；「服务类型」要和网页上选的一致，电信/联通账号必须选对。",
        carriers=CJT_CARRIERS,
        portal_api_first=True,
    ),
    "czu": SchoolProfile(
        key="czu",
        name=SCHOOL_CZU,
        portal_url=default_portal_url(),
        account_hint="账号为你的手机号，密码就是你最后一次获取的验证码。",
    ),
}

SCHOOL_ORDER: tuple[str, ...] = ("cjt", "czu")


def school_options() -> list[tuple[str, str]]:
    """设置窗口下拉框用：[(key, 学校名), ...]"""
    return [(key, SCHOOL_PROFILES[key].name) for key in SCHOOL_ORDER]


def school_profile(key: str) -> SchoolProfile:
    return SCHOOL_PROFILES.get((key or "").strip().lower(), SCHOOL_PROFILES[DEFAULT_SCHOOL])


def carrier_options(key: str) -> tuple[CarrierOption, ...]:
    """该校门户需要选择运营商时返回选项列表，否则为空。"""
    return school_profile(key).carriers


def resolve_school_key(school: str, portal_url: str) -> str:
    """school 合法时以它为准；否则按旧的 portal_url 推断，最后回落到默认学校。"""
    key = (school or "").strip().lower()
    if key in SCHOOL_PROFILES:
        return key
    url = (portal_url or "").lower()
    if PORTAL_HOST in url or "a79.htm" in url:
        return "czu"
    return DEFAULT_SCHOOL


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
    school: str = ""
    carrier: str = ""
    account_suffix: str = ""
    retry_count: int = DEFAULT_RETRY_COUNT
    retry_interval_sec: int = DEFAULT_RETRY_INTERVAL_SEC
    startup_delay_sec: int = DEFAULT_STARTUP_DELAY_SEC
    portal_wait_sec: int = DEFAULT_PORTAL_WAIT_SEC

    def resolved_school(self) -> str:
        return resolve_school_key(self.school, self.portal_url)

    def profile(self) -> SchoolProfile:
        return school_profile(self.resolved_school())

    def normalized_portal_url(self) -> str:
        return self.profile().portal_url


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
        school=str(raw.get("school") or ""),
        carrier=str(raw.get("carrier") or ""),
        account_suffix=str(raw.get("account_suffix") or ""),
        retry_count=int(raw.get("retry_count") or DEFAULT_RETRY_COUNT),
        retry_interval_sec=int(raw.get("retry_interval_sec") or DEFAULT_RETRY_INTERVAL_SEC),
        startup_delay_sec=int(raw.get("startup_delay_sec") or DEFAULT_STARTUP_DELAY_SEC),
        portal_wait_sec=int(raw.get("portal_wait_sec") or DEFAULT_PORTAL_WAIT_SEC),
    )


def save_config(cfg: AppConfig) -> None:
    cfg.school = resolve_school_key(cfg.school, cfg.portal_url)
    cfg.portal_url = cfg.profile().portal_url
    path = config_path()
    path.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("已保存配置: %s", path)


def config_exists() -> bool:
    cfg = load_config()
    return bool(cfg.username.strip())
