"""常见校园网 Web 门户登录适配。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import APP_NAME

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
        return False, "页面中没有找到登录表单，可能是需要专用适配的门户"

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
    return _guess_login_result(response)


def login_drcom(session: requests.Session, url: str, html: str, username: str, password: str) -> tuple[bool, str]:
    if _is_drcom_portal(url, html) or not BeautifulSoup(html or "", "html.parser").find("form"):
        return login_drcom_portal(session, url, html, username, password)
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
    return _guess_login_result(response)


def _qs_first(query: dict[str, list[str]], *keys: str) -> str:
    lower = {k.lower(): v for k, v in query.items()}
    for key in keys:
        values = lower.get(key.lower())
        if values and values[0]:
            return values[0]
    return ""


def _js_assign(html: str, name: str) -> str:
    match = re.search(rf"""\b{re.escape(name)}\s*=\s*['"]([^'"]*)['"]""", html)
    if match:
        return match.group(1).strip()
    match = re.search(rf"""\b{re.escape(name)}\s*=\s*([^;]+);""", html)
    if match:
        return match.group(1).strip().strip("'\"")
    return ""


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
    if nums[0] == 10:
        return True
    if nums[0] == 192 and nums[1] == 168:
        return True
    if nums[0] == 172 and 16 <= nums[1] <= 31:
        return True
    return False


def _outbound_ip(host: str) -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.2)
    try:
        sock.connect((host or "211.103.11.101", 801))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _win_unicast_ipv4() -> list[str]:
    import ctypes
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi")
    af_inet = 2
    overflow = 111
    size = wintypes.ULONG(15000)
    buf = ctypes.create_string_buffer(size.value)

    class SOCKADDR(ctypes.Structure):
        _fields_ = [("sa_family", wintypes.USHORT), ("sa_data", ctypes.c_char * 14)]

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
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ]

    ret = iphlpapi.GetAdaptersAddresses(af_inet, 0, None, buf, ctypes.byref(size))
    if ret == overflow:
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetAdaptersAddresses(af_inet, 0, None, buf, ctypes.byref(size))
    if ret != 0:
        return []

    ips: list[str] = []
    adapter = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
    while adapter:
        unicast = adapter.contents.FirstUnicastAddress
        while unicast:
            sockaddr = unicast.contents.Address.lpSockaddr
            if sockaddr and sockaddr.contents.sa_family == af_inet:
                data = bytes(sockaddr.contents.sa_data)
                ip = f"{data[2]}.{data[3]}.{data[4]}.{data[5]}"
                ips.append(ip)
            unicast = unicast.contents.Next
        adapter = adapter.contents.Next
    return ips


def campus_ipv4s() -> list[str]:
    import sys

    ips: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        ip = (ip or "").strip()
        if not ip or ip in seen or not _is_campus_ip(ip):
            return
        if ip.endswith(".1") or ip.endswith(".255"):
            return
        seen.add(ip)
        ips.append(ip)

    try:
        from config import load_config

        host = urlparse(load_config().normalized_portal_url()).hostname or "211.103.11.101"
    except Exception:
        host = "211.103.11.101"
    add(_outbound_ip(host))
    if sys.platform == "win32":
        try:
            for ip in _win_unicast_ipv4():
                add(ip)
        except Exception:
            log.debug("读取网卡地址失败", exc_info=True)
    return ips


def _account_suffix() -> str:
    try:
        from config import load_config

        return (load_config().account_suffix or "").strip()
    except Exception:
        return ""


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
        if not ip or not _is_campus_ip(ip) or ip in ordered:
            return
        if ip.endswith(".1") or ip.endswith(".255"):
            return
        ordered.append(ip)

    add(_qs_first(query, "wlanuserip", "ip", "userip", "user-ip", "UserIP"))
    add(_js_assign(html, "ss5"))
    add(_js_assign(html, "v46ip"))
    add(_js_assign(html, "v4ip"))
    for ip in campus_ipv4s():
        add(ip)
    return ordered


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
    host = _js_assign(html, "v4serip") or parsed.hostname or cfg_parsed.hostname or ""
    if not host:
        return False, "登录页地址无效"
    ips = _candidate_ips(url, html)
    if not ips:
        return False, "无法确定认证用的内网 IP（wlanuserip），请用未登录时的完整跳转链接"
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
                "wlan_ac_name": _qs_first(query, "wlanacname", "sysname")
                or _qs_first(cfg_query, "wlanacname", "sysname"),
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
                last_message = f"认证接口暂不可达: {exc}"
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
    host = _js_assign(html, "v4serip") or parsed.hostname or cfg_parsed.hostname or ""
    if not host:
        return False, "登录页地址无效"
    scheme = parsed.scheme or cfg_parsed.scheme or "http"
    ips = _candidate_ips(url, html)
    ac_name = _qs_first(query, "wlanacname", "sysname") or _qs_first(cfg_query, "wlanacname", "sysname")
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


def _guess_login_result(response: requests.Response) -> tuple[bool, str]:
    text = response.text or ""
    lowered = text.lower()
    success_tokens = ("登录成功", "login success", "auth success", "success\":true", '"result":"success"', "successfully")
    fail_tokens = ("密码错误", "账号错误", "用户不存在", "login fail", "auth fail", "password error", "失败")
    if any(token in lowered or token in text for token in success_tokens):
        return True, "登录成功"
    if any(token in lowered or token in text for token in fail_tokens):
        return False, "登录失败，请检查账号密码"
    if response.status_code >= 400:
        return False, f"登录请求失败 HTTP {response.status_code}"
    return True, f"已提交登录（HTTP {response.status_code}），将再检测是否已联网"
