import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

JOBSTARTER = Path(__file__).resolve().parents[1] / "jobstarter.qrmi"


class CleanupRegressionTests(unittest.TestCase):
    def run_scenario(self, scenario, app_exit=0):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)

            config = {
                "resources": [
                    {
                        "name": name,
                        "type": "qiskit-runtime-service",
                        "environment": {},
                    }
                    for name in ("resource_a", "resource_b")
                ]
            }
            (tmp / "qrmi_config.json").write_text(json.dumps(config))

            mock_module = tmp / "qrmi.py"
            mock_module.write_text("""
import os

class ResourceType:
    IBMQuantumSystem = 1
    IBMQuantumComputeService = 2
    IBMQiskitRuntimeService = 3
    PasqalCloud = 4
    PasqalLocal = 5
    AliceBobFelis = 6
    IQMServer = 7
    OQTOPUS = 8

class ResourceStatusCode:
    Online = "online"

class ResourceProvider:
    pass

class Status:
    def __init__(self, status):
        self.status = status

    def to_dict(self):
        return {"status": self.status}

class QuantumResource:
    def __init__(self, name):
        self.name = name

    @classmethod
    def from_config(cls, name, resource_type, config):
        return cls(name)

    def resource_id(self):
        return self.name

    def resource_type(self):
        return "mock"

    def status(self):
        scenario = os.getenv("SCENARIO")
        if scenario == "offline_all":
            return Status("offline")
        if scenario == "offline_first" and self.name == "resource_a":
            return Status("offline")
        if scenario == "offline_second" and self.name == "resource_b":
            return Status("offline")
        return Status(ResourceStatusCode.Online)

    def acquire(self):
        if (os.getenv("SCENARIO") == "acquire_failure"
                and self.name == "resource_b"):
            raise RuntimeError("Simulated acquisition failure")
        with open(os.environ["EVENT_LOG"], "a") as f:
            f.write(f"acquire:{self.name}\\n")
        if (os.getenv("SCENARIO") == "missing_token"
                and self.name == "resource_a"):
            return ""
        return f"token-{self.name}"

    def release(self, token):
        with open(os.environ["EVENT_LOG"], "a") as f:
            f.write(f"release:{self.name}\\n")
        if (os.getenv("SCENARIO") == "release_failure"
                and self.name == "resource_a"):
            raise RuntimeError("Simulated release failure")
""")

            event_log = tmp / "events.log"
            env = os.environ.copy()
            env.update({
                "PYTHONPATH": str(tmp),
                "LSF_ENVDIR": str(tmp),
                "QRMI_QPU_RESOURCES": "resource_a,resource_b",
                "LSF_QRMI_DEBUG": "1",
                "SCENARIO": scenario,
                "EVENT_LOG": str(event_log),
            })

            result = subprocess.run(
                [
                    sys.executable,
                    str(JOBSTARTER),
                    "/bin/sh",
                    "-c",
                    f"exit {app_exit}",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )

            events = (
                event_log.read_text().splitlines()
                if event_log.exists()
                else []
            )

            return result, events

    def test_offline_second_resource(self):
        result, events = self.run_scenario("offline_second")

        print("\\nOffline second resource:", result.returncode, events)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(events, [
            "acquire:resource_a",
            "release:resource_a",
        ])

    def test_offline_first_resource(self):
        result, events = self.run_scenario("offline_first")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(events, [
            "acquire:resource_b",
            "release:resource_b",
        ])

    def test_all_resources_offline(self):
        result, events = self.run_scenario("offline_all")

        self.assertEqual(result.returncode, 99)
        self.assertEqual(events, [])
        self.assertIn("No specified quantum resources", result.stderr)

    def test_first_release_failure(self):
        result, events = self.run_scenario("release_failure")

        print("\\nFirst release failure:", result.returncode, events)

        self.assertIn("release:resource_a", events)

        # Expected safe behavior; likely fails on current upstream.
        self.assertIn("release:resource_b", events)



    def test_success_releases_all_resources(self):
        result, events = self.run_scenario("normal")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(events, [
            "acquire:resource_a",
            "acquire:resource_b",
            "release:resource_a",
            "release:resource_b",
        ])

    def test_exit_42_releases_all_resources(self):
        result, events = self.run_scenario("normal", app_exit=42)
        self.assertEqual(result.returncode, 42)
        self.assertIn("release:resource_a", events)
        self.assertIn("release:resource_b", events)

    def test_exit_255_releases_all_resources(self):
        result, events = self.run_scenario("normal", app_exit=255)
        self.assertEqual(result.returncode, 255)
        self.assertIn("release:resource_a", events)
        self.assertIn("release:resource_b", events)

    def test_release_failure_preserves_application_status(self):
        result, events = self.run_scenario(
            "release_failure", app_exit=42
        )
        self.assertEqual(result.returncode, 42)
        self.assertIn("release:resource_a", events)
        self.assertIn("release:resource_b", events)


    def test_second_acquisition_failure_releases_first(self):
        result, events = self.run_scenario("acquire_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("acquire:resource_a", events)
        self.assertIn("release:resource_a", events)

    def test_missing_first_token_still_releases_second(self):
        result, events = self.run_scenario("missing_token")
        self.assertIn("acquire:resource_a", events)
        self.assertIn("acquire:resource_b", events)
        self.assertIn("release:resource_b", events)
        self.assertIn("Missing token for resource_a", result.stderr)

if __name__ == "__main__":
    unittest.main(verbosity=2)
