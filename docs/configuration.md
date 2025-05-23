# Configuration

Alertmanager Actions is an Alertmanager Receiver. The first step to use it would
be deploy it.

## Deploy Alertmanager Actions
### Kubernetes
There's a [helm
chart](https://github.com/little-angry-clouds/charts/tree/master/alertmanager-actions)
with some features. It has secrets and pre-installing tools support. Say you'd
want to ssh to a machine to restart an nginx service when the alert `NginxDown`
bumps. You'd define a `values.yml` like the next:

```yaml
preInstall:
  enabled: true
  value: |
    apt update; apt install -y openssh-client

secrets:
  enabled: true
  values:
  - key: ssh-key
    value: SGksIHlvdSBjdXJpb3VzIHBlcnNvbiA6KQ==

config:
  alertmanager_actions: |
    - name: RestartNginx
      labels:
        alertname: NginxDown
        action: restart
      command: # This is the legacy way, treated as 'on_firing'
        - ssh -o StrictHostKeyChecking=no -i /secrets/ssh-key ec2-user@$PRIVATE_IP sudo systemctl restart nginx
```

There's some stuff going here. First of all, pre-installing stuff. There's to
ways of doing so, using a custom image with the Alertmanager or using the
`preInstall` feature. Using the feature is pretty easy, you just activate the
feature and then install what you need in the container. In the example, since
we want to ssh to a box and restart a service, we only will need `ssh`.

Next, the secrets. It's possible to define a list of secrets. The `value` must be
base64 encoded. The `key` will be the name of the file containing the secret.
All secrets are mounted in the cointanier in `/secrets/`. So, the defined secret
will be in a file in the container in `/secrets/ssh-key`.

The last part, the actions. In the example there's defined an action called
`NginxDown`. When an alert matching its `labels` is received, a command is executed.
The command is pretty plain, but it has something special. The environmental variable
`$PRIVATE_IP`. This is one of the strongest features, all alert labels are passed as
environmental variables to the command. In the example, it's assumed that the
triggered alert has one label called `$PRIVATE_IP` that contains the IP that can
be used to reach the service.

This example uses the legacy `command:` field. For more granular control based on
alert status, see the "Status-Aware Actions" section below.

### Status-Aware Actions (Firing and Resolved)

You can configure different commands and timeouts based on whether an alert is 'firing'
or 'resolved'. This is done using the `on_firing` and `on_resolved` directives
within an action block.

Each of these directives (`on_firing`, `on_resolved`) can contain:
- `command`: A list of strings representing the command(s) to execute. This is mandatory if the directive (`on_firing` or `on_resolved`) is used.
- `timeout`: An optional integer specifying the timeout in seconds for the command. If not provided, it defaults to 180 seconds.

Here's an example:

```yaml
alertmanager_actions:
  - name: ExampleStatusAction
    labels:
      alertname: MyTestAlert
      service: MyService
    on_firing:
      command:
        - echo "ALERT MyTestAlert for MyService is FIRING!"
        - echo "Instance: $INSTANCE, Severity: $SEVERITY" # Example of using env vars
      timeout: 120 # Optional timeout for firing commands
    on_resolved:
      command:
        - echo "ALERT MyTestAlert for MyService is RESOLVED!"
        - echo "Instance: $INSTANCE, Severity: $SEVERITY has cleared."
      timeout: 60 # Optional timeout for resolved commands
  
  - name: FiringOnlyExample
    labels:
      alertname: AnotherAlert
    on_firing:
      command: ["/usr/local/bin/handle_another_alert_firing.sh"]
      # timeout will default to 180s

  - name: OldStyleForComparison
    labels:
      alertname: LegacyAlert
    command: # This will be treated as on_firing
      - echo "LegacyAlert FIRING (using old command field)"
    # timeout, if specified here, would also apply to the on_firing command. Otherwise, defaults to 180s.
```

**Backward Compatibility:**
- If `on_firing` is **not** defined for an action, but a top-level `command` field exists, that top-level `command` and its associated top-level `timeout` (if any) will be used for 'firing' alerts.
- If `on_firing` **is** defined, it takes precedence over any top-level `command` field for 'firing' alerts.
- The `on_resolved` directive only works if explicitly defined. There is no fallback to the top-level `command` for resolved alerts.

**Important Note for Resolved Alerts:**
For `alertmanager-actions` to receive and act upon 'resolved' notifications, Alertmanager itself must be configured to send them. Ensure `send_resolved: true` is set in your Alertmanager webhook configuration that points to `alertmanager-actions`. Example:

```yaml
receivers:
- name: 'alertmanager-actions-webhook'
  webhook_configs:
  - url: 'http://<alertmanager-actions-host>:<port>/'
    send_resolved: true # This is crucial
```

### Plain box
TBD

## Configure the Alertmanager
The only part explained will be the one related to the receivers configuration.
So the next pieces of code won't work as is, you'll need a working prometheus +
alertmanager installation.

In this section there will be explained how to configure the route and the
receiver. The first one will be the route:

```yaml
route:
  routes:
  - receiver: alertmanager-actions
    match:
      actions: 'true'
```

This means that all alerts that the Alertmanager receive with the label
`actions` with the value `true` will be redirected to the `alertmanager-actions`
receiver.

The receiver itself is a webhook, its configuration it's pretty simple:

```yaml
receivers:
- name: alertmanager-actions
  webhook_configs:
  - send_resolved: true # Set this to true to enable on_resolved actions
    url: "http://alertmanager-actions/" # Adjust URL as necessary
```

## Configure the alert
Again, the only part explained will be the one related to alert configuration.
So the next pieces of code won't work as is, you'll need a working prometheus +
alertmanager installation.

The next section is the configuration of an alert:

```yaml
groups:
- name: ipsec.rules
  rules:
  - alert: RestartNginx
    expr: nginx_down == 0
    labels:
      action: restart
    annotations:
      description: The proxy is down, will try to restart it automatically.
    for: 30s
```
