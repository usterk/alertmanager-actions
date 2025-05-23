import unittest.mock
import project.app
import os
import pytest
from flask import Flask


@pytest.fixture()
def setup():
    # Create a real Flask app instance to be used by AlertmanagerActions
    test_flask_app = Flask("test_app_for_fixture")
    # Add a dummy config if necessary for app.logger or other Flask functionalities
    # test_flask_app.config['TESTING'] = True

    # Patch project.app.Microservice
    mock_ms_class_patcher = unittest.mock.patch("project.app.Microservice")
    mock_ms_class = mock_ms_class_patcher.start()
    
    # Configure the instance returned by Microservice()
    mock_ms_instance = mock_ms_class.return_value
    # Configure its create_app() method to return our real Flask app
    mock_ms_instance.create_app.return_value = test_flask_app

    # Patch project.app.Counter
    mock_counter_patcher = unittest.mock.patch("project.app.Counter")
    mock_counter_class = mock_counter_patcher.start()
    # mock_counter_instance = mock_counter_class.return_value # If needed

    yield {
        "app": test_flask_app,
        "mock_ms_class": mock_ms_class,
        "mock_ms_instance": mock_ms_instance,
        "mock_counter_class": mock_counter_class
    } # Yield the app and mocks if tests need to interact with them

    # Stop the patchers
    mock_ms_class_patcher.stop()
    mock_counter_patcher.stop()


@pytest.mark.parametrize("case,number_exit_calls", [("ok", 0), ("ko", 1), ("nope", 1)])
@unittest.mock.patch("sys.exit")
def test_read_config(exit_calls, case, number_exit_calls, setup):
    os.environ["ALERTMANAGER_ACTIONS_CONFIG"] = "tests/config-" + case + ".yml"
    project.app.AlertmanagerActions()
    assert exit_calls.call_count == number_exit_calls


@pytest.mark.parametrize(
    "test_name,alerts,lock,command_executions,result",
    [
        (
            "all good",
            {"alerts": [{"labels": {"alertname": "TestActions", "test": "yes"}}]},
            False,
            1,
            "OK",
        ),
        (
            "lock active",
            {"alerts": [{"labels": {"alertname": "TestActions", "test": "yes"}}]},
            True,
            0,
            "KO",
        ),
        ("request without alerts", {"example": "fail"}, False, 0, "KO"),
        (
            "request without labels as list",
            {"alerts": {"labels": {"alertname": "TestActions", "test": "yes"}}},
            False,
            0,
            "KO",
        ),
    ],
)
@unittest.mock.patch("subprocess.Popen")
def test_launch_action_local(
    popen, test_name, alerts, lock, command_executions, result, setup
):
    os.environ["ALERTMANAGER_ACTIONS_CONFIG"] = "tests/config-ok.yml"
    popen.return_value.communicate.return_value = (b"equilibry", None)
    alertmanager_actions = project.app.AlertmanagerActions()
    # Ensure the app context is available for the test client
    with alertmanager_actions.app.app_context():
        client = alertmanager_actions.app.test_client()
        alertmanager_actions.lock["TestActions"] = lock
        response = client.post('/', json=alerts)
        assert response.json['message'] == result
    assert popen.call_count == command_executions
