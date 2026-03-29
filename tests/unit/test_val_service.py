"""
Tests unitarios para val-service.
Verifica que el nodo VAL construye el system prompt correctamente
y que las directivas de expertos se inyectan en el prompt.
No requiere LLM real ni Pub/Sub.
"""
import sys
import os
from unittest.mock import MagicMock, patch

# Stubs de dependencias externas
sys.modules["google.cloud.pubsub_v1"] = MagicMock()
sys.modules["google.cloud"] = MagicMock()
sys.modules["psycopg"] = MagicMock()
sys.modules["langgraph.checkpoint.postgres"] = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/val-service"))


def test_val_node_includes_directives_in_prompt():
    """Cuando hay expert_directives en el estado, deben aparecer en el SystemMessage enviado al LLM."""
    from langchain_core.messages import HumanMessage, AIMessage

    captured_messages = []

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = lambda msgs: (
        captured_messages.extend(msgs) or AIMessage(content="Respuesta de prueba.")
    )

    with patch("graph.llm", mock_llm):
        from graph import val_node

        state = {
            "messages": [HumanMessage(content="¿Cuál es el mayor reto de tu comunidad?")],
            "expert_directives": ["Indaga más sobre el acceso al agua potable"],
        }

        result = val_node(state)

    assert result is not None
    system_msgs = [m for m in captured_messages if hasattr(m, "content") and "DIRECTIVAS" in m.content]
    assert len(system_msgs) >= 1
    assert "acceso al agua potable" in system_msgs[0].content


def test_val_node_clears_directives_after_use():
    """Las expert_directives deben vaciarse en el estado de retorno tras ser aplicadas."""
    from langchain_core.messages import HumanMessage, AIMessage

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Respuesta.")

    with patch("graph.llm", mock_llm):
        from graph import val_node

        state = {
            "messages": [HumanMessage(content="Hola")],
            "expert_directives": ["Una directiva pendiente"],
        }

        result = val_node(state)

    assert result["expert_directives"] == []


def test_val_node_works_without_directives():
    """Sin directivas en el estado, VAL responde normalmente sin error."""
    from langchain_core.messages import HumanMessage, AIMessage

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Hola, soy VAL.")

    with patch("graph.llm", mock_llm):
        from graph import val_node

        state = {
            "messages": [HumanMessage(content="¿Quién eres?")],
            "expert_directives": [],
        }

        result = val_node(state)

    assert "messages" in result
    assert result["messages"][-1].content == "Hola, soy VAL."
