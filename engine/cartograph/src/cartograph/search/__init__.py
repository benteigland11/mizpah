"""
Search package for Cartograph.

Hybrid TF-IDF + n-gram backend. Pure Python, zero external dependencies.
"""

from .hybrid import HybridBackend

__all__ = ["HybridBackend"]
