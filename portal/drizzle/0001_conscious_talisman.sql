CREATE TABLE `cloud_tasks` (
	`id` text PRIMARY KEY NOT NULL,
	`owner` text NOT NULL,
	`request_id` text NOT NULL,
	`created` integer NOT NULL,
	`updated` integer NOT NULL,
	`status` text NOT NULL,
	`prompt` text NOT NULL,
	`tool` text NOT NULL,
	`refs` text NOT NULL,
	`snapshot` text NOT NULL,
	`decision` text,
	`local_id` text,
	`worker` text
);
--> statement-breakpoint
CREATE TABLE `cloud_uploads` (
	`id` text PRIMARY KEY NOT NULL,
	`owner` text NOT NULL,
	`mime` text NOT NULL,
	`created` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `cloud_worker` (
	`id` integer PRIMARY KEY NOT NULL,
	`updated` integer NOT NULL,
	`payload` text NOT NULL
);
