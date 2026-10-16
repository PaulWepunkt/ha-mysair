"""Shared fixtures and helpers for the tests that do not need Home Assistant.

Inserts ``custom_components/mysair`` into sys.path so the pure modules
(status_parser, api, mqtt_handler) can be imported AS top-level modules,
i.e. without running the package ``__init__.py`` (which depends on
homeassistant). This works because those modules have no relative imports.
All fixture data is SANITISED (fictitious values, no secrets).
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "custom_components", "mysair"))
# Needed so the HA harness (Docker) can `import custom_components` as a
# namespace package and discover `custom_components/mysair`
# (see homeassistant.loader._get_custom_components).
sys.path.insert(0, _REPO_ROOT)

import pytest  # noqa: E402 (deliberate: needs the modified sys.path, see above)

try:
    import pytest_homeassistant_custom_component  # noqa: F401

    _HA_TEST_HARNESS_AVAILABLE = True
except ImportError:
    _HA_TEST_HARNESS_AVAILABLE = False

if _HA_TEST_HARNESS_AVAILABLE:
    # Only defined when the HA harness is installed (Docker, see
    # Dockerfile.test). When the hass() fixture is set up,
    # pytest-homeassistant-custom-component imports ITS OWN test
    # `custom_components` package (a regular package, not a namespace one) and
    # caches it in sys.modules with __path__ pointing only to that internal
    # folder. Since __path__ is a mutable list, our real folder is appended so
    # `homeassistant.loader` also finds custom_components/mysair.
    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(hass, enable_custom_integrations):
        import custom_components as _test_custom_components

        our_custom_components_dir = os.path.join(_REPO_ROOT, "custom_components")
        if our_custom_components_dir not in _test_custom_components.__path__:
            _test_custom_components.__path__.append(our_custom_components_dir)
        yield


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.text = text

    def json(self):
        return self._json


class FakeSession:
    """Fake requests session: returns queued responses per method and records calls.

    Usage:
        s = FakeSession()
        s.queue("post", FakeResponse(200, {...}))
        api = MySairAPI("e", "p", session=s)
        ...
        assert s.calls[-1]["json"] == [...]
    """

    def __init__(self):
        self.responses = {"get": [], "post": [], "put": []}
        self.calls = []

    def queue(self, method, *responses):
        self.responses[method].extend(responses)
        return self

    def _handle(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        queue = self.responses[method]
        if not queue:
            raise AssertionError(f"Sin respuesta encolada para {method.upper()} {url}")
        return queue.pop(0)

    def request(self, method, url, **kwargs):
        """Generic entry point used by MySairAPI._authed_request."""
        return self._handle(method.lower(), url, **kwargs)

    def get(self, url, **kwargs):
        return self._handle("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("post", url, **kwargs)

    def put(self, url, **kwargs):
        return self._handle("put", url, **kwargs)


@pytest.fixture
def make_response():
    def _make(status_code=200, json_data=None, text=""):
        return FakeResponse(status_code, json_data, text)

    return _make


@pytest.fixture
def fake_session():
    return FakeSession()


# --- Sanitised payloads (only fields the code actually consumes) ---


@pytest.fixture
def login_ok():
    return {"entity": {"access_token": "TEST_ACCESS", "refresh_token": "TEST_REFRESH"}}


@pytest.fixture
def aws_credentials_ok():
    return {
        "entity": {
            "aws_mqtt_host": "test.iot.eu-west-1.amazonaws.com",
            "aws_default_region": "eu-west-1",
            "aws_access_key_id": "TESTKEYID",
            "aws_secret_access_key": "TESTSECRET",
            "aws_security_token": "TESTTOKEN",
            "aws_mqtt_user": "web0000",
        }
    }


@pytest.fixture
def status_payload():
    """MQTT 'status' payload with value as a JSON string and trailing ';' (as in production).

    Fields (see docs/protocol-findings.md): e=power, m=mode (0=air heat),
    tr/tc/tmm/tmx=temperatures, c/f/v/s=capabilities.
    Humidity: "hum" reports "0" while "hm" holds the measured value
    (see status_parser.parse_humidity).
    """
    return {
        "ctl": "INST_A",
        "value": (
            '{"t":[{"rf":"DEV_1","n":"Salon","e":"1","m":"0",'
            '"tr":22.5,"tc":21.0,"tmm":10.0,"tmx":30.0,"hum":"0","hm":45,'
            '"vv":"0","c":"1","f":"1","v":"0","s":"0"}]};'
        ),
    }
