"""
This module provides utility functions for parsing data from instruments.
"""

def parse_keithley_output(data_string: str) -> list[float]:
    """
    Parses a comma-separated string of scientific notation numbers from a Keithley instrument
    and returns a list of floats.

    Args:
        data_string: A string containing comma-separated numbers,
                     e.g., "+1.234E-06,+4.567E+00".

    Returns:
        A list of floats parsed from the string.
    """
    try:
        parts = data_string.strip().split(',')
        return [float(part) for part in parts]
    except (ValueError, IndexError) as e:
        raise ValueError(f"Failed to parse data string: '{data_string}'. Reason: {e}") from e

