CREATE TABLE bug_reports (
 id TEXT PRIMARY KEY NOT NULL,
 owner TEXT NOT NULL,
 request_id TEXT NOT NULL,
 category TEXT NOT NULL,
 description TEXT NOT NULL,
 task_id TEXT,
 error TEXT NOT NULL,
 status TEXT NOT NULL,
 reply TEXT NOT NULL,
 created INTEGER NOT NULL,
 updated INTEGER NOT NULL,
 delivered INTEGER
);
CREATE UNIQUE INDEX bug_report_request ON bug_reports(owner,request_id);
