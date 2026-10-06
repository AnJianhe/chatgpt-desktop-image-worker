# ChatGPT 桌面远程画图助手

版本 **0.1.0（测试版）**。Windows 上运行自己的 ChatGPT 桌面端，远端使用浏览器提交提示词或一张参考图片，执行电脑通过 PyAutoGUI 完成操作并返回下载原图。

程序独立运行，无须 Codex 或 computer-use 技能；不使用 ChatGPT 图片 API。提供普通 Windows 桌面的基本画图流程，以及 Windows 虚拟机中的隧道看门狗和固定入口同步。

## 功能

- 参考图片上传：PNG、JPEG、WEBP，一次一张，最大 20 MB、2000 万像素。
- 布料设计预设：四方连续、花型提取、去布纹、配色、风格转换和高清重绘等。
- 自动新建对话、粘贴参考图及文字、发送、滚到底部、识别完成、右键下载副本。
- 超过 60 秒未识别完成时，将截图交给远端确认；右键菜单重试失败也支持人工选择重试或等待。
- 单线程画图队列、重复请求保护、任务进度、原格式图片下载。
- 看门狗第 10 版：持续无心跳只进入排查；进程退出或确认持续断线 30 秒才重建。首次确认尚未连接时等待 180 秒；指标读不到时继续排查。默认无周期重启。
- 申请隧道遇到 429 时依次等待 5、10、20、最多 30 分钟，冷却保存在本地。
- 可选的固定入口，通过 iframe 展示最新地址，浏览器地址栏保留固定网址。

高清和矢量风格属于提示词要求：当前以实际下载的位图返回，不保证 4K，也不导出 SVG/EPS。服务进程重启会丢失内存里的任务记录。

## 目录

```text
app/                         桌面自动化、网页服务、预设、看门狗、链接同步
  config.example.json        空白校准配置示例
  link_sync_config.example.json  自己的入口和同步令牌示例
portal/                      可选固定入口的 Worker、数据库结构和测试
tools/                       使用者安装 cloudflared 的位置
tests/                       离线回归测试
start.ps1                    使用项目内 .venv 的统一启动脚本
LICENSE                      MIT 许可证
```

## 安装

执行电脑需要 Windows 10/11、包含 Tkinter 的 Python 3.10+、已安装并登录的 ChatGPT 桌面端。首次使用要校准自己的界面。需要同时使用执行电脑处理其他工作时，可将画图程序放到 Windows 虚拟机中运行。

在项目根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\app\requirements.txt
```

Python 依赖为 PyAutoGUI、pyperclip、Pillow、OpenCV、Flask 和 Waitress；看门狗只使用 Python 标准库。

## 首次校准

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode ui
```

1. 打开并登录 ChatGPT。启动路径可以留空，由程序寻找启动项；也可填写自己的快捷方式或程序路径。
2. 在控制窗口点击“打开 ChatGPT”，确认能找到正确窗口。
3. 在冻结截图里分别选择新对话、输入框和可用发送按钮的位置。输入框应取空白新对话中的位置；校准发送按钮前先手动输入少量文字使它可用。
4. 手动生成一张图片，确认完成后截取只在图片完成时出现的标记，例如图片左下的“编辑”文字。不要选加载提示、图片内容或普通点赞工具栏。
5. 选择图片内部的位置，供悬停和右击使用。程序会先滚到底部。
6. 在已生成图片上右击，再用控制窗口截取“下载副本”的菜单文字标记。本开源版不预填别人的坐标和截图。
7. 下载文件夹可留空，程序读取 Windows 的实际下载目录；也可填写自己的目录。

校准结果保存到 `app/config.json` 和同目录的标记图片。换分辨率、缩放、主题或界面布局后，重新校准受影响的项目。正常生成需要已登录、未锁屏的桌面，程序会占用前台鼠标和键盘。

`config.example.json` 供查看配置字段；直接运行校准界面会从默认值创建自己的配置，无须先复制示例。校准完成后关闭控制窗口，再启动服务器。

## 本地及远端画图

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode server
```

本机打开 `http://127.0.0.1:8765/`。选择布料工具和预设，按需上传参考图、编辑提示词并开始生成。等待期间页面显示进度；需要人工判断时，会显示当前截图和操作按钮。

参考图和下载文件保存在自己的应用目录。一次仅执行一个桌面任务；不要同时从控制窗口启动另一任务。

## 公网隧道

从 [cloudflared 官方发布页](https://github.com/cloudflare/cloudflared/releases/latest) 获取 Windows amd64 程序，保存为 `tools/cloudflared-windows-amd64.exe`。项目源码不附带第三方程序安装包。

普通 Windows 桌面可以在另一窗口直接启动隧道：

```powershell
.\tools\cloudflared-windows-amd64.exe tunnel --protocol auto --edge-ip-version 4 --url http://127.0.0.1:8765
```

远端用户打开日志中实际生成的 `https://…trycloudflare.com` 地址即可使用网页。

在 **Windows 虚拟机内**使用自动恢复看门狗：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode watchdog
```

看门狗和链接同步保留 Windows 虚拟机检查。无需运行迁移脚本或使用特定用户名。状态在 `app/logs/tunnel-status.json`，地址在 `app/公网地址.txt`；虚拟机桌面也会写入地址文件。

正常时 `watchdog_version` 为 10、`self_restart_seconds` 为 0。`connection_confirmed` 为 true/false/null，表示确认连接、未连接或断开、状态待确认。`local_service_ready` 表示本地服务检查结果。无心跳不单独触发重建。

隧道地址会变化；Quick Tunnel 用于测试和开发，服务恢复所需时间取决于网络及平台状态。[官方说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## 可选固定入口

按 [portal/README.md](portal/README.md) 将入口部署到自己的地址，配置数据库及 `LINK_SYNC_TOKEN`。

复制并编辑自己的同步配置：

```powershell
Copy-Item .\app\link_sync_config.example.json .\app\link_sync_config.json
```

`endpoint` 填自己的 `https://入口域名/api/update`，`token` 填与入口部署相同的令牌。示例中的空令牌不能使用。

启动画图服务器时指定入口来源地址，只填写协议、域名和可选端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode server -EntryOrigin "https://your-entry.example.com"
```

然后在虚拟机另一窗口启动链接同步：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Mode sync
```

也可通过 `FABRIC_ENTRY_ORIGIN` 环境变量，或 `app/server.py --entry-origin` 配置入口来源。入口与工作台使用校验来源的 postMessage 同步状态，替换隧道后继续查询同一任务。

看门狗和链接同步支持当前虚拟机用户登录自启动，配置完成后按需执行：

```powershell
.\.venv\Scripts\python.exe .\app\tunnel_watchdog.py --install-startup
.\.venv\Scripts\python.exe .\app\link_sync.py --install-startup
```

画图服务器须在虚拟机桌面会话内运行。启动脚本不会主动更改注册表或重启系统；上述登录自启动命令需由使用者执行。

## 验证

离线测试使用模拟窗口、任务和隧道进程，不实际操控 ChatGPT：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -p "test_*.py" -v
.\.venv\Scripts\python.exe .\tests\check_server.py
cd portal
node --test tests/worker.test.mjs
node scripts/build.mjs
node scripts/validate-artifact.mjs
```

门户逻辑测试及构建使用 Node.js 标准库。修改数据库结构时，再执行 `npm ci` 安装项目声明的开发依赖。

本发行副本完成的具体检查见 `verification/RESULTS.md`。离线通过与在每位使用者的实际桌面上完成画图是不同的验证范围。

## 许可证

本项目源码采用 [MIT](LICENSE)。依赖由使用者独立安装，按各自发行包中的许可证使用。
