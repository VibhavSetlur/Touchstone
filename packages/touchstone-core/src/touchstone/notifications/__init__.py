"""Notification senders.

CRITICAL: The AI assistant calls `send(channel_name, message)` — never
`send(webhook_url, message)`. Channel-to-webhook resolution happens in
config, behind the trust boundary. The AI cannot exfiltrate by passing
a malicious URL.

Channels are configured in `touchstone.toml`:

    [notifications.channels.data-team-alerts]
    kind = "slack"
    webhook_ref = "vault://touchstone/slack/data-team-alerts"

    [notifications.channels.qa-bot-room]
    kind = "teams"
    webhook_ref = "env://TEAMS_QA_WEBHOOK"

    [notifications.channels.security-on-call]
    kind = "email"
    to_ref = "env://SECURITY_ONCALL_LIST"
    smtp_host = "smtp.acme.com"
    smtp_user_ref = "env://SMTP_USER"
    smtp_pass_ref = "vault://acme/smtp/pass"
"""

from touchstone.notifications.sender import (
    Channel,
    Notifier,
    SlackChannel,
    TeamsChannel,
    EmailChannel,
)

__all__ = ["Channel", "Notifier", "SlackChannel", "TeamsChannel", "EmailChannel"]
