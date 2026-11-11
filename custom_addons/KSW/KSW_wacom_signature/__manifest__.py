{
    'name': 'KSW Wacom Signature',
    'version': '19.0.1.1.0',
    'author': 'KSW',
    'category': 'Human Resources',
    'summary': 'Sign uploaded PDF documents with a Wacom STU signature pad',
    'description': """
Upload a PDF and stamp a handwritten signature onto it.

Capture works two ways: via a Wacom STU pad through the official SigCaptX
bridge (requires the Wacom Signature SDK + SigCaptX installed on the
client PC - see README.md), or as a fallback, by drawing with mouse,
touch, or any pointer device using Odoo's built-in signature pad, which
also works out of the box with a Wacom tablet used as a plain HID pointer.
""",
    'depends': ['mail', 'hr'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/ksw_signature_document_views.xml',
        'wizard/ksw_signature_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'KSW_wacom_signature/static/src/js/**/*',
            'KSW_wacom_signature/static/src/xml/**/*',
            'KSW_wacom_signature/static/src/scss/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
