"""Regression test: openclaw JSON extraction must find the outermost object.

Bug: launch.sh scanned stderr backwards for '{', finding a tiny nested object
(e.g. {"name": "memory_get", ...}) instead of the root response with 'payloads'.
Fix: scan forward to find the first '{' that parses into a complete JSON.
"""

import json
import subprocess
import textwrap

import pytest


EXTRACTION_SCRIPT = textwrap.dedent("""\
import json, sys
raw = open(sys.argv[1]).read()
decoder = json.JSONDecoder()
for i in range(len(raw)):
    if raw[i] == '{':
        try:
            obj, end = decoder.raw_decode(raw[i:])
            print(json.dumps(obj))
            break
        except (json.JSONDecodeError, ValueError):
            continue
""")


class TestOpenClawJsonExtraction:
    """The forward-scanning extraction must find the outermost JSON object."""

    def test_extracts_outermost_json_from_mixed_stderr(self, tmp_path):
        """ANSI log lines + nested JSON → must return root object with 'payloads'."""
        stderr_content = (
            '\x1b[36m[skills]\x1b[39m \x1b[33mSkipping skill path.\x1b[39m\n'
            '{"payloads": [{"text": "Hello!"}], "meta": {"agentMeta": '
            '{"model": "test-model", "usage": {"input": 10, "output": 5}, '
            '"toolsUsed": [{"name": "memory_get", "summaryChars": 151, '
            '"schemaChars": 128, "propertiesCount": 3}]}}, "stopReason": "stop"}\n'
        )
        stderr_file = tmp_path / "stderr.txt"
        stderr_file.write_text(stderr_content)

        result = subprocess.run(
            ["python3", "-c", EXTRACTION_SCRIPT, str(stderr_file)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.strip())
        assert "payloads" in data
        assert data["payloads"][0]["text"] == "Hello!"
        assert data["meta"]["agentMeta"]["model"] == "test-model"

    def test_does_not_return_nested_object(self, tmp_path):
        """Must NOT return the last nested '{...}' — that was the old bug."""
        stderr_content = (
            'log line\n'
            '{"outer": true, "nested": {"inner": true}}\n'
        )
        stderr_file = tmp_path / "stderr.txt"
        stderr_file.write_text(stderr_content)

        result = subprocess.run(
            ["python3", "-c", EXTRACTION_SCRIPT, str(stderr_file)],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(result.stdout.strip())
        assert data["outer"] is True
        assert "nested" in data

    def test_no_json_returns_empty(self, tmp_path):
        """Pure log output with no JSON → no output."""
        stderr_file = tmp_path / "stderr.txt"
        stderr_file.write_text("just some log lines\nno json here\n")

        result = subprocess.run(
            ["python3", "-c", EXTRACTION_SCRIPT, str(stderr_file)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.stdout.strip() == ""

    def test_json_on_first_line(self, tmp_path):
        """JSON at start of stderr (no log prefix) → extracted correctly."""
        stderr_file = tmp_path / "stderr.txt"
        stderr_file.write_text('{"payloads": [{"text": "Direct"}]}\n')

        result = subprocess.run(
            ["python3", "-c", EXTRACTION_SCRIPT, str(stderr_file)],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(result.stdout.strip())
        assert data["payloads"][0]["text"] == "Direct"
