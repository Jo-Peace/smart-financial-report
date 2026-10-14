import logging
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception

logger = logging.getLogger(__name__)

def is_retryable_exception(exception: BaseException) -> bool:
    """Check if the exception should trigger a retry."""
    # Daily budget refusals never succeed on retry.
    try:
        from modules.ai_usage_guard import GeminiUsageLimitError

        if isinstance(exception, GeminiUsageLimitError):
            return False
    except ImportError:
        pass
    # gemini_client.GeminiResponseError (empty / truncated / incomplete answer)
    # carries its own retry decision.
    retryable_flag = getattr(exception, "retryable", None)
    if isinstance(retryable_flag, bool):
        return retryable_flag
    error_str = str(exception)
    # Check for HTTP rate limits and server errors
    if any(code in error_str for code in ["429", "500", "502", "503", "504"]):
        return True
    
    # Also retry on generic requests / httpx timeouts (Gemini SDK uses httpx)
    try:
        import requests
        if isinstance(exception, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
    except ImportError:
        pass
    try:
        import httpx
        if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass
        
    # Check for Gemini specific quota errors
    try:
        from google.api_core.exceptions import ResourceExhausted, InternalServerError, ServiceUnavailable
        if isinstance(exception, (ResourceExhausted, InternalServerError, ServiceUnavailable)):
            return True
    except ImportError:
        pass
        
    return False

def get_api_retry_decorator(max_attempts=4, min_wait=2, max_wait=60):
    """
    Returns a tenacity retry decorator configured with exponential backoff.
    It retries on rate limits (429) and server errors (5xx).
    """
    def log_retry(retry_state):
        if retry_state.outcome.failed:
            err = retry_state.outcome.exception()
            print(f"  [Retry] API call failed: {err}. Retrying in {retry_state.next_action.sleep:.1f} seconds (Attempt {retry_state.attempt_number})...")
            logger.warning(f"API call failed: {err}. Retrying...")

    return retry(
        retry=retry_if_exception(is_retryable_exception),
        wait=wait_exponential(multiplier=min_wait, min=min_wait, max=max_wait),
        stop=stop_after_attempt(max_attempts),
        before_sleep=log_retry
    )

api_retry = get_api_retry_decorator()
