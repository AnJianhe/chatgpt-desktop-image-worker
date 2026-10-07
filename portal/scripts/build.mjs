import { mkdir, copyFile, cp, access } from "node:fs/promises";
await mkdir("dist/server", {recursive:true});
await copyFile("worker/index.js", "dist/server/index.js");
await cp("drizzle", "dist/drizzle", {recursive:true});
try {
  await access(".openai/hosting.json");
  await mkdir("dist/.openai", {recursive:true});
  await copyFile(".openai/hosting.json", "dist/.openai/hosting.json");
} catch (error) { if (error.code !== "ENOENT") throw error; }
console.log("Worker and D1 migrations built; hosting metadata is optional.");
