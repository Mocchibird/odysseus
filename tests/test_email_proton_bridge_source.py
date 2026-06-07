from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_proton_bridge_provider_preset_is_available():
    settings_js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "proton_bridge" in settings_js
    assert "Proton Bridge (host)" in settings_js
    assert "Proton Bridge (Docker)" in settings_js
    assert "host: 'host.docker.internal'" in settings_js
    assert "host: 'proton-bridge'" in settings_js
    assert "port: 1143, starttls: true" in settings_js
    assert "port: 1025, security: 'starttls'" in settings_js
    assert "port: 143, starttls: true" in settings_js
    assert "port: 25, security: 'starttls'" in settings_js
    assert "Bridge-generated username and password" in settings_js
    assert "/api/email/proton-bridge/status" in settings_js
    assert "Bridge reachable" in settings_js
    assert "bridgeMode: 'docker'" in settings_js


def test_proton_bridge_docker_docs_match_preset():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compact_readme = " ".join(readme.split())

    assert "Proton Mail Bridge with Docker" in readme
    assert "IMAP port: 1143" in readme
    assert "SMTP port: 1025" in readme
    assert "Security: STARTTLS" in readme
    assert "not your Proton account password" in compact_readme
    assert "container by default" in readme
    assert "Proton Bridge (Docker)" in readme
    assert "PROTON_BRIDGE_IMAGE" in readme
    assert "ODYSSEUS_PROTON_BRIDGE_IMAGE" in readme
    assert "libfido2.so.1" in readme
    assert "docker/proton-bridge.Dockerfile" in compose
    assert "PROTON_BRIDGE_BASE" in compose
    assert ":1143:143" in compose
    assert ":1025:25" in compose
    assert "proton-bridge:143/25" in readme
    assert "proton-bridge:" in compose
    assert "profiles:" not in compose
    assert "proton-bridge-data:" in compose


def test_proton_bridge_wrapper_installs_fido_runtime_dependency():
    dockerfile = (ROOT / "docker" / "proton-bridge.Dockerfile").read_text(encoding="utf-8")

    assert "ARG PROTON_BRIDGE_BASE" in dockerfile
    assert "FROM ${PROTON_BRIDGE_BASE}" in dockerfile
    assert "libfido2-1" in dockerfile
    assert "libfido2.so.1" in dockerfile


def test_proton_bridge_backend_sidecar_routes_exist():
    helpers = (ROOT / "routes" / "email_helpers.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes" / "email_routes.py").read_text(encoding="utf-8")

    assert '"proton-bridge"' in helpers
    assert "_LOCAL_MAIL_BRIDGE_HOSTS" in helpers
    assert "PROTON_BRIDGE_PRESETS" in routes
    assert '"imap_host": "proton-bridge"' in routes
    assert '"imap_port": 143' in routes
    assert '"smtp_host": "proton-bridge"' in routes
    assert '"smtp_port": 25' in routes
    assert "def _tcp_status" in routes
    assert '"/proton-bridge/status"' in routes
    assert '"/accounts/proton-bridge"' in routes
    assert "create_proton_bridge_account" in routes
    assert "def _mail_endpoint_error" in routes
    assert "localhost is the Odysseus container in Docker" in routes
    assert "the Proton Bridge sidecar is not listening yet" in routes
    assert "switch to Proton Bridge (Docker)" in routes
