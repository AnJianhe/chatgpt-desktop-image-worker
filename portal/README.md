# 布料花型助手 · 云端工作台

整个网页和任务 API 部署到 Sites。D1 保存任务和监控快照，R2 保存参考图片、人工确认截图及结果图片。虚拟机运行 cloud_agent.py 主动请求 HTTPS 接口，不需要 Cloudflare Tunnel。

网页沿用 /uploads、/jobs、/history、任务查询、下载和三种人工确认接口。HttpOnly Cookie 隔离浏览器数据，同一浏览器刷新保留历史。清除 Cookie 后不显示旧会话任务。

虚拟机使用 Sites 环境变量 CLOUD_WORKER_TOKEN 访问 /worker/*。密钥仅保存在 Sites 和虚拟机的 cloud_agent_config.json，不提交仓库。

云端原子领取一个任务，桌面操作串行。网页允许连续提交独立任务。断线保留本地任务身份，不自动重复生成。完成检测和 60 秒人工确认由现有服务器处理；确认时仍继续检测。版本号防止过期决定覆盖成功结果。

/monitor 展示虚拟机主动同步的 Uptime Kuma 监控数据。超过 30 秒无同步标记离线。完整 Uptime Kuma 及其 Node.js、SQLite 和管理界面在虚拟机运行，Sites 提供展示页。

运行 npm ci、npm test、npm run build、npm run validate。数据库结构变化执行 npm run db:generate，已应用的迁移不得修改。esbuild 打包模块和网页为单个 Worker。

worker/assets.js 是现有 HTML、客户端脚本和预设的静态副本，需要随画图服务更新。legacy.js 仅兼容未绑定 R2 的旧入口，不参与新云端工作台的通信。

虚拟机配置示例：
```json
{"endpoint":"https://your-site.example","worker_token":"在本地填写","local_url":"http://127.0.0.1:8765"}
```
虚拟机须登录桌面并保持 ChatGPT 桌面端可操作。云端在虚拟机离线时仍可接收任务，虚拟机恢复后领取。原有 PyAutoGUI 生成、完成识别和下载逻辑保留。
