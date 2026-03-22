"""Tests for SWE-bench runner script."""
import json
import subprocess
import sys
import os
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))


class TestParseArgs:
    def test_defaults(self):
        from swe_bench_runner import parse_args

        args = parse_args([])
        assert args.dataset == "princeton-nlp/SWE-bench_Verified"
        assert args.split == "test"
        assert args.workdir == "swe_bench_workdir"
        assert args.server_url == "http://localhost:8000"
        assert args.timeout == 1800
        assert args.max_tasks is None

    def test_custom_args(self):
        from swe_bench_runner import parse_args

        args = parse_args([
            "--dataset", "princeton-nlp/SWE-bench_Lite",
            "--workdir", "/tmp/bench",
            "--timeout", "600",
            "--max-tasks", "5",
        ])
        assert args.dataset == "princeton-nlp/SWE-bench_Lite"
        assert args.workdir == "/tmp/bench"
        assert args.timeout == 600
        assert args.max_tasks == 5


class TestPredictionsIO:
    def test_load_empty(self, tmp_path):
        from swe_bench_runner import load_predictions

        path = tmp_path / "predictions.json"
        result = load_predictions(path)
        assert result == []

    def test_load_existing(self, tmp_path):
        from swe_bench_runner import load_predictions

        path = tmp_path / "predictions.json"
        path.write_text(json.dumps([
            {"instance_id": "foo__bar-123", "model_name_or_path": "OneManCompany", "model_patch": "diff..."}
        ]))
        result = load_predictions(path)
        assert len(result) == 1
        assert result[0]["instance_id"] == "foo__bar-123"

    def test_save_prediction(self, tmp_path):
        from swe_bench_runner import Prediction, save_prediction

        path = tmp_path / "predictions.json"
        pred = Prediction(instance_id="foo__bar-123", model_patch="diff --git ...")
        save_prediction(path, pred)
        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["instance_id"] == "foo__bar-123"

    def test_save_appends(self, tmp_path):
        from swe_bench_runner import Prediction, save_prediction

        path = tmp_path / "predictions.json"
        save_prediction(path, Prediction(instance_id="a", model_patch="diff1"))
        save_prediction(path, Prediction(instance_id="b", model_patch="diff2"))
        data = json.loads(path.read_text())
        assert len(data) == 2

    def test_completed_ids(self, tmp_path):
        from swe_bench_runner import load_predictions, get_completed_ids

        path = tmp_path / "predictions.json"
        path.write_text(json.dumps([
            {"instance_id": "a", "model_name_or_path": "x", "model_patch": ""},
            {"instance_id": "b", "model_name_or_path": "x", "model_patch": "diff"},
        ]))
        preds = load_predictions(path)
        assert get_completed_ids(preds) == {"a", "b"}


class TestGitOps:
    def test_clone_repo(self, tmp_path):
        from swe_bench_runner import clone_repo

        src = tmp_path / "source"
        src.mkdir()
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True, capture_output=True)
        (src / "file.py").write_text("v1")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "init"], check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        dest = tmp_path / "dest"
        result = clone_repo(str(src), commit, dest)
        assert result is True
        assert (dest / "file.py").read_text() == "v1"

    def test_collect_patch_no_changes(self, tmp_path):
        from swe_bench_runner import collect_patch

        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
        (repo / "f.py").write_text("orig")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

        patch = collect_patch(repo)
        assert patch == ""

    def test_collect_patch_with_modifications(self, tmp_path):
        from swe_bench_runner import collect_patch

        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
        (repo / "f.py").write_text("orig")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

        (repo / "f.py").write_text("fixed")
        patch = collect_patch(repo)
        assert "diff --git" in patch
        assert "fixed" in patch

    def test_collect_patch_with_new_file(self, tmp_path):
        from swe_bench_runner import collect_patch

        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
        (repo / "f.py").write_text("orig")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

        (repo / "new_file.py").write_text("new content")
        patch = collect_patch(repo)
        assert "new_file.py" in patch
        assert "new content" in patch


class TestTaskDescription:
    def test_build_task_description(self):
        from swe_bench_runner import build_task_description

        desc = build_task_description(
            repo_name="astropy/astropy",
            repo_path="/tmp/bench/instances/astropy__astropy-12907/repo",
            problem_statement="When I run `astropy.table.Table()` it crashes with...",
        )
        assert "[SWE-Bench]" in desc
        assert "astropy/astropy" in desc
        assert "/tmp/bench/instances/astropy__astropy-12907/repo" in desc
        assert "When I run" in desc
        assert "Do NOT commit" in desc
        assert "Do NOT modify test files" in desc


class TestOMCClient:
    def test_submit_task(self):
        from swe_bench_runner import submit_task

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "project_id": "swe_astropy_12907",
            "iteration_id": "iter_001",
        }
        mock_response.raise_for_status = mock.MagicMock()

        with mock.patch("swe_bench_runner.httpx.Client") as MockClient:
            client = mock.MagicMock()
            MockClient.return_value.__enter__ = mock.MagicMock(return_value=client)
            MockClient.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.post.return_value = mock_response

            pid, iid = submit_task("http://localhost:8000", "Fix the bug")
            assert pid == "swe_astropy_12907"
            assert iid == "iter_001"

            call_args = client.post.call_args
            assert "/api/ceo/task" in call_args[0][0]

    def test_poll_status_completed(self):
        from swe_bench_runner import poll_until_done

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "completed"}

        with mock.patch("swe_bench_runner.httpx.Client") as MockClient:
            client = mock.MagicMock()
            MockClient.return_value.__enter__ = mock.MagicMock(return_value=client)
            MockClient.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.get.return_value = mock_response

            status = poll_until_done("http://localhost:8000", "proj1", "iter_001", timeout=10, interval=0.1)
            assert status == "completed"

    def test_poll_status_timeout(self):
        from swe_bench_runner import poll_until_done

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "in_progress"}

        with mock.patch("swe_bench_runner.httpx.Client") as MockClient:
            client = mock.MagicMock()
            MockClient.return_value.__enter__ = mock.MagicMock(return_value=client)
            MockClient.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.get.return_value = mock_response

            status = poll_until_done("http://localhost:8000", "proj1", "iter_001", timeout=1, interval=0.2)
            assert status == "timeout"


class TestPrepareAndSubmit:
    def test_success(self, tmp_path):
        from swe_bench_runner import prepare_and_submit, SubmittedTask

        instance = {
            "instance_id": "test__repo-1",
            "repo": "test/repo",
            "base_commit": "abc123def",
            "problem_statement": "Something is broken",
        }

        with mock.patch("swe_bench_runner.clone_repo", return_value=True), \
             mock.patch("swe_bench_runner.submit_task", return_value=("proj1", "iter_001")):

            result = prepare_and_submit(instance, tmp_path / "instances", "http://localhost:8000")
            assert isinstance(result, SubmittedTask)
            assert result.instance_id == "test__repo-1"
            assert result.project_id == "proj1"

    def test_clone_fails(self, tmp_path):
        from swe_bench_runner import prepare_and_submit

        instance = {
            "instance_id": "test__repo-2",
            "repo": "test/repo",
            "base_commit": "abc123def",
            "problem_statement": "Bug",
        }

        with mock.patch("swe_bench_runner.clone_repo", return_value=False):
            result = prepare_and_submit(instance, tmp_path / "instances", "http://localhost:8000")
            assert result is None

    def test_submit_fails(self, tmp_path):
        from swe_bench_runner import prepare_and_submit, Prediction

        instance = {
            "instance_id": "test__repo-3",
            "repo": "test/repo",
            "base_commit": "abc123def",
            "problem_statement": "Bug",
        }

        with mock.patch("swe_bench_runner.clone_repo", return_value=True), \
             mock.patch("swe_bench_runner.submit_task", side_effect=RuntimeError("fail")):
            result = prepare_and_submit(instance, tmp_path / "instances", "http://localhost:8000")
            assert isinstance(result, Prediction)
            assert result.model_patch == ""


class TestPollAndCollectBatch:
    def test_single_task_completes(self):
        from swe_bench_runner import poll_and_collect_batch, SubmittedTask
        from pathlib import Path

        task = SubmittedTask(
            instance_id="test__repo-1",
            repo_dir=Path("/tmp/fake"),
            project_id="p1",
            iteration_id="iter_001",
        )

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "completed"}

        with mock.patch("swe_bench_runner.httpx.Client") as MockClient, \
             mock.patch("swe_bench_runner.collect_patch", return_value="diff --git a/f.py"):
            client = mock.MagicMock()
            MockClient.return_value.__enter__ = mock.MagicMock(return_value=client)
            MockClient.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.get.return_value = mock_response

            preds = poll_and_collect_batch([task], "http://localhost:8000", timeout=10, interval=0.1)
            assert len(preds) == 1
            assert preds[0].instance_id == "test__repo-1"
            assert preds[0].model_patch == "diff --git a/f.py"

    def test_timeout_collects_partial(self):
        from swe_bench_runner import poll_and_collect_batch, SubmittedTask
        from pathlib import Path

        task = SubmittedTask(
            instance_id="test__repo-1",
            repo_dir=Path("/tmp/fake"),
            project_id="p1",
            iteration_id="iter_001",
        )

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "in_progress"}

        with mock.patch("swe_bench_runner.httpx.Client") as MockClient, \
             mock.patch("swe_bench_runner.collect_patch", return_value="partial diff"):
            client = mock.MagicMock()
            MockClient.return_value.__enter__ = mock.MagicMock(return_value=client)
            MockClient.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.get.return_value = mock_response

            preds = poll_and_collect_batch([task], "http://localhost:8000", timeout=1, interval=0.2)
            assert len(preds) == 1
            assert preds[0].model_patch == "partial diff"


class TestRunInstance:
    def test_run_instance_success(self, tmp_path):
        from swe_bench_runner import run_instance, Prediction

        instance = {
            "instance_id": "test__repo-1",
            "repo": "test/repo",
            "base_commit": "abc123def",
            "problem_statement": "Something is broken",
        }

        mock_pred = Prediction(instance_id="test__repo-1", model_patch="diff --git a/f.py")

        with mock.patch("swe_bench_runner.clone_repo", return_value=True), \
             mock.patch("swe_bench_runner.submit_task", return_value=("proj1", "iter_001")), \
             mock.patch("swe_bench_runner.poll_and_collect_batch", return_value=[mock_pred]):

            pred = run_instance(instance, tmp_path / "instances", "http://localhost:8000", 1800)
            assert pred is not None
            assert pred.instance_id == "test__repo-1"
            assert pred.model_patch == "diff --git a/f.py"

    def test_run_instance_clone_fails(self, tmp_path):
        from swe_bench_runner import run_instance

        instance = {
            "instance_id": "test__repo-2",
            "repo": "test/repo",
            "base_commit": "abc123def",
            "problem_statement": "Bug",
        }

        with mock.patch("swe_bench_runner.clone_repo", return_value=False):
            pred = run_instance(instance, tmp_path / "instances", "http://localhost:8000", 1800)
            assert pred is None


class TestMainFlow:
    def test_main_end_to_end(self, tmp_path, monkeypatch):
        from swe_bench_runner import main, Prediction

        fake_ds = [
            {
                "instance_id": "test__repo-1",
                "repo": "test/repo",
                "base_commit": "abc123def",
                "problem_statement": "Bug report",
            },
        ]

        workdir = str(tmp_path / "work")

        monkeypatch.setattr("sys.argv", [
            "swe_bench_runner.py",
            "--workdir", workdir,
            "--max-tasks", "1",
            "--timeout", "5",
        ])

        mock_pred = Prediction(instance_id="test__repo-1", model_patch="diff --git fixed")

        # Create a fake 'datasets' module so the lazy import inside main() works
        fake_datasets = mock.MagicMock()
        fake_datasets.load_dataset = mock.MagicMock(return_value=fake_ds)

        with mock.patch("swe_bench_runner.clone_repo", return_value=True), \
             mock.patch("swe_bench_runner.submit_task", return_value=("p1", "iter_001")), \
             mock.patch("swe_bench_runner.poll_and_collect_batch", return_value=[mock_pred]), \
             mock.patch.dict("sys.modules", {"datasets": fake_datasets}):

            main()

        preds = json.loads((tmp_path / "work" / "predictions.json").read_text())
        assert len(preds) == 1
        assert preds[0]["instance_id"] == "test__repo-1"
        assert preds[0]["model_patch"] == "diff --git fixed"

    def test_main_batch_mode(self, tmp_path, monkeypatch):
        from swe_bench_runner import main, Prediction

        fake_ds = [
            {"instance_id": f"test__repo-{i}", "repo": "test/repo",
             "base_commit": "abc123", "problem_statement": f"Bug {i}"}
            for i in range(3)
        ]

        workdir = str(tmp_path / "work")

        monkeypatch.setattr("sys.argv", [
            "swe_bench_runner.py",
            "--workdir", workdir,
            "--max-tasks", "3",
            "--batch-size", "3",
            "--timeout", "5",
        ])

        mock_preds = [
            Prediction(instance_id=f"test__repo-{i}", model_patch=f"diff {i}")
            for i in range(3)
        ]

        fake_datasets = mock.MagicMock()
        fake_datasets.load_dataset = mock.MagicMock(return_value=fake_ds)

        with mock.patch("swe_bench_runner.clone_repo", return_value=True), \
             mock.patch("swe_bench_runner.submit_task", return_value=("p1", "iter_001")), \
             mock.patch("swe_bench_runner.poll_and_collect_batch", return_value=mock_preds), \
             mock.patch.dict("sys.modules", {"datasets": fake_datasets}):

            main()

        preds = json.loads((tmp_path / "work" / "predictions.json").read_text())
        assert len(preds) == 3
