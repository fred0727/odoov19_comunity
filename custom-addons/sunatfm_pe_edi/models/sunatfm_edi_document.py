import base64
import io
import json
import re
import zipfile

from lxml import etree
from odoo import _, fields, models
from odoo.exceptions import ValidationError

from .sunatfm_signing import sign_xml
from .sunatfm_transport import (
    build_send_bill_payload, extract_cdr, get_gre_api_token, get_gre_status,
    get_status, send_bill, send_gre, send_summary,
)
from .sunatfm_gre_ubl import build_gre_draft
from .sunatfm_ubl import build_credit_note_draft, build_invoice_draft
from .sunatfm_voided_ubl import build_voided_document, voided_document_filename
from .sunatfm_xsd import validate_invoice_xml


def sunat_filename(move, extension="xml"):
    """Build the filename required by SUNAT's ``sendBill`` service.

    Odoo displays invoices as ``F 001-00000001`` while SUNAT expects the RUC,
    document type and series without the display-space.
    """
    ruc = re.sub(r"\D", "", move.company_id.partner_id.vat or "")
    document_code = move.l10n_latam_document_type_id.code or "01"
    document_number = move.l10n_latam_document_number or ""
    match = re.fullmatch(r"([A-Za-z0-9]+)[- ](\d+)", document_number.strip())
    if not match:
        raise ValidationError(
            _("El número del comprobante debe tener formato serie-correlativo antes de enviarlo a SUNAT.")
        )
    series, number = match.groups()
    prefix = move.l10n_latam_document_type_id.doc_code_prefix or "F"
    identifier = f"{prefix}{series}-{number}"
    return f"{ruc}-{document_code}-{identifier}.{extension}"


def sunat_sol_username(company):
    """Return the SOL user in SUNAT's required ``RUC + usuario`` form."""
    ruc = re.sub(r"\D", "", company.partner_id.vat or "")
    username = (company.sunatfm_edi_sol_user or "").strip()
    return username if username.startswith(ruc) else f"{ruc}{username}"


class SunatFmEdiDocument(models.Model):
    """Auditable queue for future direct CPE and GRE transmissions."""

    _name = "sunatfm.pe.edi.document"
    _description = "Documento electrónico SUNAT"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, copy=False, default="Nuevo")
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    account_move_id = fields.Many2one(
        "account.move",
        string="Comprobante contable",
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    stock_picking_id = fields.Many2one(
        "stock.picking",
        string="Guía de remisión",
        ondelete="restrict",
        check_company=True,
        index=True,
        help="Guía asociada cuando el documento electrónico es una GRE.",
    )
    document_kind = fields.Selection(
        [
            ("cpe", "Comprobante de pago electrónico"),
            ("gre", "Guía de remisión electrónica"),
            ("void", "Comunicación de baja"),
        ],
        required=True,
        default="cpe",
    )
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("queued", "En cola"),
            ("sent", "Enviado"),
            ("accepted", "Aceptado"),
            ("rejected", "Rechazado"),
        ],
        required=True,
        default="draft",
        copy=False,
        index=True,
    )
    response_code = fields.Char(copy=False, readonly=True)
    response_message = fields.Text(copy=False, readonly=True)
    error_message = fields.Text(
        string="Error técnico",
        copy=False,
        readonly=True,
        help="Error local de validación, firma, conexión o lectura de la respuesta.",
    )
    xml_filename = fields.Char(copy=False, readonly=True)
    xml_file = fields.Binary(copy=False, readonly=True, attachment=True)
    cdr_filename = fields.Char(copy=False, readonly=True)
    cdr_file = fields.Binary(copy=False, readonly=True, attachment=True)
    ticket = fields.Char(copy=False, readonly=True)
    void_reason = fields.Char(string="Motivo de anulación", copy=False, readonly=True)

    def action_send(self):
        self.ensure_one()
        if self.document_kind == "void":
            return self._action_send_void()
        if self.document_kind == "gre":
            return self._action_send_gre()
        if self.document_kind != "cpe" or not self.account_move_id:
            raise ValidationError(_("Solo se pueden enviar CPE vinculados a una factura."))
        # Credentials/certificate are protected company fields. Sending is
        # an allowed business operation, so read them with sudo without
        # granting users access to the secrets themselves.
        company = self.company_id.sudo()
        if not company.sunatfm_edi_cpe_enabled:
            raise ValidationError(_("Active CPE SUNAT en la configuración de la empresa."))
        if not all((company.sunatfm_edi_certificate_p12, company.sunatfm_edi_certificate_password, company.sunatfm_edi_sol_user, company.sunatfm_edi_sol_password)):
            raise ValidationError(_("Complete certificado y credenciales SOL antes de enviar."))

        try:
            move = self.account_move_id
            unsigned_xml = (
                build_credit_note_draft(move, draft=False)
                if move.move_type == "out_refund"
                else build_invoice_draft(move, draft=False)
            )
            if move.move_type == "out_refund":
                # The bundled XSD set currently contains the Invoice schema.
                # Credit notes have a different main document XSD, so validate
                # well-formedness here and let SUNAT validate its own CPE 07
                # schema rather than incorrectly applying UBL-Invoice to it.
                etree.fromstring(unsigned_xml)
            else:
                schema_errors = validate_invoice_xml(unsigned_xml)
                if schema_errors:
                    raise ValidationError(_("El XML no supera el XSD UBL 2.1: %s") % " | ".join(schema_errors[:3]))
            signed_xml = sign_xml(
                unsigned_xml,
                base64.b64decode(company.sunatfm_edi_certificate_p12),
                company.sunatfm_edi_certificate_password,
            )
            # send_bill expects a ZIP payload; build it from the signed XML.
            xml_filename = sunat_filename(self.account_move_id)
            # Persist the exact signed XML *before* the HTTP call.  A network
            # error does not prove SUNAT did not receive it, so preserving it
            # is essential for auditability and subsequent support.
            self.write({
                "state": "sent",
                "xml_filename": xml_filename,
                "xml_file": base64.b64encode(signed_xml),
                "cdr_filename": False,
                "cdr_file": False,
                "response_code": False,
                "response_message": False,
                "error_message": False,
            })
            payload = build_send_bill_payload(signed_xml, xml_filename)
            cdr_payload = send_bill(
                company.sunatfm_edi_service_endpoint,
                sunat_sol_username(company),
                company.sunatfm_edi_sol_password,
                payload,
            )
            cdr_filename, cdr_zip, cdr_xml = extract_cdr(cdr_payload)
            cdr_root = etree.fromstring(cdr_xml)
            response_code = (cdr_root.xpath("//*[local-name()='ResponseCode']/text()") or [""])[0]
            response_message = (cdr_root.xpath("//*[local-name()='Description']/text()") or [""])[0]
            self.write({
                "state": "accepted" if response_code == "0" else "rejected",
                "xml_filename": xml_filename,
                "xml_file": base64.b64encode(signed_xml),
                "cdr_filename": cdr_filename,
                # Preserve the original ZIP returned by SUNAT. This is the
                # CDR that users and auditors expect to download; cdr_xml is
                # used only to read its response code and description.
                "cdr_file": base64.b64encode(cdr_zip),
                "response_code": response_code,
                "response_message": response_message,
                "error_message": False,
            })
        except Exception as error:
            self.write({
                "state": "rejected",
                "response_code": False,
                "response_message": False,
                "error_message": str(error),
            })
            # The technical error is persisted and shown in the invoice's
            # “Facturación electrónica” tab.  Do not raise it again here: a
            # modal RPC error hides the fiscal result and forces the user to
            # rely on a transient notification.
            return False
        return True

    def _action_send_gre(self):
        """Validate GRE-specific prerequisites before REST transmission.

        GRE uses the REST API and a DespatchAdvice XML, not the invoice SOAP
        path. The XML builder is deliberately checked separately so a missing
        GRE payload cannot be mistaken for an invoice error.
        """
        self.ensure_one()
        picking = self.stock_picking_id
        company = self.company_id.sudo()
        if not picking:
            raise ValidationError(_("La GRE no está vinculada a una orden de entrega."))
        if not company.sunatfm_edi_gre_enabled:
            raise ValidationError(_("Active GRE SUNAT en la configuración de la empresa."))
        if not all((company.sunatfm_edi_certificate_p12, company.sunatfm_edi_certificate_password,
                    company.sunatfm_edi_gre_api_client_id, company.sunatfm_edi_gre_api_client_secret,
                    company.sunatfm_edi_sol_user, company.sunatfm_edi_sol_password)):
            raise ValidationError(_("Complete certificado, credenciales API GRE y credenciales SOL antes de enviar."))
        try:
            unsigned_xml = build_gre_draft(picking)
            signed_xml = sign_xml(
                unsigned_xml,
                base64.b64decode(company.sunatfm_edi_certificate_p12),
                company.sunatfm_edi_certificate_password,
            )
            xml_filename = "%s-09-%s.xml" % (
                re.sub(r"\D", "", company.partner_id.vat or ""), picking.sunatfm_gre_number,
            )
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(xml_filename, signed_xml)
            zip_bytes = zip_buffer.getvalue()
            self.write({
                "state": "sent", "name": picking.sunatfm_gre_number,
                "xml_filename": xml_filename, "xml_file": base64.b64encode(signed_xml),
                "error_message": False, "response_message": False,
            })
            token_data = get_gre_api_token(
                company.sunatfm_edi_gre_api_client_id,
                company.sunatfm_edi_gre_api_client_secret,
                sunat_sol_username(company), company.sunatfm_edi_sol_password,
            )
            result = send_gre(token_data["access_token"], company.sunatfm_edi_environment, xml_filename, zip_bytes)
            self.write({
                "ticket": result.get("numTicket"),
                "response_message": json.dumps(result, ensure_ascii=False),
                "error_message": False,
            })
            return True
        except Exception as error:
            self.write({"state": "rejected", "error_message": str(error)})
            return False

    def _action_send_void(self):
        self.ensure_one()
        move = self.account_move_id
        company = self.company_id.sudo()
        if not move or not self.void_reason:
            raise ValidationError(_("La comunicación de baja debe indicar el comprobante y el motivo."))
        if not all((company.sunatfm_edi_certificate_p12, company.sunatfm_edi_certificate_password, company.sunatfm_edi_sol_user, company.sunatfm_edi_sol_password)):
            raise ValidationError(_("Complete certificado y credenciales SOL antes de enviar."))
        try:
            issue_date = fields.Date.context_today(self)
            sequence = int((self.name or "RA-00000000-0001").rsplit("-", 1)[-1])
            unsigned_xml = build_voided_document(move, issue_date, sequence, self.void_reason)
            xml_filename = voided_document_filename(company, issue_date, sequence)
            signed_xml = sign_xml(
                unsigned_xml, base64.b64decode(company.sunatfm_edi_certificate_p12), company.sunatfm_edi_certificate_password,
            )
            self.write({"state": "sent", "xml_filename": xml_filename, "xml_file": base64.b64encode(signed_xml), "error_message": False})
            ticket = send_summary(
                company.sunatfm_edi_service_endpoint, sunat_sol_username(company), company.sunatfm_edi_sol_password,
                build_send_bill_payload(signed_xml, xml_filename),
            )
            self.write({"ticket": ticket, "response_message": _("Comunicación de baja enviada. Ticket SUNAT: %s") % ticket})
            return True
        except Exception as error:
            self.write({"state": "rejected", "error_message": str(error)})
            return False

    def action_check_void_status(self):
        self.ensure_one()
        if self.document_kind != "void" or not self.ticket:
            raise ValidationError(_("No existe un ticket SUNAT para consultar."))
        company = self.company_id.sudo()
        try:
            status, content = get_status(
                company.sunatfm_edi_service_endpoint, sunat_sol_username(company), company.sunatfm_edi_sol_password, self.ticket,
            )
            if status == "98":
                self.write({
                    "state": "sent",
                    "response_code": status,
                    "response_message": _("SUNAT aún procesa la comunicación de baja."),
                })
                return False
            # A completed ticket can return status 0 (processed) or 99
            # (processed with errors).  In both cases SUNAT attaches a ZIP
            # containing the CDR, which must remain available for audit and
            # to show the actual rejection reason.
            if status not in {"0", "99"} or not content:
                raise ValueError(_("SUNAT devolvió un estado de ticket no reconocido: %s") % status)
            cdr_filename, cdr_zip, cdr_xml = extract_cdr(content)
            root = etree.fromstring(cdr_xml)
            code = (root.xpath("//*[local-name()='ResponseCode']/text()") or [""])[0]
            message = (root.xpath("//*[local-name()='Description']/text()") or [""])[0]
            self.write({
                "state": "accepted" if code == "0" else "rejected", "response_code": code,
                "response_message": message, "cdr_filename": cdr_filename,
                "cdr_file": base64.b64encode(cdr_zip), "error_message": False,
            })
            if code == "0" and self.account_move_id and self.account_move_id.state != "cancel":
                # Odoo's button_cancel() internally passes a posted move
                # through draft first. This is an authorized system action
                # after SUNAT confirms the void, not a manual reset.
                self.account_move_id.with_context(debug=True).button_cancel()
            return True
        except Exception as error:
            self.write({"state": "rejected", "error_message": str(error)})
            return False

    def action_check_gre_status(self):
        self.ensure_one()
        if self.document_kind != "gre" or not self.ticket:
            raise ValidationError(_("No existe un ticket GRE para consultar."))
        company = self.company_id.sudo()
        try:
            token_data = get_gre_api_token(
                company.sunatfm_edi_gre_api_client_id,
                company.sunatfm_edi_gre_api_client_secret,
                sunat_sol_username(company), company.sunatfm_edi_sol_password,
            )
            result = get_gre_status(
                token_data["access_token"], company.sunatfm_edi_environment, self.ticket,
            )
            code = str(result.get("codRespuesta") or result.get("codigoRespuesta") or "")
            error_data = result.get("error") if isinstance(result.get("error"), dict) else {}
            message = (
                result.get("mensaje") or result.get("message") or result.get("descripcion")
                or error_data.get("desError") or error_data.get("mensaje")
                or json.dumps(error_data, ensure_ascii=False) if error_data else ""
            )
            if error_data.get("numError"):
                code = str(error_data["numError"])
            values = {
                "state": "accepted" if code == "0" else "rejected",
                "response_code": code,
                "response_message": json.dumps(result, ensure_ascii=False),
                "error_message": False if code == "0" else message,
            }
            cdr = result.get("arcCdr") or result.get("cdr")
            if cdr:
                values.update({
                    "cdr_filename": "%s-CDR.zip" % (self.name or "GRE"),
                    "cdr_file": base64.b64encode(base64.b64decode(cdr)),
                })
            self.write(values)
            return code == "0"
        except Exception as error:
            self.write({"state": "rejected", "error_message": str(error)})
            return False

    def action_send_beta(self):
        """Backward-compatible alias for previously created custom views."""
        return self.action_send()
