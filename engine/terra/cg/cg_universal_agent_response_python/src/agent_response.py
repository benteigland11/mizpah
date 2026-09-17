import json
from typing import Any, Optional, Dict

class AgentResponse:
    """
    Standardized machine-first response structure for agent tools.
    """
    @staticmethod
    def success(data: Any, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = {
            "status": "success",
            "data": data
        }
        if meta:
            response["meta"] = meta
        return response

    @staticmethod
    def error(message: str, code: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        error_payload = {"message": message}
        if code:
            error_payload["code"] = code
        
        response = {
            "status": "error",
            "error": error_payload
        }
        if meta:
            response["meta"] = meta
        return response

    @staticmethod
    def format_json(response: Dict[str, Any]) -> str:
        return json.dumps(response, indent=2)
