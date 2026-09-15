"""校园网开机自动登录。

用法:
  python autoconnect.py           未配置则打开设置，已配置则立即登录
  python autoconnect.py --setup   打开设置窗口
  python autoconnect.py --login   静默检测并登录（开机任务使用）
  python autoconnect.py --uninstall [--purge]  删除开机任务，可选清除凭据
"""

from __future__ import annotations

import argparse
import logging
import sys

from config import APP_NAME, config_exists, log_path
from notify import notify
from portal import run_login_loop, run_logout


def setup_logging() -> None:
    log_file = log_path()
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    if sys.stdout and getattr(sys.stdout, "isatty", lambda: False)():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger(APP_NAME).info("日志文件: %s", log_file)


def _enable_utf8_stdio() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def cmd_login(silent_notify: bool) -> int:
    log = logging.getLogger(APP_NAME)
    if not config_exists():
        message = "尚未配置校园网账号，请先运行 python autoconnect.py --setup"
        log.error(message)
        if silent_notify:
            notify("校园网自动登录", message)
        else:
            print(message)
        return 1
    result = run_login_loop()
    if result.ok:
        log.info("完成: %s", result.message)
        notify("校园网自动登录", result.message)
        return 0
    log.error("登录失败: %s", result.message)
    notify("校园网登录失败", result.message)
    return 2


def cmd_uninstall(purge: bool) -> int:
    from config import AppConfig, save_config
    from credentials import delete_password
    from startup import is_registered, unregister_startup

    existed = is_registered()
    unregister_startup()
    if purge:
        delete_password()
        save_config(AppConfig())
        extra = "并已清除保存的账号密码。"
    else:
        extra = "账号仍保留，可用 --purge 一并清除。"
    if existed:
        print(f"已删除开机任务。{extra}")
    else:
        print(f"未找到开机任务。{extra}")
    return 0


def main() -> int:
    _enable_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="校园网开机自动登录",
        epilog=(
            "首次使用: python autoconnect.py --setup\n"
            "立即登录: python autoconnect.py --login\n"
            "强制登录: python autoconnect.py --login --force\n"
            "先注销再测: python autoconnect.py --logout\n"
            "卸载任务: python autoconnect.py --uninstall"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--setup", action="store_true", help="打开首次配置窗口")
    parser.add_argument("--login", action="store_true", help="静默检测并登录")
    parser.add_argument("--force", action="store_true", help="即使已在线也提交登录（方便测试）")
    parser.add_argument("--logout", action="store_true", help="注销校园网，方便接着测试自动登录")
    parser.add_argument("--uninstall", action="store_true", help="删除开机自动登录任务")
    parser.add_argument("--purge", action="store_true", help="卸载时同时清除账号密码")
    args = parser.parse_args()
    setup_logging()

    if args.uninstall:
        return cmd_uninstall(args.purge)
    if args.setup:
        from setup_ui import run_setup_ui

        run_setup_ui()
        return 0
    if args.logout:
        result = run_logout()
        print(result.message)
        return 0 if result.ok else 2
    if args.login or args.force:
        if args.force:
            from config import load_config

            result = run_login_loop(load_config(), force=True)
            log = logging.getLogger(APP_NAME)
            if result.ok:
                log.info("完成: %s", result.message)
                print(result.message)
                return 0
            log.error("登录失败: %s", result.message)
            print(result.message)
            return 2
        return cmd_login(silent_notify=True)
    if not config_exists():
        from setup_ui import run_setup_ui

        run_setup_ui()
        return 0
    return cmd_login(silent_notify=True)


if __name__ == "__main__":
    sys.exit(main())
