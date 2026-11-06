"""Count passed / failed / errored / skipped / xfailed / xpassed from one or more pytest JUnit files.

    python summarize_junit.py LABEL junit.xml [...] > counts.json

pytest writes an expected failure as <skipped type="pytest.xfail">, so it is separated from a skip here.
"""
import json
import sys
import xml.etree.ElementTree as ET


def main() -> int:
    label, files = sys.argv[1], sys.argv[2:]
    c = dict(passed=0, failed=0, errors=0, skipped=0, xfailed=0)
    for f in files:
        for case in ET.parse(f).getroot().iter("testcase"):
            if case.find("failure") is not None:
                c["failed"] += 1
            elif case.find("error") is not None:
                c["errors"] += 1
            elif (sk := case.find("skipped")) is not None:
                c["xfailed" if sk.get("type") == "pytest.xfail" else "skipped"] += 1
            else:
                c["passed"] += 1
    c["total"] = sum(c.values())
    print(json.dumps({"label": label, **c}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
