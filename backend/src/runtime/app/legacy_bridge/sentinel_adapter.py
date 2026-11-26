"""G2 boundary only. Publishes no records or decisions."""
from .assessment import assess


def build_review_fact(context, batch, *, sample_id, archive_ref):
    """Caller supplies the trusted context and immutable scoped evidence pointer.

    The scalar is a review hint. Full issues and their evidence references belong
    in the scoped evidence store addressed by archive_ref. Do not submit the hint
    to generic G5 intake as a completion or retirement event.
    """
    from .identity import encode, digest
    from .contracts import Identifier
    from pydantic import TypeAdapter
    for ref in (sample_id, archive_ref):
        TypeAdapter(Identifier).validate_python(ref, strict=True)
    connector_id = "v3-review-" + digest(context.scope.model_dump())
    from app.integrations.core.source_contracts import SourceObservation, normalize_source_observation
    result = assess(context, batch)
    settings = context.model_dump(exclude={"bindings", "seals"})
    settings["bindings"] = sorted(encode(b.model_dump()) for b in context.bindings)
    settings["seals"] = sorted(encode(s.model_dump()) for s in context.seals)
    rows = {encode(r.model_dump(exclude={"evidence", "seen", "connector"}))
            for r in batch.records if r.seen <= context.as_of}
    version = digest([batch.version, settings, sorted(rows),
                      [(i.code, i.item) for i in result.issues]])
    observation = SourceObservation(connector_instance_id=connector_id, external_record_id=sample_id,
                                    resource_kind="LegacyClaimContinuityReview", source_version=version,
                                    schema_version=batch.version, observed_at=context.as_of, imported_at=context.as_of, payload_ref=archive_ref)
    fact = normalize_source_observation(observation, "candidate_review_state", result.status,
                                        quality_state="RESEARCH_ONLY", mapping_version="legacy-continuity.v1", effective_at=context.as_of)
    return observation, fact, result
