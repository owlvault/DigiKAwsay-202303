"""
Tests unitarios para val-service.
Verifica que el nodo VAL construye el system prompt correctamente
y que las directivas de expertos se inyectan en el prompt.
No requiere LLM real ni Pub/Sub.
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch


def load_val_graph():
    """Carga val-service/graph.py con LLM mockeado."""
    for mod in [
        "google", "google.cloud", "google.cloud.pubsub_v1",
        "psycopg", "langgraph.checkpoint.postgres",
        "langchain_google_genai",
    ]:
        sys.modules.setdefault(mod, MagicMock())

    # Cargar state.py primero (dependencia de graph.py)
    service_dir = os.path.join(os.path.dirname(__file__), "../../src/val-service")
    state_spec = importlib.util.spec_from_file_location("val_state", os.path.join(service_dir, "state.py"))
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["state"] = state_mod
    state_spec.loader.exec_module(state_mod)

    graph_spec = importlib.util.spec_from_file_location("val_graph", os.path.join(service_dir, "graph.py"))
    graph_mod = importlib.util.module_from_spec(graph_spec)
    sys.modules["graph"] = graph_mod
    graph_spec.loader.exec_module(graph_mod)
    return graph_mod


_graph_module = load_val_graph()
val_node = _graph_module.val_node


def test_val_node_includes_directives_in_prompt():
    """Cuando hay expert_directives en el estado, deben aparecer en el SystemMessage enviado al LLM."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    captured_messages = []

    def capturing_invoke(msgs):
        captured_messages.extend(msgs)
        return AIMessage(content="Respuesta de prueba.")

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = capturing_invoke

    with patch.object(_graph_module, "llm", mock_llm):
        state = {
            "messages": [HumanMessage(content="¿Cuál es el mayor reto de tu comunidad?")],
            "expert_directives": ["Indaga más sobre el acceso al agua potable"],
        }
        result = val_node(state)

    assert result is not None
    system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage) and "DIRECTIVAS" in m.content]
    assert len(system_msgs) >= 1
    assert "acceso al agua potable" in system_msgs[0].content


def test_val_node_clears_directives_after_use():
    """Las expert_directives deben vaciarse en el estado de retorno tras ser aplicadas."""
    from langchain_core.messages import HumanMessage, AIMessage

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Respuesta.")

    with patch.object(_graph_module, "llm", mock_llm):
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

    with patch.object(_graph_module, "llm", mock_llm):
        state = {
            "messages": [HumanMessage(content="¿Quién eres?")],
            "expert_directives": [],
        }
        result = val_node(state)

    assert "messages" in result
    assert result["messages"][-1].content == "Hola, soy VAL."
