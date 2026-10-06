import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';
export const applications = sqliteTable('applications', {
 id: text('id').primaryKey(),
 definition: text('definition').notNull(),
 revision: integer('revision').notNull().default(1),
 updatedAt: text('updated_at').notNull(),
});
export const memberTemplates = sqliteTable('member_templates', {
 id: text('id').primaryKey(),
 member: text('member').notNull(),
 updatedAt: text('updated_at').notNull(),
});
export const workspaceCatalogue = sqliteTable('workspace_catalogue', {
 id: integer('id').primaryKey(),
 catalogue: text('catalogue').notNull(),
 revision: integer('revision').notNull().default(1),
});
