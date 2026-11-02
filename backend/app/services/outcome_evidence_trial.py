"""Trial evidence gate: do not equate 'below the basket' with a make."""
from app.services import openai_shot_review as review
from types import SimpleNamespace


def scoring_evidence_client(client):
    def parse(**kwargs):
        kwargs["input"][0]["content"][0]["text"] += (
            " Extra rim crops are provided for scoring. Require visible downward rim/net passage "
            "for made, and visible rim deflection out of the basket for missed. Ball below the "
            "basket alone does not prove made. If neither direct result can be seen, return unclear. "
            "Never list rim_passage without direct evidence."
        )
        return client.responses.parse(**kwargs)
    return SimpleNamespace(responses=SimpleNamespace(parse=parse))


def needs_direct_outcome_check(decision):
    if not decision.is_shot:
        return False
    evidence = set(decision.visible_evidence)
    if decision.outcome == "made":
        return "rim_passage" not in evidence
    if decision.outcome == "missed":
        return "rim_deflection" not in evidence
    return True


def strict_outcome_once(client, model, images, *, effort="low"):
    response = client.responses.parse(
        model=model, reasoning={"effort": effort}, store=False,
        input=[{"role": "user", "content": [{"type": "input_text", "text": (
            "Review ONLY the scoring outcome of this already detected shot. Track the SAME ball "
            "through the chronological rim crops, using the overview for context. "
            "MADE requires visible downward passage inside the rim opening and through the net. "
            "A ball next seen below or to the side of the basket is NOT proof of passage. "
            "If a ball bounces on the rim and exits laterally outside the net without passing through, "
            "return missed with rim_deflection. If passage or miss cannot be established, return unclear. "
            "Do not claim rim_passage from a rebound, off-screen interval, or player's reaction. "
            "A ball overlapping the rim or bouncing above it may still miss. "
            "Name the relative timestamps and the actual path you observed in reason. "
            "Use rim_passage in visible_evidence ONLY for a directly visible make, "
            "rim_deflection ONLY for a directly visible miss. Do not infer a make from confidence alone."
        )}, *images]}], text_format=review.OutcomeReviewDecision,
    )
    decision = response.output_parsed
    if decision is None:
        raise review.OpenAIShotReviewError("invalid outcome response")
    expected = {"made": "rim_passage", "missed": "rim_deflection"}.get(decision.outcome)
    if expected and expected not in decision.visible_evidence:
        decision.outcome = "unclear"
        decision.review_required = True
        decision.reason = "Direct rim evidence was not returned; " + decision.reason[:240]
    return decision, review._usage_dict(response)
