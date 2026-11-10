from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Backfill close_date / closed_by on tickets closed by dragging them into a
    closed stage, which never set them (only the Close button did).

    The date comes from the chatter: the last tracked stage change into a
    closed stage. A ticket with no such tracking is left as it is rather than
    given a made-up date.
    """
    cr.execute("""
        SELECT DISTINCT ON (t.id) t.id, m.date, u.id
          FROM helpdesk_ticket t
          JOIN helpdesk_ticket_stage s ON s.id = t.stage_id AND s.is_closed
          JOIN mail_message m ON m.model = 'helpdesk.ticket' AND m.res_id = t.id
          JOIN mail_tracking_value v ON v.mail_message_id = m.id
          JOIN ir_model_fields f ON f.id = v.field_id AND f.name = 'stage_id'
          JOIN helpdesk_ticket_stage ns ON ns.id = v.new_value_integer AND ns.is_closed
     LEFT JOIN res_users u ON u.partner_id = m.author_id
         WHERE t.close_date IS NULL
      ORDER BY t.id, m.date DESC, u.id
    """)
    rows = cr.fetchall()
    if not rows:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Ticket = env['helpdesk.ticket']
    for ticket_id, close_date, user_id in rows:
        # ORM write so the stored resolution_hours recomputes.
        Ticket.browse(ticket_id).write({'close_date': close_date, 'closed_by': user_id or False})
