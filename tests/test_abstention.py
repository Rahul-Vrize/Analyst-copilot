from analyst_copilot.abstention.policy import should_abstain
from analyst_copilot.evidence.ledger import AnswerCandidate, Claim


def test_abstains_when_unsupported():
    candidate = AnswerCandidate(status="unsupported")
    assert should_abstain(candidate) is True


def test_abstains_when_slot_missing():
    candidate = AnswerCandidate(status="supported", missing_slots=["capex_fy2018"])
    assert should_abstain(candidate) is True


def test_abstains_when_claim_has_no_evidence():
    candidate = AnswerCandidate(
        status="supported", claims=[Claim(text="Revenue was $120M", evidence_ids=[])]
    )
    assert should_abstain(candidate) is True


def test_answers_when_fully_supported():
    candidate = AnswerCandidate(
        status="supported",
        claims=[Claim(text="Revenue was $120M", evidence_ids=["cell-1"])],
    )
    assert should_abstain(candidate) is False
