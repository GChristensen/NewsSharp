"""Search query translation (reading/search.py): the user's query becomes an FTS5 MATCH
expression. A syntax error never reaches the user: anything unparseable is searched as plain words."""
import re

_TOKEN = re.compile(r'(-?)(?:(title|author|tags?):)?(?:"([^"]*)"|(\S+))', re.I)
_WORD = re.compile(r"\w+", re.UNICODE)
_COLUMNS = {"title": "title", "author": "author", "tag": "tags", "tags": "tags"}


def _quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def plain_match(query: str) -> tuple[str | None, list[str]]:
    words = _WORD.findall(query or "")
    if not words:
        return None, []
    return " AND ".join(_quote(w) for w in words), words


def build_match(query: str) -> tuple[str | None, list[str]]:
    """Returns (MATCH expression, words to highlight)."""
    query = (query or "").strip()
    if query.count('"') % 2:
        query = query.replace('"', " ")
    positive, negative, terms = [], [], []
    for minus, field, phrase, word in _TOKEN.findall(query):
        if phrase is not None and phrase != "" or (not word and phrase == ""):
            words = _WORD.findall(phrase)
            if not words:
                continue
            expression = _quote(" ".join(words))
            highlight = [" ".join(words)]
        else:
            prefix = word.endswith("*")
            words = _WORD.findall(word)
            if not words:
                continue
            expression = _quote(" ".join(words)) + ("*" if prefix else "")
            highlight = words
        if field:
            expression = f"{_COLUMNS[field.lower()]} : {expression}"
        if minus:
            negative.append(expression)
        else:
            positive.append(expression)
            terms.extend(highlight)
    if not positive:
        return plain_match(query)
    match = " AND ".join(positive)
    if negative:
        match = f"({match})" + "".join(f" NOT {n}" for n in negative)
    return match, terms
