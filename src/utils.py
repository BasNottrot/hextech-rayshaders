import os
import time
from datetime import timedelta
from loguru import logger


def print_elapsed_time(start, description):
    """Print elapsed time since the given start time."""
    elapsed = time.perf_counter() - start
    logger.info(f"{description}: {timedelta(seconds=elapsed)}")


def get_env_var(name, default, convert_type=str):
    """Get environment variable with type conversion."""
    value = os.environ.get(name, default)
    
    # Strip quotes if they exist (for .env file values)
    if isinstance(value, str):
        value = value.strip('"\'')
    
    if convert_type == bool:
        return value.lower() == "true"
    
    if value:
        value = convert_type(value)
    return value
