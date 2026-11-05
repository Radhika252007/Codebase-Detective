from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DetectiveContext:
    project_root: Optional[str] = None
    database_url: Optional[str] = field(default=None, repr=False)
    database_tables: tuple[str, ...] = ()
    base_url: Optional[str] = None
    auth_token: Optional[str] = field(default=None, repr=False)
    routes_manifest: Optional[str] = None
    database_capabilities: dict[str, bool] = field(default_factory=dict, repr=False)