"""首次配置窗口：保存账号、连接、注册/卸载开机任务。"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from config import APP_NAME, SCHOOL_NAME, AppConfig, default_portal_url, load_config, save_config
from credentials import delete_password, load_password, save_password
from portal import run_login_loop, run_logout
from startup import is_registered, register_startup, unregister_startup

log = logging.getLogger(APP_NAME)


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"AutoConnect - {SCHOOL_NAME}校园网")
        self.resizable(False, False)
        self._busy = False

        cfg = load_config()
        saved_password = load_password() or ""

        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text=f"{SCHOOL_NAME}校园网自动登录", font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 4)
        )
        ttk.Label(
            frame,
            text="账号为你的手机号，密码就是你最后一次获取的验证码。",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))

        ttk.Label(frame, text="账号").grid(row=2, column=0, sticky="e", **pad)
        self.user_var = tk.StringVar(value=cfg.username)
        self.user_entry = ttk.Entry(frame, textvariable=self.user_var, width=36)
        self.user_entry.grid(row=2, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(frame, text="密码").grid(row=3, column=0, sticky="e", **pad)
        self.pass_var = tk.StringVar(value=saved_password)
        self.pass_entry = ttk.Entry(frame, textvariable=self.pass_var, width=36, show="*")
        self.pass_entry.grid(row=3, column=1, columnspan=3, sticky="we", **pad)

        self.save_btn = ttk.Button(frame, text="保存并连接", command=self.on_save)
        self.save_btn.grid(row=4, column=1, sticky="we", **pad)
        self.logout_btn = ttk.Button(frame, text="断开校园网", command=self.on_logout)
        self.logout_btn.grid(row=4, column=2, sticky="we", **pad)
        self.uninstall_btn = ttk.Button(frame, text="卸载开机任务", command=self.on_uninstall)
        self.uninstall_btn.grid(row=4, column=3, sticky="we", **pad)

        self.status_var = tk.StringVar(value=self._status_text())
        ttk.Label(frame, textvariable=self.status_var, wraplength=420).grid(
            row=5, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 0)
        )

        self.bind("<Return>", lambda _event: self.on_save())
        if not cfg.username.strip():
            self.after(80, self.user_entry.focus_set)
        else:
            self.after(80, self.pass_entry.focus_set)

    def _status_text(self) -> str:
        cfg = load_config()
        task = "已注册" if is_registered() else "未注册"
        account = cfg.username or "未设置"
        return f"当前账号：{account}    开机任务：{task}"

    def _collect(self) -> tuple[AppConfig, str] | None:
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not username or not password:
            messagebox.showwarning("缺少信息", "请填写账号和密码。")
            return None
        cfg = load_config()
        cfg.portal_url = default_portal_url()
        cfg.username = username
        return cfg, password

    def _set_busy(self, busy: bool, text: str | None = None) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.save_btn, self.logout_btn, self.uninstall_btn):
            btn.configure(state=state)
        if text:
            self.status_var.set(text)
        self.update_idletasks()

    def _persist(self, cfg: AppConfig, password: str) -> None:
        save_config(cfg)
        save_password(password)

    def on_save(self) -> None:
        if self._busy:
            return
        collected = self._collect()
        if not collected:
            return
        cfg, password = collected
        try:
            self._persist(cfg, password)
            register_startup(cfg.startup_delay_sec)
        except Exception as exc:
            log.exception("保存或注册开机任务失败")
            messagebox.showerror("失败", f"保存失败：{exc}")
            return
        self._set_busy(True, "正在连接校园网…")

        def worker() -> None:
            try:
                result = run_login_loop(cfg, attempts=range(1, 6))
                ok, message = result.ok, result.message
            except Exception as exc:
                log.exception("连接失败")
                ok, message = False, str(exc)
            self.after(0, lambda: self._save_done(ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def on_logout(self) -> None:
        if self._busy:
            return
        collected = self._collect()
        if collected:
            cfg, password = collected
            try:
                self._persist(cfg, password)
            except Exception as exc:
                messagebox.showerror("失败", f"保存失败：{exc}")
                return
        self._set_busy(True, "正在断开校园网…")

        def worker() -> None:
            try:
                result = run_logout()
                ok, message = result.ok, result.message
            except Exception as exc:
                log.exception("注销失败")
                ok, message = False, str(exc)
            self.after(0, lambda: self._logout_done(ok, message))

        threading.Thread(target=worker, daemon=True).start()

    def _logout_done(self, ok: bool, message: str) -> None:
        self._set_busy(False, self._status_text())
        if ok:
            messagebox.showinfo("已断开", message)
        else:
            messagebox.showerror("断开失败", message)

    def _save_done(self, ok: bool, message: str) -> None:
        self._set_busy(False, self._status_text())
        if ok:
            messagebox.showinfo("已连接", f"{message}\n已保存账号，并已设置开机自动登录。")
        else:
            messagebox.showerror(
                "已保存，但连接失败",
                f"账号已保存，开机自动登录也已设置。\n当前连接失败：{message}\n请确认已连上校园 Wi-Fi 后再试一次。",
            )

    def on_uninstall(self) -> None:
        if self._busy:
            return
        try:
            unregister_startup()
        except Exception as exc:
            messagebox.showerror("失败", f"删除开机任务失败：{exc}")
            return
        if messagebox.askyesno("卸载", "开机任务已删除。是否同时删除已保存的账号和密码？"):
            delete_password()
            cfg = load_config()
            cfg.username = ""
            cfg.portal_url = ""
            cfg.account_suffix = ""
            save_config(cfg)
            self.user_var.set("")
            self.pass_var.set("")
            self.user_entry.focus_set()
        self.status_var.set(self._status_text())
        messagebox.showinfo("完成", "已卸载开机自动登录。")


def run_setup_ui() -> None:
    app = SetupApp()
    app.mainloop()
