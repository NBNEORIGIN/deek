"""Research-loop pipelines for Deek.

Currently:
  * arxiv_loop — poll arXiv on Deek-relevant queries, score
    applicability via local Qwen, surface top candidates into the
    memory brief for Toby's verdict.
"""
