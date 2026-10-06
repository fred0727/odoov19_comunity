"""UBL 2.0 Communication of Void (RA) builder for SUNAT."""

import re

from lxml import etree


VOIDED_NS = "urn:sunat:names:specification:ubl:peru:schema:xsd:VoidedDocuments-1"
CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
EXT_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
SAC_NS = "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"
# ``xsi`` is not semantically required by the RA schema, but it is part of
# SUNAT's published/reference representation and keeps the generated document
# consistent with the established UBL envelope.
NSMAP = {
    "cbc": CBC_NS,
    "cac": CAC_NS,
    "ext": EXT_NS,
    "sac": SAC_NS,
    "ds": DS_NS,
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}


def _root_nsmap():
    """Use the VoidedDocuments UBL namespace as the default namespace."""
    return {None: VOIDED_NS, **NSMAP}


def _cbc(parent, name, value):
    element = etree.SubElement(parent, f"{{{CBC_NS}}}{name}")
    element.text = str(value or "")
    return element


def _sac(parent, name, value):
    element = etree.SubElement(parent, f"{{{SAC_NS}}}{name}")
    element.text = str(value or "")
    return element


def voided_document_filename(company, reference, sequence):
    ruc = re.sub(r"\D", "", company.partner_id.vat or "")
    return f"{ruc}-RA-{reference.strftime('%Y%m%d')}-{sequence:04d}.xml"


def build_voided_document(move, issue_date, sequence, reason):
    """Build one RA document for an accepted customer invoice.

    RA uses the SUNAT VoidedDocuments UBL 2.0 structure and is subsequently
    submitted through ``sendSummary``.  The document is deliberately limited
    to one invoice because the user starts the process from that invoice.
    """
    document_number = (move.l10n_latam_document_number or "").strip()
    match = re.fullmatch(r"([A-Za-z0-9]+)[- ](\d+)", document_number)
    if not match:
        raise ValueError("El comprobante no tiene una serie y correlativo válidos para comunicar su baja.")
    series, number = match.groups()
    # Odoo installations do not expose the electronic number uniformly: some
    # provide ``001-00000001`` plus prefix ``F`` while others already provide
    # ``F001-00000001``.  SUNAT requires exactly one complete serial (F001),
    # never F + F001.  Normalize both representations.
    document_prefix = move.l10n_latam_document_type_id.doc_code_prefix or "F"
    document_series = series if series.upper().startswith(document_prefix.upper()) else f"{document_prefix}{series}"
    reference_date = move.invoice_date
    root = etree.Element(f"{{{VOIDED_NS}}}VoidedDocuments", nsmap=_root_nsmap())

    extensions = etree.SubElement(root, f"{{{EXT_NS}}}UBLExtensions")
    extension = etree.SubElement(extensions, f"{{{EXT_NS}}}UBLExtension")
    etree.SubElement(extension, f"{{{EXT_NS}}}ExtensionContent")
    _cbc(root, "UBLVersionID", "2.0")
    _cbc(root, "CustomizationID", "1.0")
    _cbc(root, "ID", f"RA-{issue_date.strftime('%Y%m%d')}-{sequence:04d}")
    _cbc(root, "ReferenceDate", reference_date)
    _cbc(root, "IssueDate", issue_date)

    supplier = etree.SubElement(root, f"{{{CAC_NS}}}AccountingSupplierParty")
    _cbc(supplier, "CustomerAssignedAccountID", move.company_id.partner_id.vat)
    _cbc(supplier, "AdditionalAccountID", "6")
    party = etree.SubElement(supplier, f"{{{CAC_NS}}}Party")
    legal = etree.SubElement(party, f"{{{CAC_NS}}}PartyLegalEntity")
    _cbc(legal, "RegistrationName", move.company_id.partner_id.name)

    line = etree.SubElement(root, f"{{{SAC_NS}}}VoidedDocumentsLine")
    _cbc(line, "LineID", "1")
    _cbc(line, "DocumentTypeCode", move.l10n_latam_document_type_id.code or "01")
    # In the SUNAT VoidedDocuments schema these are SAC elements (not CBC).
    _sac(line, "DocumentSerialID", document_series)
    _sac(line, "DocumentNumberID", number)
    _sac(line, "VoidReasonDescription", reason)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
