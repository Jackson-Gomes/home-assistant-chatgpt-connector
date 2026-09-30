# ChatGPT Connector add-on

Home Assistant OS add-on for the Home Assistant ChatGPT Connector.

## Security model

The add-on uses the Supervisor-provided `SUPERVISOR_TOKEN`; no long-lived Home Assistant token is stored in the repository or add-on configuration.

Version 0.4.0 enables the Supervisor API with `hassio_role: manager`. This is intentionally narrower than the unrestricted `admin` role while still allowing the connector to inspect Home Assistant Core, apps/add-ons, backups, hardware, host information and logs.

Operations that can change the installation should be exposed as explicit MCP tools with validation and confirmation. In Admin API v2, creating a full backup requires `confirm=true`.

## MCP Events beta (0.9.0-beta)

Version 0.9.0-beta adds the reverse path from Home Assistant to ChatGPT using MCP Events on protocol `2026-07-28`.

Flow:

`Home Assistant -> chatgpt_command event -> connector -> ha.command MCP event -> subscribed ChatGPT chat`

The add-on listens internally to the Home Assistant custom event `chatgpt_command`. No ESP32, Tuya service, inbound Home Assistant port, or OpenAI API key is required for this path. The existing Secure MCP Tunnel remains the external transport.

Example Home Assistant action:

```yaml
- event: chatgpt_command
  event_data:
    text: "Verifique por que o robo nao passou pano depois de aspirar"
    source: "automation"
```

The MCP server advertises the event `ha.command` and implements:

- `events/list`
- `events/subscribe`
- `events/unsubscribe`
- signed Standard Webhooks callback verification and delivery
- persistent subscriptions under `/data/mcp_events_subscriptions.json`
- callback HTTPS/public-address validation, no redirects, bounded retries, expiration and secret rotation

Diagnostic tools:

- `mcp_events_status()` — subscription count, Home Assistant listener state and last delivery status without exposing secrets
- `emit_test_chatgpt_event(text, source)` — emits a manual test `ha.command` event

After installing the beta, rescan the plugin in ChatGPT so `ha.command` appears in the plugin event catalog. In the ChatGPT conversation that should receive Home Assistant commands, ask ChatGPT to subscribe to `ha.command` and describe what it should do when events arrive.

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

## Assist administration (0.5.0)

Version 0.5.0 adds a restricted MCP surface for Home Assistant Assist using the official Home Assistant WebSocket commands. It does not edit `.storage` files directly.

- `list_assist_pipelines()` — lists Assist pipelines and the preferred pipeline
- `update_assist_pipeline(pipeline_id, changes)` — updates only allowlisted official pipeline fields, including `conversation_engine`
- `list_assist_exposed_entities()` — lists entities exposed to the `conversation` assistant
- `set_assist_entity_exposure(entity_id, exposed)` — exposes or unexposes one validated entity
- `set_assist_exposed_entities(entity_ids)` — makes the supplied entities the exact Assist allowlist and disables automatic exposure of new entities

Initial safe configuration after installing 0.5.0:

1. Update the desired pipeline with `changes={"conversation_engine": "conversation.codex_assist"}`.
2. Call `set_assist_exposed_entities(["light.luz_do_escritorio"])`.

The Assist tools do not add generic `/config` writes and do not access the APT0307 directories.

## Restricted config file access

Version 0.3.1 mounts the Home Assistant `config` directory read/write so the connector can maintain web assets. The MCP write surface remains intentionally restricted in code to `/config/www/`; paths resolving outside that directory are rejected. This allows updates such as `/config/www/apto3d/index.html` without exposing a generic filesystem writer.

## APTO3D custom component access

Version 0.4.0 adds a separate, project-scoped file surface for the Digital Twin custom component:

- `read_apto3d_component_file(path)`
- `write_apto3d_component_file(path, content)`
- `list_apto3d_component_files()`

These tools are confined to `/config/custom_components/apto3d/`. Writes remain atomic and the existing `write_config_file` restriction to `/config/www/` is unchanged. Canonical path checks reject traversal and symlink escapes. Allowed component files are root-level `.py`, `manifest.json`, `strings.json`, and `translations/*.json`.
