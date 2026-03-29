import os
import json
import logging
import psycopg
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver

from graph import construct_graph

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "my-gcp-project")
SUBSCRIPTION_NAME = os.getenv("PUBSUB_PACKET_INBOUND_SUB", "val-packet-sub")
OUTBOUND_TOPIC = os.getenv("PUBSUB_OUTBOUND_TOPIC", "iap.channel.outbound")
SUPERVISOR_TOPIC = os.getenv("PUBSUB_VAL_TO_AG00_TOPIC", "iap.val.to.ag00")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://digikawsay_app:local_password_123@postgres:5432/digikawsay"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_NAME)
publisher = pubsub_v1.PublisherClient()

# Inicializar checkpointer y grafo al arrancar el servicio
db_conn = psycopg.connect(DATABASE_URL)
checkpointer = PostgresSaver(db_conn)
checkpointer.setup()  # Crea las tablas del checkpointer si no existen
app = construct_graph(checkpointer=checkpointer)
logger.info("LangGraph compilado con PostgresSaver checkpointer")


def fetch_and_apply_directives(participant_id: str) -> list[str]:
    """
    Lee directivas pendientes para el participante desde Postgres y las marca como APPLIED.
    Retorna la lista de contenidos de directivas a inyectar en el estado.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE digikawsay.pending_directives
                SET status = 'APPLIED', applied_at = NOW()
                WHERE participant_id = %s AND status = 'PENDING'
                RETURNING content
                """,
                (participant_id,)
            )
            rows = cur.fetchall()
            conn.commit()
    return [row[0] for row in rows]


def process_dialogue_packet(message: pubsub_v1.subscriber.message.Message):
    try:
        packet = json.loads(message.data.decode("utf-8"))
        participant_id = packet.get("participant_id")
        user_text = packet.get("original_text")
        message_id = packet.get("message_id")

        logger.info(f"VAL - Procesando request de participante {participant_id}")

        # 1. Leer directivas pendientes de expertos y marcarlas como aplicadas
        active_directives = fetch_and_apply_directives(participant_id)
        if active_directives:
            logger.info(f"VAL - {len(active_directives)} directiva(s) activa(s) para {participant_id}")

        # 2. Configurar hilo persistente (thread_id) para el checkpointer
        config = {"configurable": {"thread_id": participant_id}}

        # 3. Input para LangGraph: mensaje del participante + directivas activas
        input_data = {
            "messages": [HumanMessage(content=user_text)],
            "expert_directives": active_directives,
        }

        # 4. Ejecutar el grafo — rehidrata estado desde Postgres, invoca Gemini, guarda checkpoint
        output = app.invoke(input_data, config)

        # 5. Extraer respuesta de VAL
        val_response_text = output["messages"][-1].content
        logger.info(f"VAL - Respuesta generada: {val_response_text[:80]}...")

        # 6. Publicar respuesta a Channel Outbound (para Telegram)
        outbound_msg = {
            "participant_id": participant_id,
            "text": val_response_text,
            "in_reply_to": message_id,
        }
        publisher.publish(
            publisher.topic_path(PROJECT_ID, OUTBOUND_TOPIC),
            json.dumps(outbound_msg).encode("utf-8"),
        )

        # 7. Notificar al supervisor (AG-00) del turno completado
        ag00_report = {
            "event": "TURN_COMPLETED",
            "participant_id": participant_id,
            "turn_count": len(output["messages"]) // 2,
        }
        publisher.publish(
            publisher.topic_path(PROJECT_ID, SUPERVISOR_TOPIC),
            json.dumps(ag00_report).encode("utf-8"),
        )

        message.ack()

    except Exception as e:
        logger.error(f"Error procesando packet en VAL: {e}")
        message.nack()


def main():
    logger.info(f"VAL Agent escuchando en sub: {subscription_path}")
    streaming_pull_future = subscriber.subscribe(
        subscription_path, callback=process_dialogue_packet
    )

    with subscriber:
        try:
            streaming_pull_future.result()
        except TimeoutError:
            streaming_pull_future.cancel()
            streaming_pull_future.result()
        except Exception as e:
            logger.error(f"VAL Subscriber failed: {e}")
            streaming_pull_future.cancel()


if __name__ == "__main__":
    main()
