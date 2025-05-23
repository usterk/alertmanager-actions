#!/usr/bin/env python

import subprocess  # nosec
import sys
from os import environ
from threading import Timer

import yaml
from flask import jsonify, request
from pyms.flask.app import Microservice
from prometheus_client import Counter


class AlertmanagerActions:
    def __init__(self):
        """
        Initialize the flask endpoint and launch the function that will throw
        the threads that will update the metrics.
        """
        self.lock = {}
        self.app = Microservice().create_app()
        self.logger = self.app.logger
        self.read_config()
        self.serve_endpoints()

    def read_config(self):
        """
        Read configuration from yaml file.
        """
        if "ALERTMANAGER_ACTIONS_CONFIG" in environ:
            path = environ["ALERTMANAGER_ACTIONS_CONFIG"]
        else:
            path = "config.yml"  # pragma: no cover
        log = "Reading configuration file in path: %s" % (path)
        self.logger.debug(log)
        raw_config = []
        try:
            raw_config = yaml.safe_load(open(path))["alertmanager_actions"]
        except Exception as error:
            log = "There was an error loading the file: %s" % (error)
            self.logger.error(log)
            sys.exit(1)

        processed_config = []
        for action_config in raw_config:
            new_action = {}
            action_name = action_config.get("name")
            if not action_name:
                self.logger.error("Action 'name' is missing in config: %s", action_config)
                sys.exit(1)
            new_action["name"] = action_name

            if "labels" not in action_config:
                log = "Action '%s' is missing 'labels' key. Configuration: %s"
                self.logger.error(log, action_name, action_config)
                sys.exit(1)
            new_action["labels"] = action_config["labels"]

            has_command_defined = False

            # Process on_firing
            if "on_firing" in action_config:
                if not isinstance(action_config["on_firing"], dict):
                    log = "Action '%s': 'on_firing' must be a dictionary. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                if "command" not in action_config["on_firing"]:
                    log = "Action '%s': 'on_firing' is missing 'command' key. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                if not isinstance(action_config["on_firing"]["command"], list) or \
                   not all(isinstance(cmd, str) for cmd in action_config["on_firing"]["command"]):
                    log = "Action '%s': 'on_firing.command' must be a list of strings. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                new_action["on_firing"] = {
                    "command": action_config["on_firing"]["command"],
                    "timeout": action_config["on_firing"].get("timeout", 180)
                }
                if not isinstance(new_action["on_firing"]["timeout"], int):
                    log = "Action '%s': 'on_firing.timeout' must be an integer. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                has_command_defined = True
            # Backward compatibility: old 'command' becomes 'on_firing' if 'on_firing' not present
            elif "command" in action_config:
                if not isinstance(action_config["command"], list) or \
                   not all(isinstance(cmd, str) for cmd in action_config["command"]):
                    log = "Action '%s': legacy 'command' must be a list of strings. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                new_action["on_firing"] = {
                    "command": action_config["command"],
                    "timeout": action_config.get("timeout", 180) # Old global timeout
                }
                if not isinstance(new_action["on_firing"]["timeout"], int):
                    log = "Action '%s': legacy 'timeout' must be an integer. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                has_command_defined = True

            # Process on_resolved
            if "on_resolved" in action_config:
                if not isinstance(action_config["on_resolved"], dict):
                    log = "Action '%s': 'on_resolved' must be a dictionary. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                if "command" not in action_config["on_resolved"]:
                    log = "Action '%s': 'on_resolved' is missing 'command' key. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                if not isinstance(action_config["on_resolved"]["command"], list) or \
                   not all(isinstance(cmd, str) for cmd in action_config["on_resolved"]["command"]):
                    log = "Action '%s': 'on_resolved.command' must be a list of strings. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                new_action["on_resolved"] = {
                    "command": action_config["on_resolved"]["command"],
                    "timeout": action_config["on_resolved"].get("timeout", 180)
                }
                if not isinstance(new_action["on_resolved"]["timeout"], int):
                    log = "Action '%s': 'on_resolved.timeout' must be an integer. Configuration: %s"
                    self.logger.error(log, action_name, action_config)
                    sys.exit(1)
                has_command_defined = True
            
            if not has_command_defined:
                log = "Action '%s' must define at least one command ('on_firing', 'on_resolved', or legacy 'command'). Configuration: %s"
                self.logger.error(log, action_name, action_config)
                sys.exit(1)

            processed_config.append(new_action)

        # Initialize locks and metrics
        for action in processed_config:
            self.lock[action["name"]] = False
            try:
                # Ensure labels is a dictionary before accessing .keys()
                labels_dict = action.get("labels", {})
                if not isinstance(labels_dict, dict):
                    self.logger.error("Labels for action '%s' is not a dictionary. Found: %s", action["name"], labels_dict)
                    # Handle error appropriately, maybe skip this action or sys.exit
                    # For now, using an empty list for label keys to avoid crash
                    label_keys = []
                else:
                    label_keys = list(labels_dict.keys())

                self.counter = Counter(
                    "alertmanager_actions_executions",
                    "Number of alertmanager actions executions",
                    ["action", "state", *label_keys],
                )
            except ValueError: # Metric already exists
                pass
            except TypeError as e: # Handles cases where labels_dict.keys() might not be directly usable if not a dict
                self.logger.error("Error initializing metrics for action '%s' due to labels format: %s. Error: %s", action["name"], action.get("labels"), e)
                # Decide on error handling: skip, default, or exit
                # Using an empty list for label keys as a fallback
                label_keys = []
                try: # Retry counter initialization with empty label keys if problematic
                    self.counter = Counter(
                        "alertmanager_actions_executions",
                        "Number of alertmanager actions executions",
                        ["action", "state", *label_keys], # Use corrected label_keys
                    )
                except ValueError: # Metric already exists
                    pass


        log = "Processed configuration: %s" % (processed_config)
        self.logger.debug(log)
        self.config = processed_config

    def launch_action(self):
        treated_actions = []
        if not request.content_type:
            self.logger.warning(
                "The received content type should be 'application/json'."
            )
        valid = self._check_valid_request(request)
        if not valid:
            return "KO"
        self.logger.debug("Received request: %s" % request.json)
        alerts = request.json["alerts"]
        for alert in alerts:
            status = alert.get("status", "unknown")
            if status == "unknown":
                self.logger.warning("Alert status missing, defaulting to 'unknown': %s" % alert)
            else:
                self.logger.debug("Processing alert with status: %s" % status)

            received_labels = alert["labels"]
            for action in self.config:
                self.logger.debug("Action: %s" % action)
                labels = action["labels"]
                self.logger.debug("Received label: %s" % received_labels)
                # Proceed only if action's labels are in received labels
                if received_labels.items() >= labels.items():
                    self.logger.info(
                        "Action '%s' matched for alert with status '%s'", action["name"], status
                    )

                    command_to_execute = None
                    timeout_for_command = None
                    action_type_log = ""

                    if status == "firing":
                        if action.get("on_firing") and action["on_firing"].get("command"):
                            command_to_execute = action["on_firing"]["command"]
                            timeout_for_command = action["on_firing"]["timeout"]
                            action_type_log = "on_firing"
                            self.logger.info(
                                "Selected 'on_firing' command for action '%s'.", action["name"]
                            )
                        else:
                            self.logger.info(
                                "No 'on_firing' command configured for action '%s' and status 'firing'.", action["name"]
                            )
                    elif status == "resolved":
                        if action.get("on_resolved") and action["on_resolved"].get("command"):
                            command_to_execute = action["on_resolved"]["command"]
                            timeout_for_command = action["on_resolved"]["timeout"]
                            action_type_log = "on_resolved"
                            self.logger.info(
                                "Selected 'on_resolved' command for action '%s'.", action["name"]
                            )
                        else:
                            self.logger.info(
                                "No 'on_resolved' command configured for action '%s' and status 'resolved'.", action["name"]
                            )
                    else:
                        self.logger.info(
                            "Alert status is '%s'. No command will be executed for action '%s' as status is not 'firing' or 'resolved'.",
                            status,
                            action["name"],
                        )

                    if command_to_execute:
                        try:
                            # Lock and treat action apply to the action name, regardless of status-specific command
                            locked = self._lock_action(action["name"])
                            if locked:
                                # Logged in _lock_action. If it's locked, we might not want to proceed with other actions for this alert.
                                # Current behavior is to return "KO" for the whole request.
                                # This might be too aggressive if an alert matches multiple actions and only one is locked.
                                # For now, maintaining existing behavior.
                                return "KO" 

                            treated_actions, treated = self._treat_action(
                                action["name"], treated_actions
                            )
                            if treated:
                                self.logger.debug(
                                    "Action '%s' already treated in this request batch. Command for '%s' (%s) not executed again.",
                                    action["name"], status, action_type_log
                                )
                                self._unlock_action(action["name"]) # Unlock if treated but not executing
                                continue # Move to the next action for the current alert

                            self.logger.info(
                                "Executing '%s' command for action '%s', status '%s'.", action_type_log, action["name"], status
                            )
                            self._execute_command(
                                command_to_execute,
                                received_labels.items(), # For environment variables
                                labels.items(),          # For metrics (original labels from config)
                                action["name"],
                                timeout_for_command,
                            )
                            self._unlock_action(action["name"])
                        except Exception as err:
                            self.logger.error("Error during %s action execution for '%s': %s", action_type_log, action["name"], err)
                            self._unlock_action(action["name"]) # Ensure unlock on error
                    # else: No command was selected, appropriate logging already done.
                # else: Label match failed, continue to next action or alert.
        return "OK"

    def _execute_command(self, command, received_labels, config_labels, action_name, timeout):
        # Make available all labels through environmental variables
        env = environ.copy()
        for k, v in received_labels:
            env[k.upper()] = v
        # Join the list of commands to one line with a ; separator
        cmd = ";".join([x for x in command])
        self.logger.debug("Command: %s" % cmd)
        # Timeout for command
        kill = lambda process: process.kill()
        # TODO The command is executed in a very untrustful way
        command = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,  # nosec
            env=env,
        )
        my_timer = Timer(timeout, kill, [command])
        # Treat command output with timeout
        try:
            my_timer.start()
            stdout, stderr = command.communicate()
        finally:
            my_timer.cancel()
        self.logger.debug("Command output: %s" % stdout.decode(encoding="UTF-8"))
        if stderr:
            self.logger.error("Error: %s" % stderr)
        return_code = command.returncode
        # Only contemplate correct or incorrect executions
        if return_code != 0:
            return_code = 1

        labels_values = [v for k, v in config_labels]
        self.counter.labels(action_name, return_code, *labels_values).inc()

    def _treat_action(self, action_name, treated_actions):
        # Proceed only if the action hasn't been treated in the same request
        # AKA alerts deduplication
        if action_name in treated_actions:
            self.logger.debug(
                "Action already treated, so the command won't be executed"
            )
            return treated_actions, True
        treated_actions.append(action_name)
        return treated_actions, False

    def _lock_action(self, action_name):
        # This prevents the action to be executed if shortly after receiving
        # the alert but before executing, the same action is received
        if self.lock[action_name]:
            self.logger.debug(
                "The lock for '%s' is active, so the command won't be executed"
                % action_name
            )
            return True
        self.lock[action_name] = True
        self.logger.debug("The lock for '%s' is activated" % action_name)
        return False

    def _unlock_action(self, action_name):
        self.lock[action_name] = False
        self.logger.debug("The lock for '%s' is deactivated" % action_name)

    def _check_valid_request(self, request):
        if "alerts" not in request.json:
            self.logger.debug("Invalid request: %s" % request.json)
            return False
        if type(request.json["alerts"]) is not list:
            self.logger.debug("Invalid request: %s" % request.json)
            return False
        return True

    def serve_endpoints(self):
        """
        Main method to serve the metrics. It's used mainly to get the self
        parameter and pass it to the next function.
        """

        @self.app.route("/", methods=["POST"])
        def root():
            """
            Exposes a blank html page with a link to the metrics.
            """
            state = self.launch_action()
            return jsonify(message=state)

        @self.app.route("/-/reload")
        def reload():
            """
            Stops the threads and restarts them.
            """
            self.logger.info("Reloading configuration")
            self.read_config()
            self.logger.info("Configuration reloaded")
            return jsonify(message="OK")

    def run_webserver(self):
        "Start the web application."
        self.app.run(port="8080", host="0.0.0.0", use_reloader=False)  # nosec
