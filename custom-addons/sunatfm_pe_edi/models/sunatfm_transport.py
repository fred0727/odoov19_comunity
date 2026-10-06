"""Helpers for the direct SUNAT SOAP transport.

This layer only prepares and decodes payloads. Network calls and credentials
remain deliberately outside this first implementation until the Beta account
and certificate are provided.
"""

import base64
import binascii
import hashlib
import io
import json
import re
import urllib.parse
import urllib.error
import urllib.request
import zipfile
from xml.sax.saxutils import escape

from lxml import etree


SUNAT_ENDPOINTS = {
    "cpe_test": "https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService",
    "cpe_production": "https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService",
    "gre_test": "https://e-beta.sunat.gob.pe/ol-ti-itemision-guia-gem-beta/billService",
    "gre_production": "https://e-guiaremision.sunat.gob.pe/ol-ti-itemision-guia-gem/billService",
}

GRE_TOKEN_ENDPOINT = "https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/"
GRE_API_ENDPOINTS = {
    "test": "https://api-cpe.sunat.gob.pe/v1/contribuyente/gem",
    "production": "https://api-cpe.sunat.gob.pe/v1/contribuyente/gem",
}


def _json_response_error(error):
    try:
        body = error.read().decode("utf-8", "replace")
        data = json.loads(body)
        errors = data.get("errors") or []
        details = "; ".join(
            "%s: %s" % (item.get("codError", ""), item.get("desError", ""))
            for item in errors if isinstance(item, dict)
        )
        return data.get("msg") or details or body
    except Exception:
        return ""


def get_gre_api_token(client_id, client_secret, sol_username, sol_password, timeout=30):
    """Obtain the OAuth token required by SUNAT's REST GRE API.

    SUNAT requires the RUC concatenated with the SOL user as ``username``.
    Tokens are intentionally not persisted in the database; they are short-lived
    credentials and should be requested when a transmission is made.
    """
    if not all((client_id, client_secret, sol_username, sol_password)):
        raise ValueError("Complete Client ID, Client Secret, Usuario SOL y Clave SOL para GRE.")
    endpoint = GRE_TOKEN_ENDPOINT.format(client_id=urllib.parse.quote(str(client_id), safe=""))
    form = urllib.parse.urlencode({
        "grant_type": "password",
        "scope": "https://api-cpe.sunat.gob.pe",
        "client_id": client_id,
        "client_secret": client_secret,
        "username": sol_username,
        "password": sol_password,
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _json_response_error(error)
        raise ValueError("SUNAT no emitió el token GRE (HTTP %s): %s" % (error.code, detail)) from error
    except urllib.error.URLError as error:
        raise ValueError("No fue posible obtener el token GRE: %s" % error.reason) from error
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("SUNAT devolvió una respuesta inválida al solicitar el token GRE.") from error
    token = data.get("access_token")
    if not token:
        raise ValueError("SUNAT no devolvió access_token para GRE.")
    return data


def send_gre(token, environment, xml_filename, zip_bytes, timeout=30):
    """Submit a signed GRE ZIP and return SUNAT's JSON ticket response."""
    if not xml_filename.lower().endswith(".xml"):
        raise ValueError("El nombre del XML GRE debe terminar en .xml.")
    base_url = GRE_API_ENDPOINTS.get(environment)
    if not base_url:
        raise ValueError("El entorno SUNAT debe ser 'test' o 'production'.")
    file_name = xml_filename[:-4] + ".zip"
    payload = json.dumps({"archivo": {
        "nomArchivo": file_name,
        "arcGreZip": base64.b64encode(zip_bytes).decode("ascii"),
        "hashZip": hashlib.sha256(zip_bytes).hexdigest(),
    }}).encode("utf-8")
    request = urllib.request.Request(
        "%s/comprobantes/%s" % (base_url, urllib.parse.quote(xml_filename[:-4], safe="-_.")),
        data=payload,
        headers={"Authorization": "Bearer %s" % token, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _json_response_error(error)
        raise ValueError("SUNAT rechazó el envío GRE (HTTP %s): %s" % (error.code, detail)) from error
    except urllib.error.URLError as error:
        raise ValueError("No fue posible conectar con la API GRE: %s" % error.reason) from error
    if not data.get("numTicket"):
        raise ValueError("SUNAT no devolvió numTicket para la GRE.")
    return data


def get_gre_status(token, environment, ticket, timeout=30):
    """Query a GRE ticket; response includes codRespuesta and optional arcCdr."""
    base_url = GRE_API_ENDPOINTS.get(environment)
    if not base_url or not ticket:
        raise ValueError("Entorno o ticket GRE inválido.")
    request = urllib.request.Request(
        "%s/comprobantes/envios/%s" % (base_url, urllib.parse.quote(str(ticket), safe="")),
        headers={"Authorization": "Bearer %s" % token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _json_response_error(error)
        raise ValueError("SUNAT no pudo consultar el ticket GRE (HTTP %s): %s" % (error.code, detail)) from error
    except urllib.error.URLError as error:
        raise ValueError("No fue posible consultar la API GRE: %s" % error.reason) from error


def endpoint_for(environment, document_kind="cpe"):
    """Return SUNAT's endpoint for a CPE or a remitente GRE."""
    if document_kind not in {"cpe", "gre"}:
        raise ValueError("El tipo de documento SUNAT debe ser 'cpe' o 'gre'.")
    try:
        return SUNAT_ENDPOINTS[f"{document_kind}_{environment}"]
    except KeyError as error:
        raise ValueError("El entorno SUNAT debe ser 'test' o 'production'.") from error


def build_send_bill_payload(xml_bytes, xml_filename):
    """Build the ``sendBill`` fileName/contentFile pair expected by SUNAT."""
    if not xml_filename.lower().endswith(".xml"):
        raise ValueError("El nombre del XML debe terminar en .xml.")
    archive_name = xml_filename[:-4] + ".zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(xml_filename, xml_bytes)
    return {
        "fileName": archive_name,
        "contentFile": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }


def extract_cdr(content_file):
    """Return CDR ZIP filename, ZIP bytes and its XML content.

    SUNAT's ``applicationResponse`` is the actual CDR archive encoded in
    base64.  Keep that archive intact for the user to download, while also
    returning its XML for extracting the response code and description.
    """
    try:
        raw_zip = base64.b64decode(content_file, validate=True)
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
            xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            if not xml_names:
                raise ValueError("El ZIP de respuesta no contiene un XML CDR.")
            xml_name = xml_names[0]
            archive_name = f"{xml_name.rsplit('.', 1)[0]}.zip"
            return archive_name, raw_zip, archive.read(xml_name)
    except (ValueError, zipfile.BadZipFile, binascii.Error) as error:
        raise ValueError("La respuesta SUNAT no contiene un CDR ZIP válido.") from error


def build_send_bill_soap(payload):
    """Build a SOAP 1.1 request for SUNAT's ``sendBill`` operation."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ser="http://service.sunat.gob.pe" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
  <soapenv:Header>
    <wsse:Security>
      <wsse:UsernameToken>
        <wsse:Username>{escape(payload["sol_user"])}</wsse:Username>
        <wsse:Password>{escape(payload["sol_password"])}</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>
    <soapenv:Body>
      <ser:sendBill>
      <fileName>{escape(payload["fileName"])}</fileName>
      <contentFile>{payload["contentFile"]}</contentFile>
      </ser:sendBill>
  </soapenv:Body>
</soapenv:Envelope>'''.encode("utf-8")


def send_bill(endpoint, sol_user, sol_password, payload, timeout=30):
    """Send one signed CPE to SUNAT and return the base64 CDR payload."""
    request = urllib.request.Request(
        endpoint,
        data=build_send_bill_soap({
            **payload,
            "sol_user": sol_user,
            "sol_password": sol_password,
        }),
        # The Beta WSDL declares the document/literal action as urn:sendBill.
        # Sending only "sendBill" can make the gateway return the request body
        # with HTTP 500 instead of a SOAP response/CDR.
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "urn:sendBill"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_xml = response.read()
    except urllib.error.HTTPError as error:
        # SUNAT frequently returns a SOAP Fault in the HTTP error body.  The
        # previous implementation discarded it and exposed only “HTTP 500”,
        # making credential/XML errors indistinguishable from an outage.
        try:
            error_body = error.read()
            fault_document = etree.fromstring(error_body)
            fault = fault_document.xpath("//*[local-name()='faultstring']/text()")
            detail = fault[0].strip() if fault else error_body.decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        # Keep the complete response.  SUNAT sometimes places the useful SOAP
        # detail after a long echoed request/contentFile; truncating it hides
        # the actual diagnostic needed to correct the XML or credentials.
        detail = " ".join(detail.split())
        # SUNAT's gateway may echo the complete SOAP request on HTTP 500.
        # Never persist SOL credentials when that happens.
        detail = re.sub(
            r"(<(?:[\w.-]+:)?Username>).*?(</(?:[\w.-]+:)?Username>)",
            r"\1[REDACTED]\2",
            detail,
            flags=re.IGNORECASE,
        )
        detail = re.sub(
            r"(<(?:[\w.-]+:)?Password>).*?(</(?:[\w.-]+:)?Password>)",
            r"\1[REDACTED]\2",
            detail,
            flags=re.IGNORECASE,
        )
        message = f"SUNAT respondió HTTP {error.code}"
        if detail:
            message += f": {detail}"
        raise ValueError(message) from error
    except urllib.error.URLError as error:
        raise ValueError(f"No fue posible conectar con SUNAT: {error.reason}") from error

    document = etree.fromstring(response_xml)
    # SUNAT wraps the base64 CDR in applicationResponse/content, not directly
    # as text on applicationResponse.
    application_response = document.xpath(
        "//*[local-name()='applicationResponse']//*[local-name()='content']/text()"
    )
    if not application_response:
        application_response = document.xpath("//*[local-name()='applicationResponse']/text()")
    if not application_response:
        fault = document.xpath("//*[local-name()='faultstring']/text()")
        raise ValueError(fault[0] if fault else "SUNAT no devolvió un CDR.")
    return application_response[0]


def _send_summary_soap(payload):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ser="http://service.sunat.gob.pe" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
  <soapenv:Header><wsse:Security><wsse:UsernameToken><wsse:Username>{escape(payload["sol_user"])}</wsse:Username><wsse:Password>{escape(payload["sol_password"])}</wsse:Password></wsse:UsernameToken></wsse:Security></soapenv:Header>
  <soapenv:Body><ser:sendSummary><fileName>{escape(payload["fileName"])}</fileName><contentFile>{payload["contentFile"]}</contentFile></ser:sendSummary></soapenv:Body>
</soapenv:Envelope>'''.encode("utf-8")


def _get_status_soap(sol_user, sol_password, ticket):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ser="http://service.sunat.gob.pe" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
  <soapenv:Header><wsse:Security><wsse:UsernameToken><wsse:Username>{escape(sol_user)}</wsse:Username><wsse:Password>{escape(sol_password)}</wsse:Password></wsse:UsernameToken></wsse:Security></soapenv:Header>
  <soapenv:Body><ser:getStatus><ticket>{escape(ticket)}</ticket></ser:getStatus></soapenv:Body>
</soapenv:Envelope>'''.encode("utf-8")


def _post_soap(endpoint, payload, action, timeout=30):
    request = urllib.request.Request(
        endpoint, data=payload,
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": action}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return etree.fromstring(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise ValueError(f"SUNAT respondió HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise ValueError(f"No fue posible conectar con SUNAT: {error.reason}") from error


def send_summary(endpoint, sol_user, sol_password, payload, timeout=30):
    """Submit a Communication of Void and return the asynchronous ticket."""
    document = _post_soap(
        endpoint, _send_summary_soap({**payload, "sol_user": sol_user, "sol_password": sol_password}),
        "urn:sendSummary", timeout,
    )
    ticket = document.xpath("//*[local-name()='ticket']/text()")
    if not ticket:
        fault = document.xpath("//*[local-name()='faultstring']/text()")
        raise ValueError(fault[0] if fault else "SUNAT no devolvió ticket para la comunicación de baja.")
    return ticket[0]


def get_status(endpoint, sol_user, sol_password, ticket, timeout=30):
    """Return SUNAT asynchronous ticket status and optional base64 CDR."""
    document = _post_soap(endpoint, _get_status_soap(sol_user, sol_password, ticket), "urn:getStatus", timeout)
    status = (document.xpath("//*[local-name()='statusCode']/text()") or [""])[0]
    content = document.xpath("//*[local-name()='content']/text()")
    return status, content[0] if content else False
