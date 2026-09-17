from src.agent_response import AgentResponse

def test_success():
    resp = AgentResponse.success("data", meta={"m": 1})
    assert resp["status"] == "success"
    assert resp["data"] == "data"
    assert resp["meta"]["m"] == 1

def test_error():
    resp = AgentResponse.error("msg", code="C1")
    assert resp["status"] == "error"
    assert resp["error"]["message"] == "msg"
    assert resp["error"]["code"] == "C1"
