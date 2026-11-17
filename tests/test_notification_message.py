"""N2 — the message: the application's name, the step that waits and a link.
Nothing from the run itself, and nothing a name could smuggle in."""

import base64
import email
import email.policy

import pytest

from notification_support import CANARY, STUDIO, definition, email as email_block, slack, webhook

LINK = f"{STUDIO}/?application=digest&run=run-1"


def review_event(**change):
    return {"type": "ApplicationFinished", "status": "waiting_for_human", "reason": "Host human input handler is required",
            "stage": "review", "sequence": 7, "timestamp": "2026-11-16T10:39:12+00:00",
            "state": {"digest": CANARY["output"], "review": ""}, "views": [],
            "checkpoint": {"version": 1, "patches": {"write": CANARY["output"]}}, **change}


def pause_event(**change):
    return {"type": "ApplicationFinished", "status": "paused", "reason": "Paused between steps by the operator",
            "next_stage": "write", "sequence": 3, "timestamp": "2026-11-16T10:30:12+00:00",
            "state": {"digest": CANARY["output"]}, "views": [], **change}


def message(block, event=None, *, name="Support digest", application_id="digest", run_id="run-1", studio_url=STUDIO):
    from conclave.application.notices import waiting_message

    return waiting_message(definition(block, name=name), application_id=application_id, run_id=run_id,
                           event=event or review_event(), studio_url=studio_url)


def parsed(raw):
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=email.policy.default)


def test_slack_says_who_waits_where_with_a_link():
    sent = message(slack())
    assert (sent.via, sent.operation, sent.target, sent.reason, sent.link) == ("slack", "send_message", "#reviews", "review", LINK)
    assert sent.step == {"id": "review", "name": "Review the digest"}
    assert sent.arguments == {"channel": "#reviews",
                              "text": f"⏸ *Support digest* is waiting for you at “Review the digest”. Open the run: <{LINK}>"}


def test_a_pause_names_the_next_step():
    sent = message(slack(), pause_event())
    assert sent.reason == "pause" and sent.step == {"id": "write", "name": "Write the digest"}
    assert sent.arguments["text"] == f"⏸ *Support digest* is paused before “Write the digest”. Open the run: <{LINK}>"
    last = message(slack(), pause_event(next_stage=None))
    assert last.step is None
    assert last.arguments["text"] == f"⏸ *Support digest* is paused. Open the run: <{LINK}>"


def test_email_is_one_plain_line_to_one_address():
    sent = message(email_block())
    assert (sent.via, sent.operation, sent.target) == ("email", "send_email", "billing-leads@acme.example")
    mail = parsed(sent.arguments["raw"])
    assert set(sent.arguments) == {"raw"}
    assert mail["To"] == "billing-leads@acme.example"
    assert mail["Subject"] == "Support digest is waiting for you"
    assert mail.get_content_type() == "text/plain"
    assert mail.get_content().strip() == f"⏸ Support digest is waiting for you at “Review the digest”. Open the run: {LINK}"
    assert parsed(message(email_block(), pause_event()).arguments["raw"])["Subject"] == "Support digest is paused"


def test_the_webhook_gets_a_small_document():
    sent = message(webhook())
    assert (sent.via, sent.operation, sent.target) == ("webhook", "send", "ops_hook")
    assert sent.arguments == {"payload": {
        "type": "run.waiting", "reason": "review", "application": {"id": "digest", "name": "Support digest"},
        "step": {"id": "review", "name": "Review the digest"}, "run_id": "run-1",
        "waiting_since": "2026-11-16T10:39:12+00:00", "link": LINK}}


@pytest.mark.parametrize("block", [slack(), email_block(), webhook()], ids=["slack", "email", "webhook"])
@pytest.mark.parametrize("event", [review_event(), pause_event()], ids=["review", "pause"])
def test_nothing_from_the_run_reaches_a_message(block, event):
    sent = message(block, event)
    text = repr(sent.arguments)
    if block["via"] == "email":
        text += parsed(sent.arguments["raw"]).as_string()
    for canary in CANARY.values():
        assert canary not in text, canary


def test_names_cannot_mention_everyone_add_headers_or_run_long():
    loud = message(slack(), name="<!channel> Refunds & <https://evil.example|login>")
    assert "<!channel>" not in loud.arguments["text"] and "<https://evil.example" not in loud.arguments["text"]
    assert "&lt;!channel&gt; Refunds &amp; &lt;https://evil.example|login&gt;" in loud.arguments["text"]
    mail = parsed(message(email_block(), name="Digest\r\nBcc: everyone@evil.example").arguments["raw"])
    assert mail["Bcc"] is None and "\n" not in mail["Subject"] and "\r" not in mail["Subject"]
    long = message(slack(), name="x" * 500).arguments["text"]
    assert "x" * 90 in long and "x" * 101 not in long


def test_the_link_is_quoted_and_needs_a_studio_address():
    sent = message(slack(), application_id="a b/c", run_id="r?1")
    assert sent.link == f"{STUDIO}/?application=a%20b%2Fc&run=r%3F1"
    assert message(slack(), studio_url=STUDIO + "/").link == LINK, "a trailing slash is not doubled"
    with pytest.raises(ValueError, match="Studio address"):
        message(slack(), studio_url=None)
