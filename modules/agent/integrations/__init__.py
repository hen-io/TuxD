from .core import CoreIntegration
from .custom import CustomActionsIntegration
from .systemd import SystemdIntegration
from .tugboat import TugboatActionsIntegration

INTEGRATIONS = (
    CoreIntegration,
    CustomActionsIntegration,
    SystemdIntegration,
    TugboatActionsIntegration,
)
