"""
Abstract base class for ATS job source adapters.

Each ATS (Greenhouse, Lever, Ashby, …) implements this interface.
The fetch() method takes a slug and company name and returns a list of
RawJobPost objects ready for IngestService.
"""

from abc import ABC, abstractmethod

from ingestion.models import RawJobPost


class JobSource(ABC):
    """One ATS platform. Subclasses map platform-specific API responses to RawJobPost."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Logical source name written into RawJobPost.source_id (e.g. 'greenhouse')."""

    @abstractmethod
    def fetch(self, slug: str, company_name: str, location: str = "") -> list[RawJobPost]:
        """
        Fetch all current job postings for one company slug (or search query).

        `location` is used by search-based sources (e.g. JobSpy); ATS adapters
        ignore it. Returns an empty list on 404 / no open roles. Raises on
        network errors or unexpected HTTP status.
        """
