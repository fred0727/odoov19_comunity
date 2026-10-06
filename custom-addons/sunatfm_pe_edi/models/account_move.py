import base64
import re
from urllib.parse import quote

from lxml import etree
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .sunatfm_edi_document import sunat_filename
from .sunatfm_ubl import build_credit_note_draft, build_invoice_draft
from .sunatfm_edi_validation import validate_customer_invoice
from .sunatfm_qr import qr_png_base64


class AccountMove(models.Model):
    _inherit = "account.move"

    sunatfm_edi_withholding_applicable = fields.Boolean(
        string="Sujeta a retención IGV",
        compute="_compute_sunatfm_edi_withholding",
        store=True,
    )
    sunatfm_edi_withholding_rate = fields.Float(
        string="Tasa de retención (%)",
        default=3.0,
        copy=False,
    )
    sunatfm_edi_withholding_base = fields.Monetary(
        string="Base de retención",
        compute="_compute_sunatfm_edi_withholding",
        store=True,
        currency_field="currency_id",
    )
    sunatfm_edi_withholding_amount = fields.Monetary(
        string="Retención estimada",
        compute="_compute_sunatfm_edi_withholding",
        store=True,
        currency_field="currency_id",
    )
    sunatfm_edi_withholding_net_amount = fields.Monetary(
        string="Neto estimado a recibir",
        compute="_compute_sunatfm_edi_withholding",
        store=True,
        currency_field="currency_id",
    )

    @api.depends("partner_id", "partner_id.commercial_partner_id.sunatfm_edi_withholding_agent",
                 "amount_total", "sunatfm_edi_withholding_rate", "move_type",
                 "sunatfm_edi_operation_type", "invoice_line_ids.product_id",
                 "invoice_line_ids.product_id.product_tmpl_id.sunatfm_edi_detraction_id")
    def _compute_sunatfm_edi_withholding(self):
        for move in self:
            partner = move.partner_id.commercial_partner_id
            product_lines = move.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
            )
            has_igv_operation = any(
                tax.l10n_pe_edi_tax_code == "1000"
                for line in product_lines
                for tax in line.tax_ids
            )
            has_detraction = bool(
                move.sunatfm_edi_operation_type in {"1001", "1002", "1003", "1004"}
                and any(
                    line.product_id.product_tmpl_id.sunatfm_edi_detraction_id
                    for line in product_lines
                )
            )
            applicable = bool(
                move.move_type == "out_invoice"
                and partner.sunatfm_edi_withholding_agent
                and abs(move.amount_total) > 700.0
                and has_igv_operation
                and not has_detraction
            )
            base = abs(move.amount_total) if applicable else 0.0
            amount = round(base * max(move.sunatfm_edi_withholding_rate, 0.0) / 100, 2)
            move.sunatfm_edi_withholding_applicable = applicable
            move.sunatfm_edi_withholding_base = base
            move.sunatfm_edi_withholding_amount = amount
            move.sunatfm_edi_withholding_net_amount = base - amount

    def button_draft(self):
        """Prevent undoing a posted CPE after SUNAT has processed it."""
        blocked = self.filtered(
            lambda move: move.is_invoice()
            and move.sunatfm_edi_last_state in ("sent", "accepted", "rejected", "cancelled")
            and not self.env.context.get("debug")
            and not self.env.user.has_group("base.group_system")
        )
        if blocked:
            raise UserError(_(
                "No se puede restablecer a borrador un comprobante enviado, aceptado o rechazado por SUNAT. "
                "Revise su estado de facturación electrónica."
            ))
        return super().button_draft()

    def _get_report_base_filename(self):
        """Use SUNAT's compact series in downloaded electronic documents."""
        self.ensure_one()
        filename = super()._get_report_base_filename()
        if self.move_type in {"out_invoice", "out_refund"} and self.l10n_latam_document_type_id.code in {"01", "03", "07", "08"}:
            # Odoo displays the series as ``F 001`` for readability, while the
            # downloadable fiscal representation must retain ``F001``.
            return re.sub(r"^([A-Za-z])\s+(\w+-\d+)$", r"\1\2", filename or "")
        return filename

    @api.depends("suitable_journal_ids", "move_type")
    def _compute_show_journal(self):
        """Keep the journal selector visible on invoices.

        Odoo hides the journal field when there is only one suitable journal.
        That is convenient for a generic accounting entry, but for SUNAT
        documents it is useful to make the selected sales/purchase journal
        explicit (and it matches the accounting workflow used by Enterprise).
        The field domain remains the standard ``suitable_journal_ids`` domain.
        """
        super()._compute_show_journal()
        invoice_types = {
            "out_invoice", "out_refund", "out_receipt",
            "in_invoice", "in_refund", "in_receipt",
        }
        for move in self:
            if move.move_type in invoice_types:
                move.show_journal = True

    sunatfm_edi_operation_type = fields.Selection(
        [
            ("0101", "0101 - Venta interna"),
            ("0200", "0200 - Exportación de bienes"),
            ("1001", "1001 - Operación sujeta a detracción"),
            ("1002", "1002 - Detracción: recursos hidrobiológicos"),
            ("1003", "1003 - Detracción: transporte de pasajeros"),
            ("1004", "1004 - Detracción: transporte de carga"),
        ],
        string="Tipo de operación SUNAT",
        default="0101",
        copy=False,
    )
    sunatfm_edi_note_reason_code = fields.Selection(
        [
            ("01", "Anulación de la operación"),
            ("02", "Anulación por error en el RUC"),
            ("03", "Corrección por error en la descripción"),
            ("06", "Devolución total"),
            ("07", "Descuento total"),
            ("08", "Bonificación"),
            ("09", "Disminución en el valor"),
            ("10", "Otros conceptos"),
            ("11", "Ajuste de operaciones de exportación"),
        ],
        string="Motivo de nota de crédito SUNAT",
        default="01",
        copy=False,
    )

    @api.model
    def _sunatfm_edi_credit_note_reason_selection(self):
        return self._fields["sunatfm_edi_note_reason_code"].selection
    sunatfm_edi_document_ids = fields.One2many(
        "sunatfm.pe.edi.document", "account_move_id", string="Documentos SUNAT"
    )
    sunatfm_edi_document_count = fields.Integer(compute="_compute_sunatfm_edi_document_count")
    sunatfm_edi_xml_available = fields.Boolean(
        string="XML enviado disponible", compute="_compute_sunatfm_edi_file_availability"
    )
    sunatfm_edi_cdr_available = fields.Boolean(
        string="CDR SUNAT disponible", compute="_compute_sunatfm_edi_file_availability"
    )
    sunatfm_edi_last_state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("queued", "En cola"),
            ("sent", "Enviado"),
            ("accepted", "Aceptado"),
            ("rejected", "Rechazado"),
            ("cancelled", "Cancelado"),
        ],
        string="Estado SUNAT",
        compute="_compute_sunatfm_edi_last_result",
    )
    sunatfm_edi_status_display = fields.Char(
        string="Facturación electrónica",
        compute="_compute_sunatfm_edi_last_result",
    )
    sunatfm_edi_void_pending = fields.Boolean(
        string="Baja SUNAT pendiente",
        compute="_compute_sunatfm_edi_last_result",
    )
    sunatfm_edi_last_response_code = fields.Char(
        string="Código de respuesta SUNAT",
        compute="_compute_sunatfm_edi_last_result",
    )
    sunatfm_edi_last_response_message = fields.Text(
        string="Respuesta SUNAT",
        compute="_compute_sunatfm_edi_last_result",
        inverse="_inverse_sunatfm_edi_last_result_display",
    )
    sunatfm_edi_last_error = fields.Text(
        string="Error del sistema",
        compute="_compute_sunatfm_edi_last_result",
        inverse="_inverse_sunatfm_edi_last_result_display",
    )
    sunatfm_edi_detraction_id = fields.Many2one(
        "sunatfm.pe.detraction", string="Detracción SUNAT",
        compute="_compute_sunatfm_edi_detraction", store=True,
    )
    sunatfm_edi_detraction_amount = fields.Monetary(
        string="Monto detracción (PEN)", compute="_compute_sunatfm_edi_detraction", store=True,
        currency_field="company_currency_id",
    )

    @api.depends(
        "invoice_line_ids.product_id",
        "invoice_line_ids.product_id.product_tmpl_id.sunatfm_edi_detraction_id",
        "invoice_line_ids.product_id.product_tmpl_id.sunatfm_edi_detraction_id.rate",
        "invoice_line_ids.price_subtotal",
        "amount_total",
        "amount_total_signed",
        "sunatfm_edi_operation_type",
    )
    def _compute_sunatfm_edi_detraction(self):
        for move in self:
            if move.sunatfm_edi_operation_type not in {"1001", "1002", "1003", "1004"}:
                move.sunatfm_edi_detraction_id = False
                move.sunatfm_edi_detraction_amount = 0.0
                continue
            configurations = move.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
                and line.product_id.product_tmpl_id.sunatfm_edi_detraction_id
            ).mapped("product_id.product_tmpl_id.sunatfm_edi_detraction_id")
            move.sunatfm_edi_detraction_id = configurations[:1] if len(configurations) == 1 else False
            move.sunatfm_edi_detraction_amount = (
                abs(move.amount_total_signed) * move.sunatfm_edi_detraction_id.rate / 100
                if move.sunatfm_edi_detraction_id else 0
            )

    @api.onchange("invoice_line_ids")
    def _onchange_sunatfm_edi_detraction_operation(self):
        for move in self:
            configurations = move.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
                and line.product_id.product_tmpl_id.sunatfm_edi_detraction_id
            ).mapped("product_id.product_tmpl_id.sunatfm_edi_detraction_id")
            if len(configurations) == 1:
                detraction = configurations
                move.sunatfm_edi_operation_type = {
                    ("II", "001"): "1002",
                    ("III", "008"): "1003",
                    ("III", "027"): "1004",
                }.get((detraction.annex, detraction.code), "1001")
            elif not configurations and move.sunatfm_edi_operation_type in {"1001", "1002", "1003", "1004"}:
                move.sunatfm_edi_operation_type = "0101"

    def _compute_sunatfm_edi_document_count(self):
        for move in self:
            move.sunatfm_edi_document_count = self.env["sunatfm.pe.edi.document"].search_count(
                [("account_move_id", "=", move.id)]
            )

    @api.depends(
        "sunatfm_edi_document_ids.create_date",
        "sunatfm_edi_document_ids.state",
        "sunatfm_edi_document_ids.document_kind",
        "sunatfm_edi_document_ids.xml_file",
        "sunatfm_edi_document_ids.cdr_file",
    )
    def _compute_sunatfm_edi_file_availability(self):
        """Expose only files that are part of an attempted SUNAT delivery.

        A local UBL draft also uses ``xml_file`` but must not be presented as
        the XML sent to SUNAT.  ``action_send`` changes its state before the
        network call, so failed transmissions retain their signed XML for
        audit and support purposes.
        """
        for move in self:
            documents = move.sunatfm_edi_document_ids.filtered(
                lambda document: document.state != "draft"
            )
            move.sunatfm_edi_xml_available = bool(documents.filtered("xml_file"))
            move.sunatfm_edi_cdr_available = bool(documents.filtered("cdr_file"))

    def _sunatfm_edi_latest_file_document(self, field_name, sent_only=False):
        """Return the newest electronic document containing ``field_name``."""
        self.ensure_one()
        documents = self.sunatfm_edi_document_ids
        if sent_only:
            documents = documents.filtered(lambda document: document.state != "draft")
        return documents.filtered(field_name).sorted(
            key=lambda document: (document.create_date or "", document.id), reverse=True
        )[:1]

    def _sunatfm_edi_download_file(self, field_name, sent_only=False):
        self.ensure_one()
        document = self._sunatfm_edi_latest_file_document(field_name, sent_only=sent_only)
        if not document:
            raise UserError(_("El archivo todavía no está disponible."))
        filename_field = "xml_filename" if field_name == "xml_file" else "cdr_filename"
        filename = getattr(document, filename_field) or ("documento.xml" if field_name == "xml_file" else "cdr.zip")
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s/%s/%s/%s?download=true" % (
                document._name, document.id, field_name, quote(filename),
            ),
            "target": "self",
        }

    def action_sunatfm_edi_download_xml(self):
        """Download the signed XML actually sent (including failed attempts)."""
        return self._sunatfm_edi_download_file("xml_file", sent_only=True)

    def action_sunatfm_edi_download_cdr(self):
        """Download the CDR returned by SUNAT for the latest response."""
        return self._sunatfm_edi_download_file("cdr_file")

    @api.depends(
        "sunatfm_edi_document_ids.create_date",
        "sunatfm_edi_document_ids.state",
        "sunatfm_edi_document_ids.response_code",
        "sunatfm_edi_document_ids.response_message",
        "sunatfm_edi_document_ids.error_message",
        "sunatfm_edi_document_ids.ticket",
    )
    def _compute_sunatfm_edi_last_result(self):
        status_labels = {
            "draft": _("Borrador"),
            "queued": _("Pendiente de envío"),
            "sent": _("En proceso SUNAT"),
            "accepted": _("Aceptado por SUNAT"),
            "rejected": _("Rechazado por SUNAT"),
            "cancelled": _("Anulado"),
        }
        for move in self:
            documents = move.sunatfm_edi_document_ids
            void_document = documents.filtered(
                lambda item: item.document_kind == "void" and item.state == "accepted"
            ).sorted(key=lambda item: (item.create_date or "", item.id), reverse=True)[:1]
            document = (void_document or documents).sorted(
                key=lambda item: (item.create_date or "", item.id), reverse=True
            )[:1]
            state = "cancelled" if void_document else (document.state if document else False)
            move.sunatfm_edi_last_state = state
            move.sunatfm_edi_status_display = status_labels.get(
                state, _("Sin enviar")
            )
            move.sunatfm_edi_void_pending = bool(
                documents.filtered(
                    lambda item: item.document_kind == "void"
                    and item.state == "sent"
                    and item.ticket
                )
            )
            move.sunatfm_edi_last_response_code = document.response_code if document else False
            move.sunatfm_edi_last_response_message = document.response_message if document else False
            move.sunatfm_edi_last_error = document.error_message if document else False

    def _inverse_sunatfm_edi_last_result_display(self):
        """Keep the displayed response selectable without editing the CDR."""
        # These fields are a read-only projection of the latest electronic
        # document.  The no-op inverse makes Odoo render a selectable textarea
        # for long responses; the authoritative value remains on the document.
        return True

    def sunatfm_edi_payment_installments(self):
        """Return the original Odoo payment-term maturities for printing."""
        self.ensure_one()
        lines = self.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable" and line.date_maturity
        ).sorted(lambda line: (line.date_maturity, line.id))
        return [
            (index, line.date_maturity, abs(line.amount_currency or line.balance))
            for index, line in enumerate(lines, 1)
        ]

    def sunatfm_edi_is_credit_sale(self):
        """A sale is credit when Odoo has a maturity after invoice date."""
        self.ensure_one()
        return bool(
            self.invoice_date
            and any(due_date > self.invoice_date for _index, due_date, _amount in self.sunatfm_edi_payment_installments())
        )

    def sunatfm_edi_tax_summary(self):
        """Return printable bases and taxes from Odoo's actual tax totals.

        Labels are based on SUNAT catalog 05 codes rather than a fixed IGV
        rate, so export, exonerated, unaffected and free operations keep
        their fiscal meaning in the printed representation.
        """
        self.ensure_one()
        base_labels = {
            "1000": "GRAVADA",
            "1016": "GRAVADA",
            "2000": "GRAVADA",
            "9995": "EXPORTACIÓN",
            "9996": "GRATUITA",
            "9997": "EXONERADA",
            "9998": "INAFECTA",
            "9999": "OTROS",
        }
        tax_labels = {
            "1000": "IGV",
            "1016": "IVAP",
            "2000": "ISC",
            "7152": "ICBPER",
            "9995": "EXP",
            "9996": "GRA",
            "9997": "EXO",
            "9998": "INA",
            "9999": "OTROS",
        }
        rows = []
        for subtotal in (self.tax_totals or {}).get("subtotals", []):
            for group in subtotal.get("tax_groups", []):
                taxes = self.env["account.tax"].browse(group.get("involved_tax_ids", [])).exists()
                tax = taxes[:1]
                tax_code = tax.l10n_pe_edi_tax_code if tax else False
                base_label = base_labels.get(tax_code, "SUBTOTAL")
                tax_label = tax_labels.get(tax_code, group.get("group_name") or "IMPUESTO")
                base_amount = group.get("base_amount_currency", 0.0)
                tax_amount = group.get("tax_amount_currency", 0.0)
                rows.append((base_label, base_amount))
                if tax:
                    rate = tax.amount if tax.amount_type == "percent" else 0.0
                    label = f"{tax_label} {rate:.2f} %"
                else:
                    label = tax_label
                rows.append((label, tax_amount))
        if not rows:
            rows.append(("SUBTOTAL", self.amount_untaxed))
        return rows

    def sunatfm_edi_igv_amount(self):
        """Return only IGV for the regulated CPE QR payload."""
        self.ensure_one()
        igv_lines = self.line_ids.filtered(
            lambda line: line.tax_line_id.l10n_pe_edi_tax_code == "1000"
        )
        return sum(abs(line.amount_currency or line.balance) for line in igv_lines)

    def _sunatfm_edi_qr_components(self):
        """Build regulated QR data from the exact signed, accepted CPE XML."""
        self.ensure_one()
        if self.move_type not in {"out_invoice", "out_refund"}:
            return False
        document = self.sunatfm_edi_document_ids.filtered(
            lambda item: item.document_kind == "cpe" and item.state == "accepted" and item.xml_file
        ).sorted(lambda item: (item.create_date or "", item.id), reverse=True)[:1]
        if not document:
            return False
        try:
            root = etree.fromstring(base64.b64decode(document.xml_file))
            digest = (root.xpath("//*[local-name()='DigestValue']/text()") or [False])[0]
        except Exception:
            return False
        document_number = (self.l10n_latam_document_number or self.name or "").replace(" ", "")
        match = re.fullmatch(r"([A-Za-z0-9]+)-(\d+)", document_number)
        if not match or not digest:
            return False
        serial, correlative = match.groups()
        prefix = self.l10n_latam_document_type_id.doc_code_prefix or "F"
        if not serial.upper().startswith(prefix.upper()):
            serial = f"{prefix}{serial}"
        identification = self.partner_id.l10n_latam_identification_type_id
        receiver_type = identification.l10n_pe_vat_code if identification else "-"
        return (
            re.sub(r"\D", "", self.company_id.partner_id.vat or ""),
            self.l10n_latam_document_type_id.code or "",
            serial,
            correlative,
            f"{self.sunatfm_edi_igv_amount():.2f}",
            f"{abs(self.amount_total):.2f}",
            str(self.invoice_date or ""),
            receiver_type or "-",
            self.partner_id.vat or "-",
            digest,
        )

    def sunatfm_edi_qr_image(self):
        """Return the base64 QR image for an accepted electronic invoice/note."""
        components = self._sunatfm_edi_qr_components()
        return qr_png_base64("|".join(components)) if components else False

    def _sunatfm_edi_validate_draft(self):
        self.ensure_one()
        if self.move_type not in {"out_invoice", "out_refund"}:
            raise ValidationError(_("Solo se preparan facturas y notas de crédito de cliente."))
        if self.state != "posted":
            raise ValidationError(_("Primero contabilice el comprobante antes de preparar el XML."))
        if not self.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            raise ValidationError(_("La factura debe incluir al menos una línea de producto o servicio."))
        detractions = self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product" and line.product_id.product_tmpl_id.sunatfm_edi_detraction_id
        ).mapped("product_id.product_tmpl_id.sunatfm_edi_detraction_id")
        is_detraction = self.sunatfm_edi_operation_type in {"1001", "1002", "1003", "1004"}
        if is_detraction and len(detractions) > 1:
            raise ValidationError(_("No se pueden mezclar códigos de detracción distintos en una factura."))
        if is_detraction and not detractions:
            raise ValidationError(_("Configure la detracción en el producto antes de emitir esta operación."))
        if is_detraction:
            non_detracted_lines = self.invoice_line_ids.filtered(
                lambda line: line.display_type == "product"
                and not line.product_id.product_tmpl_id.sunatfm_edi_detraction_id
            )
            if non_detracted_lines:
                raise ValidationError(
                    _("Una factura sujeta a detracción no puede mezclar líneas no sujetas al sistema.")
                )
            if self.currency_id.name != "PEN":
                raise ValidationError(_("Las detracciones se validan actualmente únicamente en moneda PEN."))
            if abs(self.amount_total) <= 700:
                raise ValidationError(
                    _("La operación sujeta a detracción debe superar S/ 700.00 según las reglas SUNAT.")
                )
            expected_operation = {
                ("II", "001"): "1002",  # Recursos hidrobiológicos
                ("III", "008"): "1003",  # Transporte de pasajeros
                ("III", "027"): "1004",  # Transporte de carga
            }.get((detractions[0].annex, detractions[0].code), "1001")
            if self.sunatfm_edi_operation_type != expected_operation:
                # The detraction is configured on the product. Keep the
                # fiscal operation synchronized even when an invoice was
                # created before the product configuration was completed.
                # Relying only on onchange leaves posted invoices with 0101.
                self.write({"sunatfm_edi_operation_type": expected_operation})
            bank_account = self.company_id._sunatfm_get_detraction_bank_account()
            account = bank_account.acc_number if bank_account else self.company_id.sunatfm_edi_detraction_account
            if not account:
                configuration = self.env["sunatfm.pe.edi.configuration"].search(
                    [("company_id", "=", self.company_id.id), ("active", "=", True)], limit=1
                )
                account = configuration.detraction_account if configuration else False
            if not account:
                raise ValidationError(
                    _("Configure la cuenta de detracciones de la empresa antes de preparar el XML.")
                )
        errors = validate_customer_invoice(self)
        if errors:
            raise ValidationError("\n".join(f"• {error}" for error in errors))

    def action_sunatfm_edi_prepare_draft(self):
        """Create a local UBL draft. This method never performs network I/O."""
        self._sunatfm_edi_validate_draft()
        self.ensure_one()
        document = self.env["sunatfm.pe.edi.document"].create({
            "name": self.name,
            "company_id": self.company_id.id,
            "account_move_id": self.id,
            "document_kind": "cpe",
            "state": "draft",
            "xml_filename": sunat_filename(self),
            "xml_file": base64.b64encode(
                build_credit_note_draft(self) if self.move_type == "out_refund" else build_invoice_draft(self)
            ),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Borrador UBL SUNAT"),
            "res_model": "sunatfm.pe.edi.document",
            "res_id": document.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_sunatfm_edi_view_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Documentos electrónicos"),
            "res_model": "sunatfm.pe.edi.document",
            "view_mode": "list,form",
            "domain": [("account_move_id", "=", self.id)],
            "context": {"default_account_move_id": self.id},
        }

    def action_sunatfm_edi_check_sunat(self):
        """Manually query the pending void ticket and refresh this invoice."""
        self.ensure_one()
        document = self.sunatfm_edi_document_ids.filtered(
            lambda item: item.document_kind == "void"
            and item.state == "sent"
            and item.ticket
        ).sorted(key=lambda item: (item.create_date or "", item.id), reverse=True)[:1]
        if not document:
            raise ValidationError(_("No existe una comunicación de baja pendiente de consulta."))
        document.action_check_void_status()
        return {
            "type": "ir.actions.act_window",
            "name": _("Factura"),
            "res_model": "account.move",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_sunatfm_edi_void_wizard(self):
        """Ask for the legal reason before submitting an RA to SUNAT."""
        self.ensure_one()
        if self.move_type not in ("out_invoice", "out_refund") or self.state != "posted":
            raise ValidationError(_("Solo se puede solicitar la baja de un comprobante de venta contabilizado."))
        if self.l10n_latam_document_type_id.code == "03":
            raise ValidationError(_(
                "Las boletas no se anulan mediante una Comunicación de Baja (RA). "
                "Deben incluirse en un Resumen Diario de boletas según SUNAT."
            ))
        if self.sunatfm_edi_last_state != "accepted":
            raise ValidationError(_("El comprobante debe estar aceptado por SUNAT antes de solicitar su baja."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Anular comprobante"),
            "res_model": "sunatfm.pe.edi.void.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_account_move_id": self.id},
        }

    def _sunatfm_edi_submit_void(self, reason):
        """Create and submit a one-invoice RA communication of void."""
        self.ensure_one()
        if self.l10n_latam_document_type_id.code == "03":
            raise ValidationError(_(
                "Las boletas no se anulan mediante una Comunicación de Baja (RA). "
                "Deben incluirse en un Resumen Diario de boletas según SUNAT."
            ))
        if self.sunatfm_edi_document_ids.filtered(lambda document: document.document_kind == "void" and document.state in {"draft", "sent", "accepted"}):
            raise ValidationError(_("Ya existe una comunicación de baja en curso o aceptada para este comprobante."))
        today = fields.Date.context_today(self)
        prefix = f"RA-{today.strftime('%Y%m%d')}-"
        count = self.env["sunatfm.pe.edi.document"].search_count([
            ("company_id", "=", self.company_id.id), ("document_kind", "=", "void"), ("name", "like", f"{prefix}%"),
        ])
        document = self.env["sunatfm.pe.edi.document"].create({
            "name": f"{prefix}{count + 1:04d}", "company_id": self.company_id.id,
            "account_move_id": self.id, "document_kind": "void", "void_reason": reason,
        })
        document.action_send()
        return {
            "type": "ir.actions.act_window", "name": _("Factura"),
            "res_model": "account.move", "res_id": self.id,
            "view_mode": "form", "target": "current",
        }

    def action_sunatfm_edi_send(self):
        self.ensure_one()
        self._sunatfm_edi_validate_draft()
        document = self.sunatfm_edi_document_ids.filtered(lambda item: item.state in ("draft", "rejected"))[:1]
        if not document:
            self.action_sunatfm_edi_prepare_draft()
            document = self.sunatfm_edi_document_ids[:1]
        document.action_send()
        return {
            "type": "ir.actions.act_window",
            "name": _("Factura"),
            "res_model": "account.move",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    # Compatibility for documents or customizations created before the
    # button was renamed. The visible action is now simply "Enviar a SUNAT".
    def action_sunatfm_edi_send_beta(self):
        return self.action_sunatfm_edi_send()
