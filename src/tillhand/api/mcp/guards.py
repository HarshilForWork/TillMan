"""The HTTP guards in front of the two MCP endpoints (#41).

`MerchantDoorGuard` stands in front of `/merchant/mcp`. Every request, whatever its method, must carry a
valid `TillHand-Api-Key` before it reaches MCP at all. A missing, wrong or revoked key gets the same bytes:
HTTP 401 with JSON-RPC `-32000` "Unauthorized", as UCP's example has it (overview, "Error Codes"; for
streamable HTTP the status code is the primary signal). The request id is `null`, because the body is never
read for a request that can't be served. The key's id, never the key, is logged for every request.

`PublicDoorGuard` stands in front of `/mcp`: the Merchant door's headers are refused there, because
`TillHand-Customer` is accepted only together with a valid key.
"""

import logging
from collections.abc import Callable

from mcp_types import INVALID_REQUEST
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from tillhand.api.mcp.doors import MERCHANT_CALLER
from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import UCP_PROTOCOL_ERROR, ServiceUnavailable, Unauthorized
from tillhand.models.domain import MerchantDoorHeaders
from tillhand.models.ucp import JsonRpcError, JsonRpcErrorResponse, ProtocolErrorData
from tillhand.services.merchant_door import CustomerHeaderError, MerchantDoor

logger = logging.getLogger(__name__)

API_KEY_HEADER = "tillhand-api-key"
CUSTOMER_HEADER = "tillhand-customer"


async def _refuse(send: Send, status: int, error: JsonRpcError) -> None:
    body = JsonRpcErrorResponse(error=error).body()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


_UNAUTHORIZED = JsonRpcError(code=UCP_PROTOCOL_ERROR, message=Unauthorized.message)


class MerchantDoorGuard:
    def __init__(self, app: ASGIApp, door: Callable[[], MerchantDoor]) -> None:
        self._app = app
        self._door = door

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        try:
            caller = await self._door().authenticate(
                MerchantDoorHeaders(
                    api_key=headers.get(API_KEY_HEADER),
                    customer_ref=headers.get(CUSTOMER_HEADER),
                    has_customer_token="authorization" in headers,
                )
            )
        except Unauthorized:
            await _refuse(send, 401, _UNAUTHORIZED)
            return
        except CustomerHeaderError as exc:
            data = ProtocolErrorData(code="invalid_customer_header", content=str(exc))
            await _refuse(send, 400, JsonRpcError(code=INVALID_REQUEST, message="Invalid Request", data=data))
            return
        except (ServiceUnavailable, StepTimeout) as exc:
            # Fail closed, naming the step that was unavailable or overran (the deadline rule).
            logger.warning("Merchant door refused a request: %s is unavailable or too slow", exc.step)
            unavailable = ServiceUnavailable(exc.step)
            data = unavailable.data().model_copy(update={"content": f"{exc.step} is unavailable or too slow"})
            await _refuse(
                send, 503, JsonRpcError(code=UCP_PROTOCOL_ERROR, message=unavailable.message, data=data)
            )
            return
        logger.info("Merchant door request with key %s", caller.key_id)
        scope.setdefault("state", {})[MERCHANT_CALLER] = caller
        await self._app(scope, receive, send)


class PublicDoorGuard:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if API_KEY_HEADER in headers or CUSTOMER_HEADER in headers:
                data = ProtocolErrorData(
                    code="merchant_door_headers",
                    content="TillHand-Api-Key and TillHand-Customer belong on /merchant/mcp",
                )
                await _refuse(
                    send, 400, JsonRpcError(code=INVALID_REQUEST, message="Invalid Request", data=data)
                )
                return
        await self._app(scope, receive, send)
