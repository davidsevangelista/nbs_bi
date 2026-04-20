"""nbs_bi.clients — per-user revenue, LTV, cohort, and acquisition analytics."""

from nbs_bi.clients.models import ClientModel
from nbs_bi.clients.report import ClientReport

__all__ = ["ClientModel", "ClientReport"]
