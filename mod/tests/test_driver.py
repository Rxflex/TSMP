import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tsmp import driver  # noqa: E402


class TakeTest(unittest.TestCase):
    def test_commands_are_read_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, driver.FILE), "w", encoding="utf-8") as f:
                f.write("# a note\n\n  tsmp.selftest \nclock.setspeed one\n")
            self.assertEqual(driver.take(directory), ["tsmp.selftest", "clock.setspeed one"])
            self.assertEqual(driver.take(directory), [])

    def test_nothing_runs_until_it_is_switched_on(self):
        driver.tick(1)


if __name__ == "__main__":
    unittest.main()
