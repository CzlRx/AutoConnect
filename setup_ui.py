"""首次配置窗口：保存账号、连接、注册/卸载开机任务。"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from config import (
    APP_NAME,
    AppConfig,
    CarrierOption,
    carrier_options,
    load_config,
    save_config,
    school_options,
    school_profile,
)
from credentials import delete_password, load_password, save_password
from portal import fetch_portal_carriers, run_login_loop, run_logout
from startup import is_registered, register_startup, unregister_startup

log = logging.getLogger(APP_NAME)


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.resizable(False, False)
        self._busy = False

        cfg = load_config()
        saved_password = load_password() or ""
        self._schools = school_options()

        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        self.title_label = ttk.Label(frame, font=("", 12, "bold"))
        self.title_label.grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 4))

        ttk.Label(frame, text="学校").grid(row=1, column=0, sticky="e", **pad)
        self.school_var = tk.StringVar(value=school_profile(cfg.resolved_school()).name)
        self.school_box = ttk.Combobox(
            frame,
            state="readonly",
            width=33,
            values=[name for _key, name in self._schools],
            textvariable=self.school_var,
        )
        self.school_box.grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        self.school_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_school())

        self.carrier_label = ttk.Label(frame, text="服务类型")
        self.carrier_label.grid(row=2, column=0, sticky="e", **pad)
        self._carriers: tuple = ()
        self._carrier_suffix = (cfg.account_suffix or "").strip()
        self.carrier_var = tk.StringVar()
        self.carrier_box = ttk.Combobox(
            frame,
            state="readonly",
            width=33,
            textvariable=self.carrier_var,
        )
        self.carrier_box.grid(row=2, column=1, columnspan=3, sticky="we", **pad)
        self.carrier_box.bind("<<ComboboxSelected>>", lambda _event: self._on_carrier_change())

        self.hint_label = ttk.Label(frame, foreground="#555", wraplength=420, justify="left")
        self.hint_label.grid(row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))

        ttk.Label(frame, text="账号").grid(row=4, column=0, sticky="e", **pad)
        self.user_var = tk.StringVar(value=cfg.username)
        self.user_entry = ttk.Entry(frame, textvariable=self.user_var, width=36)
        self.user_entry.grid(row=4, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(frame, text="密码").grid(row=5, column=0, sticky="e", **pad)
        self.pass_var = tk.StringVar(value=saved_password)
        self.pass_entry = ttk.Entry(frame, textvariable=self.pass_var, width=36, show="*")
        self.pass_entry.grid(row=5, column=1, columnspan=3, sticky="we", **pad)

        self.save_btn = ttk.Button(frame, text="保存并连接", command=self.on_save)
        self.save_btn.grid(row=6, column=1, sticky="we", **pad)
        self.logout_btn = ttk.Button(frame, text="断开校园网", command=self.on_logout)
        self.logout_btn.grid(row=6, column=2, sticky="we", **pad)
        self.uninstall_btn = ttk.Button(frame, text="卸载开机任务", command=self.on_uninstall)
        self.uninstall_btn.grid(row=6, column=3, sticky="we", **pad)

        self.status_var = tk.StringVar(value=self._status_text())
        ttk.Label(frame, textvariable=self.status_var, wraplength=420).grid(
            row=7, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 0)
        )

        self._apply_school()
        self.bind("<Return>", lambda _event: self.on_save())
        if not cfg.username.strip():
            self.after(80, self.user_entry.focus_set)
        else:
            self.after(80, self.pass_entry.focus_set)

    def _selected_school(self) -> str:
        name = self.school_var.get().strip()
        for key, label in self._schools:
            if label == name:
                return key
        return self._schools[0][0]

    def _apply_school(self) -> None:
        profile = school_profile(self._selected_school())
        self.title(f"AutoConnect - {profile.name}校园网")
        self.title_label.configure(text=f"{profile.name}校园网自动登录")
        self.hint_label.configure(text=profile.account_hint)
        self._fill_carriers(profile.carriers)
        self._refresh_carriers_from_portal(profile)
        if hasattr(self, "status_var"):
            self.status_var.set(self._status_text())

    def _refresh_carriers_from_portal(self, profile) -> None:
        """能连上门户时，用页面自己声明的运营商列表刷新下拉框（拿不到就用内置的）。"""
        if not profile.carriers:
            return
        key = profile.key

        def worker() -> None:
            try:
                fetched = fetch_portal_carriers()
            except Exception:
                log.info("读取门户运营商列表出错", exc_info=True)
                fetched = []
            if fetched:
                try:
                    self.after(0, lambda: self._use_fetched_carriers(key, fetched))
                except Exception:
                    log.info("窗口已关闭，忽略读取到的运营商列表")

        threading.Thread(target=worker, daemon=True).start()

    def _use_fetched_carriers(self, key: str, fetched: list) -> None:
        if key != self._selected_school():
            return
        options = tuple(
            CarrierOption(id=str(item[0]), name=str(item[1]), suffix=str(item[2]))
            for item in fetched
        )
        if not options:
            return
        log.info("已按门户页声明的运营商刷新列表: %s", [item.name for item in options])
        self._fill_carriers(options)
        self.status_var.set(self._status_text())

    def _fill_carriers(self, carriers) -> None:
        """按学校填充「服务类型」下拉框；没有运营商可选的学校就隐藏这一行。"""
        self._carriers = tuple(carriers)
        if not self._carriers:
            self.carrier_var.set("")
            self.carrier_label.grid_remove()
            self.carrier_box.grid_remove()
            return
        self.carrier_box.configure(values=[item.name for item in self._carriers])
        chosen = next(
            (item for item in self._carriers if item.suffix == self._carrier_suffix),
            self._carriers[0],
        )
        self._carrier_suffix = chosen.suffix
        self.carrier_var.set(chosen.name)
        self.carrier_label.grid()
        self.carrier_box.grid()

    def _on_carrier_change(self) -> None:
        name = self.carrier_var.get().strip()
        for item in self._carriers:
            if item.name == name:
                self._carrier_suffix = item.suffix
                break
        self.status_var.set(self._status_text())

    def _status_text(self) -> str:
        cfg = load_config()
        task = "已注册" if is_registered() else "未注册"
        account = cfg.username or "未设置"
        school = school_profile(self._selected_school()).name
        carrier = self._carrier_name()
        extra = f"（{carrier}）" if carrier else ""
        return f"当前学校：{school}{extra}    账号：{account}    开机任务：{task}"

    def _carrier_name(self) -> str:
        return self.carrier_var.get().strip() if self._carriers else ""

    def _collect(self) -> tuple[AppConfig, str] | None:
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not username or not password:
            messagebox.showwarning("缺少信息", "请填写账号和密码。")
            return None
        cfg = load_config()
        school = self._selected_school()
        cfg.school = school
        cfg.username = username
        # 只有该校门户需要选运营商时才带运营商和后缀
        if carrier_options(school) and self._carriers:
            cfg.carrier = self._carrier_name()
            cfg.account_suffix = self._carrier_suffix
        else:
            cfg.carrier = ""
            cfg.account_suffix = ""
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
            cfg.carrier = ""
            cfg.account_suffix = ""
            save_config(cfg)
            self._carrier_suffix = ""
            self.user_var.set("")
            self.pass_var.set("")
            self.user_entry.focus_set()
        self._apply_school()
        messagebox.showinfo("完成", "已卸载开机自动登录。")


def run_setup_ui() -> None:
    app = SetupApp()
    app.mainloop()
