import unittest.mock
import project.app
import os
import pytest
import yaml
import sys
from threading import Timer

# --- Base Fixtures & Configs ---

BASE_PYMS_CONFIG = {
    "pyms": {
        "services": {"metrics": True, "tracer": {"enabled": False}},
        "config": {"debug": True, "app_name": "alertmanager-actions-test"},
    }
}


def get_yaml_config(actions_list):
    return yaml.dump({**BASE_PYMS_CONFIG, "alertmanager_actions": actions_list})


@pytest.fixture
def mock_dependencies(monkeypatch):
    """Mocks core dependencies like Microservice, Counter, Popen, Timer, sys.exit, open, yaml.safe_load"""
    ms_mock = unittest.mock.MagicMock()
    monkeypatch.setattr("project.app.Microservice", ms_mock)

    counter_mock = unittest.mock.MagicMock()
    monkeypatch.setattr("project.app.Counter", counter_mock)

    popen_mock = unittest.mock.MagicMock()
    popen_mock.return_value.communicate.return_value = (b"stdout", b"stderr")
    popen_mock.return_value.returncode = 0
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    timer_mock = unittest.mock.MagicMock()
    monkeypatch.setattr("threading.Timer", timer_mock)
    
    exit_mock = unittest.mock.MagicMock()
    monkeypatch.setattr("sys.exit", exit_mock)

    # Mock open and yaml.safe_load for config reading
    mock_file = unittest.mock.mock_open()
    monkeypatch.setattr("builtins.open", mock_file)
    
    yaml_load_mock = unittest.mock.MagicMock()
    monkeypatch.setattr("yaml.safe_load", yaml_load_mock)

    return {
        "ms": ms_mock,
        "counter": counter_mock,
        "popen": popen_mock,
        "timer": timer_mock,
        "exit": exit_mock,
        "mock_file": mock_file,
        "yaml_load": yaml_load_mock,
    }


# --- Config Strings for Tests ---

CONFIG_FULL = get_yaml_config([
    {
        "name": "FullAction",
        "labels": {"alertname": "TestAlert", "severity": "critical"},
        "on_firing": {"command": ["echo 'firing'", "hostname"], "timeout": 100},
        "on_resolved": {"command": ["echo 'resolved'"], "timeout": 50},
    }
])

CONFIG_FIRING_ONLY = get_yaml_config([
    {
        "name": "FiringOnlyAction",
        "labels": {"alertname": "TestAlert"},
        "on_firing": {"command": ["echo 'firing_only'"], "timeout": 120},
    }
])

CONFIG_RESOLVED_ONLY = get_yaml_config([
    {
        "name": "ResolvedOnlyAction",
        "labels": {"alertname": "TestAlert"},
        "on_resolved": {"command": ["echo 'resolved_only'"], "timeout": 60},
    }
])

CONFIG_OLD_SYNTAX = get_yaml_config([
    {
        "name": "OldSyntaxAction",
        "labels": {"alertname": "TestAlert"},
        "command": ["echo 'old_syntax_firing'"], # Implicitly on_firing
        "timeout": 180 # Old global timeout
    }
])

CONFIG_NON_MATCHING_LABELS = get_yaml_config([
    {
        "name": "NonMatchingAction",
        "labels": {"alertname": "NoMatchForThis"},
        "on_firing": {"command": ["echo 'no_match_firing'"]},
    }
])

CONFIG_DEFAULT_TIMEOUT = get_yaml_config([
    {
        "name": "DefaultTimeoutAction",
        "labels": {"alertname": "TestAlert"},
        "on_firing": {"command": ["echo 'default_timeout_firing'"]},
        # on_resolved also uses default if specified without timeout
    }
])


# --- Tests for read_config ---

@pytest.mark.parametrize(
    "config_name, config_str, expected_exit_code, expected_error_log",
    [
        ("valid_full", CONFIG_FULL, None, None),
        ("valid_firing_only", CONFIG_FIRING_ONLY, None, None),
        ("valid_resolved_only", CONFIG_RESOLVED_ONLY, None, None),
        ("valid_old_syntax", CONFIG_OLD_SYNTAX, None, None),
        ("valid_default_timeout", CONFIG_DEFAULT_TIMEOUT, None, None),
        (
            "invalid_on_firing_not_dict",
            get_yaml_config([{"name": "ErrAction", "labels": {}, "on_firing": "string"}]),
            1,
            "must be a dictionary",
        ),
        (
            "invalid_on_firing_no_command",
            get_yaml_config([{"name": "ErrAction", "labels": {}, "on_firing": {"timeout": 10}}]),
            1,
            "is missing 'command' key",
        ),
        (
            "invalid_on_firing_command_not_list",
            get_yaml_config([{"name": "ErrAction", "labels": {}, "on_firing": {"command": "string"}}]),
            1,
            "must be a list of strings",
        ),
         (
            "invalid_on_firing_timeout_not_int",
            get_yaml_config([{"name": "ErrAction", "labels": {}, "on_firing": {"command": ["echo"], "timeout": "string"}}]),
            1,
            "must be an integer",
        ),
        (
            "invalid_no_labels",
            get_yaml_config([{"name": "ErrAction", "on_firing": {"command": ["echo"]}}]),
            1,
            "is missing 'labels' key",
        ),
        (
            "invalid_no_command_whatsoever",
            get_yaml_config([{"name": "ErrAction", "labels": {}}]),
            1,
            "must define at least one command",
        ),
    ],
)
def test_read_config(mock_dependencies, config_name, config_str, expected_exit_code, expected_error_log):
    os.environ["ALERTMANAGER_ACTIONS_CONFIG"] = "dummy_path.yml" # Path is used for logging, content from mock
    mock_dependencies["yaml_load"].return_value = yaml.safe_load(config_str)
    
    logger_error_mock = unittest.mock.MagicMock()
    mock_dependencies["ms"].return_value.create_app.return_value.logger.error = logger_error_mock

    if expected_exit_code is not None:
        project.app.AlertmanagerActions().read_config() # Calling directly as __init__ calls it
        mock_dependencies["exit"].assert_called_with(expected_exit_code)
        if expected_error_log:
            assert any(expected_error_log in call.args[0] for call in logger_error_mock.call_args_list)
    else:
        # Should not exit for valid configs
        aa = project.app.AlertmanagerActions() 
        # Basic check: was config parsed?
        assert len(aa.config) > 0 
        mock_dependencies["exit"].assert_not_called()


# --- Tests for launch_action ---

def _create_alert_json(status, alertname="TestAlert", extra_labels=None):
    labels = {"alertname": alertname, "instance": "localhost", "job": "testjob"}
    if extra_labels:
        labels.update(extra_labels)
    return {"alerts": [{"status": status, "labels": labels}]}


@pytest.mark.parametrize(
    "config_str, action_name_in_config, alert_json, expected_command_substr, expected_timeout, expected_env_keys, expect_execution",
    [
        # Firing Alert
        (CONFIG_FULL, "FullAction", _create_alert_json("firing"), "echo 'firing'", 100, ["ALERTNAME", "SEVERITY"], True),
        (CONFIG_RESOLVED_ONLY, "ResolvedOnlyAction", _create_alert_json("firing"), None, None, None, False), # Firing to resolved-only config
        (CONFIG_FIRING_ONLY, "FiringOnlyAction", _create_alert_json("firing"), "echo 'firing_only'", 120, ["ALERTNAME"], True),
        
        # Resolved Alert
        (CONFIG_FULL, "FullAction", _create_alert_json("resolved"), "echo 'resolved'", 50, ["ALERTNAME", "SEVERITY"], True),
        (CONFIG_FIRING_ONLY, "FiringOnlyAction", _create_alert_json("resolved"), None, None, None, False), # Resolved to firing-only config
        (CONFIG_RESOLVED_ONLY, "ResolvedOnlyAction", _create_alert_json("resolved"), "echo 'resolved_only'", 60, ["ALERTNAME"], True),

        # Backward Compatibility
        (CONFIG_OLD_SYNTAX, "OldSyntaxAction", _create_alert_json("firing"), "echo 'old_syntax_firing'", 180, ["ALERTNAME"], True),
        (CONFIG_OLD_SYNTAX, "OldSyntaxAction", _create_alert_json("resolved"), None, None, None, False), # Resolved to old syntax (no on_resolved)

        # Label Non-Match
        (CONFIG_FULL, "FullAction", _create_alert_json("firing", alertname="WrongAlert"), None, None, None, False),
        (CONFIG_NON_MATCHING_LABELS, "NonMatchingAction", _create_alert_json("firing"), None, None, None, False),

        # Status Mismatch (e.g., 'pending')
        (CONFIG_FULL, "FullAction", _create_alert_json("pending"), None, None, None, False),

        # Default Timeout
        (CONFIG_DEFAULT_TIMEOUT, "DefaultTimeoutAction", _create_alert_json("firing"), "echo 'default_timeout_firing'", 180, ["ALERTNAME"], True), # Default is 180
        
        # Check env vars more thoroughly for one case
        (CONFIG_FULL, "FullAction", _create_alert_json("firing", extra_labels={"severity": "critical", "host": "server1"}), 
         "echo 'firing'", 100, ["ALERTNAME", "SEVERITY", "HOST", "INSTANCE", "JOB"], True),
    ]
)
def test_launch_action_scenarios(
    mock_dependencies, config_str, action_name_in_config, alert_json, 
    expected_command_substr, expected_timeout, expected_env_keys, expect_execution
):
    os.environ["ALERTMANAGER_ACTIONS_CONFIG"] = "dummy_path.yml"
    mock_dependencies["yaml_load"].return_value = yaml.safe_load(config_str)

    aa = project.app.AlertmanagerActions()
    
    # Mock Flask request context
    with aa.app.test_request_context(json=alert_json, content_type="application/json"):
        response = aa.launch_action()

    if expect_execution:
        assert response == "OK"
        mock_dependencies["popen"].assert_called_once()
        call_args = mock_dependencies["popen"].call_args
        
        # Check command
        assert isinstance(call_args[0][0], str) # Joined command string
        assert expected_command_substr in call_args[0][0]
        
        # Check timeout via Timer mock
        mock_dependencies["timer"].assert_called_once_with(expected_timeout, unittest.mock.ANY, [mock_dependencies["popen"].return_value])
        
        # Check environment variables
        passed_env = call_args[1].get("env", {})
        for key in expected_env_keys:
            assert key.upper() in passed_env # Env vars are uppercased
            # Check if alert label value matches env var value
            # Alert labels are in alert_json['alerts'][0]['labels']
            # Need to handle case variations and potential prefix if any
            alert_label_key_lower = key.lower()
            if alert_label_key_lower in alert_json["alerts"][0]["labels"]:
                 assert passed_env[key.upper()] == alert_json["alerts"][0]["labels"][alert_label_key_lower]

    else:
        # "KO" can be returned if action is locked, or if request is invalid.
        # For these tests, we expect "OK" if no command is run due to non-match/status,
        # as the request itself is valid and no lock is pre-set.
        assert response == "OK" 
        mock_dependencies["popen"].assert_not_called()
        mock_dependencies["timer"].assert_not_called()

# Test for lock behavior (adapted from old tests)
@unittest.mock.patch("project.app.request") # Keep this specific mock for this test
def test_launch_action_lock_active(mock_request, mock_dependencies):
    os.environ["ALERTMANAGER_ACTIONS_CONFIG"] = "dummy_path.yml"
    # Use a simple config for this test
    mock_dependencies["yaml_load"].return_value = yaml.safe_load(CONFIG_FIRING_ONLY)

    alert_data = _create_alert_json("firing", alertname="TestAlert") # Matches FiringOnlyAction
    mock_request.json = alert_data
    mock_request.content_type = "application/json"
    
    aa = project.app.AlertmanagerActions()
    action_name_to_lock = "FiringOnlyAction" # Name from CONFIG_FIRING_ONLY
    aa.lock[action_name_to_lock] = True # Activate the lock

    response = aa.launch_action()

    assert response == "KO" # Expect KO because the action is locked
    mock_dependencies["popen"].assert_not_called() # No command should execute


# Test for invalid request structure (adapted from old tests)
@unittest.mock.patch("project.app.request")
def test_launch_action_invalid_request(mock_request, mock_dependencies):
    os.environ["ALERTMANAGER_ACTIONS_CONFIG"] = "dummy_path.yml"
    mock_dependencies["yaml_load"].return_value = yaml.safe_load(CONFIG_FIRING_ONLY) # Any valid config

    # Examples of invalid requests
    invalid_alerts = [
        {"example": "fail"}, # 'alerts' key missing
        {"alerts": {"labels": {"alertname": "TestActions"}}}, # 'alerts' is not a list
        {"alerts": [{}]}, # Alert object missing 'labels'
        {"alerts": [{"labels": "not_a_dict"}]} # labels is not a dict
    ]

    aa = project.app.AlertmanagerActions()
    for data in invalid_alerts:
        mock_request.json = data
        mock_request.content_type = "application/json" # Assume content type is fine
        
        # Reset mocks for Popen and Timer for each invalid call if necessary, though they shouldn't be called.
        mock_dependencies["popen"].reset_mock()
        mock_dependencies["timer"].reset_mock()

        response = aa.launch_action()
        assert response == "KO"
        mock_dependencies["popen"].assert_not_called()
        mock_dependencies["timer"].assert_not_called()

# TODO: Add tests for _check_valid_request directly if complex logic there needs it.
# TODO: Add tests for metric counter calls if specific values are important.

print("Successfully parsed test_main.py")
