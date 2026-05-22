#!/usr/bin/env python3
"""
LUCID → Home Assistant MQTT bridge.

Subscribes to all lucid/agents/# topics as an observer and:
  1. Publishes HA MQTT discovery configs for each discovered agent/component entity.
  2. Relays live LUCID state to HA state topics.
  3. Intercepts HA button publishes, injects a request_id UUID, and forwards
     the enriched payload to the correct LUCID command topic.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import threading
import uuid
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ha-bridge")

LUCID_MQTT_HOST = os.environ.get("LUCID_MQTT_HOST", "emqx")
LUCID_MQTT_PORT = int(os.environ.get("LUCID_MQTT_PORT", "1883"))
LUCID_MQTT_USERNAME = os.environ.get("LUCID_MQTT_USERNAME", "ha-bridge")
LUCID_MQTT_PASSWORD = os.environ.get("LUCID_MQTT_PASSWORD", "")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "9000"))

# Standard agent-level commands that get button entities in HA
AGENT_COMMANDS = ["ping", "restart", "refresh"]

# Unit map for common LUCID metric names
UNIT_MAP: dict[str, str] = {
    "cpu_percent": "%",
    "memory_percent": "%",
    "disk_percent": "%",
    "load": "",
    "pixel_rgb": "",
}

_lock = threading.Lock()
_published_discoveries: set[str] = set()
_agent_metadata: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# HA discovery helpers
# ---------------------------------------------------------------------------

def _device(agent_id: str) -> dict:
    meta = _agent_metadata.get(agent_id, {})
    d: dict[str, Any] = {
        "identifiers": [f"lucid_{agent_id}"],
        "name": f"LUCID Agent: {agent_id}",
        "model": "LUCID Agent",
        "manufacturer": "LUCID",
    }
    if meta.get("version"):
        d["sw_version"] = meta["version"]
    if meta.get("hostname"):
        d["configuration_url"] = f"http://{meta['hostname']}:5000"
    return d


def _publish_discovery(client: mqtt.Client, uid: str, domain: str, config: dict) -> None:
    with _lock:
        if uid in _published_discoveries:
            return
        _published_discoveries.add(uid)
    topic = f"homeassistant/{domain}/{uid}/config"
    payload = json.dumps(config)
    client.publish(topic, payload, qos=1, retain=True)
    log.debug("discovery published uid=%s domain=%s", uid, domain)


def _ensure_agent_entities(client: mqtt.Client, agent_id: str) -> None:
    """Publish discovery configs for an agent's core entities."""
    # Binary sensor: online/offline
    uid = f"lucid_{agent_id}_online"
    _publish_discovery(client, uid, "binary_sensor", {
        "name": f"{agent_id} Online",
        "unique_id": uid,
        "device_class": "connectivity",
        "state_topic": f"homeassistant/lucid/{agent_id}/status",
        "payload_on": "online",
        "payload_off": "offline",
        "device": _device(agent_id),
    })

    # Buttons for standard agent commands
    for action in AGENT_COMMANDS:
        uid = f"lucid_{agent_id}_btn_{action}"
        _publish_discovery(client, uid, "button", {
            "name": f"{agent_id} {action.title()}",
            "unique_id": uid,
            "command_topic": f"homeassistant/lucid/{agent_id}/cmd/{action}",
            "payload_press": "{}",
            "entity_category": "diagnostic",
            "device": _device(agent_id),
        })


def _ensure_telemetry_entity(client: mqtt.Client, agent_id: str, metric: str) -> None:
    uid = f"lucid_{agent_id}_{metric}"
    _publish_discovery(client, uid, "sensor", {
        "name": f"{agent_id} {metric.replace('_', ' ').title()}",
        "unique_id": uid,
        "state_topic": f"homeassistant/lucid/{agent_id}/telemetry/{metric}",
        "unit_of_measurement": UNIT_MAP.get(metric, ""),
        "state_class": "measurement",
        "device": _device(agent_id),
    })


def _ensure_component_entities(
    client: mqtt.Client, agent_id: str, component_id: str
) -> None:
    safe_id = component_id.replace("-", "_").replace("/", "_")
    uid = f"lucid_{agent_id}_{safe_id}_online"
    _publish_discovery(client, uid, "binary_sensor", {
        "name": f"{agent_id}/{component_id} Online",
        "unique_id": uid,
        "device_class": "connectivity",
        "state_topic": f"homeassistant/lucid/{agent_id}/components/{component_id}/status",
        "payload_on": "online",
        "payload_off": "offline",
        "device": _device(agent_id),
    })


def _ensure_component_telemetry(
    client: mqtt.Client, agent_id: str, component_id: str, metric: str
) -> None:
    safe_id = component_id.replace("-", "_").replace("/", "_")
    uid = f"lucid_{agent_id}_{safe_id}_{metric}"
    _publish_discovery(client, uid, "sensor", {
        "name": f"{agent_id}/{component_id} {metric.replace('_', ' ').title()}",
        "unique_id": uid,
        "state_topic": f"homeassistant/lucid/{agent_id}/components/{component_id}/telemetry/{metric}",
        "unit_of_measurement": UNIT_MAP.get(metric, ""),
        "state_class": "measurement",
        "device": _device(agent_id),
    })


# ---------------------------------------------------------------------------
# MQTT message routing
# ---------------------------------------------------------------------------

def _route(client: mqtt.Client, topic: str, payload_raw: bytes) -> None:
    parts = topic.split("/")
    # Minimum: lucid/agents/{agent_id}/...
    if len(parts) < 4:
        return

    # ── HA command interception ──────────────────────────────────────────
    # homeassistant/lucid/{agent_id}/cmd/{action}
    if parts[0] == "homeassistant" and parts[1] == "lucid":
        _handle_ha_command(client, parts, payload_raw)
        return

    # ── LUCID topic routing ──────────────────────────────────────────────
    if parts[0] != "lucid" or parts[1] != "agents":
        return

    agent_id = parts[2]
    remainder = parts[3:]  # e.g. ["status"] or ["components", "led_strip", "status"]

    if not remainder:
        return

    # Agent-level topics
    if remainder[0] == "status":
        _handle_agent_status(client, agent_id, payload_raw)
    elif remainder[0] == "metadata":
        _handle_agent_metadata(client, agent_id, payload_raw)
    elif remainder[0] == "telemetry" and len(remainder) >= 2:
        _handle_agent_telemetry(client, agent_id, remainder[1], payload_raw)
    # Component-level topics: components/{cid}/...
    elif remainder[0] == "components" and len(remainder) >= 3:
        component_id = remainder[1]
        comp_remainder = remainder[2:]
        if comp_remainder[0] == "status":
            _handle_component_status(client, agent_id, component_id, payload_raw)
        elif comp_remainder[0] == "telemetry" and len(comp_remainder) >= 2:
            _handle_component_telemetry(
                client, agent_id, component_id, comp_remainder[1], payload_raw
            )


def _handle_agent_status(
    client: mqtt.Client, agent_id: str, payload_raw: bytes
) -> None:
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        log.warning("agent_status bad json agent=%s", agent_id)
        return
    state = data.get("state", "offline")
    _ensure_agent_entities(client, agent_id)
    client.publish(
        f"homeassistant/lucid/{agent_id}/status",
        "online" if state == "online" else "offline",
        qos=0,
        retain=True,
    )
    log.info("agent_status agent=%s state=%s", agent_id, state)


def _handle_agent_metadata(
    client: mqtt.Client, agent_id: str, payload_raw: bytes
) -> None:
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    with _lock:
        _agent_metadata[agent_id] = data
    # Re-publish discovery with updated device info
    with _lock:
        # Remove cached discoveries so they get re-published with new metadata
        stale = {k for k in _published_discoveries if k.startswith(f"lucid_{agent_id}_")}
        _published_discoveries.difference_update(stale)
    _ensure_agent_entities(client, agent_id)
    log.debug("agent_metadata updated agent=%s version=%s", agent_id, data.get("version"))


def _handle_agent_telemetry(
    client: mqtt.Client, agent_id: str, metric: str, payload_raw: bytes
) -> None:
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    value = data.get("value")
    if value is None:
        return
    _ensure_telemetry_entity(client, agent_id, metric)
    client.publish(
        f"homeassistant/lucid/{agent_id}/telemetry/{metric}",
        str(value),
        qos=0,
    )


def _handle_component_status(
    client: mqtt.Client, agent_id: str, component_id: str, payload_raw: bytes
) -> None:
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    state = data.get("state", "offline")
    _ensure_component_entities(client, agent_id, component_id)
    client.publish(
        f"homeassistant/lucid/{agent_id}/components/{component_id}/status",
        "online" if state in ("enabled", "online") else "offline",
        qos=0,
        retain=True,
    )
    log.info("component_status agent=%s component=%s state=%s", agent_id, component_id, state)


def _handle_component_telemetry(
    client: mqtt.Client,
    agent_id: str,
    component_id: str,
    metric: str,
    payload_raw: bytes,
) -> None:
    try:
        data = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    value = data.get("value")
    if value is None:
        return
    _ensure_component_telemetry(client, agent_id, component_id, metric)
    client.publish(
        f"homeassistant/lucid/{agent_id}/components/{component_id}/telemetry/{metric}",
        str(value) if not isinstance(value, (dict, list)) else json.dumps(value),
        qos=0,
    )


def _handle_ha_command(
    client: mqtt.Client, parts: list[str], payload_raw: bytes
) -> None:
    """
    HA publishes to: homeassistant/lucid/{agent_id}/cmd/{action}
    or:              homeassistant/lucid/{agent_id}/components/{cid}/cmd/{action}
    Bridge injects request_id and forwards to the real LUCID cmd topic.
    """
    # parts[0]=homeassistant parts[1]=lucid parts[2]=agent_id parts[3]=cmd/components
    if len(parts) < 5:
        return

    agent_id = parts[2]

    if parts[3] == "cmd":
        action = parts[4]
        lucid_topic = f"lucid/agents/{agent_id}/cmd/{action}"
    elif parts[3] == "components" and len(parts) >= 7 and parts[5] == "cmd":
        component_id = parts[4]
        action = parts[6]
        lucid_topic = f"lucid/agents/{agent_id}/components/{component_id}/cmd/{action}"
    else:
        log.warning("ha_command unrecognised topic parts=%s", parts)
        return

    try:
        body: dict = json.loads(payload_raw) if payload_raw.strip() else {}
    except json.JSONDecodeError:
        body = {}

    request_id = str(uuid.uuid4())
    enriched = {**body, "request_id": request_id}
    client.publish(lucid_topic, json.dumps(enriched), qos=1)
    log.info(
        "ha_command forwarded agent=%s action=%s request_id=%s topic=%s",
        agent_id,
        action,
        request_id,
        lucid_topic,
    )


# ---------------------------------------------------------------------------
# MQTT client setup
# ---------------------------------------------------------------------------

def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
    if reason_code != 0:
        log.error("mqtt connect failed reason_code=%s", reason_code)
        return
    log.info("mqtt connected host=%s port=%d", LUCID_MQTT_HOST, LUCID_MQTT_PORT)

    subscriptions = [
        # LUCID agent state
        ("lucid/agents/+/status", 1),
        ("lucid/agents/+/metadata", 1),
        ("lucid/agents/+/telemetry/#", 0),
        ("lucid/agents/+/state", 1),
        # LUCID component state
        ("lucid/agents/+/components/+/status", 1),
        ("lucid/agents/+/components/+/metadata", 1),
        ("lucid/agents/+/components/+/telemetry/#", 0),
        # HA command interception (agent-level buttons)
        ("homeassistant/lucid/+/cmd/+", 1),
        # HA command interception (component-level buttons)
        ("homeassistant/lucid/+/components/+/cmd/+", 1),
    ]
    client.subscribe(subscriptions)
    log.info("subscribed to %d topic patterns", len(subscriptions))


def _on_disconnect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
    if reason_code != 0:
        log.warning("mqtt disconnected unexpectedly reason_code=%s — will reconnect", reason_code)


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    try:
        _route(client, msg.topic, msg.payload)
    except Exception:
        log.exception("message routing error topic=%s", msg.topic)


# ---------------------------------------------------------------------------
# Health check HTTP server
# ---------------------------------------------------------------------------

_healthy = threading.Event()


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            if _healthy.is_set():
                body = b'{"status":"ok"}'
                self.send_response(200)
            else:
                body = b'{"status":"starting"}'
                self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
        pass  # suppress access log noise


def _start_health_server() -> None:
    server = http.server.HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info("health server listening on :%d", HEALTH_PORT)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _start_health_server()

    client_id = f"lucid-ha-bridge-{uuid.uuid4().hex[:8]}"
    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )
    client.username_pw_set(LUCID_MQTT_USERNAME, LUCID_MQTT_PASSWORD)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message

    log.info("connecting client_id=%s host=%s port=%d", client_id, LUCID_MQTT_HOST, LUCID_MQTT_PORT)
    client.connect(LUCID_MQTT_HOST, LUCID_MQTT_PORT, keepalive=60)
    _healthy.set()
    client.loop_forever()


if __name__ == "__main__":
    main()
