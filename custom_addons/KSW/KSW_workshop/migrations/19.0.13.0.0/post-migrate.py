# -*- coding: utf-8 -*-
"""19.0.13.0.0: three workshops. Everything that exists today is Nabiya's.

Until now there was one workshop, Nabiya. Tabuk and Al-Hayat are added
empty (data/workshops.xml); this places what already exists:

- every request without a workshop -> Nabiya (raw SQL: ~17k rows, and an ORM
  write would post a tracking message on each one);
- every technician except External Service Location -> Nabiya;
- every direct Workshop Manager / Technician user except admin and OdooBot
  -> Nabiya on his user. Without it the scoped rules would hide the whole
  queue from Essam the moment the upgrade finishes. Administrators read and
  manage every workshop anyway.

Pure Reviewers (the GM) get nothing: an empty list means every workshop.
Departments get nothing either: with no home workshop they fall to the main
one, which is Nabiya. Only the Tabuk and Al-Hayat departments need setting,
by hand, together with their managers.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    nabiya = env.ref('KSW_workshop.workshop_nabiya', raise_if_not_found=False)
    if not nabiya:
        _logger.warning('KSW_workshop 19.0.13.0.0: workshop_nabiya missing, nothing placed.')
        return

    cr.execute("UPDATE ksw_workshop_request SET workshop_id = %s WHERE workshop_id IS NULL", [nabiya.id])
    _logger.info('KSW_workshop 19.0.13.0.0: %s requests placed in Nabiya.', cr.rowcount)

    cr.execute("""UPDATE ksw_workshop_technician SET workshop_id = %s
                   WHERE workshop_id IS NULL AND is_external IS NOT TRUE""", [nabiya.id])
    _logger.info('KSW_workshop 19.0.13.0.0: %s technicians placed in Nabiya.', cr.rowcount)

    technical = env['res.users']
    for xmlid in ('base.user_admin', 'base.user_root'):
        technical |= env.ref(xmlid, raise_if_not_found=False) or env['res.users']
    staff = (env.ref('KSW_workshop.group_workshop_manager').user_ids
             | env.ref('KSW_workshop.group_workshop_technician').user_ids) - technical
    staff = staff.filtered(lambda u: not u.x_workshop_ids)
    staff.write({'x_workshop_ids': [(4, nabiya.id)]})
    _logger.info('KSW_workshop 19.0.13.0.0: placed in Nabiya: %s', ', '.join(staff.mapped('login')) or 'nobody')
    env.registry.clear_cache()
