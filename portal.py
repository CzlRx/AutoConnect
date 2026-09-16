"""检测是否已联网，并在需要时提交校园网门户登录。"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import adapters
from config import APP_NAME, PORTAL_HOST, AppConfig, default_portal_url, load_config, redact_secrets
from credentials import load_password

log = logging.getLogger(APP_NAME)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

FAST_TIMEOUT = (0.5, 1.5)


@dataclass
class NetworkState:
    online: bool
    portal_url: str | None
    reason: str


@dataclass
class LoginResult:
    ok: bool
    message: str
    adapter: str = ""


def _portal_host() -> str:
    host = urlparse(load_config().normalized_portal_url()).hostname
    return host or PORTAL_HOST


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.trust_env = False
    retry = Retry(total=0, connect=0, read=0, redirect=2, status=0, other=0)
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    timeout = kwargs.pop("timeout", FAST_TIMEOUT)
    allow_redirects = kwargs.pop("allow_redirects", True)
    def _send() -> requests.Response:
        return session.request(
            method,
            url,
            timeout=timeout,
            allow_redirects=allow_redirects,
            **kwargs,
        )

    try:
        response = _send()
    except requests.exceptions.SSLError:
        log.info("证书校验失败，改为忽略证书后重试: %s", url)
        session.verify = False
        response = _send()
    if not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"
    return response


def _looks_like_portal(url: str, text: str) -> bool:
    combined = f"{url}\n{text}".lower()
    tokens = (
        "eportal",
        "srun_portal",
        "登录",
        "login",
        "portal",
        "0mkkey",
        "wlanuserip",
        "ac_id",
        "interface.do",
        "get_challenge",
    )
    return any(token in combined for token in tokens)


def _chkstatus_online(text: str) -> bool | None:
    if not text:
        return None
    if re.search(r'["\']?result["\']?\s*[:=]\s*1\b', text):
        return True
    if re.search(r'["\']?result["\']?\s*[:=]\s*0\b', text):
        return False
    return None


def check_online(session: requests.Session | None = None) -> NetworkState:
    own_session = session is None
    session = session or _new_session()
    host = _portal_host()
    try:
        status_url = f"http://{host}:1028/drcom/chkstatus"
        try:
            response = _request(
                session,
                "GET",
                status_url,
                params={"callback": "dr1002", "v": "1"},
                timeout=FAST_TIMEOUT,
            )
            flag = _chkstatus_online(response.text or "")
            if flag is True:
                return NetworkState(True, None, "already_online")
            if flag is False:
                return NetworkState(False, f"http://{host}:1028/a79.htm", "portal_offline")
        except requests.RequestException as exc:
            log.info("校园网状态接口不可用: %s", exc)

        return NetworkState(False, None, "need_login")
    finally:
        if own_session:
            session.close()


def _host_changed(original: str, final: str) -> bool:
    return urlparse(original).netloc.lower() != urlparse(final).netloc.lower()


def fetch_portal_page(session: requests.Session, portal_url: str) -> tuple[str, str]:
    last_error: Exception | None = None
    best_url, best_html = portal_url, ""
    candidates: list[str] = []
    if portal_url:
        candidates.append(portal_url)
    for url in candidates:
        try:
            response = _request(session, "GET", url, timeout=FAST_TIMEOUT)
        except requests.RequestException as exc:
            last_error = exc
            log.info("打开登录入口失败 %s: %s", url, exc)
            continue
        html = response.text or ""
        final_url = response.url or url
        if "wlanuserip=" in final_url.lower() or "a79.htm" in final_url.lower() or "dr.com" in html.lower():
            return final_url, html
        if html.strip():
            best_url, best_html = final_url, html
    if not best_html and last_error:
        raise last_error
    return best_url, best_html


def submit_login(
    portal_url: str,
    username: str,
    password: str,
    session: requests.Session | None = None,
) -> LoginResult:
    own_session = session is None
    session = session or _new_session()
    try:
        log.info("直接提交认证: %s", portal_url)
        ok, message, kind = adapters.login(session, portal_url, "", username, password)
        return LoginResult(ok, message, kind)
    except requests.RequestException as exc:
        log.info("认证接口暂不可达: %s", redact_secrets(str(exc)))
        return LoginResult(False, f"认证接口暂不可达: {redact_secrets(str(exc))}")
    except Exception as exc:
        log.exception("登录过程出错")
        return LoginResult(False, f"登录出错: {exc}")
    finally:
        if own_session:
            session.close()


def submit_logout(portal_url: str, username: str) -> LoginResult:
    session = _new_session()
    try:
        final_url, html = fetch_portal_page(session, portal_url)
        log.info("注销使用页面: %s", final_url)
        ok, message = adapters.logout_drcom_portal(session, final_url, html, username)
        return LoginResult(ok, message, "drcom")
    except requests.RequestException as exc:
        log.exception("注销请求失败")
        return LoginResult(False, f"无法访问注销接口: {exc}")
    except Exception as exc:
        log.exception("注销出错")
        return LoginResult(False, f"注销出错: {exc}")
    finally:
        session.close()


def run_logout() -> LoginResult:
    cfg = load_config()
    return submit_logout(default_portal_url(), cfg.username.strip())


def run_login_loop(
    cfg: AppConfig | None = None,
    attempts: Iterable[int] | None = None,
    force: bool = False,
) -> LoginResult:
    del force
    cfg = cfg or load_config()
    username = cfg.username.strip()
    password = load_password()
    if not username:
        return LoginResult(False, "尚未配置账号，请先运行设置")
    if not password:
        return LoginResult(False, "未找到已保存的密码，请先运行设置")

    retry_count = max(1, cfg.retry_count)
    indexes = list(attempts) if attempts is not None else list(range(1, retry_count + 1))
    session = _new_session()
    last = LoginResult(False, "尚未尝试登录")
    try:
        portal_url = default_portal_url()
        for pos, index in enumerate(indexes):
            started = time.perf_counter()
            last = submit_login(portal_url, username, password, session=session)
            log.info(
                "第 %s/%s 次登录: ok=%s adapter=%s msg=%s 耗时 %.0f ms",
                index,
                retry_count,
                last.ok,
                last.adapter,
                last.message,
                (time.perf_counter() - started) * 1000,
            )
            if last.ok:
                return last
            if pos < len(indexes) - 1:
                time.sleep(0.15)
    finally:
        session.close()
    return last
