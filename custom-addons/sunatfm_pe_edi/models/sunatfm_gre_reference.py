from odoo import api, fields, models


class SunatFmGreReference(models.Model):
    _name = "sunatfm.pe.gre.reference"
    _description = "Documento relacionado GRE SUNAT"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    picking_id = fields.Many2one(
        "stock.picking", string="Guía de remisión", required=True,
        ondelete="cascade", index=True,
    )
    document_type = fields.Selection([
        ("01", "Factura"),
        ("03", "Boleta de venta"),
        ("04", "Liquidación de compra"),
        ("09", "Guía de remisión remitente"),
        ("12", "Ticket o cinta de máquina registradora"),
        ("48", "Comprobante de operaciones - Ley 29972"),
        ("49", "Constancia de depósito - IVAP"),
        ("50", "Declaración Aduanera de Mercancías (DAM)"),
        ("52", "Declaración Simplificada (DS)"),
        ("80", "Constancia de depósito - Detracción"),
        ("81", "Autorización SCOP"),
        ("82", "Declaración jurada de mudanza"),
    ], string="Tipo de documento relacionado", required=True, default="01",
       help="Catálogo SUNAT 61 para documentos relacionados al transporte.")
    document_number = fields.Char(string="Serie y número", required=True)
    issuer_ruc = fields.Char(
        string="RUC del emisor",
        default=lambda self: self.env.company.partner_id.vat,
        help="RUC de quien emitió el documento relacionado. Para una factura propia "
             "se completa automáticamente con el RUC de la empresa.",
    )
    document_date = fields.Date(string="Fecha del documento")
    description = fields.Char(string="Descripción")

    @api.onchange("picking_id")
    def _onchange_picking_id(self):
        if self.picking_id and not self.issuer_ruc:
            self.issuer_ruc = self.picking_id.company_id.partner_id.vat
