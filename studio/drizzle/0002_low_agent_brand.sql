CREATE TABLE `workspace_catalogue` (
	`id` integer PRIMARY KEY NOT NULL,
	`catalogue` text NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL
);
