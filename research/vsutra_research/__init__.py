"""Offline research and training workspace for V-Sutra.

This package is intentionally separate from the FastAPI application. It reads and validates
datasets, scores evaluations, estimates hardware, manages model manifests, benchmarks real
endpoints, and describes inference compatibility. It does not train or deploy a model.
"""

__version__ = "0.1.0"
