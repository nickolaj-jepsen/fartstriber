"""iRacing's CPS (client-proxy service): the Socket.IO channel the web app uses for its real-time RPCs, the
replay-credentials request among them. Connecting needs only the owner's session cookie; the client version
and id are static values read from the web app, not per-user secrets.

The per-launch credentials it returns are the owner's own and are never logged.
"""

import contextlib
import threading
import uuid
from typing import Any

import socketio
from socketio import exceptions as sio_exc

from livery import browser
from livery.errors import LiveryError

BASE = "https://members-ng.iracing.com"
CLIENT_NS = "/client.io"  # the web app's clientSocket namespace; the authSocket is "/"
# Static handshake values from the web bundle: kf("members") for the base, the clientVersion and clientId
# constants; CPSVER is empty there, so it is omitted.
CLIENT_VERSION = "11.25.0"
IR_CLIENT_ID = "iracing_ui"


class CpsError(LiveryError):
    """The client-proxy service couldn't be reached, or answered with an error."""


def request_args(
    *,
    session_id: int | None = None,
    track_id: int | None = None,
    car_id: int | None = None,
    sess_carids: list[int] | None = None,
) -> dict[str, Any]:
    """A replay is identified by its subsession `session_id` (online), or `track_id` and `car_id` (offline),
    as the web app sends them. An offline replay with more than one car also sends `sess_carids`, the
    participant ids, so the sim can load every car."""
    args: dict[str, Any] = {"session_id": session_id, "track_id": track_id, "car_id": car_id}
    chosen = {k: v for k, v in args.items() if v is not None}
    if sess_carids:
        chosen["sess_carids"] = sess_carids
    if not chosen:
        raise CpsError("a replay needs a session_id, or a track_id and car_id")
    return chosen


def parse_ack(ack: Any) -> dict:
    """A data_services ack is `[error, {type, data}]`. Return the inner data, or raise on an error."""
    err, resp = (ack[0], ack[1]) if isinstance(ack, list | tuple) and len(ack) == 2 else (None, ack)
    if err:
        raise CpsError(str(err))
    if not isinstance(resp, dict):
        raise CpsError(f"unexpected response: {str(resp)[:200]}")
    return resp.get("data", resp)


def data_service(cookies: list[dict], service: str, method: str, args: dict, timeout: float = 25.0) -> dict:
    """One data_services RPC over CPS, as the signed-in owner, over a websocket authenticated by the session
    cookie. Returns the response's data."""
    sio = socketio.Client(reconnection=False)
    ready = threading.Event()
    # the channel fires "initialized" on the client namespace once it is authenticated
    sio.on("initialized", lambda *_a: ready.set(), namespace=CLIENT_NS)

    try:
        try:
            sio.connect(
                BASE,
                headers={"Cookie": browser.cookie_header(cookies), "Origin": BASE},
                auth={"clientVersion": CLIENT_VERSION, "irClientId": IR_CLIENT_ID},
                transports=["websocket"],
                namespaces=["/", CLIENT_NS],
                wait_timeout=timeout,
            )
        except sio_exc.ConnectionError as e:
            raise CpsError(f"couldn't connect to the client-proxy service: {e}") from None
        if not ready.wait(timeout):
            raise CpsError(
                "the session didn't authenticate in time — it may have expired; run `livery sim login`"
            )
        payload = {"refid": str(uuid.uuid4()), "service": service, "method": method, "args": args}
        try:
            ack = sio.call("data_services", payload, namespace=CLIENT_NS, timeout=timeout)
        except sio_exc.TimeoutError:
            raise CpsError(f"no response from {service}/{method} within {timeout:.0f}s") from None
        return parse_ack(ack)
    finally:
        with contextlib.suppress(Exception):  # best-effort close of a possibly-half-open socket
            sio.disconnect()


def replay_credentials(cookies: list[dict], args: dict, timeout: float = 25.0) -> dict:
    """Request the credentials that let the sim open a replay, from `request_args`. Returns the response
    data: `success` and the keysrv/credentials/customerid/sess_cred/sess_cred_srv fields the sim needs."""
    return data_service(cookies, "registration", "process_replay_request", args, timeout)
