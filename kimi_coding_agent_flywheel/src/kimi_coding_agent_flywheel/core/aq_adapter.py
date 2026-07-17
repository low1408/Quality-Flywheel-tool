"""Agent Quality database adapter compatibility facade."""

from .aq_analysis_store import AQAnalysisStoreMixin
from .aq_base import AQAdapterBase
from .aq_ingestion import AQIngestionMixin
from .aq_legacy import AQLegacyImportMixin
from .aq_projection import AQTraceProjectionMixin


class AQDbAdapter(
    AQLegacyImportMixin,
    AQAnalysisStoreMixin,
    AQTraceProjectionMixin,
    AQIngestionMixin,
    AQAdapterBase,
):
    """Bridge worker telemetry and analysis data to Agent Quality SQLite.

    The facade preserves the original public class while its capabilities are
    grouped into focused ingestion, projection, analysis, and legacy-import
    modules.
    """


__all__ = ["AQDbAdapter"]
