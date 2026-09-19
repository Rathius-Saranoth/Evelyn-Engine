"""Tier-scoring invariants for the profile evolver.

These guard the behavioural-hardening contract: the deterministic pruner must not
be able to delete the user's technical or relational identity while protecting
transient symptom logging. Prior to this, `_USER_TIER_1_PATTERNS` protected
fatigue/pain/sleep vocabulary while `_USER_TIER_2_PATTERNS` (technical,
infrastructure, architecture) was pruned first — a ratchet that converted the
profile into a medical chart over successive evolution passes.

Scores are inverted relative to the written marker: 3 == Tier 1 (prune last),
1 == Tier 3 (prune first).
"""

import evelyn_config as cfg
from Evelyn.tools.profile_evolver import score_bullet_tier

USER = cfg.PERSONA_FILE_USER
DIRECTIVES = cfg.PERSONA_FILE_DIRECTIVES

TIER_1, TIER_2, TIER_3 = 3, 2, 1


class TestExplicitTierMarkerWins:
    """A written [Tier N] marker is authoritative over keyword heuristics."""

    def test_explicit_marker_overrides_heuristic(self):
        # 'migraine' is a Tier 3 keyword, but an explicit marker outranks it.
        bullet = "* [Tier 1] **Condition Management**: He manages migraine episodes."
        assert score_bullet_tier(USER, "## Identity & Core Values", bullet) == TIER_1

    def test_explicit_tier_3_demotes_protected_keyword(self):
        # 'partnership' is a Tier 1 keyword, but the marker demotes it.
        bullet = "* [Tier 3] **Partnership Note**: A passing remark about partnership."
        assert score_bullet_tier(USER, "## Relationship Dynamics", bullet) == TIER_3

    def test_unlabelled_bullet_falls_back_to_heuristics(self):
        bullet = "* **Collaboratory Partnership**: He views their bond as a partnership."
        assert score_bullet_tier(USER, "## Relationship Dynamics", bullet) == TIER_1


class TestTechnicalIdentityOutranksTransientSymptoms:
    """The core regression: identity must never prune before symptom logging."""

    def test_technical_domain_outranks_symptom_episode(self):
        technical = "* **Technical Domains**: He focuses on generative AI and systems engineering."
        symptom = "* **Physical Thresholds**: He monitors neck tension and eye strain."
        t_score = score_bullet_tier(USER, "## Personal Context", technical)
        s_score = score_bullet_tier(USER, "## Interaction Preferences & Constraints", symptom)
        assert t_score > s_score, "technical identity must be pruned after transient symptoms"

    def test_transient_symptoms_are_tier_3(self):
        for bullet in (
            "* **Migraine Management**: During migraines he requires low-stimulation environments.",
            "* **Energy Limits**: He treats end-of-day exhaustion as a physical constraint.",
            "* **Condition Management**: He manages seasonal allergies with specific protocols.",
        ):
            assert score_bullet_tier(USER, "## Interaction Preferences & Constraints", bullet) == TIER_3

    def test_chronic_conditions_remain_tier_1(self):
        bullet = "* **Physical Wellbeing**: He manages chronic physical conditions, including respiratory needs."
        assert score_bullet_tier(USER, "## Identity & Core Values", bullet) == TIER_1

    def test_autonomy_is_tier_1(self):
        bullet = "* **Autonomous Pacing**: He manages his own energy and retains pacing authority."
        assert score_bullet_tier(USER, "## Relationship Dynamics", bullet) == TIER_1


class TestForwardMomentumDirectivesProtected:
    """Anti-passivity directives must be as durable as anti-sycophancy directives."""

    def test_momentum_directive_is_tier_1(self):
        bullet = "* **Forward Momentum Default**: Default to active collaboration and forward progress."
        assert score_bullet_tier(DIRECTIVES, "## Conversation & Formatting", bullet) == TIER_1

    def test_proactive_engagement_is_tier_1(self):
        bullet = "* **Proactive Engagement**: Conclude substantive turns with a concrete technical hook."
        assert score_bullet_tier(DIRECTIVES, "## Operational Guidelines", bullet) == TIER_1

    def test_momentum_matches_existing_candor_protection(self):
        candor = "* **Critical Candor**: Provide honest feedback without sycophancy."
        momentum = "* **Co-Pilot Default**: Offer a concrete path forward rather than suggesting retreat."
        assert score_bullet_tier(DIRECTIVES, "## Operational Guidelines", candor) == score_bullet_tier(
            DIRECTIVES, "## Operational Guidelines", momentum
        )


class TestJournalRagExclusionHolds:
    """The journal feedback loop was the largest historical drift driver.

    Evelyn's own journal entries were being retrieved back into her context as the
    single largest RAG source. The exclusion is in place; this asserts it stays.
    """

    def test_journal_path_is_rag_excluded(self):
        from Evelyn.tools.chroma_rag import is_rag_excluded_source

        path = f"{cfg.ASSISTANT_NAME}/{cfg.ASSISTANT_NAME}'s Journal/2026/09/Journal Entry 2026-09-18.md"
        assert is_rag_excluded_source(path) is True

    def test_ordinary_vault_note_is_not_excluded(self):
        from Evelyn.tools.chroma_rag import is_rag_excluded_source

        assert is_rag_excluded_source("Projects/Evelyn Engine/Architecture.md") is False
