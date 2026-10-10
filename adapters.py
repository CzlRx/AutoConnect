"""常见校园网 Web 门户登录适配。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import APP_NAME, DEFAULT_WLAN_AC_NAME, PORTAL_HOST, data_dir, redact_secrets

log = logging.getLogger(APP_NAME)

USERNAME_KEYS = {
    "username",
    "user",
    "userid",
    "user_id",
    "account",
    "name",
    "ddddd",
    "id",
    "uid",
    "auth_user",
    "user_account",
    "stunum",
    "stuid",
}
PASSWORD_KEYS = {
    "password",
    "pass",
    "passwd",
    "pwd",
    "upass",
    "user_password",
    "userpwd",
}


def _jsonp_payload(text: str) -> Any:
    text = (text or "").strip()
    match = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", text, re.S)
    body = match.group(1) if match else text
    return json.loads(body)


def detect_kind(url: str, html: str) -> str:
    combined = f"{url}\n{html}".lower()
    if "srun_portal" in combined or "cgi-bin/get_challenge" in combined:
        return "srun"
    # 城市热点 a79.htm 的表单由 JS 生成，但脚本里带 eportal，必须先于锐捷判断
    if _is_drcom_portal(url, html):
        return "drcom"
    if "0mkkey" in combined or re.search(r'name=["\']DDDDD["\']', html, re.I):
        return "drcom"
    if "/eportal/" in combined or "interface.do" in combined or "eportal/portal/login" in combined:
        return "ruijie"
    return "generic"


def _is_drcom_portal(url: str, html: str) -> bool:
    combined = f"{url}\n{html}".lower()
    tokens = (
        "a79.htm",
        "a70.htm",
        "dr.comwebloginid",
        "authuserfield",
        "c=portal&a=login",
        "c=acsetting&a=login",
        "/drcom/",
    )
    return any(token in combined for token in tokens)


def login(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str, str]:
    kind = detect_kind(url, html)
    log.info("使用适配器: %s", kind)
    if kind == "srun":
        ok, message = login_srun(session, url, html, username, password)
    elif kind == "ruijie":
        ok, message = login_ruijie(session, url, html, username, password)
    elif kind == "drcom":
        ok, message = login_drcom(session, url, html, username, password)
    else:
        ok, message = login_generic(session, url, html, username, password)
        if not ok and "/eportal" in (url + html).lower():
            log.info("通用表单失败，尝试锐捷接口")
            ok, message = login_ruijie(session, url, html, username, password)
            kind = "ruijie"
    return ok, message, kind


def login_generic(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    form = None
    for candidate in soup.find_all("form"):
        if candidate.find("input", {"type": re.compile(r"password", re.I)}):
            form = candidate
            break
    if form is None:
        forms = soup.find_all("form")
        form = forms[0] if forms else None
    if form is None:
        if _contains_any(html, LOGIN_SUCCESS_TOKENS):
            # 网关在已认证时通常只显示“已在线 / 注销”，不再给登录表单
            log.info("页面没有登录表单，但出现已登录特征，判定为已经在线")
            return True, "已经在线"
        return False, "页面中没有找到登录表单，可能是需要专用适配的门户（可运行 --dump 导出页面后反馈）"

    action = form.get("action") or url
    method = (form.get("method") or "post").lower()
    target = urljoin(url, action)
    data: dict[str, str] = {}
    user_field = None
    pass_field = None
    text_fields: list[str] = []

    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        key = name.lower()
        if itype in {"submit", "button", "image", "reset", "file"}:
            if key == "0mkkey":
                data[name] = inp.get("value") or "Login"
            continue
        data[name] = inp.get("value") or ""
        if itype == "password" or key in PASSWORD_KEYS:
            pass_field = name
        elif key in USERNAME_KEYS:
            user_field = name
        elif itype in {"text", "tel", "email"}:
            text_fields.append(name)

    if pass_field:
        data[pass_field] = password
    else:
        data["password"] = password
    if user_field:
        data[user_field] = username
    elif text_fields:
        data[text_fields[0]] = username
    else:
        data["username"] = username

    log.info("通用表单提交: %s %s 字段=%s", method.upper(), target, ",".join(data.keys()))
    if method == "get":
        response = session.get(target, params=data, timeout=12)
    else:
        response = session.post(target, data=data, timeout=12)
    _fix_encoding(response)
    ok, message = _guess_login_result(response)
    if not ok:
        return False, message
    return _verify_generic_login(session, url, response, message)


# 页面上出现这些字样，说明已经登录（或登录后跳到了带注销入口的页面）
LOGIN_SUCCESS_TOKENS = ("登录成功", "成功登录", "注销", "退出", "logout", "logoff", "welcome")
LOGOUT_HINTS = ("logout", "logoff", "quit", "注销", "退出", "下线")


def _fix_encoding(response: requests.Response) -> None:
    charset = _response_charset(response)
    if charset:
        response.encoding = charset
    elif not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or "utf-8"


def _contains_any(text: str, tokens) -> bool:
    lowered = (text or "").lower()
    return any(token.lower() in lowered for token in tokens)


def _has_password_field(html: str) -> bool:
    soup = BeautifulSoup(html or "", "html.parser")
    for inp in soup.find_all("input"):
        if (inp.get("type") or "").lower() == "password":
            return True
        if (inp.get("name") or "").strip().lower() in PASSWORD_KEYS:
            return True
    return False


def _verify_generic_login(
    session: requests.Session,
    portal_url: str,
    response: requests.Response,
    fallback_message: str,
) -> tuple[bool, str]:
    """提交后回访门户页：仍然拿得到登录表单就视为失败。"""
    if _contains_any(response.text or "", LOGIN_SUCCESS_TOKENS):
        return True, "登录成功"
    try:
        check = session.get(portal_url, timeout=6, headers={"Referer": portal_url})
    except requests.RequestException as exc:
        detail = redact_secrets(str(exc))
        log.info("回访门户页失败，按已提交处理: %s", detail)
        return True, f"已提交登录（未能回访验证：{detail}）"
    _fix_encoding(check)
    check_html = check.text or ""
    if _contains_any(check_html, LOGIN_SUCCESS_TOKENS):
        return True, "登录成功"
    if _has_password_field(check_html):
        log.info("回访门户页仍存在密码输入框，判定登录失败")
        return False, "提交后仍然显示登录表单，请检查账号密码是否正确"
    if not check_html.strip():
        return True, fallback_message or "已提交登录"
    return True, "登录成功"


def logout_generic(session: requests.Session, url: str, html: str) -> tuple[bool, str]:
    """在门户页面上找注销入口并访问；找不到就如实说明。"""
    if not (html or "").strip():
        return False, "打不开门户页面，请确认已连上校园网"
    soup = BeautifulSoup(html, "html.parser")
    target = ""
    for link in soup.find_all("a"):
        href = (link.get("href") or "").strip()
        if not href or href.lower().startswith(("javascript:", "#", "mailto:")):
            continue
        if _contains_any(f"{href} {link.get_text() or ''}", LOGOUT_HINTS):
            target = urljoin(url, href)
            break
    if not target:
        for form in soup.find_all("form"):
            action = (form.get("action") or "").strip()
            if action and _contains_any(f"{action} {form.get_text() or ''}", LOGOUT_HINTS):
                target = urljoin(url, action)
                break
    if not target:
        return False, "门户页面上没有找到注销入口，无法自动断开"
    log.info("通用注销: %s", target)
    try:
        response = session.get(target, timeout=10, headers={"Referer": url})
    except requests.RequestException as exc:
        return False, f"注销请求失败: {exc}"
    _fix_encoding(response)
    return True, "已发送注销请求，请再试外网是否已断开"


def login_drcom_api(
    session: requests.Session,
    url: str,
    html: str,
    username: str,
    password: str,
) -> tuple[bool, str]:
    """城市热点「全业务接口」：GET {host}/drcom/login（官方页面 default_login 用的就是它）。

    官方实现（a40.js）：
      account = 账号 + 运营商后缀（ISP_select 的值）
      data = {DDDDD, upass, 0MKKey:123456, R1,R2,R3,R6,para,v6ip, terminal_type, lang}
      result == 1 / 'ok' 表示成功，ret_code == 2 表示已经在线
    """
    params = _drcom_web_params(html) if html else {}
    parsed = urlparse(url)
    host = params.get("serip") or parsed.hostname or PORTAL_HOST
    if not host:
        return False, "登录页地址无效，请确认已连接校园 Wi-Fi"
    scheme = parsed.scheme or "http"
    target = f"{scheme}://{host}/drcom/login"
    base = username.strip()
    suffix = _carrier_suffix(html)
    account = base + suffix if suffix and not base.lower().endswith(suffix.lower()) else base
    data = {
        "callback": f"dr{int(time.time() * 1000) % 100000}",
        "DDDDD": account,
        "upass": password,
        "0MKKey": "123456",
        "R1": "0",
        "R2": "",
        "R3": "0",
        "R6": "0",
        "para": "00",
        "v6ip": "",
        "terminal_type": "1",
        "lang": "zh",
        "v": str(int(time.time() * 1000)),
    }
    log.info(
        "城市热点全业务接口登录: %s?%s 运营商后缀=%s",
        target,
        redact_secrets(urlencode(data)),
        suffix or "(未选)",
    )
    try:
        response = session.get(target, params=data, timeout=10, headers={"Referer": url})
    except requests.RequestException as exc:
        return False, f"认证接口暂不可达: {redact_secrets(str(exc))}"
    text = _decode_body(response, params.get("charset", ""))
    try:
        payload = _jsonp_payload(text)
    except Exception:
        log.info("全业务接口返回无法解析(HTTP %s): %s", response.status_code, " ".join(text.split())[:200])
        return False, f"全业务接口返回无法解析（HTTP {response.status_code}）"
    if not isinstance(payload, dict):
        return False, "全业务接口返回格式异常"
    result = payload.get("result")
    message = str(payload.get("msg") or payload.get("message") or "")
    ret_code = payload.get("ret_code")
    log.info("全业务接口返回: result=%s ret_code=%s msg=%s", result, ret_code, message[:80])
    if str(result) in {"1", "ok"} or result is True:
        return True, message or "校园网登录成功"
    if str(ret_code) == "2" or "already" in message.lower() or "已经在线" in message:
        return True, message or "已经在线"
    if message:
        if _looks_like_credential_error(message):
            return False, message + _drcom_carrier_hint(suffix)
        return False, message
    return False, f"登录失败（result={result}）"


def _portal_api_first() -> bool:
    """该校是否优先走 801 的现代门户接口（常州工学院是，常州大学走老接口）。"""
    try:
        from config import load_config

        return bool(load_config().profile().portal_api_first)
    except Exception:
        return False


def login_drcom_portal_api(
    session: requests.Session,
    url: str,
    html: str,
    username: str,
    password: str,
) -> tuple[bool, str]:
    """现代门户接口：GET http://<网关>:801/eportal/portal/login。

    参数和顺序照抄浏览器实际发出的登录请求（2026-10-06 抓包）：
      callback, login_method=1, user_account, user_password, wlan_user_ip,
      wlan_user_ipv6, wlan_user_mac, wlan_ac_ip, wlan_ac_name, jsVersion=4.2.1,
      terminal_type=1, lang=zh-cn, v, lang=zh
    成功 = JSONP 里 result 为 1/'ok'；ret_code=2 表示已经在线。
    """
    params = _drcom_web_params(html) if html else {}
    parsed = urlparse(url)
    host = params.get("serip") or parsed.hostname or PORTAL_HOST
    if not host:
        return False, "登录页地址无效，请确认已连接校园 Wi-Fi"
    scheme = parsed.scheme or "http"
    hostname = host.split(":")[0]
    target = f"{scheme}://{hostname}:801/eportal/portal/login"
    base = username.strip()
    suffix = _carrier_suffix(html)
    account = base + suffix if suffix and not base.lower().endswith(suffix.lower()) else base
    items = [
        ("callback", f"dr{int(time.time() * 1000) % 10000}"),
        ("login_method", "1"),
        ("user_account", account),
        ("user_password", password),
        ("wlan_user_ip", params.get("ip") or ""),
        ("wlan_user_ipv6", ""),
        ("wlan_user_mac", "000000000000"),
        ("wlan_ac_ip", ""),
        ("wlan_ac_name", ""),
        ("jsVersion", "4.2.1"),
        ("terminal_type", "1"),
        ("lang", "zh-cn"),
        ("v", str(random.randint(500, 10500))),
        ("lang", "zh"),
    ]
    log.info(
        "城市热点门户接口登录: %s?%s",
        target,
        redact_secrets(urlencode(items)),
    )
    try:
        response = session.get(target, params=items, timeout=10, headers={"Referer": url})
    except requests.RequestException as exc:
        return False, f"认证接口暂不可达: {redact_secrets(str(exc))}"
    text = _decode_body(response, params.get("charset", ""))
    try:
        payload = _jsonp_payload(text)
    except Exception:
        log.info("门户接口返回无法解析(HTTP %s): %s", response.status_code, " ".join(text.split())[:200])
        return False, f"门户接口返回无法解析（HTTP {response.status_code}）"
    if not isinstance(payload, dict):
        return False, "门户接口返回格式异常"
    result = payload.get("result")
    message = str(payload.get("msg") or payload.get("message") or "")
    ret_code = payload.get("ret_code")
    log.info("门户接口返回: result=%s ret_code=%s msg=%s", result, ret_code, message[:80])
    if str(result) in {"1", "ok"} or result is True:
        return True, message or "校园网登录成功"
    if str(ret_code) == "2" or "already" in message.lower() or "已经在线" in message:
        return True, message or "已经在线"
    if message:
        if _looks_like_credential_error(message):
            return False, message + _drcom_carrier_hint(suffix)
        return False, message
    return False, f"登录失败（result={result}）"


def login_drcom(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str]:
    api_first = _portal_api_first()
    # 常州大学走老式 /eportal/?c=Portal&a=login。80 端口的 /drcom/login 和
    # /eportal/portal/login 是常州工学院的接口，打到常大上会超时或 404。
    if not api_first:
        ok, message = login_drcom_portal(session, url, html, username, password)
        if ok or _looks_like_credential_error(message):
            return ok, message
        log.info("eportal 接口未成功: %s", message)
        status = drcom_status_after_login(session, url, html)
        if status is True:
            return True, "已经在线"
        if status is False:
            message = f"{message}；回访确认仍未在线"
        return False, message

    # 1) 现代门户接口：801 端口 /eportal/portal/login（常州工学院浏览器实际用的就是这个）
    if api_first:
        ok, message = login_drcom_portal_api(session, url, html, username, password)
        if ok or _looks_like_credential_error(message):
            return ok, message
        log.info("门户接口未成功，改试 AC 本地接口: %s", message)

    # 2) AC 本地接口：/drcom/login（官方 a40.js 里 login_method=0 那一路）
    ok, message = login_drcom_api(session, url, html, username, password)
    if ok or _looks_like_credential_error(message):
        return ok, message
    log.info("AC 本地接口未成功，改试网页表单接口: %s", message)

    # 2b) 该校不优先走门户接口时，在这一步补试一次
    if not api_first:
        ok, message = login_drcom_portal_api(session, url, html, username, password)
        if ok or _looks_like_credential_error(message):
            return ok, message
        log.info("门户接口未成功，改试网页表单接口: %s", message)

    # 3) 页面自己声明的 WebLoginID（ACSetting）流程
    if is_drcom_web_login_page(html):
        ok, message = login_drcom_web_login(session, url, html, username, password)
        if ok or _looks_like_credential_error(message):
            return ok, message
        log.info("WebLoginID 登录未成功，改用 eportal 接口: %s", message)

    # 4) 老式 eportal 接口（常州大学走的这条）
    if _is_drcom_portal(url, html) or not BeautifulSoup(html or "", "html.parser").find("form"):
        ok, message = login_drcom_portal(session, url, html, username, password)
    else:
        soup = BeautifulSoup(html or "", "html.parser")
        form = soup.find("form")
        target = urljoin(url, form.get("action") if form else url)
        data: dict[str, str] = {}
        if form:
            for inp in form.find_all("input"):
                name = inp.get("name")
                if name:
                    data[name] = inp.get("value") or ""
        data["DDDDD"] = username
        data["upass"] = password
        data.setdefault("0MKKey", "Login")
        response = session.post(target, data=data, timeout=12)
        text = response.text or ""
        if any(token in text for token in ("successfully logged in", "登录成功", "您已经成功登录", "logout")):
            return True, "Dr.com 登录成功"
        if any(token in text for token in ("密码错误", "账号错误", "msga", "Info:")):
            return False, "Dr.com 登录失败，请检查账号密码"
        ok, message = _guess_login_result(response)
    if ok or _looks_like_credential_error(message):
        return ok, message

    # 4) 都没给明确结果，回访确认是否已经在线
    status = drcom_status_after_login(session, url, html)
    if status is True:
        return True, "已经在线"
    if status is False:
        message = f"{message}；回访确认仍未在线"
    return False, message


def _qs_first(query: dict[str, list[str]], *keys: str) -> str:
    lower = {k.lower(): v for k, v in query.items()}
    for key in keys:
        values = lower.get(key.lower())
        if values and values[0]:
            return values[0]
    return ""


_HEADER_CHARSET_RE = re.compile(r"charset\s*=\s*([A-Za-z0-9_\-]+)", re.I)
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)


def _response_charset(response: requests.Response, fallback: str = "") -> str:
    """响应自己声明的编码优先（Content-Type 头 → 页面 meta），再退回调用方给的。"""
    match = _HEADER_CHARSET_RE.search(response.headers.get("Content-Type") or "")
    if match:
        return match.group(1)
    match = _META_CHARSET_RE.search(response.content or b"")
    if match:
        return match.group(1).decode("ascii", "ignore")
    return fallback


def _decode_body(response: requests.Response, charset: str = "") -> str:
    """解码响应体：先信响应自己的编码，再用门户页声明的编码兜底。"""
    raw = response.content or b""
    for encoding in (_response_charset(response, charset), "utf-8", "gb18030", response.apparent_encoding):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _drcom_web_params(html: str) -> dict[str, str]:
    """读取 Dr.COM WebLoginID 页面里自己声明的登录参数。"""
    return {
        "serip": _js_assign(html, "v4serip"),
        "ip": _js_assign(html, "ss5") or _js_assign(html, "v46ip"),
        "ac_name": _js_assign(html, "AC"),
        "port": _js_assign(html, "authloginport") or "801",
        "path": _js_assign(html, "authloginpath") or "/eportal/?c=ACSetting&a=Login",
        "param": _js_assign(html, "authloginparam"),
        "user_field": _js_assign(html, "authuserfield") or "DDDDD",
        "pass_field": _js_assign(html, "authpassfield") or "upass",
        "success": _js_assign(html, "authsuccess") or "Dr.COMWebLoginID_3.htm",
        "fail": _js_assign(html, "authfail") or "Dr.COMWebLoginID_2.htm",
        "charset": _js_assign(html, "charset"),
    }


def is_drcom_web_login_page(html: str) -> bool:
    """是不是城市热点 WebLoginID 页面（登录参数写在页面 JS 里）。"""
    html = html or ""
    return bool(_js_assign(html, "authloginpath")) or "dr.comwebloginid" in html.lower()


def _drcom_login_page(html: str) -> bool:
    """这一页还是登录页吗（说明还没认证）。"""
    lowered = (html or "").lower()
    if "dr.comwebloginid_0.htm" in lowered:
        return True
    if "dr.comwebloginid_1.htm" in lowered or "dr.comwebloginid_3.htm" in lowered:
        return False  # 注销页 / 登录成功页，都说明已经认证
    if re.search(r"\buid\s*=", lowered) or re.search(r"\boltime\s*=", lowered):
        return False  # 注销页会带在线信息
    return "authloginpath" in lowered


def _looks_like_credential_error(message: str) -> bool:
    return any(token in (message or "") for token in ("密码", "口令", "账号", "用户不存在"))


# 城市热点 WebLoginID 表单里的固定隐藏字段（官方客户端就是发这一组）
_DRCOM_FORM_DEFAULTS = {
    "R1": "0",
    "R2": "0",
    "R3": "0",
    "R6": "0",
    "para": "00",
    "0MKKey": "123456",
    "buttonClicked": "",
    "redirect_url": "",
    "err_flag": "",
    "username": "",
    "password": "",
    "user": "",
    "cmd": "",
    "Login": "",
}

_DEBUG_SAVED: set[str] = set()


def _save_debug_response(tag: str, response: requests.Response, text: str) -> str:
    """把判定不了的响应存到本地便于排查（每次运行每个 tag 只存一份）。"""
    if tag in _DEBUG_SAVED:
        return ""
    _DEBUG_SAVED.add(tag)
    try:
        folder = data_dir() / "debug"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"{tag}-{stamp}.html"
        path.write_text(
            f"<!-- url: {response.url}\nstatus: {response.status_code}\n"
            f"charset: {_response_charset(response) or '(未声明)'}\n-->\n{text or ''}",
            encoding="utf-8",
            errors="replace",
        )
        (folder / f"{tag}-{stamp}.bin").write_bytes(response.content or b"")
        log.warning("已保存无法判定的响应: %s", path)
        return str(path)
    except OSError as exc:
        log.warning("保存响应失败: %s", exc)
        return ""


def _with_port(host: str, port: str) -> str:
    """拼端口；host 已经带端口时不再重复拼。"""
    host = (host or "").strip()
    if not host or ":" in host:
        return host
    return f"{host}:{port}" if port else host


def login_drcom_web_login(
    session: requests.Session,
    url: str,
    html: str,
    username: str,
    password: str,
) -> tuple[bool, str]:
    """城市热点 WebLoginID / ACSetting 登录：完全按页面声明的字段和接口来。"""
    params = _drcom_web_params(html)
    parsed = urlparse(url)
    host = params["serip"] or parsed.hostname or PORTAL_HOST
    if not host:
        return False, "登录页地址无效，请确认已连接校园 Wi-Fi"
    scheme = parsed.scheme or "http"
    target = f"{scheme}://{_with_port(host, params['port'])}{params['path']}"
    query = [params["param"]] if params["param"] else []
    if params["ip"]:
        query.append(f"wlanuserip={params['ip']}")
    if params["ac_name"]:
        query.append(f"wlanacname={params['ac_name']}")
    if query:
        target += f"{'&' if '?' in target else '?'}{'&'.join(query)}"

    base = username.strip()
    suffix = _carrier_suffix(html)
    account = base + suffix if suffix and not base.lower().endswith(suffix.lower()) else base
    data = {params["user_field"]: account, params["pass_field"]: password}
    data.update(_DRCOM_FORM_DEFAULTS)
    log.info(
        "城市热点 WebLoginID 登录: %s 账号字段=%s 运营商=%s",
        target,
        params["user_field"],
        suffix or "(未选)",
    )
    try:
        response = session.post(
            target,
            data=data,
            timeout=10,
            headers={
                "Referer": url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except requests.RequestException as exc:
        return False, f"认证接口暂不可达: {redact_secrets(str(exc))}"
    text = _decode_body(response, params["charset"])
    final_url = response.url or target
    snippet = " ".join(text.split())[:200]
    log.info(
        "WebLoginID 返回: HTTP %s charset=%s url=%s 片段=%s",
        response.status_code,
        _response_charset(response, params["charset"]),
        final_url,
        snippet,
    )
    success = params["success"].lower()
    fail = params["fail"].lower()
    if success and (success in final_url.lower() or success in text.lower()):
        return True, "校园网登录成功"
    if any(token in text for token in ("登录成功", "已经在线", "已经成功登录", "已在线")):
        return True, "校园网登录成功"
    if fail and (fail in final_url.lower() or fail in text.lower()):
        return False, "账号或密码错误，请检查后重试" + _drcom_carrier_hint(suffix)
    if any(token in text for token in ("密码错误", "口令错误", "账号错误", "用户不存在")):
        return False, "账号或密码错误，请检查后重试" + _drcom_carrier_hint(suffix)
    if response.status_code >= 400:
        return False, f"登录请求失败 HTTP {response.status_code}"
    # 没有明确标志时不能当成功，交给调用方继续用别的接口 / 回访复核
    dump = _save_debug_response("drcom-weblogin", response, text)
    message = f"认证接口返回无法判定的页面（HTTP {response.status_code}）"
    if dump:
        message += f"，原始响应已保存到 {dump}"
    log.info("WebLoginID 结果无法判定: %s", snippet)
    return False, message


def drcom_status_after_login(session: requests.Session, url: str, html: str) -> bool | None:
    """回访门户判断是否已上线：True 已在线 / False 仍未在线 / None 无法判断。"""
    params = _drcom_web_params(html) if html else {}
    parsed = urlparse(url)
    host = params.get("serip") or parsed.hostname
    scheme = parsed.scheme or "http"
    if host and _portal_api_first():
        status_url = f"{scheme}://{host}/drcom/chkstatus"
        try:
            response = session.get(
                status_url,
                params={"callback": "dr1002", "v": str(int(time.time() * 1000))},
                timeout=6,
                headers={"Referer": url},
            )
            payload = _jsonp_payload(_decode_body(response, params.get("charset", "")))
            if isinstance(payload, dict) and "result" in payload:
                online = str(payload.get("result")) == "1"
                log.info("状态查询 /drcom/chkstatus: result=%s", payload.get("result"))
                return online
        except (requests.RequestException, ValueError, TypeError) as exc:
            log.info("状态查询失败，改用回访页面判断: %s", exc)
    try:
        response = session.get(url, timeout=6, headers={"Referer": url})
    except requests.RequestException as exc:
        log.info("回访门户页失败: %s", redact_secrets(str(exc)))
        return None
    text = _decode_body(response, params.get("charset", ""))
    if not text.strip():
        return None
    if _drcom_login_page(text) or _has_password_field(text):
        log.info("回访门户页仍是登录页")
        return False
    if _contains_any(text, ("登录成功", "成功登录", "已经在线", "已在线")):
        log.info("回访门户页显示已登录")
        return True
    log.info("回访门户页无法判断是否在线")
    return None


def _js_assign(html: str, name: str) -> str:
    match = re.search(rf"""\b{re.escape(name)}\s*=\s*['"]([^'"]*)['"]""", html)
    if match:
        return match.group(1).strip()
    match = re.search(rf"""\b{re.escape(name)}\s*=\s*([^;]+);""", html)
    if match:
        return match.group(1).strip().strip("'\"")
    return ""


def _js_single_quoted(html: str, name: str) -> str:
    """取单引号包起来的变量（值里可能含双引号，比如 carrier 的 JSON）。"""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*'([^']*)'", html or "")
    return match.group(1).strip() if match else ""


def _drcom_carriers(html: str) -> list[tuple[str, str, str]]:
    """读页面 carrier 变量里的运营商选项：[(id, 名称, 账号后缀), ...]。"""
    raw = _js_single_quoted(html, "carrier")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return []
    yys = payload.get("yys") if isinstance(payload, dict) else None
    data = (yys or {}).get("data") or []
    options: list[tuple[str, str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        options.append(
            (
                str(item.get("id") or ""),
                str(item.get("name") or ""),
                str(item.get("suffix") or ""),
            )
        )
    return options


def _isp_select_options(html: str) -> list[tuple[str, str, str]]:
    """读页面里 <select name="ISP_select"> 的运营商选项：[(id, 名称, 后缀), ...]。

    新版门户的登录页模板（pc.js）用这个下拉框；老页面里的 carrier 变量是过时列表，
    不能当准（例如老列表没有移动、且电信联通后缀与新版不同）。
    """
    if not html:
        return []
    block = re.search(
        r"""<select[^>]*name=["']?ISP_select["']?[^>]*>(.*?)</select>""",
        html,
        re.I | re.S,
    )
    if not block:
        return []
    options: list[tuple[str, str, str]] = []
    for index, item in enumerate(re.finditer(r"<option[^>]*>(.*?)</option>", block.group(1), re.I | re.S)):
        raw = item.group(0)
        value_match = re.search(r"""value=["']?([^"'>\s]*)""", raw, re.I)
        value = (value_match.group(1) if value_match else "").strip()
        label = re.sub(r"<[^>]+>", "", item.group(1)).strip()
        if value == "-1" or (not label and not value):
            continue
        options.append((str(index + 1), label or value, value))
    return options


def _drcom_carrier_hint(suffix: str) -> str:
    """选了运营商却仍然账号错误时，提醒可能是运营商没选对。"""
    if suffix:
        return ""
    try:
        from config import carrier_options, load_config

        if not carrier_options(load_config().resolved_school()):
            return ""
    except Exception:
        return ""
    return "；若账号属于移动/联通/电信，请在设置的「服务类型」里选对运营商"


_IF_TYPE_ETHERNET = 6
_IF_TYPE_LOOPBACK = 24
_IF_TYPE_IEEE80211 = 71
_IF_TYPE_TUNNEL = 131
_IF_OPER_UP = 1

_VIRTUAL_NIC_RE = re.compile(
    r"wintun|meta|clash|mihomo|\btun\b|tunnel|tap-?win|tap\b|wireguard|sing-?box|"
    r"vmware|virtualbox|hyper-?v|vethernet|docker|vpn|softether|openvpn|"
    r"zerotier|tailscale|netch",
    re.I,
)


@dataclass(frozen=True)
class NicIPv4:
    ip: str
    friendly_name: str
    description: str
    if_type: int
    virtual: bool
    physical: bool


def _is_fake_ip(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(item) for item in parts]
    except ValueError:
        return False
    return nums[0] == 198 and nums[1] in {18, 19}


def _is_campus_ip(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(item) for item in parts]
    except ValueError:
        return False
    if any(item < 0 or item > 255 for item in nums):
        return False
    if _is_fake_ip(ip):
        return False
    if nums[0] == 10:
        return True
    if nums[0] == 192 and nums[1] == 168:
        return True
    if nums[0] == 172 and 16 <= nums[1] <= 31:
        return True
    return False


def _is_host_like(ip: str) -> bool:
    return ip.endswith(".1") or ip.endswith(".255")


def _is_inbox_tunnel(name: str, description: str) -> bool:
    return bool(re.search(r"teredo|isatap|6to4", f"{name} {description}", re.I))


def _is_virtual_nic(name: str, description: str, if_type: int) -> bool:
    if if_type == _IF_TYPE_LOOPBACK or _is_inbox_tunnel(name, description):
        return False
    if if_type == _IF_TYPE_TUNNEL:
        return True
    blob = f"{name} {description}"
    return bool(_VIRTUAL_NIC_RE.search(blob))


def _is_physical_nic(if_type: int) -> bool:
    return if_type in {_IF_TYPE_IEEE80211, _IF_TYPE_ETHERNET}


def _outbound_ip(host: str) -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.2)
    try:
        sock.connect((host or PORTAL_HOST, 801))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _wchar_at(ptr: int | None) -> str:
    import ctypes

    if not ptr:
        return ""
    try:
        return ctypes.wstring_at(ptr) or ""
    except (ValueError, OSError):
        return ""


def _win_nic_ipv4s() -> list[NicIPv4]:
    import ctypes
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi")
    af_inet = 2
    overflow = 111
    flags = 0x000E  # skip anycast / multicast / DNS
    size = wintypes.ULONG(15000)
    buf = ctypes.create_string_buffer(size.value)

    class SOCKADDR(ctypes.Structure):
        _fields_ = [("sa_family", wintypes.USHORT), ("sa_data", ctypes.c_ubyte * 14)]

    class SOCKET_ADDRESS(ctypes.Structure):
        _fields_ = [
            ("lpSockaddr", ctypes.POINTER(SOCKADDR)),
            ("iSockaddrLength", wintypes.INT),
        ]

    class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
        pass

    IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
        ("Length", wintypes.ULONG),
        ("Flags", wintypes.DWORD),
        ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
        ("Address", SOCKET_ADDRESS),
    ]

    class IP_ADAPTER_ADDRESSES(ctypes.Structure):
        pass

    IP_ADAPTER_ADDRESSES._fields_ = [
        ("Length", wintypes.ULONG),
        ("IfIndex", wintypes.DWORD),
        ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
        ("AdapterName", ctypes.c_void_p),
        ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_void_p),
        ("Description", ctypes.c_void_p),
        ("FriendlyName", ctypes.c_void_p),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.ULONG),
        ("Flags", wintypes.ULONG),
        ("Mtu", wintypes.ULONG),
        ("IfType", wintypes.ULONG),
        ("OperStatus", ctypes.c_int),
    ]

    ret = iphlpapi.GetAdaptersAddresses(af_inet, flags, None, buf, ctypes.byref(size))
    if ret == overflow:
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetAdaptersAddresses(af_inet, flags, None, buf, ctypes.byref(size))
    if ret != 0:
        return []

    nics: list[NicIPv4] = []
    adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
    while adapter:
        item = adapter.contents
        if item.OperStatus != _IF_OPER_UP:
            adapter = item.Next
            continue
        if_type = int(item.IfType)
        if if_type == _IF_TYPE_LOOPBACK:
            adapter = item.Next
            continue
        friendly = _wchar_at(item.FriendlyName)
        description = _wchar_at(item.Description)
        virtual = _is_virtual_nic(friendly, description, if_type)
        physical = _is_physical_nic(if_type) and not virtual
        unicast = item.FirstUnicastAddress
        found_ip = False
        while unicast:
            sockaddr = unicast.contents.Address.lpSockaddr
            if sockaddr and sockaddr.contents.sa_family == af_inet:
                data = sockaddr.contents.sa_data
                ip = f"{data[2]}.{data[3]}.{data[4]}.{data[5]}"
                nics.append(
                    NicIPv4(
                        ip=ip,
                        friendly_name=friendly,
                        description=description,
                        if_type=if_type,
                        virtual=virtual,
                        physical=physical,
                    )
                )
                found_ip = True
            unicast = unicast.contents.Next
        if not found_ip and virtual:
            nics.append(
                NicIPv4(
                    ip="",
                    friendly_name=friendly,
                    description=description,
                    if_type=if_type,
                    virtual=True,
                    physical=False,
                )
            )
        adapter = item.Next
    return nics


def _nic_sort_key(nic: NicIPv4) -> tuple[int, int, str]:
    if nic.if_type == _IF_TYPE_IEEE80211:
        kind = 0
    elif nic.if_type == _IF_TYPE_ETHERNET:
        kind = 1
    else:
        kind = 2
    return (0 if nic.physical else 1, kind, nic.ip)


def list_nic_ipv4s() -> list[NicIPv4]:
    import sys

    if sys.platform != "win32":
        return []
    try:
        return _win_nic_ipv4s()
    except Exception:
        log.warning("读取网卡地址失败", exc_info=True)
        return []


def virtual_nic_names() -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for nic in list_nic_ipv4s():
        if not nic.virtual:
            continue
        label = nic.friendly_name or nic.description or nic.ip or "虚拟网卡"
        if label in seen:
            continue
        seen.add(label)
        names.append(label)
    return names


def virtual_nic_active() -> bool:
    return bool(virtual_nic_names())


def _virtual_ips() -> set[str]:
    return {nic.ip for nic in list_nic_ipv4s() if nic.virtual and nic.ip}


def _is_virtual_ip(ip: str) -> bool:
    ip = (ip or "").strip()
    return bool(ip) and ip in _virtual_ips()


def _usable_client_ip(ip: str) -> bool:
    ip = (ip or "").strip()
    if not ip or not _is_campus_ip(ip) or _is_host_like(ip) or _is_fake_ip(ip):
        return False
    return not _is_virtual_ip(ip)


def preferred_source_ip() -> str:
    ips = campus_ipv4s()
    return ips[0] if ips else ""


def nic_summary() -> str:
    parts: list[str] = []
    for nic in list_nic_ipv4s():
        tag = "虚拟" if nic.virtual else ("物理" if nic.physical else "其他")
        label = nic.friendly_name or nic.description or "?"
        addr = nic.ip or "-"
        parts.append(f"{addr} ({label}/{tag})")
    return "; ".join(parts) or "无"


def campus_ipv4s() -> list[str]:
    ips: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        ip = (ip or "").strip()
        if not ip or ip in seen or not _usable_client_ip(ip):
            return
        seen.add(ip)
        ips.append(ip)

    try:
        from config import load_config

        host = urlparse(load_config().normalized_portal_url()).hostname or PORTAL_HOST
    except Exception:
        host = PORTAL_HOST
    outbound = _outbound_ip(host)
    if outbound and not _usable_client_ip(outbound):
        log.info("出站 IP %s 不是可用的校园网地址，已忽略", outbound)
    else:
        add(outbound)
    physical: list[NicIPv4] = []
    other: list[NicIPv4] = []
    for nic in list_nic_ipv4s():
        if nic.virtual or not nic.ip or not _usable_client_ip(nic.ip):
            continue
        if nic.physical:
            physical.append(nic)
        else:
            other.append(nic)
    for nic in sorted(physical, key=_nic_sort_key) + sorted(other, key=_nic_sort_key):
        add(nic.ip)
    return ips


def _account_suffix() -> str:
    try:
        from config import load_config

        return (load_config().account_suffix or "").strip()
    except Exception:
        return ""


def _selected_carrier_name() -> str:
    """设置窗口里选的运营商名称（服务类型）。"""
    try:
        from config import load_config

        return (load_config().carrier or "").strip()
    except Exception:
        return ""


def _carrier_suffix(html: str) -> str:
    """账号后缀：门户页声明的运营商列表优先（按名称匹配），否则用本机保存的后缀。"""
    saved = _account_suffix()
    name = _selected_carrier_name()
    if not name:
        return saved
    for _cid, cname, csuffix in _drcom_carriers(html):
        if cname == name:
            return csuffix
    log.info("门户页没声明运营商「%s」，改用保存的后缀 %s", name, saved or "(无)")
    return saved


def _account_variants(username: str) -> list[str]:
    base = username.strip()
    suffix = _account_suffix()
    if suffix and not base.lower().endswith(suffix.lower()):
        base_with_suffix = base + suffix
    else:
        base_with_suffix = base
    variants: list[str] = []
    for item in (f",b,{base_with_suffix}", f",a,{base_with_suffix}", base_with_suffix, f",b,{base}", base):
        if item and item not in variants:
            variants.append(item)
    return variants


def _candidate_ips(url: str, html: str) -> list[str]:
    query = parse_qs(urlparse(url).query)
    ordered: list[str] = []

    def add(ip: str) -> None:
        ip = (ip or "").strip()
        if not ip or not _usable_client_ip(ip) or ip in ordered:
            return
        ordered.append(ip)

    add(_qs_first(query, "wlanuserip", "ip", "userip", "user-ip", "UserIP"))
    add(_js_assign(html, "ss5"))
    add(_js_assign(html, "v46ip"))
    add(_js_assign(html, "v4ip"))
    for ip in campus_ipv4s():
        add(ip)
    return ordered


def _drcom_ac_name(query: dict[str, list[str]], cfg_query: dict[str, list[str]], html: str, host: str) -> str:
    """AC 名只在页面或链接里声明过时才用；常州大学的默认值不能带到别的学校。"""
    name = (
        _qs_first(query, "wlanacname", "sysname")
        or _qs_first(cfg_query, "wlanacname", "sysname")
        or _js_assign(html, "AC")
    )
    if name:
        return name
    return DEFAULT_WLAN_AC_NAME if (host or "").strip() == PORTAL_HOST else ""


def login_drcom_portal(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    try:
        from config import load_config

        cfg_url = load_config().normalized_portal_url()
    except Exception:
        cfg_url = ""
    cfg_parsed = urlparse(cfg_url)
    cfg_query = parse_qs(cfg_parsed.query)
    query = parse_qs(parsed.query)
    host = _js_assign(html, "v4serip") or parsed.hostname or cfg_parsed.hostname or PORTAL_HOST
    if not host:
        return False, "登录页地址无效，请确认已连接校园 Wi-Fi"
    ips = _candidate_ips(url, html)
    if not ips:
        return False, "无法获取本机校园网 IP，请确认已连接校园 Wi-Fi 后再试"
    accounts = _account_variants(username)
    scheme = parsed.scheme or cfg_parsed.scheme or "http"
    login_url = f"{scheme}://{host}:801/eportal/"
    last_message = "登录失败"
    for user_ip in ips:
        for account in accounts:
            params = {
                "c": "Portal",
                "a": "login",
                "callback": "dr1003",
                "login_method": "1",
                "user_account": account,
                "user_password": password,
                "wlan_user_ip": user_ip,
                "wlan_user_ipv6": "",
                "wlan_user_mac": (
                    _qs_first(query, "wlanusermac", "mac", "usermac")
                    or _qs_first(cfg_query, "wlanusermac", "mac")
                    or "000000000000"
                ).replace(":", "").replace("-", ""),
                "wlan_ac_ip": _qs_first(query, "wlanacip", "acip") or _qs_first(cfg_query, "wlanacip", "acip"),
                "wlan_ac_name": _drcom_ac_name(query, cfg_query, html, host),
                "jsVersion": _js_assign(html, "jsVersion") or "3.0",
                "v": str(int(time.time() * 1000)),
            }
            log.info(
                "城市热点 Portal 登录: %s account=%s ip=%s ac=%s",
                login_url,
                account,
                user_ip,
                params["wlan_ac_name"],
            )
            try:
                response = session.get(
                    login_url,
                    params=params,
                    timeout=(0.5, 1.5),
                    headers={"Referer": url, "Accept": "*/*"},
                )
            except requests.RequestException as exc:
                last_message = f"认证接口暂不可达: {redact_secrets(str(exc))}"
                log.info(last_message)
                return False, last_message
            raw = response.content or b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", "replace")
            try:
                payload = _jsonp_payload(text)
            except Exception:
                last_message = f"Portal 返回无法解析: {text[:200]}"
                log.info(last_message)
                continue
            log.info("Portal 返回: %s", {k: payload.get(k) for k in ("result", "ret_code", "msg", "ret_msg")})
            result = payload.get("result")
            message = str(payload.get("msg") or payload.get("ret_msg") or payload.get("message") or "")
            ret_code = payload.get("ret_code")
            last_message = message or f"登录失败 ret_code={ret_code}"
            if str(result) in {"1", "ok"} or result is True:
                return True, message or "校园网登录成功"
            if str(ret_code) == "2" or "already" in message.lower() or "已经在线" in message:
                return True, message or "已经在线"
            if str(ret_code) in {"3", "4"} or "密码" in message or "口令" in message:
                return False, last_message
    return False, last_message


def logout_drcom_portal(session: requests.Session, url: str, html: str, username: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    try:
        from config import load_config

        cfg_url = load_config().normalized_portal_url()
    except Exception:
        cfg_url = ""
    cfg_parsed = urlparse(cfg_url)
    cfg_query = parse_qs(cfg_parsed.query)
    query = parse_qs(parsed.query)
    host = _js_assign(html, "v4serip") or parsed.hostname or cfg_parsed.hostname or PORTAL_HOST
    if not host:
        return False, "登录页地址无效，请确认已连接校园 Wi-Fi"
    scheme = parsed.scheme or cfg_parsed.scheme or "http"
    ips = _candidate_ips(url, html)
    ac_name = _drcom_ac_name(query, cfg_query, html, host)
    ac_ip = _qs_first(query, "wlanacip", "acip") or _qs_first(cfg_query, "wlanacip", "acip")
    accounts = _account_variants(username) if username else ["drcom"]
    last_message = "注销失败"
    logout_url = f"{scheme}://{host}:801/eportal/"
    for user_ip in ips or [""]:
        for account in accounts[:2]:
            params = {
                "c": "Portal",
                "a": "logout",
                "callback": "dr1004",
                "login_method": "1",
                "user_account": account,
                "wlan_user_ip": user_ip,
                "wlan_user_ipv6": "",
                "wlan_user_mac": "000000000000",
                "wlan_ac_ip": ac_ip,
                "wlan_ac_name": ac_name,
                "jsVersion": _js_assign(html, "jsVersion") or "3.0",
                "v": str(int(time.time() * 1000)),
            }
            log.info("城市热点 Portal 注销: account=%s ip=%s", account, user_ip)
            try:
                response = session.get(
                    logout_url,
                    params=params,
                    timeout=10,
                    headers={"Referer": url, "Accept": "*/*"},
                )
            except requests.RequestException as exc:
                last_message = str(exc)
                continue
            raw = response.content or b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", "replace")
            try:
                payload = _jsonp_payload(text)
            except Exception:
                continue
            log.info("注销返回: %s", {k: payload.get(k) for k in ("result", "ret_code", "msg")})
            result = payload.get("result")
            message = str(payload.get("msg") or payload.get("message") or "")
            last_message = message or f"result={result}"
            if str(result) in {"1", "ok"} or result is True:
                return True, message or "已注销校园网"
    try:
        session.get(f"{scheme}://{host}:1028/F.htm", timeout=8, headers={"Referer": url})
        session.get(
            f"{scheme}://{host}:801/eportal/",
            params={"c": "ACSetting", "a": "Logout", "ver": "1.0"},
            timeout=8,
        )
    except requests.RequestException:
        pass
    return True, last_message or "已发送注销请求，请再试外网是否已断开"


def login_ruijie(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    query = parsed.query
    if not query:
        query_match = re.search(r"""[?&](wlanuserip|userip|switchip)=""", url, re.I)
        if query_match:
            query = url.split("?", 1)[-1]
    html_l = html.lower()
    use_new = "eportal/portal/login" in html_l or "/eportal/portal/" in url.lower()
    if use_new:
        ok, message = _ruijie_new(session, url, username, password)
        if ok:
            return ok, message
        log.info("锐捷新接口未成功，尝试 InterFace.do: %s", message)
    return _ruijie_old(session, url, query, username, password)


def _ruijie_page_service(session: requests.Session, base: str, query: str) -> str:
    try:
        response = session.post(
            urljoin(base, "/eportal/InterFace.do?method=pageInfo"),
            data={"queryString": query},
            timeout=8,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        info = response.json()
        services = str(info.get("service") or "").split("`")
        services = [item for item in services if item.strip()]
        return services[0] if services else ""
    except Exception as exc:
        log.info("读取锐捷 service 失败，使用空服务: %s", exc)
        return ""


def _ruijie_old(session: requests.Session, url: str, query: str, username: str, password: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    service = _ruijie_page_service(session, base, query)
    login_url = urljoin(base, "/eportal/InterFace.do?method=login")
    data = {
        "userId": username,
        "password": password,
        "service": service,
        "queryString": query,
        "operatorPwd": "",
        "operatorUserId": "",
        "validcode": "",
        "passwordEncrypt": "false",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": url,
    }
    response = session.post(login_url, data=data, headers=headers, timeout=12)
    try:
        payload = response.json()
    except Exception:
        payload = {}
        text = response.text or ""
        if "success" in text.lower():
            return True, "锐捷登录成功"
        return False, f"锐捷登录返回无法解析: {text[:120]}"
    result = str(payload.get("result") or "").lower()
    message = str(payload.get("message") or payload.get("msg") or "")
    if result == "success":
        return True, message or "锐捷登录成功"
    return False, message or "锐捷登录失败"


def _query_first(query: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        values = query.get(key) or query.get(key.lower()) or query.get(key.upper())
        if values:
            return values[0]
    lower = {k.lower(): v for k, v in query.items()}
    for key in keys:
        values = lower.get(key.lower())
        if values:
            return values[0]
    return ""


def _ruijie_new(session: requests.Session, url: str, username: str, password: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qs(parsed.query)
    login_url = urljoin(base, "/eportal/portal/login")
    params = {
        "callback": "dr1003",
        "login_method": "1",
        "user_account": username,
        "user_password": password,
        "wlan_user_ip": _query_first(query, "wlanuserip", "wlan_user_ip", "userip"),
        "wlan_user_ipv6": "",
        "wlan_user_mac": _query_first(query, "wlanusermac", "wlan_user_mac", "mac") or "000000000000",
        "wlan_ac_ip": _query_first(query, "wlanacip", "wlan_ac_ip"),
        "wlan_ac_name": _query_first(query, "wlanacname", "wlan_ac_name"),
        "jsVersion": "4.2.1",
        "terminal_type": "1",
        "lang": "zh-cn",
        "v": str(int(time.time() * 1000)),
    }
    response = session.get(login_url, params=params, timeout=12, headers={"Referer": url})
    try:
        payload = _jsonp_payload(response.text)
    except Exception:
        return False, f"锐捷新接口返回无法解析: {(response.text or '')[:120]}"
    result = payload.get("result")
    message = str(payload.get("msg") or payload.get("message") or "")
    if str(result) in {"1", "ok", "success"} or result is True:
        return True, message or "锐捷登录成功"
    return False, message or "锐捷登录失败"


# --- 深澜 srun（算法与开源脚本一致） ---

_PADCHAR = "="
_ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"


def _getbyte(s: str, i: int) -> int:
    value = ord(s[i])
    if value > 255:
        raise ValueError("srun base64 仅支持字节字符")
    return value


def _srun_base64(s: str) -> str:
    if not s:
        return s
    x: list[str] = []
    imax = len(s) - len(s) % 3
    for i in range(0, imax, 3):
        b10 = (_getbyte(s, i) << 16) | (_getbyte(s, i + 1) << 8) | _getbyte(s, i + 2)
        x.append(_ALPHA[(b10 >> 18) & 63])
        x.append(_ALPHA[(b10 >> 12) & 63])
        x.append(_ALPHA[(b10 >> 6) & 63])
        x.append(_ALPHA[b10 & 63])
    i = imax
    if len(s) - imax == 1:
        b10 = _getbyte(s, i) << 16
        x.append(_ALPHA[(b10 >> 18) & 63] + _ALPHA[(b10 >> 12) & 63] + _PADCHAR + _PADCHAR)
    elif len(s) - imax == 2:
        b10 = (_getbyte(s, i) << 16) | (_getbyte(s, i + 1) << 8)
        x.append(
            _ALPHA[(b10 >> 18) & 63]
            + _ALPHA[(b10 >> 12) & 63]
            + _ALPHA[(b10 >> 6) & 63]
            + _PADCHAR
        )
    return "".join(x)


def _ordat(msg: str, idx: int) -> int:
    return ord(msg[idx]) if len(msg) > idx else 0


def _sencode(msg: str, key: bool) -> list[int]:
    length = len(msg)
    pwd = []
    for i in range(0, length, 4):
        pwd.append(
            _ordat(msg, i)
            | _ordat(msg, i + 1) << 8
            | _ordat(msg, i + 2) << 16
            | _ordat(msg, i + 3) << 24
        )
    if key:
        pwd.append(length)
    return pwd


def _lencode(msg: list[int], key: bool) -> str | None:
    length = len(msg)
    ll = (length - 1) << 2
    if key:
        m = msg[length - 1]
        if m < ll - 3 or m > ll:
            return None
        ll = m
    chunks = []
    for item in msg:
        chunks.append(
            chr(item & 0xFF)
            + chr((item >> 8) & 0xFF)
            + chr((item >> 16) & 0xFF)
            + chr((item >> 24) & 0xFF)
        )
    result = "".join(chunks)
    return result[:ll] if key else result


def _xencode(msg: str, key: str) -> str:
    if msg == "":
        return ""
    pwd = _sencode(msg, True)
    pwdk = _sencode(key, False)
    if len(pwdk) < 4:
        pwdk = pwdk + [0] * (4 - len(pwdk))
    n = len(pwd) - 1
    z = pwd[n]
    y = pwd[0]
    c = 0x86014019 | 0x183639A0
    q = math.floor(6 + 52 / (n + 1))
    d = 0
    while q > 0:
        d = (d + c) & (0x8CE0D9BF | 0x731F2640)
        e = (d >> 2) & 3
        p = 0
        while p < n:
            y = pwd[p + 1]
            m = (z >> 5 ^ y << 2) + ((y >> 3 ^ z << 4) ^ (d ^ y))
            m = m + (pwdk[(p & 3) ^ e] ^ z)
            pwd[p] = (pwd[p] + m) & (0xEFB8D130 | 0x10472ECF)
            z = pwd[p]
            p += 1
        y = pwd[0]
        m = (z >> 5 ^ y << 2) + ((y >> 3 ^ z << 4) ^ (d ^ y))
        m = m + (pwdk[(p & 3) ^ e] ^ z)
        pwd[n] = (pwd[n] + m) & (0xBB390742 | 0x44C6F8BD)
        z = pwd[n]
        q -= 1
    encoded = _lencode(pwd, False)
    return encoded or ""


def _srun_md5(password: str, token: str) -> str:
    return hmac.new(token.encode(), password.encode(), hashlib.md5).hexdigest()


def _srun_sha1(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()


def _srun_info(username: str, password: str, ip: str, ac_id: str, token: str) -> str:
    payload = {
        "username": username,
        "password": password,
        "ip": ip,
        "acid": ac_id,
        "enc_ver": "srun_bx1",
    }
    raw = json.dumps(payload, separators=(",", ":"))
    return "{SRBX1}" + _srun_base64(_xencode(raw, token))


def _extract_ac_id(url: str, html: str) -> str:
    parsed = parse_qs(urlparse(url).query)
    for key in ("ac_id", "acId", "ac-id"):
        if parsed.get(key):
            return parsed[key][0]
    match = re.search(r"""ac_id["'\s:=]+["']?(\d+)""", html, re.I)
    if match:
        return match.group(1)
    return "1"


def login_srun(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    ac_id = _extract_ac_id(url, html)
    callback = f"jQuery{int(time.time() * 1000)}"
    challenge_url = urljoin(origin, "/cgi-bin/get_challenge")
    challenge = session.get(
        challenge_url,
        params={
            "callback": callback,
            "username": username,
            "ip": "",
            "_": int(time.time() * 1000),
        },
        timeout=12,
        headers={"Referer": url},
    )
    try:
        info = _jsonp_payload(challenge.text)
    except Exception:
        return False, f"深澜 get_challenge 无法解析: {(challenge.text or '')[:120]}"
    token = str(info.get("challenge") or "")
    ip = str(info.get("client_ip") or info.get("online_ip") or "")
    if not token:
        return False, "深澜未返回 challenge"
    n_val = "200"
    type_val = "1"
    encoded_info = _srun_info(username, password, ip, ac_id, token)
    hmd5 = _srun_md5(password, token)
    chkstr = token + username
    chkstr += token + hmd5
    chkstr += token + ac_id
    chkstr += token + ip
    chkstr += token + n_val
    chkstr += token + type_val
    chkstr += token + encoded_info
    chksum = _srun_sha1(chkstr)
    login_url = urljoin(origin, "/cgi-bin/srun_portal")
    response = session.get(
        login_url,
        params={
            "callback": callback,
            "action": "login",
            "username": username,
            "password": "{MD5}" + hmd5,
            "os": "Windows",
            "name": "Windows",
            "double_stack": "0",
            "chksum": chksum,
            "info": encoded_info,
            "ac_id": ac_id,
            "ip": ip,
            "n": n_val,
            "type": type_val,
            "_": int(time.time() * 1000),
        },
        timeout=12,
        headers={"Referer": url},
    )
    try:
        payload = _jsonp_payload(response.text)
    except Exception:
        return False, f"深澜登录返回无法解析: {(response.text or '')[:120]}"
    res = str(payload.get("res") or payload.get("error") or "").lower()
    message = str(payload.get("suc_msg") or payload.get("error_msg") or payload.get("error") or "")
    if res in {"ok", "success"} or "login_ok" in message.lower() or payload.get("suc_msg"):
        if "already" in message.lower() or "已经" in message:
            return True, message or "已在线"
        return True, message or "深澜登录成功"
    return False, message or "深澜登录失败"


def _guess_result_text(text: str, status_code: int = 200) -> tuple[bool, str]:
    text = text or ""
    lowered = text.lower()
    success_tokens = ("登录成功", "login success", "auth success", "success\":true", '"result":"success"', "successfully")
    fail_tokens = ("密码错误", "账号错误", "用户不存在", "login fail", "auth fail", "password error", "失败")
    if any(token in lowered or token in text for token in success_tokens):
        return True, "登录成功"
    if any(token in lowered or token in text for token in fail_tokens):
        return False, "登录失败，请检查账号密码"
    if status_code >= 400:
        return False, f"登录请求失败 HTTP {status_code}"
    return True, f"已提交登录（HTTP {status_code}），将再检测是否已联网"


def _guess_login_result(response: requests.Response) -> tuple[bool, str]:
    return _guess_result_text(response.text or "", response.status_code)
