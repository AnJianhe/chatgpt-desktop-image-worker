CREATE TABLE `latest_link` (
	`id` integer PRIMARY KEY NOT NULL,
	`url` text NOT NULL,
	`state` text NOT NULL,
	`source_age` real NOT NULL,
	`received_at` integer NOT NULL
);
