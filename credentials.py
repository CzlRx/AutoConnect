"""用 Windows 凭据管理器保存密码；失败时回退到 DPAPI 加密文件。"""

from __future__ import annotations

import base64
import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

from config import APP_NAME, data_dir

log = logging.getLogger(APP_NAME)

SERVICE_NAME = "AutoConnect-Campus"
KEYRING_USERNAME = "campus-portal"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_file() -> Path:
    return data_dir() / "password.dpapi"


def _crypt32():
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    return crypt32


def _crypt_protect(plain: bytes) -> bytes:
    crypt32 = _crypt32()
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(plain, len(plain))
    in_blob = DATA_BLOB(len(plain), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _crypt_unprotect(cipher: bytes) -> bytes:
    crypt32 = _crypt32()
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(cipher, len(cipher))
    in_blob = DATA_BLOB(len(cipher), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _save_dpapi(password: str) -> None:
    blob = _crypt_protect(password.encode("utf-8"))
    _dpapi_file().write_text(base64.b64encode(blob).decode("ascii"), encoding="utf-8")
    log.info("密码已用 DPAPI 保存到本地加密文件")


def _load_dpapi() -> str | None:
    path = _dpapi_file()
    if not path.exists():
        return None
    try:
        blob = base64.b64decode(path.read_text(encoding="utf-8").strip())
        return _crypt_unprotect(blob).decode("utf-8")
    except Exception as exc:
        log.warning("读取 DPAPI 密码失败: %s", exc)
        return None


def _delete_dpapi() -> None:
    path = _dpapi_file()
    if path.exists():
        path.unlink()


def save_password(password: str) -> None:
    password = password or ""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, KEYRING_USERNAME, password)
        _delete_dpapi()
        log.info("密码已写入 Windows 凭据管理器")
        return
    except Exception as exc:
        log.warning("凭据管理器不可用，改用 DPAPI: %s", exc)
    _save_dpapi(password)


def load_password() -> str | None:
    try:
        import keyring

        value = keyring.get_password(SERVICE_NAME, KEYRING_USERNAME)
        if value:
            return value
    except Exception as exc:
        log.warning("读取凭据管理器失败: %s", exc)
    return _load_dpapi()


def delete_password() -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, KEYRING_USERNAME)
    except Exception:
        pass
    _delete_dpapi()
    log.info("已清除保存的密码")
