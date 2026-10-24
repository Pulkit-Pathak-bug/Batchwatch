"""Orchestrator: watch -> parse -> match -> act, sharing one state dict."""


def should_stop(state: dict) -> bool:
    """Stop rules: e.g. no new alerts, no confirmed or uncertain matches."""
    raise NotImplementedError


def run_pipeline() -> dict:
    """Run the full pipeline once and return the final state."""
    raise NotImplementedError
