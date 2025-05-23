import hashlib
import logging

import dateutil
import html2text
import hyperlink
from cvss import parser as cvss_parser
from defusedxml.ElementTree import parse

from dojo.models import Endpoint, Finding

logger = logging.getLogger(__name__)


class AcunetixXMLParser:

    """This parser is written for Acunetix XML reports"""

    def get_fields(self) -> list[str]:
        """
        Return the list of fields used in the Acunetix XML Parser.

        Fields:
        - title: Set to the name outputted by the Acunetix XML Scanner.
        - severity: Set to severity from Acunetix XML Scanner converted into Defect Dojo format.
        - description: Set to description, Details, and TechnivalDetails variables outputted from Acunetix XML Scanner.
        - false_p: Set to True/False based on Defect Dojo standards.
        - static_finding: Set to True by default and updated to False if requests are present.
        - dynamic_finding: Set to False by default and updated to True if requests are present.
        - nb_occurences: Set to 1 and increased based on presence of occurences.
        - impact: Set to impact outputted from Acunetix XML Scanner if it is present.
        - mitigation: Set to Recommendation outputted from Acunetix XML Scanner if it is present.
        - date: Set to StartTime outputted from Acunetix XML Scanner if it is present.
        - cwe: Set to converted cwe outputted from Acunetix XML Scanner if it is present.
        - cvssv3: Set to converted cvssv3 values outputted from Acunetix XML Scanner if it is present.
        """
        return [
            "title",
            "severity",
            "description",
            "false_p",
            "static_finding",
            "dynamic_finding",
            "nb_occurences",
            "impact",
            "mitigation",
            "date",
            "cwe",
            "cvssv3",
        ]

    def get_dedupe_fields(self) -> list[str]:
        """
        Return the list of fields used for deduplication in the Acunetix XML Parser.

        Fields:
        - title: Set to the name outputted by the Acunetix XML Scanner.
        - description: Set to description, Details, and TechnivalDetails variables outputted from Acunetix XML Scanner.
        """
        return [
            "title",
            "description",
        ]

    def get_findings(self, filename, test):
        dupes = {}
        root = parse(filename).getroot()
        for scan in root.findall("Scan"):
            start_url = scan.findtext("StartURL")
            if ":" not in start_url:
                start_url = "//" + start_url
            # get report date
            if scan.findtext("StartTime") and scan.findtext("StartTime") != "":
                report_date = dateutil.parser.parse(
                    scan.findtext("StartTime"), dayfirst=True,
                ).date()
            for item in scan.findall("ReportItems/ReportItem"):
                finding = Finding(
                    test=test,
                    title=item.findtext("Name"),
                    severity=self.get_severity(item.findtext("Severity")),
                    description=html2text.html2text(
                        item.findtext("Description"),
                    ).strip(),
                    false_p=self.get_false_positive(
                        item.findtext("IsFalsePositive"),
                    ),
                    static_finding=True,
                    dynamic_finding=False,
                    nb_occurences=1,
                )
                if item.findtext("Impact") and item.findtext("Impact") != "":
                    finding.impact = item.findtext("Impact")
                if item.findtext("Recommendation") and item.findtext(
                    "Recommendation",
                ) != "":
                    finding.mitigation = item.findtext("Recommendation")
                if report_date:
                    finding.date = report_date
                if item.findtext("CWEList/CWE"):
                    finding.cwe = self.get_cwe_number(
                        item.findtext("CWEList/CWE"),
                    )
                references = []
                for reference in item.findall("References/Reference"):
                    url = reference.findtext("URL")
                    db = reference.findtext("Database") or url
                    references.append(f" * [{db}]({url})")
                if len(references) > 0:
                    finding.references = "\n".join(references)
                if item.findtext("CVSS3/Descriptor"):
                    cvss_objects = cvss_parser.parse_cvss_from_text(
                        item.findtext("CVSS3/Descriptor"),
                    )
                    if len(cvss_objects) > 0:
                        finding.cvssv3 = cvss_objects[0].clean_vector()
                # more description are in "Details"
                if (
                    item.findtext("Details")
                    and len(item.findtext("Details").strip()) > 0
                ):
                    finding.description += "\n\n**Details:**\n{}".format(
                        html2text.html2text(item.findtext("Details")),
                    )
                if (
                    item.findtext("TechnicalDetails")
                    and len(item.findtext("TechnicalDetails").strip()) > 0
                ):
                    finding.description += (
                        "\n\n**TechnicalDetails:**\n\n{}".format(
                            item.findtext("TechnicalDetails"),
                        )
                    )
                # add requests
                finding.unsaved_req_resp = []
                if len(item.findall("TechnicalDetails/Request")):
                    finding.dynamic_finding = (
                        True  # if there is some requests it's dynamic
                    )
                    finding.static_finding = (
                        False  # if there is some requests it's dynamic
                    )
                    for request in item.findall("TechnicalDetails/Request"):
                        finding.unsaved_req_resp.append(
                            {"req": (request.text or ""), "resp": ""},
                        )
                # manage the endpoint
                url = hyperlink.parse(start_url)
                endpoint = Endpoint(
                    host=url.host,
                    port=url.port,
                    path=item.findtext("Affects"),
                )
                if url.scheme is not None and url.scheme != "":
                    endpoint.protocol = url.scheme
                finding.unsaved_endpoints = [endpoint]
                dupe_key = hashlib.sha256(
                    "|".join(
                        [
                            finding.title,
                            str(finding.impact),
                            str(finding.mitigation),
                        ],
                    ).encode("utf-8"),
                ).hexdigest()
                if dupe_key in dupes:
                    find = dupes[dupe_key]
                    # add details for the duplicate finding
                    if (
                        item.findtext("Details")
                        and len(item.findtext("Details").strip()) > 0
                    ):
                        find.description += (
                            "\n-----\n\n**Details:**\n{}".format(
                                html2text.html2text(item.findtext("Details")),
                            )
                        )
                    find.unsaved_endpoints.extend(finding.unsaved_endpoints)
                    find.unsaved_req_resp.extend(finding.unsaved_req_resp)
                    find.nb_occurences += finding.nb_occurences
                    logger.debug(
                        f"Duplicate finding : {finding.title}",
                    )
                else:
                    dupes[dupe_key] = finding
        return list(dupes.values())

    def get_cwe_number(self, cwe):
        """
            Returns cwe number.
        :param cwe:
        :return: cwe number
        """
        if cwe is None:
            return None
        return int(cwe.split("-")[1])

    def get_severity(self, severity):
        """
            Returns Severity as per DefectDojo standards.
        :param severity:
        :return:
        """
        if severity == "high":
            return "High"
        if severity == "medium":
            return "Medium"
        if severity == "low":
            return "Low"
        if severity == "informational":
            return "Info"
        return "Critical"

    def get_false_positive(self, false_p):
        """
            Returns True, False for false positive as per DefectDojo standards.
        :param false_p:
        :return:
        """
        return bool(false_p)
