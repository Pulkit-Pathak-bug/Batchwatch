"""Actor agent: drafts text for matches. Never sends anything itself."""


def _v(row: dict, key: str) -> str:
    """Field value, or 'n/a' when missing or empty."""
    return str(row.get(key) or "").strip() or "n/a"


def draft_alert(match: dict) -> str:
    """Draft the internal alert message for one match."""
    s, a = match["stock"], match["alert"]
    text = (f"NSQ ALERT: {_v(s, 'drug_name')}, batch {_v(s, 'batch_no')} "
            f"(shelf {_v(s, 'shelf_location')}, qty {_v(s, 'quantity')}).\n"
            f"CDSCO lists batch {_v(a, 'batch_no')} of {_v(a, 'drug_name')} as Not of Standard Quality: "
            f"{_v(a, 'reason')}.\n")
    if match["status"] == "confirmed":
        return text + "Quarantine this stock and stop dispensing."
    return text + "Match is unverified - check the pack against the alert. Quarantine if in doubt."


def draft_return_request(match: dict) -> str:
    """Draft the return request to the supplier for one match."""
    s, a = match["stock"], match["alert"]
    maker = _v(a, "manufacturer").split(",")[0].strip()
    return (f"Subject: Return request - NSQ batch {_v(a, 'batch_no')} ({_v(a, 'drug_name')})\n"
            f"To: {maker}\n\n"
            f"Dear Sir/Madam,\n\n"
            f"CDSCO's {_v(a, 'report_month')} Not of Standard Quality alert lists {_v(a, 'drug_name')}, "
            f"batch {_v(a, 'batch_no')}, manufactured by {maker}. Finding: \"{_v(a, 'reason')}\" "
            f"(reported by {_v(a, 'lab_state')}).\n\n"
            f"Our pharmacy holds {_v(s, 'quantity')} units of batch {_v(s, 'batch_no')} "
            f"(expiry {_v(s, 'exp_date')}). The stock is quarantined. Please arrange collection with "
            f"credit or replacement and confirm a pick-up date.\n\n"
            f"Regards,\nBatchWatch Demo Pharmacy")
