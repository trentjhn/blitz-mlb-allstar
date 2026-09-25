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
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.headers = headers or {}
        self.body = body


def refusal(pacer: RequestPacer, url: str, response: requests.Response) -> Refused:
    """The Refused for a status that stops the run.

    A 403 or 429 means the site is blocking us, not that one page is bad. It is recorded with
    the pacer before the body is read, so the block holds even if the run dies while that body
    arrives.
    """
    status = response.status_code
    reason, notes = f"HTTP {status}", []
    if status in (403, 429):
        reason += ", the site is refusing or rate limiting us (no retry)"
        try:
            pacer.block(f"{url}: {reason}")
        except OSError as exc:
            notes.append(
                f"the block file could not be written ({exc}); "
                "wait for the block to lift before running again"
            )
    try:
        body = response.content
    except requests.RequestException:
        body, reason = b"", reason + ", body unreadable"
    return Refused("; ".join([reason, *notes]), status, dict(response.headers), body)


def download(
    session: requests.Session,
    pacer: RequestPacer,
    sleep: Callable[[float], None],
    url: str,
) -> tuple[dict, bytes]:
    """GET a page and return (headers, body) for a 200. Anything else raises Refused."""
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
                raise refusal(pacer, url, response)
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
