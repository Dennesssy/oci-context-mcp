# Fix pyproject.toml metadata

**Status:** pending
**Owner:** unassigned
**Blocked by:** nothing
**Priority:** high

## Context
Two stale fields in `pyproject.toml` will appear in PyPI and tooling output.
Quick win — no code changes required.

## Acceptance Criteria
- [ ] `description` updated from "28 tools" to "69 tools across 22 OCI services"
- [ ] `[project.urls]` Homepage and Repository updated from `github.com/oracle/oci-context-mcp` to `github.com/Dennesssy/oci-context-mcp`
- [ ] `pyproject.toml` passes `python -m build --dry-run` or `hatch build --dry-run` without error

## Technical Notes
File: `pyproject.toml` lines 8, 33-34.
No logic changes — metadata only.

## References
- Identified during ARCSEM audit session 2026-02-23
