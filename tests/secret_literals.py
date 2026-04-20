from __future__ import annotations

from typing import Any

AWS_ACCESS_KEY_ID = "AKIA" + "1234567890ABCDEF"
OPENAI_KEY_SHORT = "sk-" + "ABCDEFGHIJKLMNOPQRSTUV"
OPENAI_KEY_LONG = "sk-" + "ABCDEFGHIJKLMNOPQRSTUVWX123456"
ANTHROPIC_KEY_LONG = "sk-ant-" + "ABCDEFGHIJKLMNOPQRSTUVWX123456"
ANTHROPIC_KEY_FAKE = "sk-ant-" + "fake1234567890abcdef"
GITHUB_PAT = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWX1234567890ab"
GCP_API_KEY = "AIza" + "12345678901234567890123456789012345"
STRIPE_SECRET_KEY = "sk_live_" + "ABCDEFGHIJKLMNOPQRSTUVWX"
SLACK_BOT_TOKEN = (
    "xoxb-" + "123456789012" + "-" + "123456789012" + "-" + "abcdefghijklmnopqrstuvwx"
)

SECRET_FIXTURE_VALUES = {
    "__AWS_ACCESS_KEY_ID__": AWS_ACCESS_KEY_ID,
    "__OPENAI_KEY_SHORT__": OPENAI_KEY_SHORT,
    "__OPENAI_KEY_LONG__": OPENAI_KEY_LONG,
    "__ANTHROPIC_KEY_LONG__": ANTHROPIC_KEY_LONG,
    "__ANTHROPIC_KEY_FAKE__": ANTHROPIC_KEY_FAKE,
    "__GITHUB_PAT__": GITHUB_PAT,
    "__GCP_API_KEY__": GCP_API_KEY,
    "__STRIPE_SECRET_KEY__": STRIPE_SECRET_KEY,
    "__SLACK_BOT_TOKEN__": SLACK_BOT_TOKEN,
}


def materialize_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        result = value
        for placeholder, secret in SECRET_FIXTURE_VALUES.items():
            result = result.replace(placeholder, secret)
        return result
    if isinstance(value, list):
        return [materialize_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {
            key: materialize_placeholders(item)
            for key, item in value.items()
        }
    return value
