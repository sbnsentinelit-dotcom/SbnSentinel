# Draft disposition: picomatch GHSA-fc5q-39xc-vjpf (F-33 release gate)

Status: **DRAFT — NOT APPROVED.** This file is evidence for the accountable approver.
It does not change `frontend/dependency-disposition.json`, and the F-33 gate stays active
until an approver adds an entry there.

## Advisory

| Field | Value |
| --- | --- |
| Advisory | GHSA-fc5q-39xc-vjpf — "picomatch vulnerable to ReDoS through single-branch repeated extglobs" |
| Severity | High |
| Vulnerable range | picomatch <= 4.0.7 |
| Patched version | None (GitHub advisory rechecked 18 Nov 2026, updated that day; npm latest is 4.0.7) |
| Installed versions | picomatch 4.0.7 and 2.3.2 |

An upgrade is therefore not available; a generic "upgrade" remediation is not possible.

## Dependency path and exposure (verified 18 Nov 2026)

- picomatch is reached only through development/test tooling:
  `vitest` → `vite` / `tinyglobby` / `fdir` → picomatch 4.0.7;
  `eslint-config-next` → `fast-glob` / `micromatch` → picomatch 2.3.2;
  `@vitejs/plugin-react` → `vite` → picomatch 4.0.7.
- All of these are `devDependencies` in `frontend/package.json`. Runtime `dependencies` are only
  `@reduxjs/toolkit`, `lucide-react`, `next`, `react`, `react-dom`, `react-redux`.
- No file under `frontend/src` imports picomatch, micromatch, tinyglobby or fast-glob.
- A production build (`next build`, standalone output) contains no picomatch files.
- The ReDoS needs an attacker-controlled glob pattern. In this project, glob patterns come only from
  repository configuration used by lint and test runs on developer machines and CI runners.

Exposure: build/test tooling only; no production runtime or network-reachable path.

## Proposed entry for `frontend/dependency-disposition.json` (to be added by the approver)

```json
{
  "package": "picomatch",
  "advisory": "GHSA-fc5q-39xc-vjpf",
  "severity": "high",
  "affected_version": "<=4.0.7",
  "fixed_version": "none published (rechecked 2026-11-18)",
  "exposure": "Development and CI tooling only (vitest, vite, eslint-config-next); not in runtime dependencies or the production build",
  "justification": "No production code path; glob patterns come only from repository configuration, not user input",
  "remediation_plan": "Re-check the advisory at each release; upgrade picomatch (or the tools that pull it in) as soon as a patched version is published; remove this disposition then",
  "approved_by": "<name and role of the accountable approver>"
}
```

## Required after approval

1. Approver adds the entry above (with their name) to `frontend/dependency-disposition.json`.
2. Rerun the frontend CI job; tests, lint and build must then run and pass or be reported.
3. Keep the F-33 gate unchanged.
