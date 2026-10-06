"""Local validation against the public SUNAT-distributed UBL 2.1 XSD."""

from pathlib import Path

from lxml import etree


XSD_PATH = Path(__file__).parent.parent / "static" / "xsd" / "sunat" / "2.1" / "maindoc" / "UBL-Invoice-2.1.xsd"


def validate_invoice_xml(xml_bytes):
    """Return schema errors without making any network request."""
    try:
        schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
        document = etree.fromstring(xml_bytes)
        schema.assertValid(document)
    except (OSError, etree.XMLSyntaxError, etree.XMLSchemaParseError, etree.DocumentInvalid) as error:
        return [str(error)]
    return []
