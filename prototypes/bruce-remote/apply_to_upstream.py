"""Build-time overlay on a clean BruceDevices/firmware checkout."""
from pathlib import Path
import argparse
import shutil

def replace_once(path, old, new):
    text=path.read_text()
    if text.count(old)!=1:
        raise RuntimeError(f"Upstream layout changed: {path} ({old!r})")
    path.write_text(text.replace(old,new,1))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--upstream",required=True,type=Path)
    p.add_argument("--overlay",required=True,type=Path)
    a=p.parse_args()
    for name in ("bruce_remote.cpp","bruce_remote.h","bruce_screen.cpp","bruce_screen.h","bruce_ota.cpp","bruce_ota.h"):
        shutil.copy2(a.overlay/"firmware"/name,a.upstream/"src"/name)
    shutil.copy2(a.overlay/"bruce_remote_ota_16Mb.csv",a.upstream/"bruce_remote_ota_16Mb.csv")
    replace_once(a.upstream/"boards/lilygo-t-embed-cc1101/lilygo-t-embed-cc1101.ini",
                 "board_build.partitions = custom_16Mb.csv",
                 "board_build.partitions = bruce_remote_ota_16Mb.csv")
    replace_once(a.upstream/"src/main.cpp", '#include "core/serialcmds.h"', '#include "core/serialcmds.h"\n#include "bruce_remote.h"')
    replace_once(a.upstream/"src/main.cpp", '    startSerialCommandsHandlerTask(true);', '    startSerialCommandsHandlerTask(true);\n    startBruceRemoteBridge();')
    replace_once(a.upstream/"platformio.ini", "fastled/FastLED @^3.10.3", "fastled/FastLED @3.10.3")
    replace_once(a.upstream/"boards/lilygo-t-embed-cc1101/lilygo-t-embed-cc1101.ini",
                 "lewisxhe/XPowersLib @^0.3.3","lewisxhe/XPowersLib @^0.3.3\n\tknolleary/PubSubClient @ 2.8")
    print("Opt-in BruceRemote module applied")
if __name__=="__main__":
    main()
