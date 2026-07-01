"""
Spec-compliance checks for the M4 combined scoring.

Verifies that the generated scoring functions match the exact thresholds and
formulas in planning.md before they drive any real decision. Runnable with no
extra dependencies:

    python tests/test_scoring.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector import (
    classify,
    combine_signals,
    confidence_for,
    sentence_rhythm_score,
    transparency_label,
)

# Classification boundaries (inclusive >= 0.75 / <= 0.25)
assert classify(0.25) == "likely_human"
assert classify(0.26) == "uncertain"
assert classify(0.74) == "uncertain"
assert classify(0.75) == "likely_ai"
assert classify(0.00) == "likely_human"
assert classify(1.00) == "likely_ai"
assert classify(0.50) == "uncertain"

# Combine weights (0.50 / 0.50, rounded to 2 decimals)
assert combine_signals(0.8, 0.7) == 0.75
assert combine_signals(0.0, 0.0) == 0.0
assert combine_signals(1.0, 1.0) == 1.0
assert combine_signals(0.4, 0.6) == 0.5

# Confidence branches
assert confidence_for(0.90) == 0.90
assert confidence_for(0.75) == 0.75
assert confidence_for(0.25) == 0.75
assert confidence_for(0.10) == 0.90
assert confidence_for(0.50) == 0.0     # NOT 0.5 (the M3-placeholder bug)
assert confidence_for(0.60) == 0.20    # NOT 0.40
assert confidence_for(0.26) == 0.48
assert confidence_for(0.74) == 0.48

# Signal 2 sanity
assert sentence_rhythm_score("Short.") == 0.50  # <2 sentences -> neutral
uniform = "The cat sat on the mat. The dog ran in the park. The bird flew in the sky."
varied = "Yes! I ran, and then exhausted, gasping, utterly spent, I finally, slowly stopped; why? Who knows."
assert sentence_rhythm_score(uniform) > sentence_rhythm_score(varied)

# Transparency label variants (exact text from planning.md, one per classification)
assert transparency_label("likely_ai") == (
    "This content was flagged as likely AI-generated based on multiple "
    "detection signals. This label is not a final judgment, and the creator "
    "may appeal the decision."
)
assert transparency_label("likely_human") == (
    "This content was assessed as likely human-created based on multiple "
    "detection signals. No strong indicators of AI generation were found."
)
assert transparency_label("uncertain") == (
    "This content could not be confidently classified as human-created or "
    "AI-generated. The result is uncertain, so readers should treat this "
    "label as contextual information rather than a final judgment."
)
# All three variants are distinct, and each classification is reachable via classify()
assert len({transparency_label(c) for c in ("likely_ai", "likely_human", "uncertain")}) == 3
assert transparency_label(classify(0.90)) == transparency_label("likely_ai")
assert transparency_label(classify(0.10)) == transparency_label("likely_human")
assert transparency_label(classify(0.50)) == transparency_label("uncertain")

print("all scoring spec checks passed")
