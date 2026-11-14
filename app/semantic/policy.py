"""Host policy over checked signals. The model proposes signals with quotes; this code decides the action.

Core risk signals are the ones that move money or credentials. Support signals are pressure tactics.
Protective signals lower the score. The tiers are fixed here and do not depend on the model's own
``is_fraud`` field.

    score = 2 * core + support - 2 * protective
    HOLD       score >= 3
    STEP_UP    score == 2
    RELEASE    otherwise
"""
CORE = {"offpath_transfer_request", "credential_request", "upfront_fee_for_release", "guaranteed_returns"}
SUPPORT = {"authority_claim", "urgency_pressure", "secrecy_or_isolation", "third_party_coaching"}
PROTECTIVE = {"official_verification_offered", "risk_disclosure"}


def decide(signal_names):
    names = set(signal_names)
    score = 2 * len(names & CORE) + len(names & SUPPORT) - 2 * len(names & PROTECTIVE)
    action = "HOLD_FOR_ANTI_SCAM_INTERVENTION" if score >= 3 else "STEP_UP_VERIFICATION" if score == 2 else "RELEASE"
    return dict(action=action, score=score, core=sorted(names & CORE), support=sorted(names & SUPPORT),
                protective=sorted(names & PROTECTIVE))


def is_intercept(decision):
    return decision["action"] != "RELEASE"
