# Agent Lab release follow-up

This follows PR #54's initial research journal. The missing schema declaration
is now aligned with migration `0019_agent_lab`. The equality check is retained;
older, unknown, multiple or missing database revisions are not accepted.
A regression test compares the declared version with Alembic's migration head.

## Navigation

The main dashboard now has a native **Agent Lab** link below the brand in the
sidebar. It is outside `nav-companies`, so coverage loading, coverage errors and
`renderSidebar()` cannot remove it. It works without JavaScript and has visible
keyboard focus. On narrow screens the sidebar becomes a top section and the
coverage list scrolls within a bounded area.

The Trade Decision Dashboard also links to `/agents` in its navigation. The
Agent Lab page already links back to the main and trade dashboards and marks
its own Agent Lab link as the current page.

These are research links, not controls to run agents or place trades.

## Release sequence remains deliberate

Passing CI and a successful preview build are code-release checks only. Before
production capture: coordinate the matching application/worker release with
`alembic upgrade head`, verify the journal tables can be read, explicitly enable
`AGENT_LAB_ENABLED=true`, and perform the documented authenticated five-stock
capture. Older workers with the exact 0018 gate must not continue after the
0019 migration. Do not point test databases or previews at production data.

No automatic migration, feature-flag change, broker access, trading, rewards,
exploration or scheduled delivery is added by this follow-up.
