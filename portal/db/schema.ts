import { sqliteTable, integer, text, real, uniqueIndex } from "drizzle-orm/sqlite-core";
export const latestLink = sqliteTable("latest_link", {
  id: integer("id").primaryKey(),
  url: text("url").notNull(),
  state: text("state").notNull(),
  sourceAge: real("source_age").notNull(),
  receivedAt: integer("received_at").notNull(),
});
export const cloudTasks = sqliteTable("cloud_tasks", {
 id: text("id").primaryKey(), owner: text("owner").notNull(), requestId: text("request_id").notNull(),
 created: integer("created").notNull(), updated: integer("updated").notNull(), status: text("status").notNull(),
 prompt: text("prompt").notNull(), tool: text("tool").notNull(), refs: text("refs").notNull(),
 snapshot: text("snapshot").notNull(), decision: text("decision"), localId: text("local_id"), worker: text("worker"),
}, table => [uniqueIndex('cloud_task_request').on(table.owner,table.requestId)]);
export const cloudUploads = sqliteTable("cloud_uploads", {
 id: text("id").primaryKey(), owner: text("owner").notNull(), mime: text("mime").notNull(), created: integer("created").notNull(),
});
export const cloudWorker = sqliteTable("cloud_worker", {
 id: integer("id").primaryKey(), updated: integer("updated").notNull(), payload: text("payload").notNull(),
});

export const bugReports = sqliteTable("bug_reports", {
 id:text("id").primaryKey(), owner:text("owner").notNull(), requestId:text("request_id").notNull(),
 category:text("category").notNull(), description:text("description").notNull(), taskId:text("task_id"),
 error:text("error").notNull(), status:text("status").notNull(), reply:text("reply").notNull(),
 created:integer("created").notNull(), updated:integer("updated").notNull(), delivered:integer("delivered"),
}, table=>[uniqueIndex('bug_report_request').on(table.owner,table.requestId)]);
