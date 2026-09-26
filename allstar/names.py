"""The spec's rule for matching a Baseball Reference name to a Show name."""

import re
import unicodedata
from collections.abc import Mapping, Sequence

from allstar.parse_player import Player
from allstar.parse_show import ShowPlayer

SUFFIX = re.compile(r"\s+(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)


def normalize(name: str) -> str:
    """No suffix or accents, hyphens as spaces, no periods or apostrophes, lower case.

    "José Ramírez" -> "jose ramirez", "Bobby Witt Jr." -> "bobby witt",
    "Pete Crow-Armstrong" -> "pete crow armstrong".
    """
    # Decomposing splits "é" into "e" plus an accent mark, which the ASCII encoding drops.
    # It drops curly apostrophes (U+2019) the same way.
    # Any dash punctuation (hyphen, non-breaking hyphen, en dash) counts as a hyphen, so the
    # ASCII step below cannot delete one and glue two words together.
    name = "".join("-" if unicodedata.category(ch) == "Pd" else ch for ch in name)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = SUFFIX.sub("", name.strip())
    name = name.replace("-", " ").replace(".", "").replace("'", "")
    return " ".join(name.lower().split())


def match_show(
    all_stars: Mapping[str, Player], show: Sequence[ShowPlayer]
) -> tuple[dict[str, ShowPlayer], list[str]]:
    """Each All-Star's Show entry, keyed by player id, and one line per bats/throws mismatch.

    Two All-Stars whose names normalize alike, or two Show entries under an All-Star's name,
    would make the join guess; that stops the build instead. None occurs in 2024-2026.
    """
    by_name: dict[str, list[ShowPlayer]] = {}
    for entry in show:
        by_name.setdefault(normalize(entry.name), []).append(entry)
    owners: dict[str, list[str]] = {}
    for player_id, player in all_stars.items():
        owners.setdefault(normalize(player.full_name), []).append(player_id)
    matches, mismatches = {}, []
    for key, player_ids in owners.items():
        entries = by_name.get(key, [])
        if not entries:
            continue
        # Two Show entries under one name matter only when an All-Star has that name.
        if len(entries) > 1:
            names = " and ".join(entry.name for entry in entries)
            raise ValueError(f"Show list: {names} read as one name")
        entry = entries[0]
        if len(player_ids) > 1:
            raise ValueError(
                f"{', '.join(sorted(player_ids))} all read as {entry.name} on the Show list"
            )
        player = all_stars[player_ids[0]]
        # The same name with different bats/throws is a different player, or a stale Show entry.
        if (player.bats, player.throws) != (entry.bats, entry.throws):
            mismatches.append(
                f"{player.player_id} ({player.full_name}): Baseball Reference has "
                f"{player.bats}/{player.throws}, the Show has {entry.bats}/{entry.throws}"
            )
        matches[player.player_id] = entry
    return matches, mismatches
