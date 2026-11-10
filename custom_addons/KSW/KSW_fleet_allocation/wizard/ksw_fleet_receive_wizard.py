from odoo import _, api, fields, models
from odoo.exceptions import UserError


class KswFleetReceiveWizard(models.TransientModel):
    _name = 'ksw.fleet.receive.wizard'
    _description = 'Confirm Vehicle Arrival'

    line_ids = fields.One2many('ksw.fleet.receive.wizard.line', 'wizard_id', string='Vehicles')

    def action_confirm(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('No vehicle to receive.'))
        for line in self.line_ids:
            if line.condition != 'good' and not line.note:
                raise UserError(_('Describe what is wrong with %s.', line.move_id.vehicle_id.name))
        self.line_ids.move_id._ksw_fleet_receive({
            line.move_id.id: {
                'received_odometer': line.odometer,
                'received_condition': line.condition,
                'received_note': line.note,
            } for line in self.line_ids})
        return {'type': 'ir.actions.act_window_close'}


class KswFleetReceiveWizardLine(models.TransientModel):
    _name = 'ksw.fleet.receive.wizard.line'
    _description = 'Confirm Vehicle Arrival: Vehicle'

    wizard_id = fields.Many2one('ksw.fleet.receive.wizard', required=True, ondelete='cascade')
    move_id = fields.Many2one('ksw.fleet.move', required=True, readonly=True)
    vehicle_id = fields.Many2one(related='move_id.vehicle_id', string='Vehicle')
    from_department_id = fields.Many2one(related='move_id.from_department_id', string='From')
    odometer = fields.Integer(string='Odometer')
    condition = fields.Selection([
        ('good', 'Good'),
        ('minor', 'Minor Issues'),
        ('damaged', 'Damaged'),
    ], required=True, default='good')
    note = fields.Char(string='Note')
