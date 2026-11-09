"""agents-workspace: one root for every agents domain."""

from .workspace import (
    ALL_DOMAINS,
    DIR_DOMAINS,
    DOMAIN_ENV,
    ENV_ROOT,
    ENV_ROOT_LEGACY,
    ENV_WORKDIR,
    ENV_WORKDIR_LEGACY,
    FILE_DOMAINS,
    Workspace,
    current,
    legacy_root,
    workspaces_dir,
)

__version__ = "0.1.0"

__all__ = [
    "ALL_DOMAINS",
    "DIR_DOMAINS",
    "DOMAIN_ENV",
    "ENV_ROOT",
    "ENV_ROOT_LEGACY",
    "ENV_WORKDIR",
    "ENV_WORKDIR_LEGACY",
    "FILE_DOMAINS",
    "Workspace",
    "current",
    "legacy_root",
    "workspaces_dir",
    "__version__",
]
