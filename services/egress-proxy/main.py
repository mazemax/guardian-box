"""
GuardianBox Egress Proxy

Enforces network allowlist - only api.telegram.org can be reached.
Counts bytes for the dashboard egress monitor.
"""

import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx


app = FastAPI(title="GuardianBox Egress Proxy")


ALLOWED_HOSTS = set(os.getenv("ALLOWED_HOSTS", "api.telegram.org").split(","))
SIMULATE_OFFLINE = os.getenv("SIMULATE_OFFLINE", "false").lower() == "true"


egress_stats = {
    "total_requests": 0,
    "total_bytes_out": 0,
    "total_bytes_in": 0,
    "blocked_requests": 0,
    "by_host": {},
}

egress_log: List[Dict] = []
http_client: Optional[httpx.AsyncClient] = None


class ProxyRequest(BaseModel):
    method: str = "POST"
    url: str
    headers: Dict[str, str] = {}
    body: Optional[str] = None


async def get_client() -> httpx.AsyncClient:
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
    return http_client


def is_host_allowed(url: str) -> tuple[bool, str]:
    """Check if the host in the URL is in the allowlist."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc.split(":")[0]
        return host in ALLOWED_HOSTS, host
    except Exception:
        return False, "unknown"


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "allowed_hosts": list(ALLOWED_HOSTS),
        "simulate_offline": SIMULATE_OFFLINE,
    }


@app.post("/proxy")
async def proxy_request(req: ProxyRequest):
    """
    Proxy a request if the destination is allowed.
    
    Only hosts in ALLOWED_HOSTS can be reached.
    """
    allowed, host = is_host_allowed(req.url)
    
    if not allowed:
        egress_stats["blocked_requests"] += 1
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": host,
            "allowed": False,
            "reason": "host_not_in_allowlist",
        })
        raise HTTPException(
            status_code=403,
            detail=f"Host {host} not in allowlist. Only {ALLOWED_HOSTS} allowed."
        )
    
    if SIMULATE_OFFLINE:
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": host,
            "allowed": False,
            "reason": "simulate_offline",
        })
        raise HTTPException(status_code=503, detail="Simulated offline mode")
    
    bytes_out = len(req.body or "")
    
    try:
        client = await get_client()
        
        if req.method.upper() == "GET":
            response = await client.get(req.url, headers=req.headers)
        else:
            response = await client.post(
                req.url,
                headers=req.headers,
                content=req.body,
            )
        
        bytes_in = len(response.content)
        
        egress_stats["total_requests"] += 1
        egress_stats["total_bytes_out"] += bytes_out
        egress_stats["total_bytes_in"] += bytes_in
        
        if host not in egress_stats["by_host"]:
            egress_stats["by_host"][host] = {"requests": 0, "bytes_out": 0, "bytes_in": 0}
        egress_stats["by_host"][host]["requests"] += 1
        egress_stats["by_host"][host]["bytes_out"] += bytes_out
        egress_stats["by_host"][host]["bytes_in"] += bytes_in
        
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": host,
            "method": req.method,
            "bytes_out": bytes_out,
            "bytes_in": bytes_in,
            "status_code": response.status_code,
            "allowed": True,
        })
        
        return {
            "status_code": response.status_code,
            "content": response.text,
            "headers": dict(response.headers),
        }
        
    except httpx.RequestError as e:
        egress_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "host": host,
            "allowed": True,
            "error": str(e),
        })
        raise HTTPException(status_code=502, detail=f"Request failed: {e}")


@app.get("/stats")
async def get_stats():
    """Get egress statistics."""
    return {
        "stats": egress_stats,
        "simulate_offline": SIMULATE_OFFLINE,
        "video_bytes_out": 0,
        "image_bytes_out": 0,
    }


@app.get("/log")
async def get_log(limit: int = 100):
    """Get recent egress log entries."""
    return {"log": egress_log[-limit:]}


@app.post("/simulate-offline")
async def set_simulate_offline(on: bool = True):
    """Toggle simulated offline mode."""
    global SIMULATE_OFFLINE
    SIMULATE_OFFLINE = on
    return {"simulate_offline": SIMULATE_OFFLINE}


@app.on_event("shutdown")
async def shutdown():
    global http_client
    if http_client:
        await http_client.aclose()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
