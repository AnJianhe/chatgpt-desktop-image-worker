// Pinned to the Socket.IO interface of Uptime Kuma 2.5.6.
// Existing accounts, monitors and notification settings are preserved.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const root = process.env.KUMA_ROOT || path.join(__dirname, "..", "uptime-kuma");
const { io } = require(path.join(root, "node_modules", "socket.io-client"));
const accountPath = path.join(process.env.DATA_DIR || path.join(root, "data"), "worker-admin.json");
const socket = io("http://127.0.0.1:3001", { transports: ["websocket"], timeout: 10000 });
let monitors = {};
socket.on("monitorList", value => { monitors = value; });
function call(event, ...args) {
    return new Promise((resolve, reject) => {
        socket.timeout(15000).emit(event, ...args, (err, result) => {
            if (err) return reject(err);
            if (result && result.ok === false) return reject(new Error(result.msg));
            resolve(result);
        });
    });
}
const timer = setTimeout(() => { console.error("Kuma configuration timed out"); process.exit(1); }, 60000);
socket.once("connect", async () => {
    try {
        const needsSetup = await call("needSetup");
        let account;
        if (fs.existsSync(accountPath)) account = JSON.parse(fs.readFileSync(accountPath, "utf8"));
        if (needsSetup) {
            account ||= { username: "worker-admin", password: crypto.randomBytes(24).toString("base64url") + "Aa1!" };
            fs.writeFileSync(accountPath, JSON.stringify(account, null, 2), { mode: 0o600 });
            await call("setup", account.username, account.password);
        }
        if (!account) throw new Error("Existing Kuma account detected; configure monitors in its dashboard.");
        await call("login", account);
        const items = [
            { name: "画图服务器与执行线程", url: "http://127.0.0.1:8765/health", jsonPath: 'status = "ok" and worker_alive = true' },
        ];
        const cloudMode = fs.existsSync(path.join(__dirname, 'cloud_agent_config.json'));
        const configPath = path.join(__dirname, cloudMode ? 'cloud_agent_config.json' : 'link_sync_config.json');
        if (fs.existsSync(configPath)) {
            const cfg = JSON.parse(fs.readFileSync(configPath, "utf8").replace(/^\uFEFF/, ""));
            const entry = new URL(cfg.endpoint);
            if (entry.protocol !== "https:" || (!cloudMode && entry.pathname !== "/api/update")) throw new Error("Invalid entry configuration");
            items.push({ name: cloudMode ? "云端工作台与虚拟机同步" : "固定入口与公网隧道心跳", url: new URL("/api/latest", entry).href, jsonPath: "ready = true", headers: JSON.stringify({'User-Agent':'Mozilla/5.0'}) });
        }
        for (const item of items) {
            if (Object.values(monitors).some(m => m.name === item.name)) continue;
            await call("add", { ...item, type: "json-query", method: "GET", interval: 30,
                retryInterval: 30, maxretries: 2, resendInterval: 0, active: true,
                timeout: 10, maxredirects: 3, accepted_statuscodes: ["200-299"],
                notificationIDList: {}, ignoreTls: false, upsideDown: false,
                jsonPathOperator: "==", expectedValue: "true", conditions: [],
                kafkaProducerBrokers: [], kafkaProducerSaslOptions: {}, rabbitmqNodes: [] });
        }
        console.log("Kuma configured: 30-second checks, two retries, no automatic tunnel restarts.");
        console.log("Login credentials saved locally in uptime-kuma/data/worker-admin.json");
    } catch (error) {
        console.error(error.message); process.exitCode = 1;
    } finally { clearTimeout(timer); socket.disconnect(); }
});
