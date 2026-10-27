"""The shared end-to-end scenario: acme-labs.com, three buyer questions, a scripted Gemini."""

from tests.mockweb import MockWeb

Q_LOST = "best hands-on lab platform for enterprises"  # not cited; a matching page exists
Q_WON = "pricing for enterprise lab platforms"  # cited
Q_NO_PAGE = "cheap cloud certification courses"  # not cited; no matching page
QUESTIONS = [Q_LOST, Q_WON, Q_NO_PAGE]


def script(web: MockWeb) -> None:
    web.answer(Q_LOST, ["rival", "g2", "reddit"])
    web.answer(Q_WON, ["own", "g2"])
    web.answer(Q_NO_PAGE, ["g2", "reddit"])


def script_open(web: MockWeb) -> None:
    """The same story on the open engine (Tavily results + Groq citations)."""
    web.search(Q_LOST, ["rival", "g2", "own", "reddit"], cites=[1, 2])  # seen at #3, not chosen
    web.search(Q_WON, ["own", "g2"], cites=[1])  # cited
    web.search(Q_NO_PAGE, ["g2", "reddit"], cites=[1, 2])  # never in the search results
