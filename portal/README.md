# 自己的固定入口

入口是标准 Worker 模块，使用一个 D1 数据库的 `latest_link` 表。根页面通过 iframe 内嵌最新地址，不把浏览器跳转到临时域名。地址更新后会自动替换工作台。

接口为 `/api/latest`、`/latest.txt` 和经过令牌检查的 `/api/update`。源码没有预设他人的入口域名、数据库标识或同步令牌。

## Cloudflare Workers + D1 部署

需要 Node.js 和自己的 Cloudflare 账户，在本目录执行：

```powershell
Copy-Item .\wrangler.example.jsonc .\wrangler.jsonc
npx wrangler@latest login
npx wrangler@latest d1 create fabric-image-entry
```

将创建结果的数据库 ID 填入 `wrangler.jsonc` 的 `database_id`。绑定名称必须为 `DB`；可以自行修改 Worker 名称和数据库名称。

```powershell
npx wrangler@latest d1 execute fabric-image-entry --remote --file .\drizzle\0000_round_lucky_pierre.sql
npx wrangler@latest deploy
npx wrangler@latest secret put LINK_SYNC_TOKEN
```

在 secret 提示中填自己的随机令牌，例如先用执行电脑的 Python 生成：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

将相同令牌填入执行电脑的 `app/link_sync_config.json`，将 Worker 的实际 HTTPS 地址加上 `/api/update` 作为 `endpoint`。画图服务器的 `--entry-origin` 填 Worker 的来源地址。无需将令牌放进本项目源码。

部署及数据库命令依据 [Cloudflare D1 文档](https://developers.cloudflare.com/d1/get-started/)。本副本没有替使用者创建或部署任何云资源。

## Sites 部署

如使用 Sites，将 `.openai/hosting.example.json` 复制为 `.openai/hosting.json`，填自己的 `project_id`，配置 `DB` 和 `LINK_SYNC_TOKEN`。执行 `node scripts/build.mjs`，将构建产物交给该平台的发布工具。实际 hosting 配置不纳入源码版本。

## 本地检查

```powershell
node --test tests/worker.test.mjs
node scripts/build.mjs
node scripts/validate-artifact.mjs
node scripts/preview.mjs
```

预览地址为 `http://127.0.0.1:8791/`。预览使用内存数据库和 `local-preview` 测试令牌，重启后清空，不会连接云端数据库。若测试内嵌工作台，将画图服务器的入口来源设为 `http://127.0.0.1:8791`，预览同步接口为 `http://127.0.0.1:8791/api/update`，可用 HTTP 客户端手动上报模拟状态；正常链接同步脚本要求公网 HTTPS。
