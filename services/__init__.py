"""Services package.

NOTE: all Xray logic (install, keys, config generation, link generation,
process control) lives in `xray_service`. The old `xray_core` module was a
conflicting duplicate (different path constant + fake checksums) and has been
removed. Do NOT reintroduce a second xray module.
"""
from .xray_service import (
    generate_vless_link,
    generate_xray_server_config,
    generate_reality_keys,
    ensure_reality_keys,
    start_xray,
    stop_xray,
    restart_xray,
    get_xray_status,
    install_xray_core,
    is_xray_installed,
    get_xray_version,
    validate_xray_config,
    write_xray_config,
    RealityIncompleteError,
)

from .relay_vless import (
    parse_vless_header,
    check_and_use,
    websocket_tunnel,
    _ws_client_ip,
)

from .xhttp_siz10 import (
    router as xhttp_siz10_router,
)
