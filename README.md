# AutoConnect

Windows 下校园网 Web 门户开机自动登录。首次保存账号后，登录 Windows 就会静默认证；**不做后台保活**。

当前针对常州大学校园网（SSID `CCZU-CMCC`，城市热点 Dr.COM / 锐捷 Portal）。密码保存在 Windows 凭据管理器，不会写入配置文件。

## 环境

- Windows 10 / 11
- Python 3.10+（建议安装时勾选 “Add python.exe to PATH”）

## 安装

在项目目录执行：

```powershell
cd C:\Users\你的用户名\Desktop\AutoConnect
python -m pip install -r requirements.txt
```

## 第一次使用

```powershell
python autoconnect.py --setup
```

在窗口中填写：

1. **登录页网址**（推荐，不要带本机 IP）：

   ```text
   http://211.103.11.101:1028/a79.htm?wlanacname=0011.0519.250.00&ssid=CCZU-CMCC
   ```

   不要保存浏览器里带 `wlanuserip=` 的整段跳转链接。那是当时电脑的地址，重启后会变，会导致认证失败。

2. **账号、密码**

3. **运营商**
   - 校园移动：不追加后缀
   - 校园用户：`@xyw`
   - 校园电信：`@dx`
   - 校园联通：`@lt`

然后点 **保存并开机自启**。可用 **立即试登** 先验证。点 **注销以便测试** 后再试登，更接近开机未认证的状态。

未配置过时，直接运行 `python autoconnect.py` 也会打开设置窗口。

## 日常命令

| 命令 | 作用 |
| --- | --- |
| `python autoconnect.py` | 已配置则立即登录；未配置则打开设置 |
| `python autoconnect.py --setup` | 打开设置窗口 |
| `python autoconnect.py --login` | 静默登录（开机任务使用） |
| `python autoconnect.py --force` | 即使看似已在线也再提交一次 |
| `python autoconnect.py --logout` | 注销校园网 |
| `python autoconnect.py --uninstall` | 删除开机任务，保留账号 |
| `python autoconnect.py --uninstall --purge` | 删除开机任务并清除账号密码 |

开机任务名称：`AutoConnect Campus Login`。登录 Windows 后立即运行，无额外等待。成功或失败会弹出系统气泡。

## 文件位置

| 内容 | 路径 |
| --- | --- |
| 配置（无密码） | `%APPDATA%\AutoConnect\config.json` |
| 日志 | `%APPDATA%\AutoConnect\autoconnect.log` |
| 密码 | Windows 凭据管理器（服务名 `AutoConnect-Campus`） |

## 使用 Clash 时

程序不会等 Clash 启动，认证请求也不走系统 HTTP 代理。

若开了 Clash TUN，请把认证服务器 **`211.103.11.101` 设为直连/绕过**，避免 TUN 截走校园网认证包。

## 卸载

```powershell
python autoconnect.py --uninstall --purge
```

也可在设置窗口点 **卸载开机任务**。
