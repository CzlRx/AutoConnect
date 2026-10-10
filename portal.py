"""检测是否已联网，并在需要时提交校园网门户登录。"""

from __future__ import annotations

import logging
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import adapters
from config import APP_NAME, PORTAL_HOST, AppConfig, data_dir, load_config, redact_secrets
from credentials import load_password

log = logging.getLogger(APP_NAME)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

FAST_TIMEOUT = (0.5, 1.5)

# 命中这些字样说明账号密码本身不对：继续重试没有意义，还可能触发校园网失败计数
CREDENTIAL_TOKENS = ("密码错误", "口令错误", "账号错误", "用户不存在", "账号或密码")

# 登录后用来实测"是否真的能上网"的探测地址（跟随跳转会落到门户=被劫持）
ONLINE_PROBES: tuple[tuple[str, int], ...] = (
    ("http://connect.rom.miui.com/generate_204", 204),
    ("http://www.baidu.com", 200),
    ("http://www.msftconnecttest.com/connecttest.txt", 200),
)

# 实测常工院：用 /drcom/login 登录后，网关可能只建出"半成品会话"
# （chkstatus 里 olmac=000000000000、ispid=0、流量计数为 0），此时流量仍被拦到门户页。
# 是否会自动补全尚未证实，所以这里只等一小段时间，不做"等它自己好"的承诺。
ONLINE_WAIT_ATTEMPTS = 18
ONLINE_WAIT_INTERVAL = 5.0

AUTH_OK_NO_NET_MESSAGE = (
    "认证接口返回成功，但流量仍被拦在门户页（实测打不开网页），"
    "说明本次登录没有真正放行。请等 1 分钟再刷网页；若仍无网，"
    "点「断开校园网」后重新「保存并连接」，或在浏览器里登录一次。"
)
ALREADY_ONLINE_NO_NET_MESSAGE = (
    "网关显示已经在线，但流量仍被拦在门户页（实测打不开网页），"
    "说明这个会话是无效的。请点「断开校园网」后重新「保存并连接」，"
    "或在浏览器里登录一次。"
)


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


class BoundHTTPAdapter(HTTPAdapter):
    def __init__(self, source_ip: str = "", **kwargs) -> None:
        self.source_ip = (source_ip or "").strip()
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        if self.source_ip:
            pool_kwargs["source_address"] = (self.source_ip, 0)
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def _portal_host() -> str:
    host = urlparse(load_config().normalized_portal_url()).hostname
    return host or PORTAL_HOST


def _tun_bypass_hint() -> str:
    host = _portal_host()
    if host == PORTAL_HOST:
        return f"请把 {host} 加入代理直连/绕过（含 801、1028 端口）"
    return f"请把 {host} 加入代理直连/绕过"


def _looks_like_credential_error(message: str) -> bool:
    text = message or ""
    return any(token in text for token in CREDENTIAL_TOKENS)


def _with_tun_hint(message: str) -> str:
    if not adapters.virtual_nic_active() or _looks_like_credential_error(message):
        return message
    hint = _tun_bypass_hint()
    if hint in (message or ""):
        return message
    return f"{message}。检测到代理虚拟网卡，{hint}"


def _new_session(source_ip: str | None = None) -> requests.Session:
    if source_ip is None:
        source_ip = adapters.preferred_source_ip()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.trust_env = False
    retry = Retry(total=0, connect=0, read=0, redirect=2, status=0, other=0)
    adapter = BoundHTTPAdapter(source_ip or "", max_retries=retry, pool_maxsize=4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if source_ip:
        log.info("HTTP 绑定本地地址: %s", source_ip)
    return session


def _rebind_session(session: requests.Session, source_ip: str) -> requests.Session:
    current = ""
    for item in session.adapters.values():
        if isinstance(item, BoundHTTPAdapter):
            current = item.source_ip
            break
    if (source_ip or "") == (current or ""):
        return session
    session.close()
    return _new_session(source_ip)


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
        page_url, html = portal_url, ""
        if adapters.detect_kind(portal_url, "") == "generic":
            # 通用表单门户（如网关页面）必须先取到页面才能找到账号密码框
            page_url, html = fetch_portal_page(session, portal_url)
            if not (html or "").strip():
                message = f"打不开登录页 {portal_url}，请确认已连上校园网后再试"
                log.info(message)
                return LoginResult(False, message)
            log.info("已获取登录页: %s", page_url)
        log.info("提交认证: %s", page_url)
        ok, message, kind = adapters.login(session, page_url, html, username, password)
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
        kind = adapters.detect_kind(final_url, html)
        if kind == "drcom":
            ok, message = adapters.logout_drcom_portal(session, final_url, html, username)
            return LoginResult(ok, message, "drcom")
        ok, message = adapters.logout_generic(session, final_url, html)
        return LoginResult(ok, message, kind)
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
    return submit_logout(cfg.normalized_portal_url(), cfg.username.strip())


def portal_host_port(cfg: AppConfig | None = None) -> tuple[str, int]:
    cfg = cfg or load_config()
    parsed = urlparse(cfg.normalized_portal_url())
    host = parsed.hostname or PORTAL_HOST
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def portal_reachable(cfg: AppConfig | None = None, timeout: float = 0.6) -> bool:
    """网关那个端口现在通不通（开机后网卡/DHCP 就绪前是不通的）。"""
    host, port = portal_host_port(cfg)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_portal(cfg: AppConfig | None = None, wait_sec: int | None = None, interval: float = 1.0) -> bool:
    """等校园网就绪再登录：每秒探一次网关，最多等 wait_sec 秒。

    开机时网卡/DHCP 还没就绪，直接登录只会白烧重试次数（旧版本因此要等
    计划任务"失败后 1 分钟重试"才连上）。这里改成就绪瞬间立刻返回。
    """
    cfg = cfg or load_config()
    limit = cfg.portal_wait_sec if wait_sec is None else wait_sec
    limit = max(0, int(limit))
    if portal_reachable(cfg):
        return True
    started = time.monotonic()
    log.info("校园网还没就绪，最多等 %s 秒（每秒探测一次）", limit)
    while time.monotonic() - started < limit:
        time.sleep(interval)
        if portal_reachable(cfg):
            log.info("校园网已就绪（等了 %.1f 秒），立刻登录", time.monotonic() - started)
            return True
    log.warning("等待校园网就绪超时（%s 秒），仍继续尝试登录", limit)
    return False


def verify_online(session: requests.Session | None = None, cfg: AppConfig | None = None) -> bool:
    """实测本机能不能真的上外网（门户说成功不代表数据通道已经通）。"""
    cfg = cfg or load_config()
    portal_host = (urlparse(cfg.normalized_portal_url()).hostname or "").lower()
    own_session = session is None
    session = session or _new_session()
    try:
        for url, expected in ONLINE_PROBES:
            try:
                response = session.get(url, timeout=(0.5, 3), allow_redirects=True)
            except requests.RequestException as exc:
                log.info("联网探测失败 %s: %s", url, exc)
                continue
            final_host = (urlparse(response.url).hostname or "").lower()
            if portal_host and final_host == portal_host:
                log.info("联网探测被门户劫持: %s -> %s", url, response.url)
                continue
            if response.status_code == expected:
                log.info("联网探测通过: %s -> HTTP %s", url, response.status_code)
                return True
            log.info("联网探测异常: %s -> HTTP %s", url, response.status_code)
    finally:
        if own_session:
            session.close()
    return False


def wait_online(
    session: requests.Session | None = None,
    cfg: AppConfig | None = None,
    attempts: int = ONLINE_WAIT_ATTEMPTS,
    interval: float = ONLINE_WAIT_INTERVAL,
) -> bool:
    """网关已受理认证后，给它一点时间把数据通道建起来。"""
    for index in range(max(1, attempts)):
        if verify_online(session=session, cfg=cfg):
            return True
        if index < attempts - 1:
            log.info("外网还没放行，%s 秒后再试（第 %s/%s 次）", interval, index + 1, attempts)
            time.sleep(interval)
    return False


def probe_gateway_online(cfg: AppConfig | None = None, session: requests.Session | None = None) -> bool | None:
    """直接问网关自己：这台机器现在算不算在线（拿不到答案返回 None）。

    已经在线时**不要重复提交登录** —— 实测重复登录会把会话重置成"待放行"状态。
    """
    cfg = cfg or load_config()
    parsed = urlparse(cfg.normalized_portal_url())
    host = parsed.hostname
    if not host:
        return None
    scheme = parsed.scheme or "http"
    own_session = session is None
    session = session or _new_session()
    try:
        response = session.get(
            f"{scheme}://{host}/drcom/chkstatus",
            params={"callback": "dr1002", "v": str(int(time.time() * 1000))},
            timeout=(0.5, 3),
        )
        payload = adapters._jsonp_payload(response.text or "")
        if isinstance(payload, dict) and "result" in payload:
            online = str(payload.get("result")) == "1"
            log.info("网关在线查询: result=%s uid=%s", payload.get("result"), payload.get("uid"))
            return online
    except (requests.RequestException, ValueError, TypeError) as exc:
        log.info("网关在线查询不可用: %s", exc)
    finally:
        if own_session:
            session.close()
    return None


def fetch_portal_carriers(cfg: AppConfig | None = None) -> list[tuple[str, str, str]]:
    """连得上门户时读它页面里 ISP_select 的运营商列表 [(id, 名称, 后缀), ...]，读不到就返回空。

    读不到时（例如门户页把下拉框放在模板里、由脚本渲染）保持内置列表不变。
    """
    cfg = cfg or load_config()
    session = _new_session()
    try:
        _final_url, html = fetch_portal_page(session, cfg.normalized_portal_url())
    except requests.RequestException as exc:
        log.info("读取门户运营商列表失败: %s", exc)
        return []
    finally:
        session.close()
    return adapters._isp_select_options(html or "")


def dump_portal_page(cfg: AppConfig | None = None) -> tuple[Path, str]:
    """把当前学校的门户页面存到本地，便于适配不确定的登录页。"""
    cfg = cfg or load_config()
    portal_url = cfg.normalized_portal_url()
    session = _new_session()
    try:
        final_url, html = fetch_portal_page(session, portal_url)
    finally:
        session.close()
    path = data_dir() / "portal-dump.html"
    header = (
        "<!-- AutoConnect portal dump\n"
        f"  school: {cfg.resolved_school()} ({cfg.profile().name})\n"
        f"  portal_url: {portal_url}\n"
        f"  final_url: {final_url}\n"
        f"  bytes: {len(html)}\n"
        "-->\n"
    )
    path.write_text(header + (html or ""), encoding="utf-8", errors="replace")
    log.info("已导出登录页: %s", path)
    return path, final_url


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
    interval = max(1, int(cfg.retry_interval_sec))
    indexes = list(attempts) if attempts is not None else list(range(1, retry_count + 1))
    log.info(
        "开始登录 重试=%s 间隔=%ss 网卡=%s 虚拟网卡=%s",
        retry_count,
        interval,
        adapters.nic_summary(),
        ",".join(adapters.virtual_nic_names()) or "无",
    )
    # 开机时网卡/DHCP 还没就绪，先等网关能连通，避免把重试次数白烧掉
    wait_for_portal(cfg)
    ips = adapters.campus_ipv4s()
    if ips:
        log.info("校园网 IP 已就绪: %s", ", ".join(ips))
    else:
        log.info("尚未取得可用的校园网 IP，将继续尝试认证")
    session = _new_session(ips[0] if ips else "")
    last = LoginResult(False, "尚未尝试登录")
    try:
        portal_url = cfg.normalized_portal_url()
        current_ips = adapters.campus_ipv4s()
        session = _rebind_session(session, current_ips[0] if current_ips else "")
        # 常州工学院的网关会在 80 端口回答在线状态。常州大学没有这个接口，
        # 去问只会白等一次超时。
        state = probe_gateway_online(cfg, session) if cfg.profile().portal_api_first else None
        if state is True:
            if wait_online(session=session, cfg=cfg):
                return LoginResult(True, "已经在线（已确认可以上网）", "drcom")
            log.warning("网关显示在线，但外网仍被劫持")
            return LoginResult(False, _with_tun_hint(ALREADY_ONLINE_NO_NET_MESSAGE), "drcom")
        for pos, index in enumerate(indexes):
            current_ips = adapters.campus_ipv4s()
            source_ip = current_ips[0] if current_ips else ""
            session = _rebind_session(session, source_ip)
            started = time.perf_counter()
            last = submit_login(portal_url, username, password, session=session)
            log.info(
                "第 %s/%s 次登录: ok=%s adapter=%s msg=%s ips=%s 虚拟网卡=%s 耗时 %.0f ms",
                index,
                len(indexes),
                last.ok,
                last.adapter,
                last.message,
                ",".join(current_ips) or "(无)",
                ",".join(adapters.virtual_nic_names()) or "无",
                (time.perf_counter() - started) * 1000,
            )
            if last.ok:
                # 常州工学院的接口会先回成功、过一会儿才放行，所以要实测外网。
                # 常州大学的 eportal 回「认证成功」就是已经登上了。这时如果还拿
                # 绑定在校园网网卡上的连接去探测百度，Clash 的 fake-ip 会让探测全部超时，
                # 把一次成功的登录报成失败。
                if not cfg.profile().portal_api_first:
                    return last
                if wait_online(session=session, cfg=cfg):
                    return LoginResult(True, f"{last.message}（已确认可以上网）", last.adapter)
                log.warning("网关已受理认证，但外网仍未放行")
                return LoginResult(False, _with_tun_hint(AUTH_OK_NO_NET_MESSAGE), last.adapter)
            if any(token in last.message for token in CREDENTIAL_TOKENS):
                log.info("判定为账号密码问题，停止重试: %s", last.message)
                return last
            if pos < len(indexes) - 1:
                time.sleep(interval)
    finally:
        session.close()
    return LoginResult(False, _with_tun_hint(last.message), last.adapter)
