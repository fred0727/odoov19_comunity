"""GRE extensions used by the printable delivery slip."""

import base64
import io
import zipfile

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .sunatfm_qr import qr_png_base64


class StockPicking(models.Model):
    _inherit = "stock.picking"

    sunatfm_gre_motivo_traslado = fields.Selection([
        ("01", "Venta"),
        ("02", "Compra"),
        ("03", "Venta con entrega a terceros"),
        ("04", "Traslado entre establecimientos de la misma empresa"),
        ("05", "Consignación"),
        ("06", "Devolución"),
        ("07", "Recojo de bienes transformados"),
        ("08", "Importación"),
        ("09", "Exportación"),
        ("13", "Otros"),
        ("14", "Venta sujeta a confirmación del comprador"),
        ("17", "Traslado de bienes para transformación"),
        ("18", "Traslado emisor itinerante"),
        ("19", "Traslado de mercancía extranjera a zona primaria"),
    ], string="Motivo de traslado GRE", default="01", copy=False)
    sunatfm_gre_modalidad = fields.Selection([
        ("01", "Transporte público"), ("02", "Transporte privado"),
    ], string="Modalidad de transporte", default="02", copy=False)
    sunatfm_gre_fecha_inicio = fields.Date(
        string="Fecha de inicio de traslado", default=fields.Date.context_today, copy=False,
    )
    sunatfm_gre_peso_bruto = fields.Float(string="Peso bruto total", copy=False)
    sunatfm_gre_unidad_peso = fields.Selection(
        [("KGM", "Kilogramos"), ("TNE", "Toneladas")],
        string="Unidad de peso", default="KGM", copy=False,
    )
    sunatfm_gre_numero_bultos = fields.Integer(string="Número de bultos", copy=False)
    sunatfm_gre_direccion_partida = fields.Char(string="Dirección de partida", copy=False)
    sunatfm_gre_ubigeo_partida = fields.Char(string="Ubigeo de partida", size=6, copy=False)
    sunatfm_gre_direccion_llegada = fields.Char(string="Dirección de llegada", copy=False)
    sunatfm_gre_ubigeo_llegada = fields.Char(string="Ubigeo de llegada", size=6, copy=False)
    sunatfm_gre_transportista_id = fields.Many2one(
        "res.partner", string="Empresa transportista", copy=False,
    )
    sunatfm_gre_conductor_id = fields.Many2one(
        "res.partner", string="Conductor", copy=False,
    )
    sunatfm_gre_conductor_dni = fields.Char(
        related="sunatfm_gre_conductor_id.vat",
        string="DNI que se enviará a SUNAT",
        readonly=True,
    )
    sunatfm_gre_vehiculo_placa = fields.Char(string="Placa del vehículo", copy=False)
    sunatfm_gre_vehicle_id = fields.Many2one("sunatfm.pe.vehicle", string="Vehículo", copy=False)
    sunatfm_gre_conductor_licencia = fields.Char(string="Licencia del conductor", copy=False)
    sunatfm_edi_document_ids = fields.One2many(
        "sunatfm.pe.edi.document", "stock_picking_id", string="Documentos GRE", copy=False,
    )
    sunatfm_gre_reference_ids = fields.One2many(
        "sunatfm.pe.gre.reference", "picking_id", string="Documentos relacionados GRE",
        copy=False,
    )
    sunatfm_gre_observations = fields.Text(string="Observaciones GRE", copy=False)
    sunatfm_gre_edi_document_id = fields.Many2one(
        "sunatfm.pe.edi.document", string="Documento GRE", compute="_compute_sunatfm_gre_document",
    )
    sunatfm_gre_edi_state = fields.Selection(
        related="sunatfm_gre_edi_document_id.state", string="Estado SUNAT", readonly=True,
    )
    sunatfm_gre_response_code = fields.Char(
        related="sunatfm_gre_edi_document_id.response_code", string="Código de respuesta SUNAT", readonly=True,
    )
    sunatfm_gre_response_message = fields.Text(
        related="sunatfm_gre_edi_document_id.response_message", string="Mensaje SUNAT", readonly=True,
    )
    sunatfm_gre_error_message = fields.Text(
        related="sunatfm_gre_edi_document_id.error_message", string="Motivo del rechazo / error", readonly=True,
    )
    sunatfm_gre_ticket = fields.Char(
        related="sunatfm_gre_edi_document_id.ticket", string="Ticket SUNAT", readonly=True,
    )
    sunatfm_gre_xml_filename = fields.Char(
        related="sunatfm_gre_edi_document_id.xml_filename", string="Nombre XML GRE", readonly=True,
    )
    sunatfm_gre_xml_file = fields.Binary(
        related="sunatfm_gre_edi_document_id.xml_file", string="XML GRE enviado", readonly=True,
    )
    sunatfm_gre_cdr_filename = fields.Char(
        related="sunatfm_gre_edi_document_id.cdr_filename", string="Nombre CDR GRE", readonly=True,
    )
    sunatfm_gre_cdr_file = fields.Binary(
        related="sunatfm_gre_edi_document_id.cdr_file", string="CDR GRE SUNAT", readonly=True,
    )

    @api.depends("sunatfm_edi_document_ids.document_kind", "sunatfm_edi_document_ids.create_date")
    def _compute_sunatfm_gre_document(self):
        for picking in self:
            picking.sunatfm_gre_edi_document_id = picking.sunatfm_edi_document_ids.filtered(
                lambda doc: doc.document_kind == "gre"
            ).sorted(key=lambda doc: (doc.create_date, doc.id), reverse=True)[:1]
    sunatfm_gre_series = fields.Char(string="Serie GRE", copy=False, readonly=True)
    sunatfm_gre_correlativo = fields.Integer(string="Correlativo GRE", copy=False, readonly=True)
    sunatfm_gre_number = fields.Char(
        string="Número GRE", compute="_compute_sunatfm_gre_number", copy=False,
    )

    @staticmethod
    def _sunatfm_partner_address(partner):
        """Build the fiscal address without the partner display name.

        ``contact_address`` is convenient in the form view but includes the
        contact/company name. GRE's Punto de partida/llegada must contain only
        the address and ubigeo information.
        """
        if not partner:
            return ""
        parts = [partner.street, partner.street2, partner.city]
        if partner.state_id:
            parts.append(partner.state_id.name)
        if partner.country_id and partner.country_id.code != "PE":
            parts.append(partner.country_id.name)
        return ", ".join(part.strip() for part in parts if part and part.strip())

    @staticmethod
    def _sunatfm_partner_ubigeo(partner):
        """Find the six-digit ubigeo entered on the address or its company."""
        if not partner:
            return ""
        candidates = [partner, partner.commercial_partner_id, partner.parent_id]
        candidates += list(partner.child_ids.filtered(lambda child: child.zip))
        for candidate in candidates:
            value = (candidate.zip or "").strip()
            if value.isdigit() and len(value) == 6:
                return value
        return ""

    def _sunatfm_gre_fill_addresses(self):
        self.ensure_one()
        company_partner = self.company_id.partner_id
        customer = self.partner_id
        values = {}
        # Keep the persisted values synchronized with the company/customer.
        # This also repairs GREs created before contact_address was corrected.
        values["sunatfm_gre_direccion_partida"] = self._sunatfm_partner_address(company_partner)
        values["sunatfm_gre_ubigeo_partida"] = self._sunatfm_partner_ubigeo(company_partner)
        values["sunatfm_gre_direccion_llegada"] = self._sunatfm_partner_address(customer)
        values["sunatfm_gre_ubigeo_llegada"] = self._sunatfm_partner_ubigeo(customer)
        if values:
            self.write(values)

    @api.onchange("partner_id")
    def _onchange_sunatfm_gre_destination(self):
        if self.partner_id:
            self.sunatfm_gre_direccion_llegada = self._sunatfm_partner_address(self.partner_id)
            self.sunatfm_gre_ubigeo_llegada = self._sunatfm_partner_ubigeo(self.partner_id)

    @api.onchange("sunatfm_gre_vehicle_id")
    def _onchange_sunatfm_gre_vehicle(self):
        if self.sunatfm_gre_vehicle_id:
            vehicle = self.sunatfm_gre_vehicle_id
            self.sunatfm_gre_vehiculo_placa = vehicle.license_plate
            if not self.sunatfm_gre_conductor_id:
                self.sunatfm_gre_conductor_id = vehicle.driver_id
            if not self.sunatfm_gre_conductor_licencia:
                self.sunatfm_gre_conductor_licencia = vehicle.driver_license

    def _compute_sunatfm_gre_number(self):
        for picking in self:
            if picking.sunatfm_gre_series and picking.sunatfm_gre_correlativo:
                picking.sunatfm_gre_number = "%s-%08d" % (
                    picking.sunatfm_gre_series, picking.sunatfm_gre_correlativo,
                )
            else:
                picking.sunatfm_gre_number = False

    def action_sunatfm_edi_prepare_gre(self):
        """Create/open the auditable GRE document for this delivery order."""
        self.ensure_one()
        if self.picking_type_code != "outgoing":
            raise ValidationError(_("Solo se puede emitir una GRE desde una entrega de salida."))
        if not self.company_id.sunatfm_edi_gre_enabled:
            raise ValidationError(_("Active GRE SUNAT en la configuración de la empresa."))
        self._sunatfm_gre_fill_addresses()
        document = self.env["sunatfm.pe.edi.document"].search(
            [("stock_picking_id", "=", self.id), ("document_kind", "=", "gre")],
            order="id desc", limit=1,
        )
        if not document:
            series = (self.company_id.sunatfm_edi_gre_series or "T001").strip().upper()
            if not series or len(series) != 4:
                raise ValidationError(_("La serie GRE debe tener exactamente cuatro caracteres, por ejemplo T001."))
            self.write({
                "sunatfm_gre_series": series,
                "sunatfm_gre_correlativo": self.env["ir.sequence"].next_by_code("sunatfm_pe_edi.gre") or 0,
            })
            document = self.env["sunatfm.pe.edi.document"].create({
                "name": self.sunatfm_gre_number or self.name,
                "company_id": self.company_id.id,
                "stock_picking_id": self.id,
                "document_kind": "gre",
            })
        elif document.state == "draft" and not document.xml_file:
            # Drafts created before GRE numbering was enabled may still have
            # the internal WH/OUT name. Assign the fiscal series before XML
            # generation so it is never used as the SUNAT document number.
            series = (self.company_id.sunatfm_edi_gre_series or "T001").strip().upper()
            if not series or len(series) != 4:
                raise ValidationError(_("La serie GRE debe tener exactamente cuatro caracteres, por ejemplo T001."))
            if not self.sunatfm_gre_series or not self.sunatfm_gre_correlativo:
                self.write({
                    "sunatfm_gre_series": series,
                    "sunatfm_gre_correlativo": self.env["ir.sequence"].next_by_code("sunatfm_pe_edi.gre") or 0,
                })
            document.write({"name": self.sunatfm_gre_number or document.name})
        return True

    def action_sunatfm_edi_send_gre(self):
        self.ensure_one()
        self.action_sunatfm_edi_prepare_gre()
        document = self.sunatfm_gre_edi_document_id
        if not document:
            raise ValidationError(_("No se pudo crear el documento GRE."))
        return document.action_send()

    def action_sunatfm_edi_check_gre_status(self):
        self.ensure_one()
        document = self.sunatfm_gre_edi_document_id
        if not document or document.document_kind != "gre" or not document.ticket:
            raise ValidationError(_("No existe un ticket GRE pendiente de consulta."))
        document.action_check_gre_status()
        return {
            "type": "ir.actions.act_window",
            "name": _("Guía de remisión"),
            "res_model": "stock.picking",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _sunatfm_edi_gre_qr_url(self):
        """Read the verification URL that SUNAT returns in an accepted GRE CDR."""
        self.ensure_one()
        document = self.env["sunatfm.pe.edi.document"].search(
            [
                ("stock_picking_id", "=", self.id),
                ("document_kind", "=", "gre"),
                ("state", "=", "accepted"),
                ("cdr_file", "!=", False),
            ],
            order="create_date desc, id desc",
            limit=1,
        )
        if not document:
            return False
        try:
            archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(document.cdr_file)))
            xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
            root = etree.fromstring(archive.read(xml_name))
            url = (root.xpath("//*[local-name()='DocumentDescription']/text()") or [False])[0]
            return url if url and url.startswith(("https://", "http://")) else False
        except Exception:
            return False

    def sunatfm_edi_qr_image(self):
        """Return the GRE verification QR only after SUNAT acceptance."""
        self.ensure_one()
        return qr_png_base64(self._sunatfm_edi_gre_qr_url())
