"""recall_text — Server-side recall text builder.

Moved from ~/workshop/mcp/memvault/scripts/recall.py to eliminate the Python
subprocess fork on UserPromptSubmit hook. Same logic, now callable as a
function (and exposed via POST /api/memvault/recall/text).

Public API:
    build_recall_text(prompt, session_id, cwd) -> str
"""

from .builder import build_recall_text

__all__ = ["build_recall_text"]
