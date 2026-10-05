CREATE TABLE `applications` (
	`id` text PRIMARY KEY NOT NULL,
	`definition` text NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL,
	`updated_at` text NOT NULL
);
