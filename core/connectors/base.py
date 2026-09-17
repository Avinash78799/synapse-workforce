"""Base Connector protocol for external business systems and simulation backends.

Adheres to the connector acceptance checklist:
- Idempotency key propagation
- Authoritative state reconciliation
- Read-back verification
- Explicit status differentiation: confirmed, failed, unknown
"""
from typing import Protocol, Any


class BaseConnector(Protocol):
    def investigate(self, conn: Any, args: dict, abuse_window_days: int = 30) -> dict:
        """Investigates customer orders, payments, and refund claim history."""
        ...

    def execute(self, conn: Any, tenant: Any, agent: Any, tool: str, args: dict, idempotency_key: str) -> dict:
        """Executes a business action (refund, retry, follow-up, task creation, ticket update, notification)."""
        ...

    def verify(self, conn: Any, tool: str, result: dict) -> bool:
        """Reads back provider state to verify that the mutation actually occurred."""
        ...

    def reconcile(self, conn: Any, tool: str, args: dict, idempotency_key: str) -> dict | None:
        """Authoritatively checks provider state for a prior mutation using the idempotency key.
        Returns the result if already confirmed, or None if absent.
        """
        ...
