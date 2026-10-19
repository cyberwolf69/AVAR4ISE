"""Bounded exchange/local clock skew for timestamped market observations only."""
MAX_FUTURE_SKEW_SECONDS = 1.0


def fresh_age(age_seconds, max_age_seconds):
    # Never apply this tolerance to signal expiry or closed-candle availability.
    return -MAX_FUTURE_SKEW_SECONDS <= age_seconds <= max_age_seconds
