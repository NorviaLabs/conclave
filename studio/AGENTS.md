# Studio agent instructions

Read ../STUDIO_HANDOFF.md and ../docs/studio/PRODUCT.md for the accepted product
decisions. ../docs/studio/BUILD_PLAN.md is a recommended roadmap, not a claim
that those services already exist. The repository root AGENTS.md applies too.

## User experience

- The primary audience is nontechnical. Use ordinary words such as application,
  team member, step, source, result and review. Hide schemas, state keys, model
  details, raw events and integration machinery in advanced disclosures.
- Keep the first screen calm: one primary action, compact summaries, short
  labels, details shown when relevant. Avoid dense instruction blocks.
- Show only user-created or intentionally customized applications in the
  sidebar; initially show at most three. Catalog starters are separate.
- New application begins with a goal and editable suggestions for the team,
  tools and results. Suggestions must accurately describe how they were made.
- Canvas positions and workflow dependencies are independent. Preserve arbitrary
  acyclic graphs, multiple roots, branches and joins, plus keyboard/list editing.
- Preserve the six featured workflows and their realistic source constraints.
  Fictional samples must stay labeled as samples.

## Boundaries and development

- Keep reusable runtime code in the root conclave/ package and Studio-specific
  behavior here. Preserve core provider independence and capability gates.
- The Node local preview is development-only and binds to loopback. Production
  APIs need their own authenticated host and persistent run service.
- Human draft review never grants blanket permission to send, write or publish.
- Keep credentials outside application definitions, browser source and Git.
- Preserve migrations. Add a migration for schema changes rather than editing
  an already-applied migration.
- Preserve package-lock.json. npm run dev builds and serves the actual Worker;
  restart after edits. npm run build produces the Site Worker artifact.
- Read ../docs/studio/site-readme-snapshot.md only as historical context. Current
  catalog counts and graph behavior come from code and the current README.
- Local development is the default for this terminal handoff. Publishing is a
  separate requested task; a Git commit alone does not update the existing Site.
- Run focused checks appropriate to the change. Do not claim provider, connector
  or workflow quality from deterministic sample output.
