"""Actor agent: drafts text for matches. Never sends anything itself."""


def draft_alert(match: dict) -> str:
    """Draft the internal alert message for one match."""
    raise NotImplementedError


def draft_return_request(match: dict) -> str:
    """Draft the return request to the supplier for one match."""
    raise NotImplementedError
