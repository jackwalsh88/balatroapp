"""Balatro Advisor - reads Balatro game state and returns strategic advice.

Architecture (spec section 1): arithmetic and judgment are separate stages.

    input adapter -> canonical state -> deterministic scorer -> advisor
                                                                  |
                                            output <- validator <-+

The advisor never computes a score. It receives computed scores as input and
reasons about them.
"""

from __future__ import annotations

__version__ = "0.1.0"
