# ============================================================
# websocket_manager.py
# Central real-time event manager for the EMR system
# ============================================================

from fastapi import WebSocket
from typing import List
import json


class ConnectionManager:

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()

        if websocket not in self.active_connections:
            self.active_connections.append(websocket)

        print(
            f"🔌 WebSocket connected. "
            f"Active connections: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):

        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

        print(
            f"🔌 WebSocket disconnected. "
            f"Active connections: {len(self.active_connections)}"
        )

    async def send_personal_message(
        self,
        message: dict,
        websocket: WebSocket
    ):
        await websocket.send_text(json.dumps(message))

    async def broadcast(self, message: dict):

        disconnected = []

        for connection in list(self.active_connections):

            try:
                await connection.send_text(
                    json.dumps(message)
                )

            except Exception:
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection)
            

    async def broadcast_event(
        self,
        event_type: str,
        entity: str,
        action: str,
        **data
    ):
        """
        Central event format.

        Example:
        {
            "type": "medical_record_updated",
            "entity": "medical_record",
            "action": "updated",
            "record_id": 10,
            "patient_id": 5
        }
        """

        message = {
            "type": event_type,
            "entity": entity,
            "action": action,
            **data
        }

        print(
            f"📡 Broadcasting: {event_type} "
            f"entity={entity} action={action}"
        )

        await self.broadcast(message)


manager = ConnectionManager()