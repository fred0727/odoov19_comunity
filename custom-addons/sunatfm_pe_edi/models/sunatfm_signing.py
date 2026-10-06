"""Digital XML signing helpers for SUNAT CPE/GRE documents.

The signing helper accepts PKCS#12 material only in memory. Odoo configuration
is responsible for access control and supplies the bytes to this helper.
"""

from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from lxml import etree
from signxml import XMLSigner, XMLVerifier, methods
from signxml.verifier import SignatureConfiguration

EXT_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"


def load_pkcs12(p12_bytes, password):
    """Load a PKCS#12 certificate bundle without writing it to disk."""
    if not p12_bytes:
        raise ValueError("Debe proporcionar un certificado PKCS#12.")
    if isinstance(password, str):
        password = password.encode()
    private_key, certificate, chain = pkcs12.load_key_and_certificates(p12_bytes, password)
    if private_key is None or certificate is None:
        raise ValueError("El PKCS#12 no contiene una clave privada y un certificado válidos.")
    return private_key, certificate, chain or []


def sign_xml(xml_bytes, p12_bytes, password):
    """Return an enveloped XMLDSig signature using RSA-SHA256.

    SUNAT conformance still requires checking the complete document schema and
    certificate requirements; this helper only performs the cryptographic step.
    """
    private_key, certificate, chain = load_pkcs12(p12_bytes, password)
    root = etree.fromstring(xml_bytes)
    cert_chain = [certificate] + chain
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    # SignXML supports a placeholder to control the location of an enveloped
    # signature.  Put it directly in SUNAT's ExtensionContent so the digest is
    # calculated with the final XML tree shape (moving a generated signature
    # afterwards would invalidate the digest).
    extension_content = root.find(
        f"{{{EXT_NS}}}UBLExtensions/{{{EXT_NS}}}UBLExtension/{{{EXT_NS}}}ExtensionContent"
    )
    if extension_content is None:
        raise ValueError("El XML no contiene la extensión UBL requerida para la firma.")
    for child in list(extension_content):
        extension_content.remove(child)
    etree.SubElement(extension_content, f"{{{DS_NS}}}Signature", Id="placeholder")
    signed_root = signer.sign(
        root,
        key=private_key,
        cert=[cert.public_bytes(Encoding.PEM) for cert in cert_chain],
    )
    # Do not pretty-print after signing: adding whitespace changes the
    # canonicalized document and invalidates the digest.
    return etree.tostring(signed_root, encoding="UTF-8", xml_declaration=True)


def verify_xml_signature(signed_xml_bytes, certificate_pem):
    """Verify XMLDSig against the explicitly trusted signer certificate."""
    if not certificate_pem:
        raise ValueError("Debe proporcionar el certificado de confianza para verificar la firma.")
    configuration = SignatureConfiguration(require_x509=True)
    return XMLVerifier().verify(
        signed_xml_bytes,
        x509_cert=certificate_pem,
        expect_config=configuration,
    )
