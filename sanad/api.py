from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel

app = FastAPI(title="Sanad Gateway API")

# Module-level gateway — set during initialization
_gateway = None
_api_key = "dev-key-change-me"

class ExecutionRequest(BaseModel):
    item: str
    amount_minor: int
    currency: str

def set_gateway(gw):
    global _gateway
    _gateway = gw

def verify_bearer(authorization: str = Header(default=None)):
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth scheme")
    token = authorization[7:]
    if token != _api_key:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

@app.post("/executions")
def execute(req: ExecutionRequest, token: str = Depends(verify_bearer)):
    if _gateway is None:
        raise HTTPException(status_code=503, detail="Gateway not initialized")
    
    # Delegates 100% to existing Gateway — zero new auth logic
    approval = _gateway.derive_approval(req.item, req.amount_minor, req.currency)
    if approval is None:
        return {"state": "DENIED", "reason": "PreAuthorization validation failed"}
    
    result = _gateway.execute(approval)
    return result
