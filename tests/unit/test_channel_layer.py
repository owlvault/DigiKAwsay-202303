"""
Tests unitarios para channel-layer.
Verifica normalización de mensajes Telegram sin dependencias externas (Pub/Sub mockeado).
"""
import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient, ASGITransport


def load_channel_layer_app():
    """Carga channel-layer/main.py con Pub/Sub mockeado."""
    pubsub_mock = MagicMock()
    pubsub_mock.PublisherClient.return_value.topic_path.return_value = "projects/test/topics/test"
    pubsub_mock.PublisherClient.return_value.publish.return_value.result.return_value = None

    google_mock = MagicMock()
    google_cloud_mock = MagicMock()
    google_cloud_mock.pubsub_v1 = pubsub_mock

    sys.modules.setdefault("google", google_mock)
    sys.modules["google.cloud"] = google_cloud_mock
    sys.modules["google.cloud.pubsub_v1"] = pubsub_mock

    module_path = os.path.join(os.path.dirname(__file__), "../../src/channel-layer/main.py")
    spec = importlib.util.spec_from_file_location("channel_layer_main", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, pubsub_mock


_channel_module, _pubsub_mock = load_channel_layer_app()
app = _channel_module.app


TELEGRAM_TEXT_UPDATE = {
    "update_id": 123456,
    "message": {
        "message_id": 42,
        "from": {"id": 987654321, "first_name": "Test"},
        "chat": {"id": 987654321, "type": "private"},
        "date": 1700000000,
        "text": "Hola, ¿cómo estás?",
    },
}


@pytest.mark.asyncio
async def test_webhook_text_message_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhook", json=TELEGRAM_TEXT_UPDATE)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_non_text_message_is_ignored():
    update_without_text = {"update_id": 1, "message": {"message_id": 1, "from": {"id": 1}}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhook", json=update_without_text)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_publishes_normalized_message():
    """El mensaje publicado debe contener participant_id, text, channel y modality."""
    published_payloads = []

    def capture_publish(topic, data, **kwargs):
        published_payloads.append(json.loads(data.decode()))
        future = MagicMock()
        future.result.return_value = None
        return future

    _pubsub_mock.PublisherClient.return_value.publish.side_effect = capture_publish

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/webhook", json=TELEGRAM_TEXT_UPDATE)

    assert len(published_payloads) >= 1
    msg = published_payloads[-1]
    assert msg["participant_id"] == "987654321"
    assert msg["text"] == "Hola, ¿cómo estás?"
    assert msg["channel"] == "telegram"
    assert msg["modality"] == "text"
    assert "timestamp" in msg


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
