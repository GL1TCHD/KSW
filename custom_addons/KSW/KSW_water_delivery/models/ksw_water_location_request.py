from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

DISPATCHER_GROUP = 'KSW_water_delivery.group_water_dispatcher'


class KswWaterLocationRequest(models.Model):
    """A driver saying "this is where the client is", waiting for the office.

    Under the enforce rule a client with no location cannot receive a note at
    all, so the location is the gate -- and a gate a driver could open by
    himself is not one. The pin he sends from the site is therefore a request:
    a dispatcher looks at it on the map, corrects it if he must, and only the
    approved figure reaches the client. From then on every note for that
    client is checked against it.
    """
    _name = 'ksw.water.location.request'
    _description = 'Client Location Request'
    _inherit = ['mail.thread']
    _order = 'state_sequence, received_at desc, id desc'

    uuid = fields.Char(required=True, readonly=True, copy=False, index=True)
    partner_id = fields.Many2one(
        'res.partner', string='Client', required=True, readonly=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Sent by', readonly=True)
    user_id = fields.Many2one('res.users', string='Sent by (user)', readonly=True)

    # What the phone recorded, kept as sent. The two below are what will be
    # approved -- a dispatcher may move the pin onto the gate before approving,
    # and the original reading must survive that edit.
    phone_latitude = fields.Float(digits=(10, 7), readonly=True)
    phone_longitude = fields.Float(digits=(10, 7), readonly=True)
    latitude = fields.Float(digits=(10, 7), required=True, tracking=True)
    longitude = fields.Float(digits=(10, 7), required=True, tracking=True)
    accuracy_m = fields.Float(string='Accuracy (m)', readonly=True)
    captured_at = fields.Datetime(readonly=True, help="The phone's clock at the client.")
    received_at = fields.Datetime(readonly=True, default=fields.Datetime.now)
    map_url = fields.Char(string='Map', compute='_compute_map_url')

    state = fields.Selection(
        [('pending', 'Waiting for Approval'),
         ('approved', 'Approved'),
         ('rejected', 'Rejected')],
        default='pending', required=True, readonly=True, tracking=True, index=True)
    state_sequence = fields.Integer(compute='_compute_state_sequence', store=True)
    reviewed_by = fields.Many2one('res.users', readonly=True)
    reviewed_on = fields.Datetime(readonly=True)
    reject_reason = fields.Char(tracking=True)

    _uuid_unique = models.Constraint('UNIQUE(uuid)', 'This location was already received.')

    @api.depends('state')
    def _compute_state_sequence(self):
        order = {'pending': 0, 'approved': 1, 'rejected': 2}
        for request in self:
            request.state_sequence = order.get(request.state, 9)

    @api.depends('latitude', 'longitude')
    def _compute_map_url(self):
        for request in self:
            request.map_url = (
                'https://www.google.com/maps/search/?api=1&query=%.7f,%.7f'
                % (request.latitude, request.longitude)
                if request.latitude or request.longitude else False)

    def _compute_display_name(self):
        for request in self:
            request.display_name = request.partner_id.display_name or _('Location')

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        # The driver is usually standing at the gate: nothing can be issued
        # for this client until somebody approves, so tell that somebody.
        for request in requests.filtered(lambda r: r.state == 'pending'):
            request._notify_supervisor_pending()
        return requests

    # ------------------------------------------------------------------
    # Telling the supervisor
    # ------------------------------------------------------------------
    def _pending_supervisor(self):
        """The nearest Supervisor up the sender's reporting line.

        Three levels, the same reach as `rule_water_location_request_dispatcher`,
        so whoever is emailed can open the request and approve it. Never the
        sender himself: he may not approve his own pin.
        Returns (user, employee, email) or None.
        """
        self.ensure_one()
        manager = self.employee_id.sudo().parent_id
        for _level in range(3):
            if not manager:
                break
            user = manager.user_id
            email = manager.work_email or user.email
            if (user and user.active and email and user != self.user_id
                    and user.has_group(DISPATCHER_GROUP)):
                return user, manager, email
            manager = manager.parent_id
        return None

    def _notify_supervisor_pending(self):
        """Email the supervisor that a client location waits for him.

        A plain queued email in his own language, like the deduction DM
        notice (`ksw.deduction._notify_dm_deduction_applied`), and the queue is
        woken now rather than at its hourly run. A chatter note on the request
        records who was told, or that nobody could be.
        """
        self.ensure_one()
        found = self._pending_supervisor()
        if not found:
            self.sudo().message_post(
                body=_('No supervisor with an email address was found in the '
                       "driver's reporting line, so nobody was notified."),
                subtype_xmlid='mail.mt_note')
            return
        user, manager, email = found
        request = self.sudo().with_context(
            lang=user.lang or 'en_US', tz=user.tz or 'Asia/Riyadh')
        t = request.env._
        action = self.env.ref(
            'KSW_water_delivery.action_water_location_requests',
            raise_if_not_found=False)
        link = '%s/odoo/%s/%d' % (
            request.get_base_url(),
            'action-%d' % action.id if action else self._name, request.id)
        client = request.partner_id.display_name
        sent_on = fields.Datetime.context_timestamp(
            request, request.received_at or fields.Datetime.now())
        body = Markup('<p>%(dear)s</p><p>%(intro)s</p>') % {
            'dear': t('Dear %(name)s,', name=manager.name),
            'intro': Markup(t(
                '%(driver)s sent the location of <b>%(client)s</b> and it is '
                'waiting for your approval. No delivery note can be issued for '
                'this client until it is approved.')) % {
                    'driver': request.employee_id.name or request.user_id.name or '',
                    'client': client},
        }
        body += Markup(
            '<p><b>%(sent_label)s</b> %(sent)s<br/>'
            '<b>%(acc_label)s</b> ±%(acc)d m<br/>'
            '<a href="%(map)s">%(map_label)s</a></p>'
            '<p><a href="%(link)s">%(open_label)s</a></p>'
        ) % {
            'sent_label': t('Sent on:'), 'sent': sent_on.strftime('%Y-%m-%d %H:%M'),
            'acc_label': t('Accuracy:'), 'acc': int(request.accuracy_m),
            'map': request.map_url or '', 'map_label': t('View on the map'),
            'link': link, 'open_label': t('Open the request to approve or reject it'),
        }
        if (user.lang or '').startswith('ar'):
            body = Markup('<div dir="rtl">%s</div>') % body
        self.env['mail.mail'].sudo().create({
            'subject': t('Client location waiting for your approval: %(client)s',
                         client=client),
            'body_html': body,
            'email_to': email,
            'model': self._name,
            'res_id': self.id,
            'auto_delete': True,
        })
        self.env.ref('mail.ir_cron_mail_scheduler_action').sudo()._trigger()
        self.sudo().message_post(
            body=Markup('&#9993; %s') % _(
                'Supervisor %(name)s notified by email (%(email)s).',
                name=manager.name, email=email),
            subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------
    def _check_reviewer(self):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_group(DISPATCHER_GROUP):
            raise UserError(_('Only a supervisor can approve or reject a client location.'))
        # A dispatcher is also a driver (the group implies it), so he could
        # otherwise pin a client and wave his own pin through. The approval is
        # only worth something if it is somebody else's.
        if not user.has_group('base.group_system') and self.filtered(
                lambda r: r.user_id == user):
            raise UserError(_(
                'You sent this location yourself, so another supervisor has to '
                'approve it.'))

    def action_approve(self):
        self._check_reviewer()
        for request in self:
            if request.state != 'pending':
                raise UserError(_('Only a location waiting for approval can be approved.'))
            if not (request.latitude or request.longitude) or not (
                    -90.0 <= request.latitude <= 90.0
                    and -180.0 <= request.longitude <= 180.0):
                raise UserError(_('The location is not a valid position.'))
            partner = request.partner_id.sudo()
            partner.write({
                'partner_latitude': request.latitude,
                'partner_longitude': request.longitude,
                'date_localization': fields.Date.context_today(request),
            })
            request.write({
                'state': 'approved',
                'reviewed_by': self.env.user.id,
                'reviewed_on': fields.Datetime.now(),
            })
            # The client has one location; the other readings for it are
            # answered rather than left waiting forever.
            others = self.search([
                ('partner_id', '=', partner.id), ('state', '=', 'pending'),
                ('id', '!=', request.id),
            ])
            others.write({
                'state': 'rejected',
                'reviewed_by': self.env.user.id,
                'reviewed_on': fields.Datetime.now(),
                'reject_reason': _('Another location was approved for this client.'),
            })
            moved = (abs(request.latitude - request.phone_latitude) > 1e-7
                     or abs(request.longitude - request.phone_longitude) > 1e-7)
            partner.message_post(
                body=Markup(
                    '📍 Location approved by %(reviewer)s: %(lat).6f, %(lon).6f<br/>'
                    'Sent from the site by %(driver)s (±%(accuracy)d m)%(moved)s'
                ) % {
                    'reviewer': self.env.user.name,
                    'lat': request.latitude, 'lon': request.longitude,
                    'driver': request.employee_id.name or request.user_id.name or '',
                    'accuracy': int(request.accuracy_m),
                    'moved': _(', adjusted before approval') if moved else '',
                },
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_reject(self):
        self._check_reviewer()
        for request in self:
            if request.state != 'pending':
                raise UserError(_('Only a location waiting for approval can be rejected.'))
            if not request.reject_reason:
                raise UserError(_('Write the reason for rejecting it first, so the driver knows.'))
            request.write({
                'state': 'rejected',
                'reviewed_by': self.env.user.id,
                'reviewed_on': fields.Datetime.now(),
            })
        return True

    # ------------------------------------------------------------------
    # From the phone
    # ------------------------------------------------------------------
    @api.model
    def _pending_partner_ids(self, partner_ids):
        return set(self.sudo().search([
            ('partner_id', 'in', list(partner_ids)), ('state', '=', 'pending'),
        ]).partner_id.ids)
