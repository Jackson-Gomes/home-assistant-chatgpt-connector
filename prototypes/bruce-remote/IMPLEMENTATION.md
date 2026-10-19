# BruceRemote implementation — experimental

**Firmware:** \`firmware/bruce_remote.cpp\` and \`firmware/bruce_remote.h\`. These are applied to a fresh BruceDevices/firmware checkout by \`apply_to_upstream.py\` in GitHub Actions. Board target: \`lilygo-t-embed-cc1101\`. The GitHub branch \`feature/bruce-mqtt-control\` is experimental and must not be merged into the Home Assistant production addon.

**Provisioning without microSD:** Upload \`bruce-remote.json\` and \`bruce-ca.pem\` to **LittleFS** using Bruce's local authenticated WebUI/file manager, change the default local WebUI credentials first, and set \`enabled: true\` in a copy of the example. Configure unique MQTT username/password and device topic ACL. Configure **Wi-Fi at startup** in Bruce and register trusted Wi-Fi SSIDs locally before remote use.

**TLS:** MQTT 8883, root CA PEM file, no \`setInsecure\`; credentials remain local on flash. Device cannot reach the broker without a routable Internet network. No hidden remote Wi-Fi password collection. All remote MQTT packets contain \`v=1\`, unique 8-64-char \`id\`, \`op\`, bounded \`args\`, and \`expires\` (UNIX seconds within the next 120s); device refuses expired, duplicate and fast repeated commands. Incoming MQTT commands MUST use retain=false.

**Ops (14):** \`info.get\`, \`remote.capabilities\`, \`screen.brightness\`, \`screen.color\`, \`sound.tone\`, \`sound.say\`, \`remote.lock\`, \`remote.unlock\`, \`menu.nav\`, \`menu.option\`, \`ir.send_saved\`, \`rf.send_saved\`, \`rf.receive\`, \`rf.scan\`.

**Physical unlock:** The remote operator must issue \`remote.unlock\` while the local user presses the encoder's center button. It grants 5 minutes of access to menu navigation and the pre-approved IR/RF actions. This includes potentially sensitive Bruce menu functions: only unlock when the device operator approves.

**Known limitations:** \`queued\` means sent to the Bruce SerialCli command queue, **not completed**. RF scan/receive results are visible on USB serial, not over MQTT. This initial module does not provide file system management, code execution, remote arbitrary commands, or OTA. Wi-Fi is inherited from Bruce's **Wi-Fi at startup** setting. MQTT reconnects only after the device has an Internet connection. User must create a real MQTT broker and grant gateway access. This is not yet physically tested or integrated into the connected ChatGPT Home Assistant addon.

**Separate gateway:** \`gateway/server.py\` is a loopback-only HTTP/MQTT service, protected by a long bearer token, intended for wrapping inside the existing Home Assistant ChatGPT connector. It must not be exposed directly to the internet.
