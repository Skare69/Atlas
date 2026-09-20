import json
import os
import secrets
import stat
from pathlib import Path

from src.paths import app_dir

BASE_DIR = app_dir()
config_file = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
SERVICE = "atlas"

KEYRING_AVAILABLE = False
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    pass


def _env_config():
    host = os.environ.get("ATLAS_NNTP_HOST")
    user = os.environ.get("ATLAS_NNTP_USER")
    pwd = os.environ.get("ATLAS_NNTP_PASS")
    port = os.environ.get("ATLAS_NNTP_PORT", "563")
    mode = os.environ.get("ATLAS_INDEX_MODE", "dynamic")

    if not all([host, user, pwd]):
        return None

    try:
        port = int(port)
    except ValueError:
        port = 563

    return {
        "host": host,
        "username": user,
        "password": pwd,
        "port": port,
        "group": "",
        "groups": [],
        "index_mode": mode,
    }


def load_config():
    env_cfg = _env_config()
    if env_cfg:
        return env_cfg

    if not os.path.exists(config_file):
        return None

    try:
        with open(config_file, "r") as f:
            config = json.load(f)

    except (OSError, json.JSONDecodeError):
        return None

    if KEYRING_AVAILABLE:
        try:
            password = keyring.get_password(SERVICE, config.get("username", ""))
        except keyring.errors.KeyringError:
            password = None

        if password:
            config["password"] = password

    if not config.get("groups") and config.get("group"):
        config["groups"] = [config["group"]]

    return config


def save_config(host, username, password, port, group, index_mode="dynamic", groups=None):
    store_password = True

    if KEYRING_AVAILABLE:
        try:
            keyring.set_password(SERVICE, username, password)
            store_password = False
        except keyring.errors.KeyringError:
            store_password = True

    if groups is None:
        groups = [group] if group else []

    #merge over the raw file so api keys/settings added later survive a rewrite
    config = _raw_config()
    config.update({
        "host": host,
        "username": username,
        "port": port,
        "group": group,
        "groups": [g for g in groups if g],
        "index_mode": index_mode
    })

    if store_password:
        config["password"] = password

    config_file.parent.mkdir(parents = True, exist_ok = True)

    tmp = config_file.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=4)

    if store_password:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)

    os.replace(tmp, config_file)


#raw read straight from disk, NOT load_config (env override would hide stored keys)
def _raw_config():
    try:
        with open(config_file, "r") as f:
            return json.load(f)

    except (OSError, json.JSONDecodeError):
        return {}


def update_config(**kw):
    #merge into whatever is on disk so unrelated keys (incl stored password) survive
    config = _raw_config()
    config.update(kw)

    config_file.parent.mkdir(parents = True, exist_ok = True)

    tmp = config_file.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=4)

    os.replace(tmp, config_file)


def get_api_key():
    env = os.environ.get("ATLAS_API_KEY")
    if env:
        return env

    stored = _raw_config().get("apikey")
    if stored:
        return stored

    key = secrets.token_hex(16)
    update_config(apikey = key)
    return key


def rotate_api_key():
    key = secrets.token_hex(16)
    update_config(apikey = key)
    return key


def api_settings():
    raw = _raw_config()

    host = os.environ.get("ATLAS_API_HOST", "0.0.0.0")

    port = os.environ.get("ATLAS_API_PORT")
    if port is None:
        port = raw.get("api_port")

    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 8085

    env_enabled = os.environ.get("ATLAS_API_ENABLED")
    if env_enabled is not None:
        enabled = env_enabled.strip().lower() in ("1", "true")
    else:
        enabled = bool(raw.get("api_enabled", False))

    return {
        "enabled": enabled,
        "port": port,
        "key": get_api_key(),
        "host": host,
    }