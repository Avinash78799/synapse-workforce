"""Default simulation connector wrapping the Acme ecommerce store."""
from typing import Any
from simulations.ecommerce import store
from core.connectors.base import BaseConnector


class SimulationConnector(BaseConnector):
    """Transactional simulation store connector for local runs and regression tests."""

    def investigate(self, conn: Any, args: dict, abuse_window_days: int = 30) -> dict:
        return store.investigate(conn, args, abuse_window_days)

    def execute(self, conn: Any, tenant: Any, agent: Any, tool: str, args: dict, idempotency_key: str) -> dict:
        return store.execute(conn, tenant, agent, tool, args, idempotency_key)

    def verify(self, conn: Any, tool: str, result: dict) -> bool:
        return store.verify(conn, tool, result)

    def reconcile(self, conn: Any, tool: str, args: dict, idempotency_key: str) -> dict | None:
        return store.reconcile(conn, tool, args, idempotency_key)
