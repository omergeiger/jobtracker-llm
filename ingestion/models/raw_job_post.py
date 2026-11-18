from datetime import datetime
from pydantic import BaseModel


class RawJobPost(BaseModel):
    """
    Contract between the sourcing module and ingestion.
    Sourcing produces these; ingestion consumes them.
    All fields reflect raw source values — no normalisation applied here.
    """
    source_id: str          # logical source name, e.g. "greenhouse"
    external_id: str        # unique ID within that source (e.g. Greenhouse job integer ID)
    title: str
    company_name: str
    location_raw: str       # exactly as the source provides it — may be noisy
    apply_url: str
    raw_description: str    # HTML or plain text job body
    posting_date: datetime
    metadata: dict = {}     # source-specific extras (departments, offices, etc.)
