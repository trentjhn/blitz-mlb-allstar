"""Finding elements on Baseball Reference pages, including the ones shipped inside comments.

Many sections arrive as HTML comments that the site's JavaScript unpacks in the browser. An
element is looked up by its exact id on the page first, then inside the comments, so a table
is read once however it ships, and a section the site moves into a comment is still found.
"""

from bs4 import BeautifulSoup, Comment, Tag


def find_by_id(soup: BeautifulSoup, tag: str, element_id: str) -> Tag | None:
    found = soup.find(tag, id=element_id)
    if found is not None:
        return found
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if f'id="{element_id}"' in comment:
            return BeautifulSoup(comment, "html.parser").find(tag, id=element_id)
    return None
