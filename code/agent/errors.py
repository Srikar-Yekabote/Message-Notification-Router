"""Errors shared by the router and the critic."""


class QuotaExhausted(Exception):
    """Every model for a role hit a lasting rate limit. Stop the run and resume later; finished work is cached."""
