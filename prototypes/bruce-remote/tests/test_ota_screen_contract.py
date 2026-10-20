import ast
import csv
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class SafetyContractTests(unittest.TestCase):
    def test_partition_layout(self):
        with (ROOT / "bruce_remote_ota_16Mb.csv").open() as f:
            rows = list(csv.reader(line for line in f if line.strip() and not line.startswith("#")))
        entries = {}
        for row in rows:
            name = row[0].strip()
            entries[name] = (int(row[3].strip(),0), int(row[4].strip(),0))
        self.assertEqual(entries["nvs"],(0x9000,0x5000))
        self.assertEqual(entries["otadata"],(0xE000,0x2000))
        self.assertEqual(entries["ota_0"],(0x10000,0x470000))
        self.assertEqual(entries["ota_1"],(0x480000,0x470000))
        self.assertEqual(entries["spiffs"],(0x8F0000,0x700000))
        self.assertEqual(entries["coredump"],(0xFF0000,0x10000))
        offsets=sorted(entries.values())
        self.assertTrue(all(a[0]+a[1]<=b[0] for a,b in zip(offsets, offsets[1:])))
        self.assertEqual(offsets[-1][0]+offsets[-1][1],16*1024*1024)

    def test_partition_overlay_and_manifest(self):
        source=(ROOT/"apply_to_upstream.py").read_text()
        ast.parse(source)
        self.assertIn("bruce_remote_ota_16Mb.csv",source)
        self.assertIn("board_build.partitions",source)
        for name in ("bruce_screen.cpp","bruce_ota.cpp","bruce_remote.cpp"):
            self.assertIn(name,source)

    def test_remote_guards(self):
        remote=(ROOT/"firmware/bruce_remote.cpp").read_text()
        ota=(ROOT/"firmware/bruce_ota.cpp").read_text()
        self.assertIn("physical_unlock_and_button_hold_required",remote)
        self.assertIn('op=="screen.snapshot"',remote)
        self.assertIn('op=="ota.apply"',remote)
        self.assertIn('expectedHex!=String(computed)',ota)
        self.assertIn("Update.abort()",ota)
        self.assertIn("esp_ota_get_next_update_partition",ota)
        self.assertNotIn("setInsecure(", remote+ota)

    def test_gateway_and_screen_files(self):
        src=(ROOT/"gateway/server.py").read_text()
        ast.parse(src)
        self.assertIn("zlib.crc32",src)
        self.assertIn("self.screen_waiters",src)
        self.assertTrue((ROOT/"gateway/viewer.html").read_text().startswith("<!doctype html>"))

if __name__=="__main__":
    unittest.main()
