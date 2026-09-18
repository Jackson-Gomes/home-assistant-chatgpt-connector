# ChatGPT Connector add-on

Home Assistant OS add-on for the Home Assistant ChatGPT Connector.

## Security model

The add-on uses the Supervisor-provided `SUPERVISOR_TOKEN`; no long-lived Home Assistant token is stored in the repository or add-on configuration.

Version 0.3.1 enables the Supervisor API with `hassio_role: manager`. This is intentionally narrower than the unrestricted `admin` role while still allowing the connector to inspect Home Assistant Core, apps/add-ons, backups, hardware, host information and logs.

Operations that can change the installation should be exposed as explicit MCP tools with validation and confirmation. In Admin API v2, creating a full backup requires `confirm=true`.

## Core tools

- `read_config_file` — reads UTF-8 text files under `/config/`
- `write_config_file` — atomically writes only under `/config/www/`; traversal outside that directory is rejected
- `ha_health`
- `list_entities`
- `get_entity_state`
- `list_services`
- `get_history`
- `get_logbook`
- `get_error_log`
- `call_service`
- `get_automation_config`
- `save_automation`
- `get_script_config`
- `save_script`
- `update_entity`
- `get_dashboard`
- `save_dashboard`

## Admin API v2 tools

- `admin_health`
- `list_devices`
- `list_integrations`
- `list_addons`
- `get_addon_info`
- `get_hardware_info`
- `get_host_info`
- `list_backups`
- `create_full_backup`
- `get_admin_job`
- `get_admin_logs`

Admin API v2 currently focuses on inspection, diagnostics and backup creation. App installation/update/removal and broader system-changing operations are intentionally not exposed yet.


## Restricted config file access

Version 0.3.1 mounts the Home Assistant `config` directory read/write so the connector can maintain web assets. The MCP write surface remains intentionally restricted in code to `/config/www/`; paths resolving outside that directory are rejected. This allows updates such as `/config/www/apto3d/index.html` without exposing a generic filesystem writer.
