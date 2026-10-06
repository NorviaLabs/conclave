# Studio configuration and Conclave interfaces

The current boundary is documented in [Conclave REST API](../REST_API.md).
The reusable framework, application runner, CLI, REST routing and persistence live
in Conclave. Studio communicates with them over REST only; it has no Python
runtime, command pipe or SQLite owner.

Product choices remain configurable through backend data and host settings:
provider factories, source suffixes, roles, starters, output presets, manual
planning rules, limits, connector extensions and typed output fields.
Canonical bundled data lives in `conclave/application/data/`. The backend serves
that data over `/api/config/*`; custom workspace data uses `/api/catalogue`.

Protocol versions, primitive field/rendering types, validation bounds and approval
checks remain implementation contracts. Event/schedule execution is unsupported
on the local host. UI copy and rendering are ordinary implementation code.

The REST refactor is being validated with the full offline suite and independent
Studio/Conclave smoke and browser checks; use the documented commands to refresh
evidence instead of relying on historical counts from the earlier pipe bridge.

Fictional fixtures, sample execution and the legacy investigation demo were removed. Studio runs saved applications through Conclave REST.
