from odoo import fields, models


class SunatFmVehicle(models.Model):
    _name = "sunatfm.pe.vehicle"
    _description = "Vehículo GRE SUNAT"
    _order = "license_plate"

    name = fields.Char(string="Descripción", required=True)
    license_plate = fields.Char(string="Placa", required=True, copy=False)
    brand = fields.Char(string="Marca")
    model = fields.Char(string="Modelo")
    mtc_registration = fields.Char(string="Inscripción MTC")
    driver_id = fields.Many2one("res.partner", string="Conductor habitual")
    driver_license = fields.Char(string="Licencia del conductor")
    company_id = fields.Many2one(
        "res.company", string="Empresa", required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("license_plate_company_uniq", "unique(license_plate, company_id)", "La placa ya existe para esta empresa."),
    ]
