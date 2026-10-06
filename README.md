# AutoConnect

校园网开机自动登录小工具，目前支持两所学校，在设置窗口里选择即可：

| 学校 | 认证方式 | 认证地址 |
| --- | --- | --- |
| 常州工学院 | 城市热点 Dr.COM（网关 `172.19.0.1`，认证接口 `/drcom/login`） | `http://172.19.0.1/` |
| 常州大学 | 城市热点 eportal | `211.103.11.101:1028` |

第一次保存成功后，每次登录 Windows 都会自动认证。**不会在后台一直挂着。** 密码保存在 Windows 凭据管理器，不会明文写进配置文件。

适用系统：Windows 10 / 11。

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
3. 在「学校」下拉框里选你所在的学校。
4. 填账号和密码，点 **保存并连接**。

成功后会提示已连接，并已设置开机自动登录。之后每次开机都会自己认证，一般不用再打开这个窗口。

账号密码填什么：

| 学校 | 账号 | 密码 |
| --- | --- | --- |
| 常州工学院 | 认证页面上填的账号 | 认证页面上填的密码 |
| 常州大学 | 手机号 | 你最后一次获取的验证码 |

常州工学院还要选「**服务类型**」：校园网 / 中国移动（`@cmcc`）/ 中国联通（`@unicom`）/ 中国电信（`@telecom`）。**必须和你在认证网页上选的那一项一致** —— 移动、联通、电信宽带账号如果不选对，服务器会直接判账号错误。程序会把这个后缀拼到账号后面（例如 `25020138@cmcc`）。

打开窗口时如果已经连着校园网，程序会去认证页面读取它自己声明的运营商列表来填充这个下拉框（页面声明什么就用什么）；连不上时用内置的那一份。

---

## 窗口按钮

| 按钮 | 作用 |
| --- | --- |
| **学校** | 选择认证方式：常州工学院走网关页面，常州大学走城市热点接口 |
| **服务类型** | 选择运营商；只有需要选的学校才会显示这一行 |
| **保存并连接** | 保存学校、账号密码，立即登录，并设置开机自动登录 |
| **断开校园网** | 注销当前校园网会话，方便重新测试登录 |
| **卸载开机任务** | 取消开机自动登录；可选同时删除已保存的账号密码 |

回车键等同于点「保存并连接」。换学校时记得重新点一次保存。

---

## 日常使用

- **已经设好之后**：正常开机即可。成功或失败时，右下角会弹出系统气泡。
- **改密码 / 换账号 / 换学校**：再双击 `AutoConnect.exe`，改完后重新点「保存并连接」。
- **暂时不想自动登录**：打开窗口，点「卸载开机任务」。账号可以保留，下次再保存即可恢复。

---

## 常见问题

### 提示「Windows 已保护你的电脑」

这是未签名程序的常见拦截。点 **更多信息** → **仍要运行**。

如果被杀毒软件隔离，把 `AutoConnect.exe` 加入信任区后再打开。

### 点了保存，但连接失败

1. 确认已经连的是校园网，而不是流量或家里的 Wi-Fi。
2. 确认「学校」选对了。
3. 确认账号、密码和认证页面上填写的一致。
4. 若开了 Clash / Mihomo / v2rayN 等代理的 **TUN / 虚拟网卡** 模式，把认证地址设为 **DIRECT / 绕过**。常州大学是 `211.103.11.101`（含 `801`、`1028` 端口），常州工学院是 `172.19.0.1`。

程序不会走系统 HTTP 代理，但 TUN 仍可能在网卡层截走认证包。程序会忽略虚拟网卡上的地址，只用 Wi-Fi / 以太网的校园网 IP。

### 提示「认证接口返回成功，但流量仍被拦在门户页」

程序登录用的是**和浏览器完全相同的接口**（801 端口的 `/eportal/portal/login`，参数逐字段对齐），登录后还会实测能不能上外网，只有实测通过才会报成功。

如果仍看到这句提示，说明这次没放行，按顺序试：

1. 等 1 分钟再刷新网页。
2. 点「断开校园网」，等半分钟再点「保存并连接」。
3. 还不行就把 `%APPDATA%\AutoConnect\autoconnect.log` 发给我们（日志里含登录请求和网关返回）。

另外：程序**已经在线时不会重复提交登录**（避免把已通的会话打坏），只做联网实测。

### 提示「页面中没有找到登录表单」

说明这个门户不是标准 HTML 表单登录（可能是 JS 动态提交或密码加密）。用下面的命令把页面导出后反馈，就能针对性适配：

```powershell
AutoConnect.exe --dump
```

导出文件在 `%APPDATA%\AutoConnect\portal-dump.html`。

### 开机没有自动连上

1. 确认 exe 还在原来的位置，没有被移动或重命名。
2. 再打开一次程序，点「保存并连接」，重新注册开机任务（会带上约 5 秒启动延迟，方便 Wi-Fi 和代理先起来）。
3. 若开机就开着代理 TUN：确认认证地址已加入直连/绕过；失败通知里也会提示这一点。
4. 查看日志：`%APPDATA%\AutoConnect\autoconnect.log`  
   可在资源管理器地址栏粘贴上面的路径并回车。日志里会记录选用的校园网 IP 和检测到的虚拟网卡。

### 开机后要等一会儿才有网

开机登录 Windows 时，网卡和 DHCP 往往还没就绪（网关要过几秒到几十秒才连得上）。程序的做法是**先等校园网就绪再登录**：每秒探测一次网关，一连上就立刻认证，最多等 `portal_wait_sec` 秒（默认 60，写在 `%APPDATA%\AutoConnect\config.json` 里，可自行调整）。

日志里能看到这段等待：

```text
校园网还没就绪，最多等 60 秒（每秒探测一次）
校园网已就绪（等了 12.3 秒），立刻登录
门户接口返回: result=1 msg=Portal协议认证成功！
联网探测通过: http://connect.rom.miui.com/generate_204 -> HTTP 204
完成: Portal协议认证成功！（已确认可以上网）
```

如果超过这个时间还没连上，程序会先如实报失败，Windows 计划任务随后会每分钟自动重试（最多 3 次），所以把 `portal_wait_sec` 调大也能进一步提速。

### 密码保存在哪里？安全吗？

| 内容 | 位置 |
| --- | --- |
| 学校、账号等配置（不含密码） | `%APPDATA%\AutoConnect\config.json` |
| 运行日志 | `%APPDATA%\AutoConnect\autoconnect.log` |
| 登录页导出（`--dump`） | `%APPDATA%\AutoConnect\portal-dump.html` |
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
| `python autoconnect.py --dump` | 导出当前学校的登录页 HTML，便于适配新门户 |
| `python autoconnect.py --uninstall` | 删除开机任务，保留账号 |
| `python autoconnect.py --uninstall --purge` | 删除开机任务并清除账号密码 |

开机任务名称：`AutoConnect Campus Login`。打包后的开机任务会运行 `AutoConnect.exe --login`。

### 新增一所学校

在 `config.py` 里加一条学校档案（学校名、门户地址、账号提示），再把它的 key 加进 `SCHOOL_ORDER` 即可，设置窗口的下拉框会自动出现这一项。登录时按门户特征自动分发到深澜 / 锐捷 / 城市热点 / 通用表单四种适配器。

---

## 重新打包成 exe

在项目目录双击 `build.bat`，或执行：

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean AutoConnect.spec
```

生成文件：`dist\AutoConnect.exe`。把这一份发给同学即可。
