"""The fork's Docker deploy must keep functional bubblewrap containment.

src/containment.py ships enforcing: without a working bubblewrap sandbox the
agent's native bash/python refuse to run. Inside Docker that needs the binary
in the image AND relaxed container filters on the odysseus service, because the
app runs as a non-root user (user namespaces are blocked by Docker's default
seccomp profile, a fresh /proc mount by its masked /proc paths, and mounts by
the docker-default AppArmor profile). Verified with containment's own probe
inside the built image; this keeps an upstream merge from dropping either half.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.gpu-amd.yml", "docker-compose.gpu-nvidia.yml")
REQUIRED_OPTS = {"seccomp=unconfined", "apparmor=unconfined", "systempaths=unconfined"}


def test_image_installs_bubblewrap():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^\s+bubblewrap \\$", dockerfile, re.M), "Dockerfile apt list must install bubblewrap"


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_odysseus_service_allows_bubblewrap_namespaces(name):
    compose = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    service = compose["services"]["odysseus"]
    assert REQUIRED_OPTS <= set(service.get("security_opt") or []), name
    # The relaxation is only for the sandbox: never a privileged container.
    assert not service.get("privileged"), name
    assert "SYS_ADMIN" not in (service.get("cap_add") or []), name
