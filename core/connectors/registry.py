"""Connector registry to route tool operations to simulation or external sources."""
from core.config import settings
from core.connectors.base import BaseConnector
from core.connectors.simulation import SimulationConnector
from core.connectors.http_adapter import HttpConnector

_CONNECTORS: dict[str, BaseConnector] = {
    'simulation': SimulationConnector(),
}


def register_connector(name: str, connector: BaseConnector) -> None:
    """Registers a named connector instance."""
    _CONNECTORS[name] = connector


def get_connector(tool_name: str = '') -> BaseConnector:
    """Routes a tool call to the appropriate connector based on system configuration."""
    if tool_name and tool_name in _CONNECTORS:
        return _CONNECTORS[tool_name]
    # Tool-specific connector routing
    if tool_name.startswith('payment.') and settings.payment_connector != 'simulation':
        name = settings.payment_connector
    elif tool_name.startswith(('ticket.', 'support.notify')) and settings.helpdesk_connector != 'simulation':
        name = settings.helpdesk_connector
    elif (tool_name.startswith(('operations.', 'support.investigate')) or tool_name.startswith('shipment.') or tool_name.startswith('task.')) and settings.commerce_connector != 'simulation':
        name = settings.commerce_connector
    elif settings.external_connector_base_url:
        name = 'http'
    else:
        name = 'simulation'

    if name not in _CONNECTORS:
        if name in ('http', 'external', 'sandbox'):
            _CONNECTORS[name] = HttpConnector()
        else:
            raise RuntimeError('Unknown business connector; refusing simulation fallback')

    return _CONNECTORS[name]
