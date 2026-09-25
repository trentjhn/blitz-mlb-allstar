"""One page over HTTP, with the retry and stop rules for a site that rate-limits."""

from collections.abc import Callable

import requests

from allstar import config
from allstar.pacing import RequestPacer

# Transient failures worth another try: no connection, a timeout, or a body cut off mid-read.
RETRYABLE_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class Refused(Exception):
    """The site's answer has to stop the run. Carries what it sent, for quarantine."""

    def __init__(
        self,
        reason: str,
        status: int | None = None,
        headers: dict | None = None,
        body: bytes = b"",
        blocked: bool = False,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.headers = headers or {}
        self.body = body
        # A refusal (403 or 429) means the site is blocking us, not that one page is bad.
        self.blocked = blocked


def download(
    session: requests.Session,
    pacer: RequestPacer,
    sleep: Callable[[float], None],
    url: str,
) -> tuple[dict, bytes]:
    """GET a page and return (headers, body) for a 200. Anything else raises Refused.

    Only Fetcher.get calls this: it is what turns a blocked Refused into the block file that
    stops every later run.
    """
    failures: list[str] = []
    last: tuple = (None, {}, b"")
    for wait in (*config.RETRY_WAITS_S, None):
        pacer.wait_turn()
        try:
            response = session.get(
                url, timeout=config.TIMEOUT_S, allow_redirects=False, stream=True
            )
        except RETRYABLE_ERRORS as exc:
            failures.append(type(exc).__name__)
        except requests.RequestException as exc:
            raise Refused(f"{type(exc).__name__}: {exc}") from exc
        else:
            status, headers = response.status_code, dict(response.headers)
            # The status decides before the body is read, so a refusal whose body fails to
            # arrive can never turn into a retry. Only 200 and 5xx go further.
            if status != 200 and not 500 <= status < 600:
                blocked = status in (403, 429)
                reason = f"HTTP {status}"
                if blocked:
                    reason += ", the site is refusing or rate limiting us (no retry)"
                try:
                    body = response.content
                except requests.RequestException:
                    body, reason = b"", reason + ", body unreadable"
                raise Refused(reason, status, headers, body, blocked)
            try:
                body = response.content
            except RETRYABLE_ERRORS as exc:
                failures.append(f"{type(exc).__name__} reading the body")
            except requests.RequestException as exc:
                raise Refused(f"{type(exc).__name__}: {exc}", status, headers) from exc
            else:
                if status == 200:
                    return headers, body
                failures.append(f"HTTP {status}")
                last = (status, headers, body)
        if wait is not None:
            sleep(wait)
    raise Refused(f"failed on all {len(failures)} attempts: {', '.join(failures)}", *last)
