# HSP01 PR #1 — correction matrix for the next review

Branch: `feature/hsp01-hospital-extension` (PR #1). Published head before this submission: `70b84838122bacf5eb54fda6cebcafb4c37e70a2`.
Migration head: `hsp01_schema_completion`
(`g8_financial_reporting → hsp01_hospital_extension → hsp01_auditor_corrections → hsp01_schema_completion`).
Backend paths omit `backend/src/runtime/app/`; test paths omit `backend/tests/integration/`.

## Review 2 (commit a9ee9895)

| # | Item | Changed files | Regression tests | Result |
| --- | --- | --- | --- | --- |
| 1 | Jobs API returns `row_version`; resume uses it as `expected_revision`; older events without original binding | `api/v1/endpoints/hospital.py`, `services/hospital_worker.py`, `docs/hsp01_auditor_followup_reconciliation.txt` | `test_followup_jobs_api_revision_stale_refresh_and_repeat`, `test_followup_resume_rejects_revoked_and_material_profile_change`, `test_auditor_blocked_delivery_resume_is_versioned_and_projects_once` | Implemented in f58c08e7. Local pass. Older events without `extraction_binding` → HTTP 409 `HISTORICAL_BINDING_RECONCILIATION_REQUIRED`; incompatible profile → `MATERIAL_BINDING_RECONCILIATION_REQUIRED`; no binding is back-filled |
| 2 | Unvalidated historical department attribution excluded from departmental totals | `services/financial_reporting_service.py`, `frontend/src/components/hospital/DepartmentSummary.tsx` | `test_followup_unproven_current_attribution_preserves_frozen_snapshot`, `test_followup_attribution_boundary_is_utc_in_non_utc_database_session`, `test_auditor_effective_department_date_is_required`, frontend "shows unresolved historical attribution as a count and coverage explanation" | Implemented in f58c08e7. Local pass. Report shows `unresolved_attribution_lines` and a coverage exception; frozen snapshots unchanged |
| 3 | Backend CI migrates a disposable DB before import | `.github/workflows/quality_gate.yml` (f58c08e7; workflow made valid in 70b84838) | CI step "Migrate disposable quality database before application imports" | **CI PASS** in run 44349431745 (run on 8d5fee00 — identical code to 70b84838; only the commit message was later changed). Unit-test step not reached in CI because Flake8 stops the job (see open items); locally 3/3 unit tests pass |
| 4 | Frontend dependency gate (picomatch GHSA-fc5q-39xc-vjpf) | `docs/hsp01_picomatch_disposition_draft.md` (evidence only) | F-33 gate | **Open.** No patched version (rechecked 18 Nov). Dev/test-only exposure documented; disposition needs the accountable approver. Gate kept active |
| 5 | Main conflict with 70503767 in `encounters.py` | `api/v1/endpoints/encounters.py`, `test_hsp01_encounter_merge.py` | `test_hsp01_encounter_merge.py` (10 tests), now also selected in CI | Resolved and pushed in f58c08e7. Latest main af0496b8: see "Latest main" below |
| 6 | Accurate claims and handoff | `docs/hsp01_implementation_register.json` | — | Register updated: actual SHAs, push and CI status, owners of remaining work |

## Review 1 (commit d4bb1739) — status carried forward

| # | Item | Regression tests | Result |
| --- | --- | --- | --- |
| 1 | Hospital enrollment independent of departments | `test_auditor_zero_department_enrollment_guards_legacy_routes` | Verified by reviewer at a9ee9895 |
| 2 | Line currency preserved on parent change | `test_auditor_parent_currency_correction_never_relabels_line`, `test_auditor_currency_backfill_uses_line_evidence_not_parent` | Verified at a9ee9895 |
| 3 | Re-verify from DEGRADED; fenced against revocation | `test_auditor_temporary_source_failure_can_reverify_unchanged_profile`, `test_auditor_revocation_during_failed_verification_is_not_overwritten` | Verified at a9ee9895 |
| 4 | Controlled resume of blocked deliveries | `test_auditor_blocked_delivery_resume_is_versioned_and_projects_once` + Review 2 item 1 | Completed with Review 2 item 1 |
| 5 | Effective-date department attribution | `test_auditor_effective_department_date_is_required` + Review 2 item 2 | Completed with Review 2 item 2 |
| 6 | Identifiable, recoverable quarantine | `test_auditor_quarantine_page_rows_are_recoverable_and_atomic` | Verified at a9ee9895 |
| 7 | Review queue independent of financial report | Frontend: "keeps review and assignment available without report permission", "keeps a successful queue when financial snapshot fails", "shows the queue while the independent report request is still pending", "clears old-scope results and discards a late response" | Verified at a9ee9895 |
| 8 | SQLite legacy-upgrade path | `test_forward_migration_preserves_legacy_ids_and_audits_money` | Verified at a9ee9895 |
| REST/CSV | Same version through either transport | `test_auditor_rest_csv_same_version_semantics_and_distinct_raw_digests` | Verified at a9ee9895 |

## Additional corrections found in QA review (after f58c08e7)

| Commit | Problem | Files | Evidence |
| --- | --- | --- | --- |
| 15f8bf71 | Fresh install by Alembic missed the decision-context tables; every event failed at L4 (`no such table: dce_context_evidence`) | `alembic/versions/hsp01_schema_completion.py`, `alembic/env.py` | Fresh install 70 tables; real EHR event reaches Completed; upgrade from `hsp01_auditor_corrections` and from a DB that already has the tables both pass; **CI PostgreSQL "Fresh forward migrations" PASS** (run 44349431745) |
| 15f8bf71 | `hospital_legacy_guard` read `actor.org_id` directly → 500 for principals without `org_id`; broke 2 existing D4/D5 tests | `services/hospital_legacy_guard.py` | Both tests pass |
| 15f8bf71 | Allowed-heads test did not know the HSP01 heads | `tests/integration/test_round2_acceptance.py` | `test_at28_versioned_migrations_and_error_propagation` passes; single head |
| 70b84838 | Workflow invalid: `${{ runner.temp }}` in job-level `env` → no CI job started on f58c08e7 / 15f8bf71 | `.github/workflows/quality_gate.yml` | Run 44349431745 starts all three jobs |
| this submission | CI selects only 2 of the 3 hospital suites | `.github/workflows/quality_gate.yml` | Isolated step now also runs `test_hsp01_encounter_merge.py` |

## Migration: fresh installs versus databases with earlier records

- **Fresh install:** `alembic upgrade head` only (autocreate stays disabled). Verified locally on SQLite and in CI on PostgreSQL.
- **Database at `hsp01_auditor_corrections`:** upgrade adds 6 `dce_context_*` tables and `decision_records`; no data change.
- **Older database created by the legacy `create_all` path:** tables are created with `checkfirst`; existing rows untouched.
- **Earlier hospital records:** blocked events without an original binding and unvalidated department attributions are not rewritten; they surface as reconciliation-required / unresolved (Review 2 items 1–2). Target-environment reconciliation needs the data owner and is not certified by fresh-install tests.
- **Downgrade:** refused by design (history preservation).

## CI evidence (run 44349431745, run on 8d5fee00 — identical code to 70b84838; only the commit message was later changed)

| Job | Result | Notes |
| --- | --- | --- |
| HSP01 disposable PostgreSQL migration and recovery | **PASS** | Fresh forward migrations; hospital concurrency, authority recovery and regressions |
| Backend Quality Assurance | FAIL at Flake8 | Migration step passed; later steps skipped. The same Flake8 step fails on main (04c1e0f4, eb42cc07, 70503767, af0496b8). Local flake8 6.1.0 on the PR head: 0 issues; exact CI lines pending |
| Frontend Quality Assurance | FAIL at F-33 dependency audit | picomatch (Review 2 item 4); tests, lint and build skipped; same on main |

Local verification on the PR head: HSP01 suites 3/3 (directory 21, source contract 48, encounter merge 10 tests);
full backend 142/143 (`test_a026b_practice_fusion_readiness` fails, also on main); frontend vitest 106 passed,
2 failed (BootScreen T03/T05 and T06, also on main), 10 skipped; `tsc` 0 errors; hospital ESLint 0 errors.

## Latest main (af0496b8) — not merged into this PR yet

Merging is conflict-free, but af0496b8 changes `api/v1/endpoints/decisions.py` so that:
users without organization/clinic are given `ORG-UAE-01` / `DXB-CLINIC-01` in memory;
an unknown recommendation ID creates a new ACTIVE recommendation ("Auto-approved governed clinical action");
and the journey scope check is replaced by `has_access = True`.
This removes human-decision scope enforcement and adds 8 Flake8 errors (in `decisions.py` and `api/deps.py`).
On the combined code, two more tests fail (`test_d4_pipeline_real_evidence_conflict_exact_refs`,
`test_d5_no_signal_recommendation_fallback`), which also fail on main itself.
This is tracked as a main regression for the existing developer; it should be corrected on main before
main is merged into PR #1, so the PR does not absorb it.

## Remaining dependencies and owners

| Item | Owner |
| --- | --- |
| Backend Flake8 in Linux CI | New developer, with the existing developer for main-owned files |
| picomatch disposition | Accountable approver named in `dependency-disposition.json` |
| BootScreen startup-screen tests | Existing shared frontend owner |
| `test_a026b_practice_fusion_readiness` | Existing developer (fails on main) |
| af0496b8 decision-scope regression on main | Existing developer |
| Governed billing evidence/context/policy/rule/review/outcome; FR01–FR08 and schedules; legacy reconciliation; T01–T12 acceptance | HSP02 workstream |
| Private RSA key in `quality_gate.yml` | Security owner (move to CI secrets, rotate, purge history) |
