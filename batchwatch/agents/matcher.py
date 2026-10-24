"""Matcher agent: compares stock rows with alert rows."""


def match_stock(stock_rows: list[dict], alert_rows: list[dict]) -> list[dict]:
    """Return one result per stock row: {stock, alert, status} where status
    is 'confirmed', 'uncertain' or 'none'."""
    raise NotImplementedError
