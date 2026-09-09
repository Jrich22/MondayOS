"""
A synthetic standalone project, built in a temporary directory.

The four real corpora share a history: they are all repositories this developer
wrote, and three of the four are TypeScript applications scaffolded the same way.
A rule can fit all four and still be a rule about this developer's habits. This
corpus exists to break that -- it is a Python service, laid out under `source/`
rather than `src/`, and nothing in MondayOS has ever seen it.

The name `source/` is the point. If discovery recognises the container here, it
recognised it by shape rather than by matching a blessed name.

What it plants, and what each thing is for:

- **billing** -- a capability in three structural locations (its own directory, a
  handler named after it, a test). Exercises co-occurrence.
- **notifications** -- two locations.
- **scheduling** -- a directory and nothing else. Exercises the plain case.
- **models / utils / config / types** -- architectural layers, each substantial
  enough to pass a size threshold. None may become a capability.
- **ROADMAP.md, DELIVERY_PLAN.md** -- plans. Neither may become a capability, and
  neither may rename one.
- **BILLING.md** -- a document that legitimately names a real capability.
- **source/handlers/deep/nested/deeper/** -- depth, so bounded recursion is
  exercised rather than assumed.

It gets its own git repository so history-derived evidence has somewhere to come
from, and it lives entirely under a caller-supplied temporary directory, so it
leaves nothing behind.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

# Only what git needs, and a HOME inside the temporary directory so the
# developer's own git configuration cannot change what this corpus looks like.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "synthetic",
    "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
    "GIT_COMMITTER_NAME": "synthetic",
    "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}

# The capabilities this corpus plants. A test asserting the outcome should say
# what it expects in the same words the fixture used to build it.
CAPABILITIES = ("billing", "notifications", "scheduling")

# Architectural layers. Substantial enough to clear any size rule, and never a
# capability.
LAYERS = {
    "models": ("user.py", "account.py", "record.py", "base.py"),
    "utils": ("strings.py", "dates.py", "retry.py", "paths.py"),
    "config": ("settings.py", "defaults.py", "loader.py", "env.py"),
    "types": ("common.py", "aliases.py", "protocols.py", "enums.py"),
}

# Documents that describe work rather than being it.
PLAN_DOCUMENTS = ("Roadmap", "Delivery Plan")


def build(root: Path) -> Path:
    """Write the corpus under `root` and return it. Creates a git repository."""

    def write(relative: str, text: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip())

    # Capability one: three structural locations, which is what co-occurrence is
    # for -- a directory, a handler named after it, and a test.
    write("source/billing/__init__.py", "from source.billing.invoice import Invoice\n")
    write("source/billing/invoice.py", "class Invoice:\n    '''One invoice.'''\n")
    write("source/billing/charge.py", "class Charge:\n    pass\n")
    write("source/billing/ledger.py", "class Ledger:\n    pass\n")
    write("source/handlers/billing_handler.py", "class BillingHandler:\n    pass\n")
    write("tests/test_billing.py", "def test_invoice():\n    assert True\n")

    # Capability two: two locations.
    write("source/notifications/__init__.py", "")
    write("source/notifications/email.py", "class EmailSender:\n    pass\n")
    write("source/notifications/sms.py", "class SmsSender:\n    pass\n")
    write("source/notifications/digest.py", "class Digest:\n    pass\n")
    write("source/handlers/notifications_handler.py", "class NotificationsHandler:\n    pass\n")

    # Capability three: a directory and nothing else.
    write("source/scheduling/__init__.py", "")
    write("source/scheduling/calendar.py", "class Calendar:\n    pass\n")
    write("source/scheduling/slots.py", "class Slots:\n    pass\n")
    write("source/scheduling/recurrence.py", "class Recurrence:\n    pass\n")
    write("source/scheduling/timezone.py", "class Timezone:\n    pass\n")

    for layer, files in LAYERS.items():
        for name in files:
            write(f"source/{layer}/{name}", "value = 1\n")

    # Depth, so bounded recursion is exercised rather than assumed.
    write("source/handlers/deep/nested/deeper/thing.py", "class Thing:\n    pass\n")
    write("source/handlers/deep/nested/deeper/other.py", "class Other:\n    pass\n")

    write("docs/ROADMAP.md", "# Roadmap\n\nWhat we intend to ship.\n")
    write("docs/DELIVERY_PLAN.md", "# Delivery Plan\n\nPhases and dates.\n")
    write("docs/ARCHITECTURE.md", "# Architecture\n\nHow the pieces fit.\n")
    # A document that legitimately names a capability the code already has.
    write("docs/BILLING.md", "# Billing\n\nHow billing works.\n")
    write("README.md", "# Synthetic Service\n")

    env = dict(_GIT_ENV, HOME=str(root))
    for command in (
        ["git", "init", "-q", "-b", "main", "."],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "synthetic: initial import"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True, env=env)
    return root
