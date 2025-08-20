"""
Supabase utility helpers.

Note:
- This module centralizes integration points with Supabase (auth, database).
- For this initial implementation, we rely on httpx calls in main.py to the auth endpoint.
- Future iterations can move responsibility here, including PostgREST calls and RPC functions.

Environment variables required:
- SUPABASE_URL
- SUPABASE_KEY
"""

from typing import Optional, Dict
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


# PUBLIC_INTERFACE
def get_supabase_config() -> Dict[str, Optional[str]]:
    """Return Supabase configuration values from environment."""
    return {"url": SUPABASE_URL, "key": SUPABASE_KEY}
