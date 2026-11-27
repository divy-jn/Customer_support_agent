"""Authenticated, stateless Streamable HTTP MCP for staff operators."""

from fastapi import HTTPException
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.mcp import db_server
from app.dependencies import verify_operator_access

server = FastMCP("CustomerSupportDB", mask_error_details=True)
for tool in (db_server.get_dashboard_stats, db_server.create_ticket, db_server.update_ticket, db_server.cancel_order):
    # The HTTP transport authenticates staff independently of the stdio process.
    server.tool(tool.__wrapped__)
mcp_http = server.http_app(path="/mcp", stateless_http=True, json_response=True)


class StaffMCP:
    __name__ = "staff_mcp"  # SlowAPI identifies handlers by name.

    async def __call__(self, scope, receive, send):
        headers = Request(scope).headers
        try:
            verify_operator_access(headers.get("x-api-key"), headers.get("authorization"))
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            await response(scope, receive, send)
            return
        await mcp_http(scope, receive, send)
