# Bug 修复提交台

此 Sites 站点保持维护人员私有访问。用户在画图网站 `/report-bug` 提交问题，无需选择故障分类。维护人员可选择 ChatGPT、虚拟机、Sites 或待归类，并更新待处理、处理中、已修复及修复说明。

配置服务端环境变量 `BUG_API_ORIGIN`（自己的画图网站地址）和 `BUG_ADMIN_TOKEN`（与画图网站同名秘密变量一致）。不要把真实值写入源码。以 `worker/index.js` 为 Worker 入口，运行 `node scripts/build.mjs` 构建。画图网站必须应用 `0003_bug_reports.sql` 迁移。

虚拟机运行 `app/bug_notifier.py`，沿用本地云端配置，每10秒领取报告。报告先完整落盘到 `logs/bug-inbox`，再确认送达，并显示 Windows 通知，不抢占 ChatGPT 焦点。Windows 关闭通知时，可从本地日志或此修复台查看。
