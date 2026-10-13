"""Cross-process ChatGPT credential-store concurrency tests."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.backends import chatgpt_auth as auth


class ChatGPTConcurrencyTests(unittest.TestCase):
    def test_file_lock_serializes_cross_process_read_modify_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "auth.json"
            env = os.environ.copy()
            env["SUNDARK_CHATGPT_CREDENTIALS"] = str(store)
            parent_environment = patch.dict(
                os.environ, {"SUNDARK_CHATGPT_CREDENTIALS": str(store)}
            )
            parent_environment.start()
            self.addCleanup(parent_environment.stop)
            auth._save({"host_id": "generated-host", "accounts": {}, "active": None})

            worker = """
import asyncio
import os
from app.backends import chatgpt_auth as auth

async def main():
    async with auth._locked():
        data = auth._read()
        if os.environ['WORKER'] == 'first':
            await asyncio.sleep(0.3)
        data[os.environ['WORKER']] = True
        auth._save(data)

asyncio.run(main())
"""
            first_env = {**env, "WORKER": "first"}
            second_env = {**env, "WORKER": "second"}
            first = subprocess.Popen(
                [sys.executable, "-c", worker], env=first_env, cwd=Path.cwd()
            )
            try:
                # Let the first process acquire the OS lock before the second starts.
                time.sleep(0.05)
                second = subprocess.Popen(
                    [sys.executable, "-c", worker], env=second_env, cwd=Path.cwd()
                )
                self.assertEqual(first.wait(timeout=5), 0)
                self.assertEqual(second.wait(timeout=5), 0)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.wait()

            data = auth._read()
            self.assertTrue(data["first"])
            self.assertTrue(data["second"])


if __name__ == "__main__":
    unittest.main()
