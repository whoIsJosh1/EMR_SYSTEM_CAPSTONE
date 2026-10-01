# ============================================================
# websocket.py
# Central WebSocket endpoint
# ============================================================

import json

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect
)

from websocket_manager import manager


router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    print("🔵 WebSocket request received")

    await manager.connect(websocket)

    print("🟢 WebSocket connection accepted")

    try:

        while True:

            raw_data = await websocket.receive_text()

            print(
                f"📨 WebSocket message received: "
                f"{raw_data}"
            )

            try:
                data = json.loads(raw_data)
            except Exception:
                data = {}

            # ------------------------------------------------
            # HEARTBEAT
            # ------------------------------------------------

            if data.get("type") == "ping":

                await manager.send_personal_message(
                    {
                        "type": "pong",
                        "message":
                            "WebSocket connection is working!"
                    },
                    websocket
                )

                print("📤 Pong sent to browser")

    except WebSocketDisconnect:

        print("🔴 WebSocket client disconnected")

        manager.disconnect(websocket)

    except Exception as e:

        print(f"❌ WebSocket error: {e}")

        manager.disconnect(websocket)