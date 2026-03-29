"""
Tests unitarios para preprocessor.
Verifica anonimización PII y construcción del DIALOGUE_PACKET.
No requiere Pub/Sub, Weaviate ni Vertex AI.
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock


def load_preprocessor():
    """Carga preprocessor/main.py con dependencias externas mockeadas."""
    # Stubs mínimos para que el módulo importe sin errores
    for mod in ["google", "google.cloud", "google.cloud.pubsub_v1", "weaviate"]:
        sys.modules.setdefault(mod, MagicMock())

    os.environ.setdefault("VERTEX_AI_DISABLED", "true")

    module_path = os.path.join(os.path.dirname(__file__), "../../src/preprocessor/main.py")
    spec = importlib.util.spec_from_file_location("preprocessor_main", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_preprocessor = load_preprocessor()
anonymize_text = _preprocessor.anonymize_text
embed_text = _preprocessor.embed_text


# --- Tests de anonimización ---

def test_anonymize_email():
    result = anonymize_text("Contáctame en juan.perez@example.com para más info.")
    assert "[EMAIL]" in result
    assert "juan.perez@example.com" not in result


def test_anonymize_phone_es():
    result = anonymize_text("Mi teléfono es 612345678.")
    assert "[TELEFONO]" in result
    assert "612345678" not in result


def test_anonymize_dni():
    result = anonymize_text("Mi DNI es 12345678Z.")
    assert "[DNI]" in result
    assert "12345678Z" not in result


def test_anonymize_nie():
    result = anonymize_text("Mi NIE es X1234567A.")
    assert "[NIE]" in result
    assert "X1234567A" not in result


def test_anonymize_nombre_propio():
    result = anonymize_text("Soy María García y trabajo aquí.")
    assert "[NOMBRE]" in result
    assert "María García" not in result


def test_anonymize_preserves_non_pii():
    text = "El proyecto comenzó en enero y tiene tres fases."
    result = anonymize_text(text)
    assert result == text


def test_anonymize_multiple_pii_in_one_text():
    text = "Llama a Pedro López al 666777888 o escríbele a pedro@mail.es"
    result = anonymize_text(text)
    assert "Pedro López" not in result
    assert "666777888" not in result
    assert "pedro@mail.es" not in result


# --- Tests de embeddings ---

def test_embed_text_returns_768_dims():
    """Con VERTEX_AI_DISABLED=true (default en tests) debe retornar vector de 768 ceros."""
    vector = embed_text("Texto de prueba")
    assert len(vector) == 768
    assert all(v == 0.0 for v in vector)
