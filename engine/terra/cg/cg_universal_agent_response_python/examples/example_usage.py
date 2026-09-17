"""
Example usage of Agent Response.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.agent_response import AgentResponse

success = AgentResponse.success({"foo": "bar"}, meta={"count": 1})
print(f"Success: {AgentResponse.format_json(success)}")

error = AgentResponse.error("Something went wrong", code="ERR_CODE")
print(f"Error: {AgentResponse.format_json(error)}")
