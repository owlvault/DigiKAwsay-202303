import os
import re
import json
import logging
import time
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1
import weaviate

# Configuración
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "my-gcp-project")
SUBSCRIPTION_NAME = os.getenv("PUBSUB_INBOUND_SUBSCRIPTION", "preprocessor-inbound-sub")
OUTBOUND_TOPIC = os.getenv("PUBSUB_PACKET_TOPIC", "iap.val.packet")
WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
VERTEX_AI_DISABLED = os.getenv("VERTEX_AI_DISABLED", "true").lower() == "true"
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "my-gcp-project")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_NAME)

publisher = pubsub_v1.PublisherClient()
outbound_topic_path = publisher.topic_path(PROJECT_ID, OUTBOUND_TOPIC)

# Cliente Weaviate (reconecta si falla al inicio)
_weaviate_client = None

def get_weaviate_client():
    global _weaviate_client
    if _weaviate_client is None:
        try:
            host = WEAVIATE_URL.replace("http://", "").replace("https://", "").split(":")[0]
            port = int(WEAVIATE_URL.split(":")[-1]) if ":" in WEAVIATE_URL else 8080
            _weaviate_client = weaviate.connect_to_local(host=host, port=port)
        except Exception as e:
            logger.warning(f"No se pudo conectar a Weaviate: {e}")
    return _weaviate_client


# --- Patrones de PII para español ---
_PII_PATTERNS = [
    (re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.]+\b'), '[EMAIL]'),
    (re.compile(r'\b(?:\+?34[-\s]?)?[6-9]\d{2}[-\s]?\d{3}[-\s]?\d{3}\b'), '[TELEFONO]'),
    (re.compile(r'\b\d{8}[A-HJ-NP-TV-Z]\b'), '[DNI]'),
    (re.compile(r'\b[XYZ]\d{7}[A-HJ-NP-TV-Z]\b'), '[NIE]'),
    # Nombres propios: dos palabras capitalizadas consecutivas
    (re.compile(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\s[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b'), '[NOMBRE]'),
]


def anonymize_text(text: str) -> str:
    """
    Anonimización basada en regex para PII común en español:
    emails, teléfonos ES, DNI, NIE y nombres propios de dos palabras.
    Para producción se recomienda usar presidio-analyzer con modelo NER en español.
    """
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def embed_text(text: str) -> list[float]:
    """
    Genera embeddings del texto.
    - En local (VERTEX_AI_DISABLED=true): retorna vector cero para desarrollo.
    - En producción: usa Vertex AI text-embedding-004 (768 dimensiones).
    """
    if VERTEX_AI_DISABLED:
        return [0.0] * 768

    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingModel
        vertexai.init(project=GCP_PROJECT_ID, location=VERTEX_LOCATION)
        model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        embeddings = model.get_embeddings([text])
        return list(embeddings[0].values)
    except Exception as e:
        logger.error(f"Error generando embeddings con Vertex AI: {e}")
        return [0.0] * 768


def store_in_weaviate(chunk_id: str, participant_id: str, project_id: str, text: str, timestamp: str):
    """
    Almacena el fragmento anonimizado en Weaviate clase RawFragment.
    Si Weaviate no está disponible, registra el error y continúa sin bloquear el flujo.
    """
    client = get_weaviate_client()
    if client is None:
        logger.warning(f"Weaviate no disponible — fragmento {chunk_id} no almacenado")
        return

    try:
        collection = client.collections.get("RawFragment")
        collection.data.insert(
            properties={
                "participant_id": participant_id,
                "project_id": project_id,
                "cycle_id": "default",
                "session_id": "session_mvp_1",
                "text": text,
                "modality": "text",
                "timestamp": timestamp,
                "turn_id": chunk_id,
            }
        )
        logger.info(f"Fragmento {chunk_id} almacenado en Weaviate")
    except Exception as e:
        logger.error(f"Error almacenando en Weaviate: {e}")


def process_message(message: pubsub_v1.subscriber.message.Message):
    """
    Pipeline completo: anonimización → embedding → almacenamiento vectorial → DIALOGUE_PACKET
    """
    try:
        payload = json.loads(message.data.decode("utf-8"))
        participant_id = payload.get("participant_id", "unknown")
        message_id = payload.get("message_id", "")
        original_text = payload.get("text", "")
        timestamp = payload.get("timestamp", "")

        logger.info(f"Procesando mensaje de: {participant_id}")

        # 1. Anonimización PII
        clean_text = anonymize_text(original_text)

        # 2. Embeddings
        vector = embed_text(clean_text)

        # 3. Almacenar fragmento anonimizado en Weaviate
        chunk_id = f"chunk_{message_id}"
        store_in_weaviate(
            chunk_id=chunk_id,
            participant_id=participant_id,
            project_id=payload.get("project_id", "default"),
            text=clean_text,
            timestamp=timestamp,
        )

        # 4. Construir DIALOGUE_PACKET
        dialogue_packet = {
            "participant_id": participant_id,
            "session_id": "session_mvp_1",
            "message_id": message_id,
            "original_text": original_text,
            "clean_text": clean_text,
            "chunk_id": chunk_id,
            "timestamp": timestamp,
        }

        # 5. Publicar a iap.val.packet
        future = publisher.publish(outbound_topic_path, json.dumps(dialogue_packet).encode("utf-8"))
        future.result()

        message.ack()
        logger.info(f"Mensaje procesado y ACKed: {message.message_id}")

    except Exception as e:
        logger.error(f"Error processing message {message.message_id}: {e}")
        message.nack()


def main():
    logger.info(f"Preprocessor escuchando en suscripción: {subscription_path}")
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=process_message)

    with subscriber:
        try:
            streaming_pull_future.result()
        except TimeoutError:
            streaming_pull_future.cancel()
            streaming_pull_future.result()
        except Exception as e:
            logger.error(f"Subscriber failed: {e}")
            streaming_pull_future.cancel()


if __name__ == "__main__":
    main()
