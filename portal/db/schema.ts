import { sqliteTable, integer, text, real } from "drizzle-orm/sqlite-core";
export const latestLink = sqliteTable("latest_link", {
  id: integer("id").primaryKey(),
  url: text("url").notNull(),
  state: text("state").notNull(),
  sourceAge: real("source_age").notNull(),
  receivedAt: integer("received_at").notNull(),
});
