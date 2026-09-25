"""Read Baseball Reference player pages: the profile facts the dataset carries."""

import re
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup, Tag

HANDS = {"Right": "R", "Left": "L", "Both": "S"}


@dataclass(frozen=True)
class Player:
    player_id: str
    full_name: str
    position: str
    bats: str
    throws: str
    height_inches: int
    weight_lbs: int
    birth_date: str
    birth_place: str
    debut_date: str


def words(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def labelled(meta: Tag, *labels: str) -> Tag:
    """The profile line that starts with one of these bold labels, such as "Position:"."""
    for p in meta.find_all("p"):
        strong = p.find("strong")
        if strong is not None and strong.get_text(strip=True) in labels:
            return p
    raise ValueError(f"no {' or '.join(labels)} line in the profile")


def read_player(page: bytes, player_id: str) -> Player:
    # The profile sits above the stats tables, and parsing only that part is seven times
    # faster. A page that moves it below them fails loudly: no profile header.
    first_table = page.find(b"<table")
    head = page[:first_table] if first_table >= 0 else page
    meta = BeautifulSoup(head, "html.parser").find(id="meta")
    if meta is None or meta.find("h1") is None:
        raise ValueError(f"{player_id}: no profile header")
    try:
        # "Position: Rightfielder", or "Positions: Shortstop and Second Baseman".
        position = re.sub(r"^Positions?:\s*", "", words(labelled(meta, "Position:", "Positions:")))
        hands = re.search(r"Bats: (\w+) • Throws: (\w+)", words(labelled(meta, "Bats:")))
        # The size line has no label: "6-7 , 282lb (201cm, 127kg)".
        size = next(
            (m for p in meta.find_all("p") if (m := re.match(r"(\d+)-(\d+) , (\d+)lb", words(p)))),
            None,
        )
        birth = meta.find(id="necro-birth")
        # "in Sacramento, CA", in the unclassed span after the date; the country flag after it
        # has a class, so a missing place can never be read from the flag.
        place = birth.find_next_sibling("span", class_=False) if birth is not None else None
        place = words(place) if place is not None else ""
        # The date is a link on older pages and plain text for this season's debuts.
        debut = re.search(r"Debut: (\w+ \d{1,2}, \d{4})", words(labelled(meta, "Debut:")))
    except ValueError as exc:
        raise ValueError(f"{player_id}: {exc}") from exc
    if hands is None or hands.group(1) not in HANDS or hands.group(2) not in HANDS:
        raise ValueError(f"{player_id}: no bats and throws in the profile")
    if size is None or not place.startswith("in ") or debut is None:
        raise ValueError(f"{player_id}: no height, weight, birthplace or debut in the profile")
    name = words(meta.find("h1"))
    if not name:
        raise ValueError(f"{player_id}: no name in the profile header")
    feet, inches, pounds = (int(group) for group in size.groups())
    return Player(
        player_id=player_id,
        full_name=name,
        position=position,
        bats=HANDS[hands.group(1)],
        throws=HANDS[hands.group(2)],
        height_inches=feet * 12 + inches,
        weight_lbs=pounds,
        birth_date=birth["data-birth"],
        birth_place=place.removeprefix("in "),
        debut_date=datetime.strptime(debut.group(1), "%B %d, %Y").date().isoformat(),
    )
