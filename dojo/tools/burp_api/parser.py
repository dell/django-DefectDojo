import base64
import json
import logging

from dojo.models import Endpoint, Finding

logger = logging.getLogger(__name__)


DESCRIPTION_TEMPLATE = """**{title}**
**Serial Number**: {serial_number}
**Type Index**: {type_index}
**Confidence**: {confidence}
**Description**: {description_text}
"""


class BurpApiParser:

    """Parser that can load data from Burp API"""

    def get_scan_types(self):
        return ["Burp REST API"]

    def get_label_for_scan_types(self, scan_type):
        return "Burp REST API"

    def get_description_for_scan_types(self, scan_type):
        return "Import Burp REST API scan data in JSON format (/scan/[task_id] endpoint)."

    def get_findings(self, file, test):
        # API export is a JSON file
        tree = json.load(file)

        items = []
        # for each issue found
        for issue_event in tree.get("issue_events", []):
            if (
                issue_event.get("type") == "issue_found"
                and "issue" in issue_event
            ):
                issue = issue_event.get("issue")

                title = issue.get("name", "Burp issue")
                severity = convert_severity(issue)
                description_formated = DESCRIPTION_TEMPLATE.format(
                    title=title,
                    serial_number=issue.get("serial_number", "<None>"),
                    type_index=issue.get("type_index", "<None>"),
                    confidence=issue.get("confidence", "<None>"),
                    description_text=issue.get("description", "<None>"),
                )
                false_p = False
                # manage special case of false positives
                if issue.get("severity", "undefined") == "false_positive":
                    false_p = True

                finding = Finding(
                    title=title,
                    severity=severity,
                    description=description_formated,
                    mitigation="No mitigation provided",
                    references="No references provided",
                    false_p=false_p,
                    impact="No impact provided",
                    static_finding=False,  # by definition
                    dynamic_finding=True,  # by definition
                    unique_id_from_tool=str(
                        issue.get("serial_number", ""),
                    ),  # the serial number is a good candidate for this attribute
                    vuln_id_from_tool=str(
                        issue.get("type_index", ""),
                    ),  # the type index is a good candidate for this attribute
                )
                # manage confidence
                if convert_confidence(issue) is not None:
                    finding.scanner_confidence = convert_confidence(issue)
                # manage endpoints
                if "origin" in issue and "path" in issue:
                    finding.unsaved_endpoints = [
                        Endpoint.from_uri(
                            issue.get("origin") + issue.get("path"),
                        ),
                    ]
                finding.unsaved_req_resp = []
                for evidence in issue.get("evidence", []):
                    if evidence.get("type") not in {
                        "InformationListEvidence",
                        "FirstOrderEvidence",
                    }:
                        continue
                    request = self.get_clean_base64(
                        evidence.get("request_response").get("request"),
                    )
                    response = self.get_clean_base64(
                        evidence.get("request_response").get("response"),
                    )
                    finding.unsaved_req_resp.append(
                        {"req": request, "resp": response},
                    )

                items.append(finding)
        return items

    def get_clean_base64(self, value):
        output = ""
        if value is not None:
            for segment in value:
                if segment["type"] == "DataSegment":
                    data = base64.b64decode(segment["data"])
                    try:
                        output += data.decode()
                    except UnicodeDecodeError:
                        output += "Decoding of the DataSegment failed. Thus, decoded with `latin1`. The result is the following one:\n"
                        output += data.decode("latin1")
                elif segment["type"] == "SnipSegment":
                    output += f"\n<...> ({segment['length']} bytes)"
                elif segment["type"] == "HighlightSegment":
                    output += "\n\n------------------------------------------------------------------\n\n"
                else:
                    msg = f"unknown segment type in Burp data {segment['type']}"
                    raise ValueError(msg)
        return output


def convert_severity(issue):
    """
    According to OpenAPI definition of the API

    "Severity":{
             "type":"string",
             "enum":[
                "high",
                "medium",
                "low",
                "info",
                "undefined",
                "false_positive"
             ]
          },
    """
    value = issue.get("severity", "info").lower()
    if value in {"high", "medium", "low", "info"}:
        return value.title()
    return "Info"


def convert_confidence(issue):
    """
    According to OpenAPI definition:

    "Confidence":{
             "type":"string",
             "enum":[
                "certain",
                "firm",
                "tentative",
                "undefined"
             ]
          },
    """
    value = issue.get("confidence", "undefined").lower()
    if value == "certain":
        return 2
    if value == "firm":
        return 3
    if value == "tentative":
        return 6
    return None
