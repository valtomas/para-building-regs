VERSION = "0.2.9"


def newer(a, b):
    """True if version a is later than b ("v0.2.4" against "0.2.3"). Anything that isn't a number counts as 0."""
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in str(v).lstrip("v").split(".")]
    return parts(a) > parts(b)
