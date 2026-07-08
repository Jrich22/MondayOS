"""
External integrations for MondayOS.

MondayOS remains the system of record. Integrations in this package are
outbound destinations (publishing, sync, notification) — they read from
MondayOS and push to third-party services, never the reverse. The first
integration is Confluence document publishing (`integrations.confluence`).
"""
