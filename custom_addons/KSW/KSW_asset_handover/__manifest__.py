{
    'name': 'KSW IT Asset Handover',
    'version': '19.0.1.0.0',
    'author': 'KSW',
    'category': 'Services/Helpdesk',
    'summary': 'IT Team app: assign assets to employees, sign the handover form on a Wacom pad and print it',
    'description': """
IT Assets app (IT Team only)
============================

A separate top-level app for the IT Team, so asset work no longer sits
inside the Helpdesk app every employee opens. It holds the asset register,
assignments, maintenance and consumables from KSW_helpdesk, plus the signed
handover form:

- "Assign to Employee" on an asset opens the assignment form (dates,
  condition, accessories). "Save & Sign" saves it and opens the full
  handover form
- The employee (Received By) and the IT Team member (Handed Over By) each
  sign on a Wacom STU pad through KSW_wacom_signature's SigCaptX bridge, or
  draw with mouse / touch when no pad is detected
- "Print Handover Form" produces a bilingual (English / Arabic) PDF with
  the asset and employee details, an acknowledgement and both signatures

Only the IT Team (KSW_helpdesk's IT Team group) sees the app. Employees
keep their read-only "My Assets" page in the Helpdesk.
""",
    'depends': ['KSW_helpdesk', 'KSW_wacom_signature'],
    'data': [
        'report/report_it_asset_handover.xml',
        'views/it_asset_assignment_views.xml',
        'views/menu.xml',
    ],
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
