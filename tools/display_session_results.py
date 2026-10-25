"""Parse the separate diagnostic and production results from a display check."""

import re


def parse_display_graphics(output: str, *, context: str) -> dict[str, object]:
    records = {}
    for label, key, recovery in (("Diagnostic", "diagnostic", "fallback"),
                                 ("Application", "application", "retry")):
        matches = re.findall(
            rf"^{label} graphics: graphicsApi=([A-Za-z0-9]+); renderer=(.+); "
            rf"{recovery}=(true|false)$", output, re.MULTILINE)
        if len(matches) != 1:
            raise RuntimeError(f"{context} has missing or ambiguous {key} graphics results:\n{output}")
        api, renderer, recovered = matches[0]
        records[key] = {"graphics_api": api, "renderer": renderer}
        records[recovery] = recovered == "true"

    summaries = re.findall(
        r"^Display session passed: .+; diagnostic_fallback=(true|false); app_retry=(true|false)$",
        output, re.MULTILINE)
    if len(summaries) != 1:
        raise RuntimeError(f"{context} has missing or ambiguous display recovery results:\n{output}")
    fallback, retry = (value == "true" for value in summaries[0])
    if (fallback, retry) != (records["fallback"], records["retry"]):
        raise RuntimeError(f"{context} has inconsistent display recovery results:\n{output}")
    return {
        # Preserve the original field for report consumers, referring to the
        # production viewer rather than the first diagnostic line in the log.
        "graphics_api": records["application"]["graphics_api"],
        "diagnostic": records["diagnostic"],
        "application": records["application"],
        "diagnostic_fallback": fallback,
        "app_retry": retry,
    }
