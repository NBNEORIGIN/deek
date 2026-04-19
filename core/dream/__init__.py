"""Dream state — nocturnal free-association over memories.

The package runs a loop overnight:

1. Sample high-salience recent memories (seeds)
2. For each seed, pull graph-connected but topically-distant companions
3. Ask a local LLM at temperature 0.9 if the bundle shares a pattern
4. Filter hard — reject ungrounded, generic, or duplicate candidates
5. Score and rank survivors
6. Store in `dream_candidates` for morning surfacing in the PWA

Design principle: **free association produces plausible nonsense by
default. The value is in the filter.** Budget for a brutal kill rate:
~100 attempts in → ~3 surface. Every surviving candidate cites
specific source memories and is falsifiable on inspection.

See docs/DREAM_STATE.md for mechanism + tuning notes.
"""
from . import nocturnal  # noqa: F401
from . import filter as dream_filter  # noqa: F401
