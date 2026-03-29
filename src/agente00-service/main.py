import os
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
import psycopg2.extras
import logging

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://digikawsay_app:local_password_123@postgres:5432/digikawsay"
)

app = FastAPI(title="AGENTE-00 Supervisor", version="1.1.0 (MVP)")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DirectivePayload(BaseModel):
    participant_id: str
    project_id: str
    cycle_id: int
    content: str
    urgency: str = "MEDIUM"


@app.post("/admin/inject_directive")
def inject_directive(payload: DirectivePayload):
    """
    Endpoint de 'Shadowing'. Un analista humano inyecta una directiva que
    VAL incorporará sutilmente en su próxima respuesta al participante.

    Escribe en la tabla `digikawsay.pending_directives`, que val-service
    lee y aplica atómicamente antes de cada invocación al LLM.
    """
    logger.info(f"Inyectando directiva para participante: {payload.participant_id}")

    directive_id = str(uuid.uuid4())

    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO digikawsay.pending_directives
                        (id, participant_id, content, urgency, issued_by)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        directive_id,
                        payload.participant_id,
                        payload.content,
                        payload.urgency,
                        "human_investigator",
                    ),
                )
            conn.commit()

        logger.info(f"Directiva {directive_id} persistida en BD para {payload.participant_id}")
        return {"status": "success", "directive_id": directive_id}

    except Exception as e:
        logger.error(f"Error inyectando directiva: {e}")
        raise HTTPException(status_code=500, detail="Database write failed")


@app.post("/system/pubsub/val_report")
def handle_val_report(request: dict):
    """
    Webhook para recibir notificaciones de turno completado desde val-service
    vía Pub/Sub push (típico en Cloud Run). Actualiza métricas del ciclo.
    """
    try:
        participant_id = request.get("participant_id")
        turn_count = request.get("turn_count", 0)
        logger.info(f"Turno completado — participante: {participant_id}, turno: {turn_count}")

        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE digikawsay.dialogue_states
                    SET turn_count = %s, last_turn_at = NOW()
                    WHERE participant_id = %s AND status = 'active'
                    """,
                    (turn_count, participant_id),
                )
            conn.commit()

        return {"status": "acknowledged"}

    except Exception as e:
        logger.error(f"Error procesando reporte de VAL: {e}")
        raise HTTPException(status_code=400, detail="Bad reporting format")


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "agente00-service", "mode": "Wizard-Of-Oz"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
