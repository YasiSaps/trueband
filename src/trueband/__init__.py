"""trueband: find audio files whose real bandwidth doesn't match their format.

trueband measures where a track's high-frequency content actually stops (its
spectral "cutoff") and flags files whose cutoff looks like a low-bitrate lossy
source, even if the file has since been re-encoded at a higher bitrate or
converted to a lossless format.

It is a heuristic. Treat its output as a shortlist to check, not a verdict.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
