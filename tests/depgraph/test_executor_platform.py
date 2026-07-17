from python_deps.depgraph import executor


def test_docker_executor_uses_explicit_platform_and_immutable_image(monkeypatch):
    commands = []

    def fake_run(command, *, timeout):
        commands.append(command)
        return executor.CommandResult(
            command=command, returncode=0, stdout="container-id\n", stderr=""
        )

    monkeypatch.setattr(executor, "_run_subprocess", fake_run)

    with executor.DockerExecutor(
        "sha256:resolved-image", platform="linux/arm64"
    ):
        pass

    assert "docker run -d" in commands[0]
    assert "--platform linux/arm64" in commands[0]
    assert "sha256:resolved-image" in commands[0]
