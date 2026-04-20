"""Memory Brief — daily human-in-the-loop memory audit.

Deek emails 1-4 questions a morning, generated from live memory
state, to a nominated user. Replies (Phase B) parse back into memory
corrections, closing the loop between Deek's algorithmic beliefs and
Toby's ground-truth judgement.

See docs/MEMORY_BRIEF.md for the mechanism and scope discipline.
"""
from . import questions  # noqa: F401
from . import composer  # noqa: F401
