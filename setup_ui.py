"""首次配置窗口：保存账号、试登、注册/卸载开机任务。"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from config import APP_NAME, AppConfig, load_config, save_config
from credentials import delete_password, load_password, save_password
from portal import run_logout, submit_login
from startup import is_registered, register_startup, unregister_startup

log = logging.getLogger(APP_NAME)


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AutoConnect - 校园网自动登录")
        self.resizable(False, False)
        self._busy = False

        cfg = load_config()
        saved_password = load_password() or ""

        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="登录页网址").grid(row=0, column=0, sticky="e", **pad)
        self.portal_var = tk.StringVar(value=cfg.portal_url)
        ttk.Entry(frame, textvariable=self.portal_var, width=46).grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(frame, text="账号").grid(row=1, column=0, sticky="e", **pad)
        self.user_var = tk.StringVar(value=cfg.username)
        ttk.Entry(frame, textvariable=self.user_var, width=46).grid(row=1, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(frame, text="密码").grid(row=2, column=0, sticky="e", **pad)
        self.pass_var = tk.StringVar(value=saved_password)
        ttk.Entry(frame, textvariable=self.pass_var, width=46, show="*").grid(
            row=2, column=1, columnspan=3, sticky="we", **pad
        )

        ttk.Label(frame, text="运营商").grid(row=3, column=0, sticky="e", **pad)
        self.isp_labels = [
            ("校园移动（不追加后缀）", ""),
            ("校园用户 @xyw", "@xyw"),
            ("校园电信 @dx", "@dx"),
            ("校园联通 @lt", "@lt"),
        ]
        self.isp_var = tk.StringVar()
        self.isp_combo = ttk.Combobox(
            frame,
            textvariable=self.isp_var,
            values=[item[0] for item in self.isp_labels],
            state="readonly",
            width=43,
        )
        self.isp_combo.grid(row=3, column=1, columnspan=3, sticky="we", **pad)
        current_suffix = cfg.account_suffix
        chosen = self.isp_labels[0][0]
        for label, suffix in self.isp_labels:
            if suffix == current_suffix:
                chosen = label
                break
        self.isp_combo.set(chosen)

        hint = "211.103.11.101 是学校认证服务器，应保持固定。不要保存带 wlanuserip= 的整段跳转链接（那是你电脑当时的地址，重启就会变）。开机时程序会向 172.30.0.11 重新获取当前 IP。"
        ttk.Label(frame, text=hint, wraplength=460, foreground="#555").grid(
            row=4, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8)
        )

        self.save_btn = ttk.Button(frame, text="保存并开机自启", command=self.on_save)
        self.save_btn.grid(row=5, column=1, sticky="we", **pad)
        self.test_btn = ttk.Button(frame, text="立即试登", command=self.on_test)
        self.test_btn.grid(row=5, column=2, sticky="we", **pad)
        self.uninstall_btn = ttk.Button(frame, text="卸载开机任务", command=self.on_uninstall)
        self.uninstall_btn.grid(row=5, column=3, sticky="we", **pad)
        self.logout_btn = ttk.Button(frame, text="注销以便测试", command=self.on_logout)
        self.logout_btn.grid(row=6, column=1, sticky="we", **pad)

        self.status_var = tk.StringVar(value=self._status_text())
        ttk.Label(frame, textvariable=self.status_var, wraplength=460).grid(
            row=7, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 0)
        )

        self.bind("<Return>", lambda _event: self.on_save())

    def _status_text(self) -> str:
        cfg = load_config()
        task = "已注册" if is_registered() else "未注册"
        account = cfg.username or "未设置"
        return f"当前账号：{account}    开机任务：{task}"

    def _collect(self) -> tuple[AppConfig, str] | None:
        portal = self.portal_var.get().strip()
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not portal:
            messagebox.showwarning("缺少信息", "请填写校园网登录页网址（浏览器地址栏复制即可）。")
            return None
        if not username or not password:
            messagebox.showwarning("缺少信息", "请填写账号和密码。")
            return None
        cfg = load_config()
        cfg.portal_url = portal
        cfg.username = username
        selected = self.isp_var.get()
        cfg.account_suffix = ""
        for label, suffix in self.isp_labels:
            if label == selected:
                cfg.account_suffix = suffix
                break
        return cfg, password

    def _set_busy(self, busy: bool, text: str | None = None) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.save_btn, self.test_btn, self.uninstall_btn, self.logout_btn):
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
        self.status_var.set(self._status_text())
        messagebox.showinfo("已保存", "账号已保存，并已注册开机自动登录任务。")

    def on_test(self) -> None:
        if self._busy:
            return
        collected = self._collect()
        if not collected:
            return
        cfg, password = collected
        try:
            self._persist(cfg, password)
        except Exception as exc:
            messagebox.showerror("失败", f"保存失败：{exc}")
            return
        self._set_busy(True, "正在试登，请稍候…")

        def worker() -> None:
            try:
                result = submit_login(cfg.normalized_portal_url(), cfg.username, password)
                ok, message = result.ok, result.message
            except Exception as exc:
                log.exception("试登失败")
                ok, message = False, str(exc)
            self.after(0, lambda: self._test_done(ok, message))

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
        self._set_busy(True, "正在注销校园网…")

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
            messagebox.showinfo("已注销", message + "\n请打开浏览器访问任意网页确认是否被踢回登录页，然后点「立即试登」。")
        else:
            messagebox.showerror("注销失败", message)

    def _test_done(self, ok: bool, message: str) -> None:
        self._set_busy(False, self._status_text())
        if ok:
            messagebox.showinfo("试登成功", message)
        else:
            messagebox.showerror("试登失败", message)

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
            self.portal_var.set("")
            self.isp_combo.set(self.isp_labels[0][0])
        self.status_var.set(self._status_text())
        messagebox.showinfo("完成", "已卸载开机自动登录。")


def run_setup_ui() -> None:
    app = SetupApp()
    app.mainloop()
