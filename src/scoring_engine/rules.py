"""
rules.py
========
Defines symptom weights, severity bonuses, and risk categories used by the
scoring engine.

Each weight represents the base risk contribution of a detected symptom.
Severity bonuses are additive modifiers applied on top of symptom weights.
Confidence (0.0–1.0) is a multiplier supplied by the LLM to reflect how
certain it is about the extracted symptoms.
"""

from typing import Dict

# ---------------------------------------------------------------------------
# Symptom Weights
# ---------------------------------------------------------------------------
# Each key matches a symptom identifier that Llama3 may extract from speech.
# Values represent how much risk that symptom contributes to the total score.

SYMPTOM_WEIGHTS: Dict[str, int] = {
    # ---- CRITICAL / EMERGENCY (score ≥61 alone → HIGH/CRITICAL, send WhatsApp) ----
    "unconscious":        100,
    "stroke_symptoms":   100,
    "chest_pain":         65,   # always HIGH — chest pain is always an emergency

    "fall_detected":      65,   # always HIGH
    "breathing_problem":  65,   # always HIGH

    "severe_bleeding":    70,
    "heart_palpitation":  61,   # always HIGH
    "high_fever":         61,   # fever ≥ 103°F — always HIGH

    # ---- MODERATE (need more symptoms or high severity to reach HIGH) ----
    "dizziness":          25,
    "fever":              15,   # mild/normal fever — LOW alone
    "vomiting":           20,
    "nausea":             10,
    "weakness":           15,
    "swelling":           20,
    "dehydration":        25,
    "confusion":          35,   # confusion alone can reach MEDIUM
    "high_blood_pressure": 30,
    "low_blood_pressure":  30,
    "blood_sugar_issue":   30,

    # ---- MINOR (almost never reach HIGH alone → NO WhatsApp) ----
    "headache":           10,
    "body_pain":          10,
    "joint_pain":         10,
    "mild_fever":          8,
    "cough":               8,
    "cold":                5,
    "sore_throat":         5,
    "stomach_pain":       12,
    "acidity":             8,
    "constipation":        5,
    "fatigue":            10,
    "back_pain":          10,
    "medicine_missed":    10,
    "sleep_problem":       5,
    "anxiety":            12,
    "appetite_loss":       8,
}

# ---------------------------------------------------------------------------
# Severity Bonus
# ---------------------------------------------------------------------------
# Represents how much extra risk is added based on the overall severity
# reported by the user or inferred by the LLM.

SEVERITY_BONUS: Dict[str, int] = {
    "low":      0,
    "medium":  10,
    "high":    20,
    "critical": 40,
}

# ---------------------------------------------------------------------------
# Symptom Categories
# ---------------------------------------------------------------------------
# Maps each symptom to a clinical risk category.
# When multiple symptoms span different categories, the engine picks the
# highest-weighted category as the primary one.
#
# Future categories can be added here without touching scoring logic.

SYMPTOM_CATEGORIES: Dict[str, str] = {
    # Emergency
    "chest_pain":           "CARDIAC",
    "unconscious":          "CARDIAC",
    "heart_palpitation":    "CARDIAC",
    "severe_bleeding":      "CARDIAC",
    "stroke_symptoms":      "NEUROLOGICAL",
    "confusion":            "NEUROLOGICAL",
    "breathing_problem":    "RESPIRATORY",
    "fall_detected":        "FALL",
    # Moderate
    "high_fever":           "GENERAL",
    "dizziness":            "NEUROLOGICAL",
    "vomiting":             "GENERAL",
    "nausea":               "GENERAL",
    "weakness":             "GENERAL",
    "swelling":             "GENERAL",
    "dehydration":          "GENERAL",
    "high_blood_pressure":  "CARDIAC",
    "low_blood_pressure":   "CARDIAC",
    "blood_sugar_issue":    "GENERAL",
    # Minor
    "fever":                "GENERAL",
    "headache":             "GENERAL",
    "body_pain":            "GENERAL",
    "joint_pain":           "GENERAL",
    "mild_fever":           "GENERAL",
    "cough":                "RESPIRATORY",
    "cold":                 "GENERAL",
    "sore_throat":          "GENERAL",
    "stomach_pain":         "GENERAL",
    "acidity":              "GENERAL",
    "constipation":         "GENERAL",
    "fatigue":              "GENERAL",
    "back_pain":            "GENERAL",
    "medicine_missed":      "MEDICATION",
    "sleep_problem":        "GENERAL",
    "anxiety":              "GENERAL",
    "appetite_loss":        "GENERAL",
}

# Priority order when resolving multiple categories (highest = most urgent)
CATEGORY_PRIORITY: Dict[str, int] = {
    "CARDIAC":       6,
    "NEUROLOGICAL":  5,
    "RESPIRATORY":   4,
    "FALL":          3,
    "MEDICATION":    2,
    "GENERAL":       1,
    "UNKNOWN":       0,
}

# ---------------------------------------------------------------------------
# NOTE — Future: Repeated-Symptom History Multiplier
# ---------------------------------------------------------------------------
# When a user reports the same symptom on consecutive days, the risk should
# escalate. Suggested logic (not yet implemented):
#
#   day 1 → score = base_score * 1.0
#   day 2 → score = base_score * 1.5
#   day 3 → score = base_score * 2.0
#
# This requires a persistent session store (e.g. Redis or SQLite) keyed on
# user_id + symptom. Tag this with: TODO(history-escalation)

