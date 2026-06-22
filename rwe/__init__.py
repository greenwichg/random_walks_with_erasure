"""Random Walks with Erasure (RWE).

A reference implementation of

    Bibek Paudel and Abraham Bernstein. 2021.
    "Random Walks with Erasure: Diversifying Personalized Recommendations on
    Social and Information Networks." The Web Conference (WWW '21).

Public API
----------
Graph & random walks
    :class:`FeedbackGraph`, :class:`P3`, :class:`RP3Beta`, :class:`RWE`,
    :class:`RWED`, :class:`RWEB`.
Ideology detection
    :class:`IdeologyModel`, :class:`IdeologyResult`.
Baselines
    :class:`ItemKNN`, :class:`BPRMF`.
Data, metrics, experiments
    :mod:`rwe.data`, :mod:`rwe.metrics`, :mod:`rwe.experiment`.
"""

from .graph import FeedbackGraph
from .random_walk import P3, RP3Beta, RWE, RWED, RWEB
from .ideology import IdeologyModel, IdeologyResult
from .baselines import ItemKNN, BPRMF
from .satisfaction import (WebGraph, SatisfactionModel, AdaptiveRWEB,
                           detect_communities, community_viewpoints,
                           satisfaction_score)
from . import data, metrics, experiment

__version__ = "0.1.0"

__all__ = [
    "FeedbackGraph",
    "P3", "RP3Beta", "RWE", "RWED", "RWEB",
    "IdeologyModel", "IdeologyResult",
    "ItemKNN", "BPRMF",
    "WebGraph", "SatisfactionModel", "AdaptiveRWEB",
    "detect_communities", "community_viewpoints", "satisfaction_score",
    "data", "metrics", "experiment",
]
