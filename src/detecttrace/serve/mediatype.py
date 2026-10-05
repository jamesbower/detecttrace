"""The one Content-Type rule both write routes of `detecttrace serve` apply."""

UTF8_CHARSETS = frozenset({"utf-8", "utf8"})


def to_media_type(content_type: str) -> tuple[str, bool]:
    """Return the media type in lower case and whether every charset given names UTF-8.

    The charset is matched in any case, quoted or not; with no charset the body is read as
    UTF-8, as JSON and the verdict CSV always are.
    """
    media_type, *parameters = content_type.split(";")
    is_utf8 = True
    for parameter in parameters:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset":
            is_utf8 = is_utf8 and value.strip().strip('"').lower() in UTF8_CHARSETS
    return media_type.strip().lower(), is_utf8
