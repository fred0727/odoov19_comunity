from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SunatDetraction(models.Model):
    _name = "sunatfm.pe.detraction"
    _description = "Catálogo de detracciones SUNAT"
    _order = "annex, code"

    annex = fields.Selection(
        [("I", "Anexo I - bienes"), ("II", "Anexo II - bienes"), ("III", "Anexo III - servicios")],
        string="Anexo SUNAT", required=True, default="III",
    )
    code = fields.Char(string="Código SUNAT", required=True)
    name = fields.Char(string="Descripción", required=True)
    rate = fields.Float(string="Tasa (%)", required=True, digits=(16, 4))
    minimum_amount = fields.Monetary(string="Monto mínimo", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id,
    )
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        "UNIQUE(annex, code)", "El código de detracción debe ser único dentro de su anexo."
    )

    @api.constrains("rate")
    def _check_rate(self):
        for record in self:
            if record.rate <= 0 or record.rate > 100:
                raise ValidationError("La tasa de detracción debe estar entre 0 y 100.")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    sunatfm_edi_sku = fields.Char(
        string="SKU",
        help="Código SKU comercial del producto. Es independiente del número de serie.",
    )
    sunatfm_edi_detraction_id = fields.Many2one(
        "sunatfm.pe.detraction",
        string="Detracción SUNAT",
        help="Se aplicará automáticamente en las facturas que incluyan este producto.",
    )
