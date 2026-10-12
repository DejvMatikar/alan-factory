# principal_proxy — REMOVED (2026-07-13)

**Tombstone.** `PrincipalProxyTeam` (`alan/teams/principal_proxy.py`, `alan/teams/_proxy_rules.py`, `alan/teams/_proxy_llm.py`) — an internal team meant to auto-answer certain blocking questions from worker teams on the user's behalf before they reached a human — was deleted whole per PA-14.

The team was provably unreachable in production: nothing in the codebase ever constructed an `OpenQuestion` with `resolver_role="principal_proxy"` — every construction site (`pr_merge.py`, `implementation_fix.py`, `verification.py`) hardcoded `resolver_role="human"` — so the router's proxy-bypass branch never fired outside its own unit tests. Its only production audit evidence (`~/.alan/logs/proxy_audit.jsonl`) was 100% test-fixture pollution. Two of its canned auto-answer rules directly contradicted this repo's own rules: the coverage-target rule named a "≥80% coverage on new code" bar against this repo's `fail_under=100` full-branch-coverage gate, and the type-hints rule assumed the project's mypy config left `disallow_untyped_defs` off, when `[tool.mypy] strict = true` in `pyproject.toml` enables it.

**Unregistering the team from `alan.teams.TEAMS` is what makes the residual reach-path safe.** The router's `_can_route_to_resolver` already fails closed on any resolver_role not present in the registered `teams` dict, so a stray `resolver_role="principal_proxy"` on any future handover falls through to `await_human` — the same outcome a human-facing blocking question has always had. No replacement guard was introduced; deleting the router's dedicated proxy branch and the team registration together close the only theoretical reach-path (a worker naming `principal_proxy` as a resolver).

Human-escalation behaviour is unchanged: blocking questions that require a person still route to `await_human` exactly as before.

A properly-designed blocked-question channel is deferred to separate future work — this removal does not build one.
