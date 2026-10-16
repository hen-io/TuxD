from .core import CoreIntegration
from .custom import CustomActionsIntegration
from .proxmox import ProxmoxIntegration
from .systemd import SystemdIntegration
from .tugboat import TugboatActionsIntegration

INTEGRATIONS = (
    CoreIntegration,
    CustomActionsIntegration,
    SystemdIntegration,
    TugboatActionsIntegration,
    ProxmoxIntegration,
)
