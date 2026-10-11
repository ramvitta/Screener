from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import _client, ensure_authenticated
from app.core.security import rate_limit
from app.integrations.definedge_client import DefinedgeAuthError

router = APIRouter(prefix="/api/v1/account", tags=["account"])


# Only the margin figures the UI needs. The raw /limits payload can carry
# account identifiers, so it is never passed through.
_MARGIN_FIELDS = ("cash", "payin", "payout", "marginused", "collateral", "utilizedmargin")


@router.get("/margin", dependencies=[Depends(rate_limit(30, 60))])
async def get_margin():
    """Allowlisted subset of Definedge's GET /limits (see SRS_Architecture.md §2.2)."""
    try:
        await ensure_authenticated()
        limits = await _client.get_limits()
    except DefinedgeAuthError:
        raise HTTPException(status_code=503, detail="Broker session unavailable") from None
    return {k: limits[k] for k in _MARGIN_FIELDS if k in limits}
