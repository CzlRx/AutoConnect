# AutoConnect

常州大学校园网自动登录工具。打开后只需填写账号和密码，认证地址已经内置，不用复制网址，也不用选择运营商。

第一次保存成功后，每次登录 Windows 都会自动认证。**不会在后台一直挂着。** 密码保存在 Windows 凭据管理器，不会明文写进配置文件。

适用系统：Windows 10 / 11。校园 Wi-Fi 一般为 `CCZU-CMCC`。

---

## 给同学：三步上手

### 1. 下载

到 [Releases](https://github.com/CzlRx/AutoConnect/releases/latest) 下载 `AutoConnect.exe`。

不需要安装 Python，也不需要安装这个仓库里的其他文件。

### 2. 放到固定位置

把 `AutoConnect.exe` 放到一个以后不会随便改名、移动的地方，例如：

```text
D:\Apps\AutoConnect\AutoConnect.exe
```

或自己建一个文件夹再放进去。**不要放在压缩包里直接双击。** 设好开机自启后再移动文件，开机任务会找不到程序。

### 3. 填写账号并连接

1. 先连上校园 Wi-Fi。
2. 双击 `AutoConnect.exe`。
3. 填写校园网 **账号**、**密码**。
4. 点 **保存并连接**。

成功后会提示已连接，并已设置开机自动登录。之后每次开机都会自己认证，一般不用再打开这个窗口。

---

## 窗口按钮

| 按钮 | 作用 |
| --- | --- |
| **保存并连接** | 保存账号密码、立即登录，并设置开机自动登录 |
| **断开校园网** | 注销当前校园网会话，方便重新测试登录 |
| **卸载开机任务** | 取消开机自动登录；可选同时删除已保存的账号密码 |

回车键等同于点「保存并连接」。

---

## 日常使用

- **已经设好之后**：正常开机即可。成功或失败时，右下角会弹出系统气泡。
- **改密码 / 换账号**：再双击 `AutoConnect.exe`，改完后重新点「保存并连接」。
- **暂时不想自动登录**：打开窗口，点「卸载开机任务」。账号可以保留，下次再保存即可恢复。

---

## 常见问题

### 提示「Windows 已保护你的电脑」

这是未签名程序的常见拦截。点 **更多信息** → **仍要运行**。

如果被杀毒软件隔离，把 `AutoConnect.exe` 加入信任区后再打开。

### 点了保存，但连接失败

请依次确认：

1. 已经连的是校园网，而不是流量或家里的 Wi-Fi。
2. 账号、密码和学校认证页面上填写的一致。
3. 若开了 Clash / 其他代理的 TUN 模式，把认证服务器 `211.103.11.101` 设为直连或绕过。

程序不会走系统 HTTP 代理，但 TUN 仍可能截走认证包。

### 开机没有自动连上

1. 确认 exe 还在原来的位置，没有被移动或重命名。
2. 再打开一次程序，点「保存并连接」，重新注册开机任务。
3. 查看日志：`%APPDATA%\AutoConnect\autoconnect.log`  
   可在资源管理器地址栏粘贴上面的路径并回车。

### 密码保存在哪里？安全吗？

| 内容 | 位置 |
| --- | --- |
| 账号等配置（不含密码） | `%APPDATA%\AutoConnect\config.json` |
| 运行日志 | `%APPDATA%\AutoConnect\autoconnect.log` |
| 密码 | Windows 凭据管理器（服务名 `AutoConnect-Campus`） |

密码不会写入配置文件。

---

## 卸载

打开 `AutoConnect.exe`，点 **卸载开机任务**。如果同时选择删除账号密码，本机保存的登录信息会被清除。

也可以在命令行执行：

```powershell
AutoConnect.exe --uninstall --purge
```

---

## 从源码运行

适合想自己改程序的同学。需要 Python 3.10+，安装时勾选 **Add python.exe to PATH**。

```powershell
git clone https://github.com/CzlRx/AutoConnect.git
cd AutoConnect
python -m pip install -r requirements.txt
python autoconnect.py
```

也可以双击 `安装.bat`，再双击 `启动.bat`。

| 命令 | 作用 |
| --- | --- |
| `python autoconnect.py` | 未配置则打开设置；已配置则立即登录 |
| `python autoconnect.py --setup` | 打开设置窗口 |
| `python autoconnect.py --login` | 静默登录（开机任务使用） |
| `python autoconnect.py --force` | 即使看似已在线也再提交一次 |
| `python autoconnect.py --logout` | 断开校园网 |
| `python autoconnect.py --uninstall` | 删除开机任务，保留账号 |
| `python autoconnect.py --uninstall --purge` | 删除开机任务并清除账号密码 |

开机任务名称：`AutoConnect Campus Login`。打包后的开机任务会运行 `AutoConnect.exe --login`。

---

## 重新打包成 exe

在项目目录双击 `build.bat`，或执行：

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean AutoConnect.spec
```

生成文件：`dist\AutoConnect.exe`。把这一份发给同学即可。
